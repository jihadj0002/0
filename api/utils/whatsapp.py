import uuid
import requests
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"

MIME_TO_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/aac": "aac",
    "video/mp4": "mp4",
    "video/3gpp": "3gp",
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}


def _get_media_url(media_id, access_token):
    resp = requests.get(
        f"{GRAPH_API_BASE}/{media_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("url")


def download_whatsapp_media(media_id, access_token, mime_type=None, folder="whatsapp_media"):
    media_url = _get_media_url(media_id, access_token)

    resp = requests.get(
        media_url,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
        stream=True,
    )
    resp.raise_for_status()

    ext = MIME_TO_EXT.get(mime_type or "", "bin")
    filename = f"{folder}/{uuid.uuid4().hex}.{ext}"

    path = default_storage.save(filename, ContentFile(resp.content))
    return default_storage.url(path)
