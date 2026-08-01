import logging

import requests

from back.models import Integration

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"


def _normalize_texts(text):
    if text is None:
        return []
    if isinstance(text, (list, tuple)):
        return [t for t in (str(x).strip() for x in text) if t]
    text = str(text).strip()
    return [text] if text else []


def _public_urls(urls):
    """Keep only absolute http(s) URLs — Messenger/WhatsApp/Telegram all
    require publicly fetchable URLs. Relative paths (legacy /media/... rows,
    ERP junk) are dropped and reported so failures stay visible."""
    out = []
    for u in (urls or []) or []:
        if isinstance(u, str) and u.startswith(("http://", "https://")):
            out.append(u)
    return out


def send_reply(conversation, text, image_urls=None, product_cards=None):
    """Dispatch a reply (text + images + product cards) to the customer.

    Returns a delivery report dict: {"ok": bool, "sent": {...}, "errors": [...]}
    Callers may persist it on the Message for debugging. Never raises.
    """
    platform = conversation.platform
    result = {"ok": False, "sent": {}, "errors": []}
    try:
        integration = Integration.get_active(conversation.user, platform)
        if not integration or not integration.access_token:
            logger.warning("No active integration for user=%s platform=%s", conversation.user_id, platform)
            result["errors"].append("no active integration / access token")
            return result

        texts = _normalize_texts(text)
        if platform == "whatsapp":
            result = _whatsapp(conversation, integration, texts, image_urls, product_cards)
        elif platform in ("messenger", "instagram"):
            result = _messenger(conversation, integration, texts, image_urls, product_cards)
        elif platform == "telegram":
            result = _telegram(conversation, integration, texts, image_urls, product_cards)
        else:
            result["errors"].append(f"unsupported platform {platform}")
    except Exception:
        logger.exception("send_reply failed conv=%s platform=%s", conversation.pk, platform)
        result["errors"].append("send_reply exception")
    return result


# ---------------------------------------------------------------------------
# Platform senders
# ---------------------------------------------------------------------------

def _whatsapp(conversation, integration, texts, image_urls, product_cards=None):
    phone_number_id = integration.integration_id
    token = integration.access_token
    url = f"{GRAPH_API_BASE}/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    to = conversation.customer_id

    sent = {"cards": 0, "images": 0, "texts": 0}
    errors = []

    # WhatsApp has no card carousel — send each product's first image with a
    # name + price caption as a fallback.
    for card in (product_cards or [])[:5]:
        images = _public_urls(card.get("images"))
        if not images:
            continue
        ok, err = _post(url, headers, {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "image",
            "image": {"link": images[0], "caption": _card_caption(card)},
        })
        if ok:
            sent["cards"] += 1
        else:
            errors.append(err)

    for img_url in _public_urls(image_urls)[:5]:
        ok, err = _post(url, headers, {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "image",
            "image": {"link": img_url},
        })
        if ok:
            sent["images"] += 1
        else:
            errors.append(err)

    for text in texts:
        ok, err = _post(url, headers, {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text, "preview_url": False},
        })
        if ok:
            sent["texts"] += 1
        else:
            errors.append(err)

    return {"ok": not errors, "sent": sent, "errors": errors}


def _messenger(conversation, integration, texts, image_urls, product_cards=None):
    token = integration.access_token
    url = f"{GRAPH_API_BASE}/me/messages"
    headers = {"Authorization": f"Bearer {token}"}
    recipient = {"id": conversation.customer_id}

    sent = {"cards": 0, "images": 0, "texts": 0}
    errors = []

    # Send product cards as a generic template carousel (max 10 elements)
    if product_cards:
        elements = []
        for card in product_cards[:10]:
            images = _public_urls(card.get("images", []))
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
            ok, err = _post(url, headers, {
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
            if ok:
                sent["cards"] += 1
            else:
                errors.append(err)

    for img_url in _public_urls(image_urls)[:5]:
        ok, err = _post(url, headers, {
            "recipient": recipient,
            "message": {"attachment": {"type": "image", "payload": {"url": img_url, "is_reusable": True}}},
        })
        if ok:
            sent["images"] += 1
        else:
            errors.append(err)

    for text in texts:
        ok, err = _post(url, headers, {"recipient": recipient, "message": {"text": text}})
        if ok:
            sent["texts"] += 1
        else:
            errors.append(err)

    return {"ok": not errors, "sent": sent, "errors": errors}


def _telegram(conversation, integration, texts, image_urls, product_cards=None):
    token = integration.access_token
    chat_id = conversation.customer_id
    base = f"https://api.telegram.org/bot{token}"

    sent = {"cards": 0, "images": 0, "texts": 0}
    errors = []

    # Telegram has no card carousel — send each product's first image with a
    # name + price caption as a fallback.
    for card in (product_cards or [])[:5]:
        images = _public_urls(card.get("images"))
        if not images:
            continue
        ok, err = _post(f"{base}/sendPhoto", {}, {
            "chat_id": chat_id, "photo": images[0], "caption": _card_caption(card),
        })
        if ok:
            sent["cards"] += 1
        else:
            errors.append(err)

    for img_url in _public_urls(image_urls)[:5]:
        ok, err = _post(f"{base}/sendPhoto", {}, {"chat_id": chat_id, "photo": img_url})
        if ok:
            sent["images"] += 1
        else:
            errors.append(err)

    for text in texts:
        ok, err = _post(f"{base}/sendMessage", {}, {"chat_id": chat_id, "text": text})
        if ok:
            sent["texts"] += 1
        else:
            errors.append(err)

    return {"ok": not errors, "sent": sent, "errors": errors}


def _card_caption(card):
    """Short 'Name — ৳price' caption for platforms without a card carousel."""
    name = (card.get("name") or "Product").strip()
    price = card.get("discounted_price") or card.get("price")
    return f"{name} — ৳{price}" if price else name


def _post(url, headers, payload):
    """POST to a platform API. Returns (ok, error_message_or_None)."""
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if not resp.ok:
            msg = f"{url} {payload.get('type', '')}: {resp.text[:200]}"
            logger.warning("Platform send failed %s", msg)
            return False, msg
        return True, None
    except requests.RequestException as exc:
        msg = f"{url}: request error: {exc}"
        logger.warning("Platform send request error: %s", exc)
        return False, msg
