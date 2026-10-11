"""
Parse WasenderAPI webhook payloads into the unified message dict format.

Supports multiple payload shapes to handle doc/API discrepancies.
"""


def parse_wasender(payload):
    messages = []
    event = payload.get("event", "")
    data = payload.get("data", {})

    if event not in ("messages.received", "messages.sent", "messages.upsert",
                     "message.received", "message.sent", "message"):
        return messages

    # Try multiple key paths for the message object
    msg_data = None
    for candidate in (
        data.get("messages"),
        data.get("message"),
        payload.get("messages"),
        payload.get("message"),
        data,
        payload,
    ):
        if isinstance(candidate, list):
            candidate = candidate[0] if candidate else None
        if isinstance(candidate, dict):
            msg_data = candidate
            break

    if not msg_data:
        return messages

    key = msg_data.get("key") or msg_data or {}
    if not isinstance(key, dict):
        return messages

    # Skip messages we sent ourselves (unless it's a received event)
    if key.get("fromMe", False) and event in ("messages.sent", "message.sent",
                                                "messages.upsert", "message"):
        return messages

    customer_id = ""
    for id_field in ("cleanedSenderPn", "senderPn", "cleanedParticipantPn"):
        val = key.get(id_field)
        if val:
            customer_id = str(val).split("@")[0]
            if customer_id:
                break
    if not customer_id:
        remote = key.get("remoteJid", "")
        if remote:
            customer_id = remote.split("@")[0]
    if not customer_id:
        # Last resort: try to get it from the message itself
        customer_id = msg_data.get("from") or msg_data.get("customer_id", "")

    if not customer_id:
        return messages

    message_id = key.get("id") or msg_data.get("id") or ""
    raw_message = msg_data.get("message", {})
    if not isinstance(raw_message, dict):
        raw_message = {}
    message_body = msg_data.get("messageBody", "") or msg_data.get("body", "") or msg_data.get("text", "")

    msg_type = "text"
    text = message_body
    attachments = None

    if "imageMessage" in raw_message:
        msg_type = "image"
        media = raw_message["imageMessage"]
        attachments = {"type": "image"}
        _enrich_media(attachments, media)
        text = media.get("caption") or message_body
    elif "image" in raw_message:
        msg_type = "image"
        media = raw_message["image"]
        attachments = {"type": "image"}
        _enrich_media(attachments, media)
        text = media.get("caption") or message_body

    elif "videoMessage" in raw_message:
        msg_type = "video"
        media = raw_message["videoMessage"]
        attachments = {"type": "video"}
        _enrich_media(attachments, media)
        text = media.get("caption") or message_body

    elif "audioMessage" in raw_message:
        msg_type = "audio"
        media = raw_message["audioMessage"]
        attachments = {"type": "audio"}
        _enrich_media(attachments, media)

    elif "documentMessage" in raw_message:
        msg_type = "document"
        media = raw_message["documentMessage"]
        attachments = {"type": "document"}
        _enrich_media(attachments, media)
        text = media.get("caption") or message_body

    elif "stickerMessage" in raw_message:
        msg_type = "sticker"
        media = raw_message["stickerMessage"]
        attachments = {"type": "sticker"}
        _enrich_media(attachments, media)

    elif "locationMessage" in raw_message:
        msg_type = "location"
        loc = raw_message["locationMessage"]
        attachments = {
            "type": "location",
            "payload": {
                "latitude": loc.get("latitude"),
                "longitude": loc.get("longitude"),
                "name": loc.get("name", ""),
            },
        }
        text = f"Location: {loc.get('name', '')} ({loc.get('latitude')}, {loc.get('longitude')})".strip()

    elif "contactMessage" in raw_message:
        msg_type = "contact"
        attachments = {"type": "contact", "payload": raw_message["contactMessage"]}

    messages.append({
        "platform": "whatsapp",
        "customer_id": customer_id,
        "customer_name": None,
        "message_id": message_id,
        "timestamp": str(payload.get("timestamp", "") or msg_data.get("timestamp", "")),
        "type": msg_type,
        "text": text,
        "attachments": attachments,
        "raw": raw_message,
    })

    return messages


def _enrich_media(attachments, media):
    for key in ("url", "mediaKey", "mimetype", "fileLength", "fileName", "id"):
        if key in media:
            attachments[key] = media[key]
    if "downloadUrl" in media:
        attachments["url"] = media["downloadUrl"]
    if "mimetype" in media:
        attachments["mime_type"] = media["mimetype"]