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
    "messages, messaging_postbacks, feed"
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
    """List Pages the user manages, with per-page tokens and linked IG account.

    Follows ``paging.next`` so accounts with more than one page of results
    (default Graph page size) are not silently truncated.
    """
    pages = []
    url = f"{GRAPH}/me/accounts"
    params = {
        "fields": "id,name,access_token,instagram_business_account",
        "access_token": user_token,
        "limit": 100,
    }
    try:
        while url:
            resp = requests.get(url, params=params, timeout=_TIMEOUT)
            data = resp.json()
            if isinstance(data, dict) and data.get("error"):
                return {"error": data["error"]}
            if isinstance(data, dict):
                pages.extend(data.get("data", []))
                url = (data.get("paging") or {}).get("next") or ""
            else:
                url = ""
            params = None  # paging.next URLs carry their own parameters
        return pages
    except Exception as exc:
        logger.warning("list_pages failed: %s", exc)
        return {"error": str(exc)}


def get_permissions(user_token):
    """List the permissions granted on a user token (connect diagnostics)."""
    try:
        resp = requests.get(
            f"{GRAPH}/me/permissions",
            params={"access_token": user_token},
            timeout=_TIMEOUT,
        )
        data = resp.json()
        if isinstance(data, dict) and data.get("error"):
            return {"error": data["error"]}
        granted = {}
        for perm in data.get("data", []) if isinstance(data, dict) else []:
            granted[perm.get("permission")] = perm.get("status")
        return granted
    except Exception as exc:
        logger.warning("get_permissions failed: %s", exc)
        return {"error": str(exc)}


def _body(resp):
    """Parse a response body as JSON, falling back to truncated text. No tokens here."""
    try:
        return resp.json()
    except ValueError:
        return (resp.text or "")[:500]


def subscribe_page(page_id, page_token):
    """
    Subscribe the app to a Page's messaging events (app-level webhook).

    Returns a normalized dict ``{"ok": bool, "status": int, "body": <json|text>}``.
    """
    try:
        resp = requests.post(
            f"{GRAPH}/{page_id}/subscribed_apps",
            data={"subscribed_fields": _SUBSCRIBE_FIELDS, "access_token": page_token},
            timeout=_TIMEOUT,
        )
        body = _body(resp)
        ok = resp.ok and not (isinstance(body, dict) and body.get("error"))
        logger.info(
            "subscribe_page page=%s status=%s ok=%s body=%s",
            page_id, resp.status_code, ok, body,
        )
        return {"ok": ok, "status": resp.status_code, "body": body}
    except Exception as exc:
        logger.warning("subscribe_page failed page=%s: %s", page_id, exc)
        return {"ok": False, "status": 0, "body": {"error": str(exc)}}


def get_subscribed_apps(page_id, page_token):
    """GET the apps currently subscribed to a Page (diagnostic confirmation)."""
    try:
        resp = requests.get(
            f"{GRAPH}/{page_id}/subscribed_apps",
            params={"access_token": page_token},
            timeout=_TIMEOUT,
        )
        return _body(resp)
    except Exception as exc:
        logger.warning("get_subscribed_apps failed page=%s: %s", page_id, exc)
        return {"error": str(exc)}


