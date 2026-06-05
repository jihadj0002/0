"""
Configure the app-level Meta webhook subscriptions (Epic 8).

This replaces the manual step in the App Dashboard of pointing the `page` and
`instagram` webhook objects at our callback URL. It uses an app access token
(`{APP_ID}|{APP_SECRET}`) to POST to the App Subscriptions API.

NOTE: Meta verifies the callback URL synchronously by issuing a GET challenge,
so the callback URL MUST be publicly reachable (and serve our verify token)
at the time you run this command. Localhost will fail verification.

Usage:
    python manage.py setup_meta_webhook
    python manage.py setup_meta_webhook --callback-url https://thematrixai.xyz/api/meta/webhook/
    python manage.py setup_meta_webhook --dry-run
"""

from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

# Per-object field subscriptions for the app-level webhook.
_FIELDS = {
    "page": "messages,messaging_postbacks,message_reads,messaging_optins,messaging_reactions,feed",
    "instagram": "messages,comments,mentions",
}


class Command(BaseCommand):
    help = (
        "Set the app-level Meta webhook subscriptions for the `page` and "
        "`instagram` objects (callback URL + verify token + fields). The "
        "callback URL must be PUBLICLY reachable so Meta can verify it."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--callback-url",
            dest="callback_url",
            default=None,
            help="Override the webhook callback URL (default: host of "
                 "META_OAUTH_REDIRECT_URI + /api/meta/webhook/).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the POSTs that would be made without sending them.",
        )

    def _default_callback_url(self):
        redirect_uri = settings.META_OAUTH_REDIRECT_URI or ""
        parts = urlsplit(redirect_uri)
        if not parts.scheme or not parts.netloc:
            return ""
        return f"{parts.scheme}://{parts.netloc}/api/meta/webhook/"

    def handle(self, *args, **options):
        app_id = settings.META_APP_ID
        app_secret = settings.META_APP_SECRET
        verify_token = settings.META_WEBHOOK_VERIFY_TOKEN

        missing = [
            name for name, val in (
                ("META_APP_ID", app_id),
                ("META_APP_SECRET", app_secret),
                ("META_WEBHOOK_VERIFY_TOKEN", verify_token),
            ) if not val
        ]
        if missing:
            raise CommandError(
                "Missing required setting(s): " + ", ".join(missing)
            )

        callback_url = options["callback_url"] or self._default_callback_url()
        if not callback_url:
            raise CommandError(
                "Could not determine a callback URL. Pass --callback-url "
                "explicitly (META_OAUTH_REDIRECT_URI is empty or has no host)."
            )

        dry_run = options["dry_run"]
        app_token = f"{app_id}|{app_secret}"
        url = (
            f"https://graph.facebook.com/{settings.META_GRAPH_VERSION}"
            f"/{app_id}/subscriptions"
        )

        self.stdout.write(
            "Callback URL must be publicly reachable for Meta to verify it "
            "(it issues a GET challenge synchronously)."
        )
        self.stdout.write(f"Callback URL: {callback_url}")

        for obj, fields in _FIELDS.items():
            payload = {
                "object": obj,
                "callback_url": callback_url,
                "verify_token": verify_token,
                "fields": fields,
                "include_values": "true",
                "access_token": app_token,
            }
            # Never print the access token / secret.
            safe = {k: v for k, v in payload.items() if k != "access_token"}
            self.stdout.write("")
            self.stdout.write(f"POST {url}")
            self.stdout.write(f"  {safe}")

            if dry_run:
                self.stdout.write(self.style.WARNING("  [dry-run] not sent"))
                continue

            try:
                resp = requests.post(url, data=payload, timeout=20)
                try:
                    body = resp.json()
                except ValueError:
                    body = (resp.text or "")[:500]
                line = f"  -> status={resp.status_code} body={body}"
                if resp.ok and not (isinstance(body, dict) and body.get("error")):
                    self.stdout.write(self.style.SUCCESS(line))
                else:
                    self.stdout.write(self.style.ERROR(line))
            except requests.RequestException as exc:
                self.stdout.write(self.style.ERROR(f"  -> request failed: {exc}"))
