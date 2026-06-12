"""
Parse raw platform webhook payloads into a unified list of message dicts.

Unified format:
{
    "platform":      str,   # whatsapp | messenger | instagram | telegram
    "customer_id":   str,   # platform-specific sender ID
    "customer_name": str | None,
    "message_id":    str | None,
    "timestamp":     str | None,
    "type":          str,   # text | image | audio | video | document | location | sticker | interactive | reaction
    "text":          str | None,
    "attachments":   dict | None,
    "raw":           dict,  # original message object (not full payload)
}
"""


def parse_whatsapp(payload):
    messages = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            value = change.get("value", {})
            contacts = {c["wa_id"]: c for c in value.get("contacts", [])}

            for msg in value.get("messages", []):
                customer_id = msg.get("from")
                contact = contacts.get(customer_id, {})
                msg_type = msg.get("type", "text")
                text = None
                attachments = None

                if msg_type == "text":
                    text = msg.get("text", {}).get("body", "")

                elif msg_type in ("image", "audio", "video", "sticker", "document"):
                    media = msg.get(msg_type, {})
                    attachments = {
                        "type": msg_type,
                        "media_id": media.get("id"),
                        "mime_type": media.get("mime_type"),
                        "caption": media.get("caption"),
                        "filename": media.get("filename"),
                        "sha256": media.get("sha256"),
                    }
                    text = media.get("caption")

                elif msg_type == "location":
                    loc = msg.get("location", {})
                    name = loc.get("name", "")
                    text = f"Location: {name} ({loc.get('latitude')}, {loc.get('longitude')})".strip()
                    attachments = {"type": "location", "payload": loc}

                elif msg_type == "interactive":
                    interactive = msg.get("interactive", {})
                    itype = interactive.get("type")
                    if itype == "button_reply":
                        text = interactive.get("button_reply", {}).get("title", "")
                    elif itype == "list_reply":
                        text = interactive.get("list_reply", {}).get("title", "")
                    attachments = {"type": "interactive", "payload": interactive}

                elif msg_type == "reaction":
                    reaction = msg.get("reaction", {})
                    text = f"Reacted {reaction.get('emoji', '')}".strip()
                    attachments = {"type": "reaction", "payload": reaction}

                elif msg_type == "order":
                    order = msg.get("order", {})
                    text = f"Order received"
                    attachments = {"type": "order", "payload": order}

                messages.append({
                    "platform": "whatsapp",
                    "customer_id": customer_id,
                    "customer_name": contact.get("profile", {}).get("name"),
                    "message_id": msg.get("id"),
                    "timestamp": msg.get("timestamp"),
                    "type": msg_type,
                    "text": text,
                    "attachments": attachments,
                    "raw": msg,
                })

    return messages


def parse_messenger(payload):
    platform = "instagram" if payload.get("object") == "instagram" else "messenger"
    messages = []

    for entry in payload.get("entry", []):
        for event in entry.get("messaging", []):
            sender_id = event.get("sender", {}).get("id")

            # Handle postback events (e.g. product card "Select" button)
            postback = event.get("postback")
            if postback:
                payload_str = postback.get("payload", "")
                if payload_str.startswith("SELECT_PRODUCT|"):
                    pid = payload_str.split("|", 1)[1]
                    mid = postback.get("mid") or f"postback_{pid}_{event.get('timestamp', '')}"
                    messages.append({
                        "platform": platform,
                        "customer_id": sender_id,
                        "customer_name": None,
                        "message_id": mid,
                        "timestamp": str(event.get("timestamp", "")),
                        "type": "text",
                        "text": f"Show product {pid} details",
                        "attachments": {"type": "postback", "payload": payload_str},
                        "raw": event,
                    })
                continue

            msg = event.get("message", {})
            if not msg or msg.get("is_echo"):
                continue

            msg_type = "text"
            text = msg.get("text")
            attachments = None

            raw_attachments = msg.get("attachments", [])
            if raw_attachments:
                att = raw_attachments[0]
                att_type = att.get("type", "file")
                msg_type = att_type
                attachments = {
                    "type": att_type,
                    "payload": att.get("payload", {}),
                }
                if not text:
                    text = f"[{att_type}]"

            messages.append({
                "platform": platform,
                "customer_id": sender_id,
                "customer_name": None,
                "message_id": msg.get("mid"),
                "timestamp": str(event.get("timestamp", "")),
                "type": msg_type,
                "text": text,
                "attachments": attachments,
                "raw": event,
            })

    return messages


def parse_instagram(payload):
    return parse_messenger(payload)


def parse_telegram(payload):
    messages = []
    msg = payload.get("message") or payload.get("channel_post") or payload.get("edited_message")
    if not msg:
        return messages

    sender = msg.get("from", {})
    chat_id = msg.get("chat", {}).get("id", "")
    customer_id = str(sender.get("id") or chat_id)
    first = sender.get("first_name", "")
    last = sender.get("last_name", "")
    customer_name = " ".join(filter(None, [first, last])) or None

    text = msg.get("text") or msg.get("caption")
    msg_type = "text"
    attachments = None

    if msg.get("photo"):
        msg_type = "image"
        attachments = {"type": "image", "payload": msg["photo"][-1]}
    elif msg.get("audio"):
        msg_type = "audio"
        attachments = {"type": "audio", "payload": msg["audio"]}
    elif msg.get("video"):
        msg_type = "video"
        attachments = {"type": "video", "payload": msg["video"]}
    elif msg.get("voice"):
        msg_type = "audio"
        attachments = {"type": "voice", "payload": msg["voice"]}
    elif msg.get("document"):
        msg_type = "document"
        attachments = {"type": "document", "payload": msg["document"]}
    elif msg.get("sticker"):
        msg_type = "sticker"
        attachments = {"type": "sticker", "payload": msg["sticker"]}
    elif msg.get("location"):
        msg_type = "location"
        loc = msg["location"]
        text = f"Location: ({loc.get('latitude')}, {loc.get('longitude')})"
        attachments = {"type": "location", "payload": loc}
    elif msg.get("contact"):
        msg_type = "contact"
        c = msg["contact"]
        text = f"Contact: {c.get('first_name', '')} {c.get('phone_number', '')}".strip()
        attachments = {"type": "contact", "payload": c}

    messages.append({
        "platform": "telegram",
        "customer_id": customer_id,
        "customer_name": customer_name,
        "message_id": str(payload.get("update_id", "")),
        "timestamp": str(msg.get("date", "")),
        "type": msg_type,
        "text": text,
        "attachments": attachments,
        "raw": msg,
    })

    return messages
