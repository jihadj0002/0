import logging

import requests

from back.models import Integration

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"


def send_reply(conversation, text, image_urls=None, product_cards=None):
    """Dispatch a reply (text + images + product cards) to the customer."""
    platform = conversation.platform
    try:
        integration = Integration.get_active(conversation.user, platform)
        if not integration or not integration.access_token:
            logger.warning("No active integration for user=%s platform=%s", conversation.user_id, platform)
            return

        if platform == "whatsapp":
            _whatsapp(conversation, integration, text, image_urls, product_cards)
        elif platform in ("messenger", "instagram"):
            _messenger(conversation, integration, text, image_urls, product_cards)
        elif platform == "telegram":
            _telegram(conversation, integration, text, image_urls, product_cards)

    except Exception:
        logger.exception("send_reply failed conv=%s platform=%s", conversation.pk, platform)


# ---------------------------------------------------------------------------
# Platform senders
# ---------------------------------------------------------------------------

def _whatsapp(conversation, integration, text, image_urls, product_cards=None):
    phone_number_id = integration.integration_id
    token = integration.access_token
    url = f"{GRAPH_API_BASE}/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    to = conversation.customer_id

    # WhatsApp has no card carousel — send each product's first image with a
    # name + price caption as a fallback.
    for card in (product_cards or [])[:5]:
        images = card.get("images") or []
        if not images:
            continue
        _post(url, headers, {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "image",
            "image": {"link": images[0], "caption": _card_caption(card)},
        })

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


def _messenger(conversation, integration, text, image_urls, product_cards=None):
    token = integration.access_token
    url = f"{GRAPH_API_BASE}/me/messages"
    headers = {"Authorization": f"Bearer {token}"}
    recipient = {"id": conversation.customer_id}

    # Send product cards as a generic template carousel (max 10 elements)
    if product_cards:
        elements = []
        for card in product_cards[:10]:
            images = card.get("images", [])
            price_str = ""
            if card.get("discounted_price"):
                price_str = f"৳{card['discounted_price']}"
            elif card.get("price"):
                price_str = f"৳{card['price']}"
            subtitle = price_str[:80] if price_str else " "
            btn_pid = card.get("sku") or card.get("pid", "")
            elements.append({
                "title": (card.get("name") or "Product")[:80],
                "subtitle": subtitle,
                "image_url": images[0] if images else None,
                "buttons": [
                    {
                        "type": "postback",
                        "title": f"View {card.get('name', 'Product')}"[:20],
                        "payload": f"SELECT_PRODUCT|{btn_pid}",
                    }
                ],
            })
        if elements:
            _post(url, headers, {
                "recipient": recipient,
                "message": {
                    "attachment": {
                        "type": "template",
                        "payload": {
                            "template_type": "generic",
                            "elements": elements,
                        },
                    }
                },
            })

    for img_url in (image_urls or [])[:5]:
        _post(url, headers, {
            "recipient": recipient,
            "message": {"attachment": {"type": "image", "payload": {"url": img_url, "is_reusable": True}}},
        })

    if text:
        _post(url, headers, {"recipient": recipient, "message": {"text": text}})


def _telegram(conversation, integration, text, image_urls, product_cards=None):
    token = integration.access_token
    chat_id = conversation.customer_id
    base = f"https://api.telegram.org/bot{token}"

    # Telegram has no card carousel — send each product's first image with a
    # name + price caption as a fallback.
    for card in (product_cards or [])[:5]:
        images = card.get("images") or []
        if not images:
            continue
        _post(f"{base}/sendPhoto", {}, {
            "chat_id": chat_id, "photo": images[0], "caption": _card_caption(card),
        })

    for img_url in (image_urls or [])[:5]:
        _post(f"{base}/sendPhoto", {}, {"chat_id": chat_id, "photo": img_url})

    if text:
        _post(f"{base}/sendMessage", {}, {"chat_id": chat_id, "text": text})


def _card_caption(card):
    """Short 'Name — ৳price' caption for platforms without a card carousel."""
    name = (card.get("name") or "Product").strip()
    price = card.get("discounted_price") or card.get("price")
    return f"{name} — ৳{price}" if price else name


def _post(url, headers, payload):
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if not resp.ok:
            logger.warning("Platform send failed %s %s: %s", url, payload.get("type", ""), resp.text[:200])
    except requests.RequestException as exc:
        logger.warning("Platform send request error: %s", exc)
