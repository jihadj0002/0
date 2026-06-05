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


def analyze_image(image_url: str) -> str:
    """
    Describe the contents of an image using a vision LLM.
    Returns a one-paragraph natural-language description.
    Falls back to a placeholder string on any error.
    """
    try:
        client = _or_client()
        resp = client.chat.completions.create(
            model=VISION_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "You are a helpful assistant for an online store. "
                            "Analyse this image sent by a customer. "
                            "If it shows a product (clothing, electronics, furniture, food, etc.), "
                            "describe it clearly: what it is, colour, visible features, brand if readable, condition. "
                            "If image contains SKU, PID, on any corner of the image, read and return it. "
                            "If it is a screenshot of something (order, receipt, chat), summarise what it says. "
                            "Reply in 1-3 short sentences only — no headers or bullet points."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url, "detail": "low"},
                    },
                ],
            }],
            max_tokens=200,
            temperature=0.3,
        )
        text = resp.choices[0].message.content or ""
        return text.strip()
    except Exception as exc:
        logger.warning("Image analysis failed url=%s: %s", image_url, exc)
        return ""


def transcribe_audio(audio_url: str, mime_type: str = "") -> str:
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
        )
        text = transcription.text or ""
        return text.strip()
    except Exception as exc:
        logger.warning("Audio transcription failed url=%s: %s", audio_url, exc)
        return "[Customer sent a voice message — transcription failed]"
