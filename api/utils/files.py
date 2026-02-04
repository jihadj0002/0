import requests
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
import uuid


def download_to_storage(url, folder="whatsapp"):
    r = requests.get(url, timeout=15)
    r.raise_for_status()

    filename = f"{folder}/{uuid.uuid4().hex}"

    path = default_storage.save(
        filename,
        ContentFile(r.content)
    )

    return default_storage.url(path)
