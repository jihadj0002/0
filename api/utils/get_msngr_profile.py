import requests
from django.conf import settings


def _app_token():
    return f"{settings.META_APP_ID}|{settings.META_APP_SECRET}"


def can_fetch_profile(page_access_token):
    """Check that the page token is valid and has public_profile / pages_messaging scope."""
    if not page_access_token:
        return False
    try:
        resp = requests.get(
            "https://graph.facebook.com/debug_token",
            params={
                "input_token": page_access_token,
                "access_token": _app_token(),
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return False
        data = resp.json()
        if not isinstance(data, dict) or "data" not in data:
            return False
        scopes = data["data"].get("scopes", [])
        return "public_profile" in scopes or "pages_messaging" in scopes
    except Exception:
        return False


def get_messenger_profile(psid, access_token):
    url = (
        f"https://graph.facebook.com/{settings.META_GRAPH_VERSION}/{psid}"
    )
    params = {
        "fields": "first_name,last_name,profile_pic",
        "access_token": access_token,
    }
    response = requests.get(url, params=params, timeout=10)
    if response.status_code != 200:
        raise Exception(
            f"Failed to get Messenger profile: {response.status_code} {response.text}"
        )
    data = response.json()
    return {
        "first_name": data.get("first_name", ""),
        "last_name": data.get("last_name", ""),
        "profile_pic": data.get("profile_pic", ""),
    }