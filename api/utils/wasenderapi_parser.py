"""
Parse WasenderAPI webhook payloads into the unified message dict format.

WasenderAPI webhook payload (messages.received):
{
  "event": "messages.received",
  "timestamp": 1633456789,
  "data": {
    "messages": {
      "key": {
        "id": "3EB0X123456789",
        "fromMe": false,
        "remoteJid": "1234567890@s.whatsapp.net",
        "cleanedSenderPn": "1234567890",
        "senderLid": "555555555@lid"
      },
      "messageBody": "Hello",
      "message": { "conversation": "Hello" }
    }
  }
}

For media, message contains e.g. imageMessage, audioMessage, etc.
"""


def parse_wasender(payload):
    messages = []
    event = payload.get("event", "")
    data = payload.get("data", {})

    # Only handle message events (received and sent)
    if event not in ("messages.received", "messages.sent", "messages.upsert"):
        return messages

    msg_data = data.get("messages")
    if not msg_data:
        return messages

    key = msg_data.get("key", {})
    if key.get("fromMe", False) and event != "messages.received":
        return messages

    customer_id = (
        key.get("cleanedSenderPn")
        or key.get("senderPn", "").split("@")[0]
        or key.get("remoteJid", "").split("@")[0]
        or ""
    )

    if not customer_id:
        return messages

    message_id = key.get("id")
    raw_message = msg_data.get("message", {})
    message_body = msg_data.get("messageBody", "")

    msg_type = "text"
    text = message_body
    attachments = None

    # Detect media type
    if "imageMessage" in raw_message:
        msg_type = "image"
        media = raw_message["imageMessage"]
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
        "timestamp": str(payload.get("timestamp", "")),
        "type": msg_type,
        "text": text,
        "attachments": attachments,
        "raw": raw_message,
    })

    return messages


def _enrich_media(attachments, media):
    """Copy media fields needed for decryption."""
    for key in ("url", "mediaKey", "mimetype", "fileLength", "fileName", "id"):
        if key in media:
            attachments[key] = media[key]
    # WasenderAPI sometimes delivers a direct downloadUrl
    if "downloadUrl" in media:
        attachments["url"] = media["downloadUrl"]
    # Map mimetype to mime_type for compatibility with existing code
    if "mimetype" in media:
        attachments["mime_type"] = media["mimetype"]