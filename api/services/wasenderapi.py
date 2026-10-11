import logging

import requests

logger = logging.getLogger(__name__)

WASENDER_BASE = "https://www.wasenderapi.com/api"


class WasenderService:
    """Lightweight client for the WasenderAPI WhatsApp service.

    All send_* methods return a dict:
        {"ok": True, "data": {...}} on success
        {"ok": False, "error": "..."} on failure
    """

    def __init__(self, integration):
        self.token = integration.access_token
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def send_text(self, to, text):
        return self._post("/send-message", {"to": to, "text": text})

    def send_image(self, to, image_url, caption=None):
        payload = {"to": to, "imageUrl": image_url}
        if caption:
            payload["text"] = caption
        return self._post("/send-message", payload)

    def send_audio(self, to, audio_url):
        return self._post("/send-message", {"to": to, "audioUrl": audio_url})

    def send_document(self, to, doc_url, caption=None):
        payload = {"to": to, "documentUrl": doc_url}
        if caption:
            payload["text"] = caption
        return self._post("/send-message", payload)

    def send_video(self, to, video_url, caption=None):
        payload = {"to": to, "videoUrl": video_url}
        if caption:
            payload["text"] = caption
        return self._post("/send-message", payload)

    def decrypt_media(self, media_key, url, mime_type):
        resp = self._post_raw(
            "/decrypt-media",
            {"mediaKey": media_key, "url": url, "mimetype": mime_type},
        )
        if resp is not None and isinstance(resp, dict):
            data = resp.get("data") or resp
            return data.get("publicUrl") if isinstance(data, dict) else None
        return None

    def upload_media(self, file_bytes, mime_type):
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            resp = requests.post(
                f"{WASENDER_BASE}/upload",
                headers=headers,
                data=file_bytes,
                timeout=60,
            )
            if resp.ok:
                return resp.json().get("publicUrl")
            logger.warning("WasenderAPI upload failed: %s", resp.text[:200])
            return None
        except requests.RequestException as exc:
            logger.warning("WasenderAPI upload error: %s", exc)
            return None

    def _post(self, path, payload):
        result = self._post_raw(path, payload)
        if result is None:
            return {"ok": False, "error": "HTTP request failed (check logs)"}
        if isinstance(result, dict) and result.get("success"):
            return {"ok": True, "data": result.get("data") or result}
        if isinstance(result, dict):
            msg = result.get("message") or result.get("error") or "unknown error"
            return {"ok": False, "error": msg}
        return {"ok": True, "data": result}

    def _post_raw(self, path, payload):
        url = f"{WASENDER_BASE}{path}"
        try:
            resp = requests.post(url, headers=self.headers, json=payload, timeout=15)
            if not resp.ok:
                msg = f"{url}: {resp.text[:200]}"
                logger.warning("WasenderAPI request failed %s", msg)
                return None
            return resp.json()
        except requests.RequestException as exc:
            logger.warning("WasenderAPI request error: %s", exc)
            return None