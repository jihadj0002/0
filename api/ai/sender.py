import logging

import requests

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"


def send_reply(conversation, text, image_urls=None):
    """Dispatch a reply (text + optional images) to the customer via their platform."""
    platform = conversation.platform
    try:
        integration = conversation.user.integrations.filter(platform=platform).first()
        if not integration or not integration.access_token:
            logger.warning("No active integration for user=%s platform=%s", conversation.user_id, platform)
            return

        if platform == "whatsapp":
            _whatsapp(conversation, integration, text, image_urls)
        elif platform in ("messenger", "instagram"):
            _messenger(conversation, integration, text, image_urls)
        elif platform == "telegram":
            _telegram(conversation, integration, text, image_urls)

    except Exception:
        logger.exception("send_reply failed conv=%s platform=%s", conversation.pk, platform)


# ---------------------------------------------------------------------------
# Platform senders
# ---------------------------------------------------------------------------

def _whatsapp(conversation, integration, text, image_urls):
    phone_number_id = integration.integration_id
    token = integration.access_token
    url = f"{GRAPH_API_BASE}/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    to = conversation.customer_id

    for img_url in (image_urls or [])[:5]:
        _post(url, headers, {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "image",
            "image": {"link": img_url},
        })

    if text:
        _post(url, headers, {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text, "preview_url": False},
        })


def _messenger(conversation, integration, text, image_urls):
    token = integration.access_token
    url = f"{GRAPH_API_BASE}/me/messages"
    headers = {"Authorization": f"Bearer {token}"}
    recipient = {"id": conversation.customer_id}

    for img_url in (image_urls or [])[:5]:
        _post(url, headers, {
            "recipient": recipient,
            "message": {"attachment": {"type": "image", "payload": {"url": img_url, "is_reusable": True}}},
        })

    if text:
        _post(url, headers, {"recipient": recipient, "message": {"text": text}})


def _telegram(conversation, integration, text, image_urls):
    token = integration.access_token
    chat_id = conversation.customer_id
    base = f"https://api.telegram.org/bot{token}"

    for img_url in (image_urls or [])[:5]:
        _post(f"{base}/sendPhoto", {}, {"chat_id": chat_id, "photo": img_url})

    if text:
        _post(f"{base}/sendMessage", {}, {"chat_id": chat_id, "text": text})


def _post(url, headers, payload):
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if not resp.ok:
            logger.warning("Platform send failed %s %s: %s", url, payload.get("type", ""), resp.text[:200])
    except requests.RequestException as exc:
        logger.warning("Platform send request error: %s", exc)
