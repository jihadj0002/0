"""
Meta (Facebook) OAuth service + dashboard views for Epic 8 (One-Click Connect).

This module is ADDITIVE — the existing per-user webhook views in
`api/webhooks.py` continue to work unchanged. OAuth-connected pages are routed
through the single app-level webhook (`MetaAppWebhookView`) instead.

Security:
  * `state` CSRF param is generated server-side, stored in the session, and
    validated on callback (mismatch/missing → reject).
  * Page access tokens are read ONLY from the server-side session, never
    trusted from a client POST.
  * Access tokens / secrets are never written to logs.
"""

import logging
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from back.models import Integration

logger = logging.getLogger(__name__)

GRAPH = f"https://graph.facebook.com/{settings.META_GRAPH_VERSION}"
_TIMEOUT = 20

# Fields we subscribe a Page to on the app-level webhook.
# `feed` delivers Page post comments; the messaging_* fields deliver DMs.
_SUBSCRIBE_FIELDS = (
    "messages,messaging_postbacks,message_deliveries,messaging_optins,"
    "message_reads,messaging_reactions,feed"
)


# ---------------------------------------------------------------------------
# Graph API wrappers — each returns parsed JSON or {"error": "..."}.
# ---------------------------------------------------------------------------

def _scopes():
    """Return the configured scopes as a comma-separated string."""
    raw = settings.META_OAUTH_SCOPES or ""
    parts = [s.strip() for s in raw.replace(" ", ",").split(",") if s.strip()]
    return ",".join(parts)


def build_auth_url(redirect_uri, state):
    """Build the Facebook OAuth dialog URL the browser is redirected to."""
    params = {
        "client_id": settings.META_APP_ID,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": _scopes(),
        "response_type": "code",
    }
    return (
        f"https://www.facebook.com/{settings.META_GRAPH_VERSION}/dialog/oauth"
        f"?{urlencode(params)}"
    )


