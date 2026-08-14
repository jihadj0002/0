import json
import logging

import requests

from back.models import Integration

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"

# ERP/3rd-party CDNs are often unreachable from the platform's fetch servers
# (Facebook's URL fetcher, WhatsApp/Telegram servers) even though the URL works
# from our own network. Reliable path: download the bytes ourselves and upload
# them as binary media. Fall back to URL payloads only when the download fails.
_IMAGE_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
             "Chrome/120.0 Safari/537.36")
_MAX_IMAGE_BYTES = 15 * 1024 * 1024


def _download_image(url, timeout=25):
    """Download image bytes for binary upload. Returns (bytes|None, error|None)."""
    try:
        resp = requests.get(
            url, timeout=timeout,
            headers={"User-Agent": _IMAGE_UA, "Accept": "image/*"},
        )
        if resp.status_code != 200:
            return None, f"download {resp.status_code}"
        if len(resp.content) > _MAX_IMAGE_BYTES:
            return None, "image too large"
        return resp.content, None
    except requests.RequestException as exc:
        return None, str(exc)[:100]


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
            media_id, media_err = _whatsapp_upload_media(url, headers, img_url)
            if media_id:
                ok2, err2 = _post(url, headers, {
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "image",
                    "image": {"id": media_id},
                })
                if ok2:
                    sent["images"] += 1
                else:
                    errors.append(err2)
            else:
                errors.append(err)
                if media_err:
                    errors.append(media_err)

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
            # PID first — the backend resolves local products by pid and external
            # products by external_id (stored as pid); SKU is only a fallback.
            btn_pid = card.get("pid") or card.get("sku", "")
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
        ok, err = _messenger_send_image(url, headers, recipient, img_url)
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


def _messenger_send_image(url, headers, recipient, img_url):
    """Send one image to Messenger. Binary upload first (the platform cannot
    fetch many external CDNs — ERP etc. — by URL), URL payload as fallback."""
    data, err = _download_image(img_url)
    if data is not None:
        try:
            resp = requests.post(
                url,
                headers=headers,
                params={"access_token": _bearer_token(headers)},
                timeout=45,
                files={"filedata": ("image.jpg", data, "image/jpeg")},
                data={
                    "recipient": json.dumps(recipient),
                    "message": json.dumps({
                        "attachment": {"type": "image", "payload": {"is_reusable": True}},
                    }),
                },
            )
            if resp.ok:
                return True, None
            msg = f"{url} binary upload: {resp.text[:200]}"
            logger.warning("Platform send failed %s", msg)
            return False, msg
        except requests.RequestException as exc:
            msg = f"{url} binary upload request error: {exc}"
            logger.warning("Platform send request error: %s", exc)
            return False, msg

    msg = f"{url} image download failed ({err}) — falling back to URL payload"
    logger.warning("%s", msg)
    return _post(url, headers, {
        "recipient": recipient,
        "message": {"attachment": {"type": "image", "payload": {"url": img_url, "is_reusable": True}}},
    })


def _bearer_token(headers):
    """Extract the bearer token from a headers dict ('Bearer xxx')."""
    auth = (headers or {}).get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return ""


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
        ok, err = _telegram_send_photo(base, chat_id, img_url)
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


def _whatsapp_upload_media(url, headers, img_url):
    """Download an image and upload it to WhatsApp as reusable media.
    Returns (media_id|None, error|None)."""
    data, err = _download_image(img_url)
    if data is None:
        return None, err or "download failed"
    try:
        import re as _re
        mime = "image/png" if img_url.lower().endswith(".png") else "image/jpeg"
        upload_url = _re.sub(r"/messages$", "/media", url)
        resp = requests.post(
            upload_url, headers=headers, timeout=45,
            files={"file": ("image.jpg", data, mime)},
            data={"messaging_product": "whatsapp", "type": mime},
        )
        if resp.ok:
            return (resp.json().get("id") or None), None
        return None, f"WA media upload: {resp.text[:150]}"
    except (requests.RequestException, ValueError) as exc:
        return None, f"WA media upload error: {exc}"


def _telegram_send_photo(base, chat_id, img_url):
    """Send one photo to Telegram. Binary upload first, URL as fallback."""
    data, err = _download_image(img_url)
    if data is not None:
        try:
            resp = requests.post(
                f"{base}/sendPhoto",
                timeout=45,
                data={"chat_id": chat_id},
                files={"photo": ("photo.jpg", data, "image/jpeg")},
            )
            if resp.ok:
                return True, None
            return False, f"sendPhoto binary: {resp.text[:200]}"
        except requests.RequestException as exc:
            return False, f"sendPhoto binary request error: {exc}"

    logger.warning("%s image download failed (%s) — URL fallback", img_url, err)
    return _post(f"{base}/sendPhoto", {}, {"chat_id": chat_id, "photo": img_url})


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
