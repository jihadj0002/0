import requests
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
import uuid


def download_to_storage(url, folder="whatsapp"):
    # user = requests.request.user
    # integration = user.integrations.filter(platform="whatsapp").first()
    # headers = {"Authorization": f"Bearer {integration.access_token}"}


    r = requests.get(url, timeout=15)
    r.raise_for_status()
    print(f"Downloaded media from {url} with status code {r.status_code}")
    filename = f"{folder}/{uuid.uuid4().hex}"

    path = default_storage.save(
        filename,
        ContentFile(r.content)
    )

    return default_storage.url(path)

def download_profile_to_storage(url, folder="customer_profiles"):
    r = requests.get(url, timeout=15)
    r.raise_for_status()

    ext = url.split("?")[0].split(".")[-1]
    if ext not in ["jpg", "jpeg", "png", "webp"]:
        ext = "jpg"

    filename = f"{folder}/{uuid.uuid4().hex}.{ext}"

    path = default_storage.save(
        filename,
        ContentFile(r.content)
    )

    # IMPORTANT: return path, not URL
    return path