def exchange_code_for_token(code, redirect_uri):
    """Exchange an auth code for a short-lived user access token."""
    try:
        resp = requests.get(
            f"{GRAPH}/oauth/access_token",
            params={
                "client_id": settings.META_APP_ID,
                "client_secret": settings.META_APP_SECRET,
                "redirect_uri": redirect_uri,
                "code": code,
            },
            timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        logger.warning("exchange_code_for_token failed: %s", exc)
        return {"error": str(exc)}


def get_long_lived_token(short_token):
    """Exchange a short-lived user token for a long-lived one (~60 days)."""
    try:
        resp = requests.get(
            f"{GRAPH}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.META_APP_ID,
                "client_secret": settings.META_APP_SECRET,
                "fb_exchange_token": short_token,
            },
            timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        logger.warning("get_long_lived_token failed: %s", exc)
        return {"error": str(exc)}


def get_me(user_token):
    """Fetch the connecting Facebook user's id + name."""
    try:
        resp = requests.get(
            f"{GRAPH}/me",
            params={"fields": "id,name", "access_token": user_token},
            timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        logger.warning("get_me failed: %s", exc)
        return {"error": str(exc)}


def list_pages(user_token):
    """List Pages the user manages, with per-page tokens and linked IG account."""
    try:
        resp = requests.get(
            f"{GRAPH}/me/accounts",
            params={
                "fields": "id,name,access_token,instagram_business_account",
                "access_token": user_token,
            },
            timeout=_TIMEOUT,
        )
        data = resp.json()
        if isinstance(data, dict) and data.get("error"):
            return {"error": data["error"]}
        return data.get("data", []) if isinstance(data, dict) else []
    except Exception as exc:
        logger.warning("list_pages failed: %s", exc)
        return {"error": str(exc)}


def subscribe_page(page_id, page_token):
    """Subscribe the app to a Page's messaging events (app-level webhook)."""
    try:
        resp = requests.post(
            f"{GRAPH}/{page_id}/subscribed_apps",
            data={"subscribed_fields": _SUBSCRIBE_FIELDS, "access_token": page_token},
            timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        logger.warning("subscribe_page failed page=%s: %s", page_id, exc)
        return {"error": str(exc)}


def unsubscribe_page(page_id, page_token):
    """Remove the app's subscription from a Page."""
    try:
        resp = requests.delete(
            f"{GRAPH}/{page_id}/subscribed_apps",
            params={"access_token": page_token},
            timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        logger.warning("unsubscribe_page failed page=%s: %s", page_id, exc)
        return {"error": str(exc)}


def get_page(page_id, page_token):
    """Fetch a Page's name + linked IG business account."""
    try:
        resp = requests.get(
            f"{GRAPH}/{page_id}",
            params={"fields": "name,instagram_business_account", "access_token": page_token},
            timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        logger.warning("get_page failed page=%s: %s", page_id, exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Internal: connect a single page (shared by callback single-page + select).
# ---------------------------------------------------------------------------

def _connect_page(user, page, meta_user_id, expires_in):
    """
    Upsert the messenger (and optional instagram) Integration for one page.
    `page` is a session dict: {"id","name","access_token","ig"}.
    Returns (page_name, warning_or_None).
    """
    page_id = page["id"]
    page_token = page.get("access_token") or ""
    page_name = page.get("name") or page_id
    ig_id = page.get("ig") or ""

    expires_at = None
    if expires_in:
        try:
            expires_at = timezone.now() + timedelta(seconds=int(expires_in))
        except (TypeError, ValueError):
            expires_at = None

    Integration.objects.update_or_create(
        user=user,
        platform="messenger",
        integration_id=page_id,
        defaults={
            "access_token": page_token,
            "app_secret": settings.META_APP_SECRET,
            "page_name": page_name,
            "connection_method": "oauth",
            "is_connected": True,
            "is_enabled": True,
            "meta_user_id": meta_user_id or "",
            "token_expires_at": expires_at,
        },
    )

    warning = None
    sub = subscribe_page(page_id, page_token)
    if isinstance(sub, dict) and sub.get("error"):
        warning = page_name

    # Linked Instagram business account → upsert an instagram Integration too.
    if ig_id:
        Integration.objects.update_or_create(
            user=user,
            platform="instagram",
            integration_id=page_id,
            defaults={
                "access_token": page_token,
                "app_secret": settings.META_APP_SECRET,
                "page_name": page_name,
                "connection_method": "oauth",
                "is_connected": True,
                "is_enabled": True,
                "meta_user_id": meta_user_id or "",
                "token_expires_at": expires_at,
                "ig_account_id": ig_id,
            },
        )

    return page_name, warning


def _clear_oauth_session(request):
    for key in (
        "meta_oauth_state",
        "meta_oauth_pages",
        "meta_oauth_user_id",
        "meta_oauth_expires_in",
        "meta_oauth_token",
    ):
        request.session.pop(key, None)


# ---------------------------------------------------------------------------
# Dashboard views (browser, session-authenticated).
# ---------------------------------------------------------------------------

@login_required
def meta_oauth_start(request):
    """Kick off the OAuth flow: store state in session, redirect to Facebook."""
    if not settings.META_APP_ID:
        messages.error(request, "Facebook app not configured")
        return redirect("back:options")

    state = secrets.token_urlsafe(24)
    request.session["meta_oauth_state"] = state

    redirect_uri = settings.META_OAUTH_REDIRECT_URI or request.build_absolute_uri(
        reverse("api:meta-oauth-callback")
    )
    return redirect(build_auth_url(redirect_uri, state))


@login_required
def meta_oauth_callback(request):
    """Handle the OAuth redirect: validate state, exchange tokens, list pages."""
    if request.GET.get("error"):
        messages.error(request, "Facebook connection was cancelled or denied.")
        return redirect("back:options")

    # CSRF: state must match what we stored (and is single-use).
    expected_state = request.session.pop("meta_oauth_state", None)
    if not expected_state or request.GET.get("state") != expected_state:
        messages.error(request, "Invalid OAuth state. Please try connecting again.")
        return redirect("back:options")

    code = request.GET.get("code")
    if not code:
        messages.error(request, "No authorization code returned from Facebook.")
        return redirect("back:options")

    redirect_uri = settings.META_OAUTH_REDIRECT_URI or request.build_absolute_uri(
        reverse("api:meta-oauth-callback")
    )

    short = exchange_code_for_token(code, redirect_uri)
    if short.get("error") or not short.get("access_token"):
        messages.error(request, "Could not complete Facebook login. Please try again.")
        return redirect("back:options")

    long_lived = get_long_lived_token(short["access_token"])
    if long_lived.get("error") or not long_lived.get("access_token"):
        messages.error(request, "Could not obtain a long-lived token. Please try again.")
        return redirect("back:options")

    user_token = long_lived["access_token"]
    expires_in = long_lived.get("expires_in")

    me = get_me(user_token)
    meta_user_id = me.get("id", "") if isinstance(me, dict) else ""

    pages = list_pages(user_token)
    if isinstance(pages, dict) and pages.get("error"):
        messages.error(request, "Could not read your Facebook Pages. Please try again.")
        return redirect("back:options")

    page_list = [
        {
            "id": p.get("id"),
            "name": p.get("name") or p.get("id"),
            "access_token": p.get("access_token") or "",
            "ig": (p.get("instagram_business_account") or {}).get("id", "") or "",
        }
        for p in pages
        if p.get("id")
    ]

    # Stash everything server-side. Page tokens NEVER go to the client.
    request.session["meta_oauth_pages"] = page_list
    request.session["meta_oauth_user_id"] = meta_user_id
    request.session["meta_oauth_expires_in"] = expires_in

    if not page_list:
        _clear_oauth_session(request)
        messages.error(request, "No Facebook Pages found on your account.")
        return redirect("back:options")

    if len(page_list) == 1:
        name, warning = _connect_page(request.user, page_list[0], meta_user_id, expires_in)
        _clear_oauth_session(request)
        if warning:
            messages.warning(
                request,
                f"Connected {name}, but event subscription may need attention.",
            )
        else:
            messages.success(request, f"Connected Facebook Page: {name}")
        return redirect("back:options")

    # Multiple pages → let the user choose.
    return render(request, "back/meta_select_pages.html", {"pages": page_list})


@login_required
@require_POST
def meta_oauth_select(request):
    """Connect the page(s) the user selected. Tokens come from the session."""
    session_pages = request.session.get("meta_oauth_pages") or []
    if not session_pages:
        messages.error(request, "Your connection session expired. Please try again.")
        return redirect("back:options")

    by_id = {p["id"]: p for p in session_pages}
    selected_ids = request.POST.getlist("page_id")
    selected = [by_id[pid] for pid in selected_ids if pid in by_id]

    if not selected:
        messages.error(request, "No pages selected.")
        return redirect("back:options")

    meta_user_id = request.session.get("meta_oauth_user_id", "")
    expires_in = request.session.get("meta_oauth_expires_in")

    connected_names = []
    warnings = []
    for page in selected:
        name, warning = _connect_page(request.user, page, meta_user_id, expires_in)
        connected_names.append(name)
        if warning:
            warnings.append(warning)

    _clear_oauth_session(request)

    if connected_names:
        messages.success(request, "Connected: " + ", ".join(connected_names))
    if warnings:
        messages.warning(
            request,
            "Event subscription may need attention for: " + ", ".join(warnings),
        )
    return redirect("back:options")


@login_required
@require_POST
def meta_disconnect(request, platform):
    """Disconnect the current user's OAuth integration for a platform."""
    integration = Integration.objects.filter(
        user=request.user, platform=platform
    ).first()

    if not integration:
        messages.error(request, "No integration found to disconnect.")
        return redirect("back:options")

    if integration.connection_method == "oauth" and integration.integration_id:
        unsubscribe_page(integration.integration_id, integration.access_token or "")

    integration.access_token = None
    integration.integration_id = None
    integration.is_connected = False
    integration.is_enabled = False
    integration.connection_method = "manual"
    integration.save()

    messages.success(request, f"Disconnected {platform}.")
    return redirect("back:options")
