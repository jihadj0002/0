"""
Media analysis helpers: image understanding via vision LLM, audio transcription via Whisper.
Called from webhooks._persist_message before the batch timer fires.
"""
import logging
import os

import requests

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
VISION_MODEL = "openai/gpt-4o-mini"
WHISPER_MODEL = "openai/whisper-1"


def _or_client():
    from openai import OpenAI
    return OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
    )


def analyze_image(image_url: str, user=None, reply_id=None) -> str:
    """Backward-compatible wrapper — returns only the description string."""
    data = analyze_image_structured(image_url, user, reply_id)
    return data.get("description", "")


def analyze_image_structured(image_url: str, user=None, reply_id=None) -> dict:
    """
    Analyse an image in a single vision call. Returns BOTH structured fields
    and a natural-language description. Call this once — do NOT also call
    analyze_image() on the same image.

    Prioritises scanning: SKU/PID → product_name → type → brand → capacity → color.

    Returns:
        {
            "sku": str,          # SKU, PID, barcode number found anywhere on the image
            "product_name": str, # product name from packaging/label
            "type": str,         # what kind of product (food container, sweater, …)
            "brand": str,        # brand name if visible
            "capacity": str,     # size, volume, weight, dimensions
            "color": str,        # colour(s)
            "description": str,  # one-sentence summary
        }
    """
    try:
        client = _or_client()
        resp = client.chat.completions.create(
            model=VISION_MODEL,
            timeout=60,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Return ONLY valid JSON — no markdown, no code fences, no extra text.\n\n"
                            "Analyse this image from a customer of an online store. "
                            "Scan the ENTIRE image carefully — all corners, labels, tags, barcodes, stickers.\n\n"
                            "Extract these fields (use empty string if not found):\n"
                            "1. \"sku\" — any SKU, PID, item code, barcode/model number visible anywhere\n"
                            "2. \"product_name\" — product name if readable from packaging or label\n"
                            "3. \"type\" — what kind of product (e.g. food container, sweater, phone case)\n"
                            "4. \"brand\" — brand name if visible\n"
                            "5. \"capacity\" — size, volume, weight, or dimensions if mentioned\n"
                            "6. \"color\" — colour(s) of the product\n\n"
                            "Then write \"description\": a single concise sentence summarising the image.\n\n"
                            "Example:\n"
                            "{\"sku\":\"34186\",\"product_name\":\"Rovco Food Container Penguin\","
                            "\"type\":\"food container\",\"brand\":\"Rovco\","
                            "\"capacity\":\"900ml\",\"color\":\"white, blue\","
                            "\"description\":\"A white and blue Rovco food container with a penguin design on the lid.\"}"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url, "detail": "low"},
                    },
                ],
            }],
            max_tokens=300,
            temperature=0.1,
        )
        text = resp.choices[0].message.content or ""
        import json as _json
        data = _json.loads(text)
        return {
            "sku": str(data.get("sku", "") or ""),
            "product_name": str(data.get("product_name", "") or ""),
            "type": str(data.get("type", "") or ""),
            "brand": str(data.get("brand", "") or ""),
            "capacity": str(data.get("capacity", "") or ""),
            "color": str(data.get("color", "") or ""),
            "description": str(data.get("description", "") or ""),
        }
    except Exception as exc:
        logger.warning("Structured image analysis failed url=%s: %s", image_url, exc)
        return {}


def transcribe_audio(audio_url: str, mime_type: str = "", user=None, reply_id=None) -> str:
    """
    Transcribe an audio/voice message.
    Attempts OpenAI Whisper via the official API if OPENAI_API_KEY is set,
    otherwise falls back to a placeholder so the AI at least knows a voice
    message was received.
    """
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if not openai_key:
        return "[Customer sent a voice message — transcription unavailable]"

    try:
        # Download the audio file first
        audio_resp = requests.get(audio_url, timeout=30)
        audio_resp.raise_for_status()

        # Determine file extension from mime type
        ext_map = {
            "audio/ogg": "ogg",
            "audio/opus": "ogg",
            "audio/mpeg": "mp3",
            "audio/mp3": "mp3",
            "audio/mp4": "mp4",
            "audio/m4a": "m4a",
            "audio/aac": "aac",
            "audio/wav": "wav",
            "audio/webm": "webm",
        }
        ext = ext_map.get(mime_type or "", "ogg")
        filename = f"voice.{ext}"

        from openai import OpenAI
        client = OpenAI(api_key=openai_key)
        transcription = client.audio.transcriptions.create(
            model="whisper-1",
            file=(filename, audio_resp.content, mime_type or "audio/ogg"),
            timeout=60,
        )
        text = transcription.text or ""
        
        # if user and reply_id and hasattr(transcription, "usage"):
        #     from .media import _log
        #     usage = transcription.usage.dict() if transcription.usage else {}
        #     _log(user, reply_id, usage, call_type="audio_transcription")
        #     return text.strip()
        # else:
        #     return text.strip()
        return text.strip()
    except Exception as exc:
        logger.warning("Audio transcription failed url=%s: %s", audio_url, exc)
        return "[Customer sent a voice message — transcription failed]"