def unsubscribe_page(page_id, page_token):
    """
    Remove the app's subscription from a Page.

    Returns a normalized dict ``{"ok": bool, "status": int, "body": <json|text>}``.
    """
    try:
        resp = requests.delete(
            f"{GRAPH}/{page_id}/subscribed_apps",
            params={"access_token": page_token},
            timeout=_TIMEOUT,
        )
        body = _body(resp)
        ok = resp.ok and not (isinstance(body, dict) and body.get("error"))
        logger.info(
            "unsubscribe_page page=%s status=%s ok=%s body=%s",
            page_id, resp.status_code, ok, body,
        )
        return {"ok": ok, "status": resp.status_code, "body": body}
    except Exception as exc:
        logger.warning("unsubscribe_page failed page=%s: %s", page_id, exc)
        return {"ok": False, "status": 0, "body": {"error": str(exc)}}


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

    # Lookup key is (user, platform) only — the app is one-page-per-platform-per-user,
    # so this reuses/updates the single existing row (incl. the manual auto-created one)
    # instead of creating a duplicate when integration_id differs.
    Integration.objects.update_or_create(
        user=user,
        platform="messenger",
        defaults={
            "integration_id": page_id,
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
    if not sub.get("ok"):
        body = sub.get("body")
        err = body.get("error", {}) if isinstance(body, dict) else {}
        detail = err.get("message") if isinstance(err, dict) else None
        warning = f"{page_name}: {detail}" if detail else page_name
    else:
        # Confirm this app now appears in the Page's subscribed apps.
        confirm = get_subscribed_apps(page_id, page_token)
        logger.info("subscribed_apps page=%s confirm=%s", page_id, confirm)

    # Linked Instagram business account → upsert an instagram Integration too.
    if ig_id:
        Integration.objects.update_or_create(
            user=user,
            platform="instagram",
            defaults={
                "integration_id": page_id,
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
    # NOTE: `meta_oauth_next` is intentionally NOT cleared here — it is a
    # navigation token consumed only by `_redirect_after_oauth()` (which runs
    # after this function in the callback/select flows).
    for key in (
        "meta_oauth_state",
        "meta_oauth_pages",
        "meta_oauth_user_id",
        "meta_oauth_expires_in",
        "meta_oauth_token",
        "meta_oauth_retried_pages",
    ):
        request.session.pop(key, None)


def _redirect_after_oauth(request, default="back:options"):
    """Redirect the user after an OAuth outcome.

    Honors a sanitized ``?next=`` stored in the session (used by the setup
    wizard to keep the user in flow); falls back to the Integrations page —
    or to the setup wizard when the user has not completed first-run setup.
    """
    target = request.session.pop("meta_oauth_next", None)
    if target and target.startswith("/") and not target.startswith("//"):
        return redirect(target)

    # User hasn't finished onboarding → keep them in the wizard.
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        try:
            from back.views import _needs_setup
            if _needs_setup(user):
                return redirect(reverse("back:setup"))
        except Exception:
            pass

    return redirect(default)


# ---------------------------------------------------------------------------
# Dashboard views (browser, session-authenticated).
# ---------------------------------------------------------------------------

def _finish_token_flow(request, user_token, expires_in):
    """Common continuation after a long-lived user token is available.

    Resolves the user's Pages and then either connects the single page
    (→ `_redirect_after_oauth`) or stashes the list and sends the user to
    the page-picker (→ `meta-oauth-choose`). Shared by the server-side
    redirect flow and the mobile JS SDK flow.
    """
    # Keep the token around briefly so `meta-oauth-choose` can re-list pages
    # when the session payload is lost mid-flow (mobile in-app browsers).
    request.session["meta_oauth_token"] = user_token

    me = get_me(user_token)
    meta_user_id = me.get("id", "") if isinstance(me, dict) else ""
    fb_name = me.get("name", "") if isinstance(me, dict) else me.get("id", "")

    pages = list_pages(user_token)
    if isinstance(pages, dict) and pages.get("error"):
        messages.error(request, "Could not read your Facebook Pages. Please try again.")
        return _redirect_after_oauth(request)

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

        # Diagnose instead of dead-ending: which permissions are missing tells
        # the user exactly what to re-grant in Facebook's dialog.
        detail = ""
        perms = get_permissions(user_token)
        if isinstance(perms, dict) and not perms.get("error"):
            requested = {
                s.strip() for s in _scopes().split(",") if s.strip()
            }
            missing = sorted(
                requested - {p for p, st in perms.items() if st == "granted"}
            )
            if missing:
                shown = ", ".join(f"“{m}”" for m in missing[:6])
                if len(missing) > 6:
                    shown += "…"
                detail = f" Missing permission(s): {shown}."

        messages.error(
            request,
            "Connected as "
            + (fb_name or "unknown Facebook account")
            + " — no Facebook Pages found on that account. Make sure you allow "
            "Pages access in Facebook's dialog and select a Page to connect."
            + detail,
        )
        return _redirect_after_oauth(request)

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
        return _redirect_after_oauth(request)

    # Multiple pages → let the user choose (GET view so the SDK fetch can
    # follow the redirect and land on the picker).
    return redirect(reverse("api:meta-oauth-choose"))


@login_required
def meta_oauth_start(request):
    """Kick off the OAuth flow: store state in session, redirect to Facebook."""
    if not settings.META_APP_ID:
        messages.error(request, "Facebook app not configured")
        return _redirect_after_oauth(request)

    state = secrets.token_urlsafe(24)
    request.session["meta_oauth_state"] = state

    # Optional sanitized ?next= — the setup wizard passes /dbsetup/ so the
    # user returns to the wizard (not the Integrations page) after connecting.
    # On a retry the previous next value (if any) is preserved.
    next_url = request.GET.get("next", "")
    if next_url.startswith("/") and not next_url.startswith("//"):
        request.session["meta_oauth_next"] = next_url

    redirect_uri = settings.META_OAUTH_REDIRECT_URI or request.build_absolute_uri(
        reverse("api:meta-oauth-callback")
    )
    return redirect(build_auth_url(redirect_uri, state))


@login_required
def meta_oauth_callback(request):
    """Handle the OAuth redirect: validate state, exchange tokens, list pages."""
    if request.GET.get("error"):
        messages.error(request, "Facebook connection was cancelled or denied.")
        return _redirect_after_oauth(request)

    # CSRF: state must match what we stored (and is single-use).
    expected_state = request.session.pop("meta_oauth_state", None)
    if not expected_state or request.GET.get("state") != expected_state:
        if not request.session.get("meta_oauth_retried"):
            # Mobile in-app browsers sometimes drop the session cookie during
            # the Facebook round-trip → one automatic re-initiation instead of
            # hard-failing (loop stays bounded by the retried flag).
            request.session["meta_oauth_retried"] = True
            return redirect(reverse("api:meta-oauth-start"))
        request.session.pop("meta_oauth_retried", None)
        messages.error(request, "Invalid OAuth state. Please try connecting again.")
        return _redirect_after_oauth(request)
    request.session.pop("meta_oauth_retried", None)

    code = request.GET.get("code")
    if not code:
        messages.error(request, "No authorization code returned from Facebook.")
        return _redirect_after_oauth(request)

    redirect_uri = settings.META_OAUTH_REDIRECT_URI or request.build_absolute_uri(
        reverse("api:meta-oauth-callback")
    )

    short = exchange_code_for_token(code, redirect_uri)
    if short.get("error") or not short.get("access_token"):
        messages.error(request, "Could not complete Facebook login. Please try again.")
        return _redirect_after_oauth(request)

    long_lived = get_long_lived_token(short["access_token"])
    if long_lived.get("error") or not long_lived.get("access_token"):
        messages.error(request, "Could not obtain a long-lived token. Please try again.")
        return _redirect_after_oauth(request)

    return _finish_token_flow(
        request,
        long_lived["access_token"],
        long_lived.get("expires_in"),
    )


@login_required
@require_POST
def meta_oauth_token(request):
    """Complete the connection from the FB JS SDK flow (mobile-friendly).

    Receives the user access token returned by ``FB.login()`` in the browser
    and continues exactly like the server-side callback. The token transits
    the client by design (Meta's SDK flow); Page tokens stay server-side.
    """
    user_token = (request.POST.get("access_token") or "").strip()
    if not user_token:
        messages.error(request, "No access token received from Facebook.")
        return _redirect_after_oauth(request)

    # The JS SDK flow has no ?next= in the URL — the page JS sends it as a
    # form field. Sanitized the same way as meta_oauth_start.
    next_url = request.POST.get("next", "")
    if next_url.startswith("/") and not next_url.startswith("//"):
        request.session["meta_oauth_next"] = next_url

    long_lived = get_long_lived_token(user_token)
    if long_lived.get("error") or not long_lived.get("access_token"):
        messages.error(request, "Could not complete Facebook login. Please try again.")
        return _redirect_after_oauth(request)

    return _finish_token_flow(
        request,
        long_lived["access_token"],
        long_lived.get("expires_in"),
    )


@login_required
def meta_oauth_choose(request):
    """Render the page-picker. Used by both the redirect and SDK flows."""
    page_list = request.session.get("meta_oauth_pages") or []
    if not page_list:
        # In-app browsers sometimes drop the session cookie mid-flow — re-list
        # the pages from the retained token once instead of hard-failing.
        token = request.session.get("meta_oauth_token") or ""
        if token and not request.session.get("meta_oauth_retried_pages"):
            request.session["meta_oauth_retried_pages"] = True
            return _finish_token_flow(
                request,
                token,
                request.session.get("meta_oauth_expires_in"),
            )
        request.session.pop("meta_oauth_retried_pages", None)
        messages.error(request, "Your connection session expired. Please try again.")
        return _redirect_after_oauth(request)
    return render(request, "back/meta_select_pages.html", {"pages": page_list})


@login_required
@require_POST
def meta_oauth_select(request):
    """Connect the page(s) the user selected. Tokens come from the session."""
    session_pages = request.session.get("meta_oauth_pages") or []
    if not session_pages:
        messages.error(request, "Your connection session expired. Please try again.")
        return _redirect_after_oauth(request)

    by_id = {p["id"]: p for p in session_pages}
    selected_ids = request.POST.getlist("page_id")
    selected = [by_id[pid] for pid in selected_ids if pid in by_id]

    if not selected:
        messages.error(request, "No pages selected.")
        return _redirect_after_oauth(request)

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
    return _redirect_after_oauth(request)


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
