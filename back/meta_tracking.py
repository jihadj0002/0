import hashlib
import logging
import time
import uuid

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def sha256_normalized(value):
    if not value:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def get_client_ip(request):
    return request.META.get("REMOTE_ADDR")


def send_meta_event(*, event_name, event_id, request, email=None, custom_data=None):
    pixel_id = getattr(settings, "META_PIXEL_ID", "")
    token = getattr(settings, "META_CONVERSION_API_TOKEN", "")
    graph_version = getattr(settings, "META_GRAPH_API_VERSION", "v22.0")

    if not pixel_id or not token:
        logger.warning("Meta CAPI not configured — skipped %s", event_name)
        return False

    user_data = {
        "client_user_agent": request.META.get("HTTP_USER_AGENT", ""),
    }

    email_hash = sha256_normalized(email)
    if email_hash:
        user_data["em"] = [email_hash]

    ip_address = get_client_ip(request)
    if ip_address:
        user_data["client_ip_address"] = ip_address

    fbp = request.COOKIES.get("_fbp")
    fbc = request.COOKIES.get("_fbc")
    if fbp:
        user_data["fbp"] = fbp
    if fbc:
        user_data["fbc"] = fbc

    event = {
        "event_name": event_name,
        "event_time": int(time.time()),
        "event_id": event_id,
        "action_source": "website",
        "event_source_url": request.build_absolute_uri(),
        "user_data": user_data,
    }

    if custom_data:
        event["custom_data"] = custom_data

    payload = {"data": [event]}

    url = f"https://graph.facebook.com/{graph_version}/{pixel_id}/events"

    try:
        response = requests.post(
            url,
            params={"access_token": token},
            json=payload,
            timeout=5,
        )
        response.raise_for_status()
        result = response.json()
        logger.info(
            "Meta CAPI response for %s: events_received=%s",
            event_name,
            result.get("events_received"),
        )
        return True
    except (requests.RequestException, ValueError):
        logger.exception("Meta CAPI request failed for %s", event_name)
        return False