import hashlib
import hmac
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth.models import User
from django.db import close_old_connections
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from back.models import Conversation, Integration, Message, MessageBatch

from .utils.parsers import parse_instagram, parse_messenger, parse_telegram, parse_whatsapp
from .utils.whatsapp import download_whatsapp_media

logger = logging.getLogger(__name__)

# Bounded thread pool — handles burst traffic without spawning unlimited threads.
_executor = ThreadPoolExecutor(max_workers=20)

# Per-conversation timers: {conversation_id: threading.Timer}
# When a new message arrives for a conversation, any existing timer is cancelled
# and a fresh 5-second timer is started. This ensures rapid bursts are combined
# into one AI turn rather than triggering a pipeline call per message.
_batch_timers: dict[int, threading.Timer] = {}
_batch_timers_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Signature helpers
# ---------------------------------------------------------------------------

def _verify_meta_signature(body_bytes, app_secret, signature_header):
    """Verify X-Hub-Signature-256. Returns True if not configured (skip mode)."""
    if not app_secret or not signature_header:
        return True
    h = hmac.new(app_secret.encode(), body_bytes, hashlib.sha256)
    expected = "sha256=" + h.hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------

def _schedule_batch_pipeline(conversation_id):
    """
    (Re)start the 5-second batch timer for a conversation.
    Cancels any existing timer so rapid message bursts are collapsed into one
    pipeline run that fires 5 seconds after the LAST message in the burst.
    Must be called from inside a `_batch_timers_lock` context is NOT required —
    the lock is acquired internally.
    """
    with _batch_timers_lock:
        existing = _batch_timers.get(conversation_id)
        if existing is not None:
            existing.cancel()
        t = threading.Timer(5.0, _fire_batch_pipeline, args=(conversation_id,))
        t.daemon = True
        _batch_timers[conversation_id] = t
        t.start()


def _fire_batch_pipeline(conversation_id):
    """
    Called by the timer after 5 seconds of silence for a conversation.
    Combines all unprocessed MessageBatch rows into a single AI turn.
    """
    with _batch_timers_lock:
        _batch_timers.pop(conversation_id, None)

    close_old_connections()
    try:
        from types import SimpleNamespace
        from api.ai.pipeline import run

        conversation = Conversation.objects.get(id=conversation_id)

        batches = MessageBatch.objects.filter(
            conversation=conversation,
            processed=False,
        ).order_by("timestamp")

        if not batches.exists():
            return

        combined_text = "\n".join(b.message_text for b in batches if b.message_text.strip())
        batches.update(processed=True)

        if not combined_text.strip():
            return

        unified = SimpleNamespace(text=combined_text)
        run(conversation, unified)

    except Conversation.DoesNotExist:
        pass
    except Exception:
        logger.exception("_fire_batch_pipeline failed for conversation=%s", conversation_id)
    finally:
        close_old_connections()


def _process_webhook(user_id, platform, unified_messages, access_token):
    """
    Runs in a thread pool worker. Persists each message and (re)starts the
    per-conversation 5-second batch timer so rapid bursts are collapsed into
    one AI pipeline call.
    """
    close_old_connections()
    try:
        user = User.objects.get(id=user_id)
        conversation_ids = set()
        for msg_data in unified_messages:
            try:
                conv_id = _persist_message(user, platform, msg_data, access_token)
                if conv_id:
                    conversation_ids.add(conv_id)
            except Exception:
                logger.exception(
                    "Failed to persist message mid=%s user=%s platform=%s",
                    msg_data.get("message_id"), user_id, platform,
                )
        # (Re)start the 5-second timer for each affected conversation
        for cid in conversation_ids:
            _schedule_batch_pipeline(cid)
    except Exception:
        logger.exception("_process_webhook crashed user_id=%s platform=%s", user_id, platform)
    finally:
        close_old_connections()


def _persist_message(user, platform, msg_data, access_token):
    """Persist a single incoming message. Returns the conversation id, or None if dropped."""
    mid = msg_data.get("message_id") or None
    customer_id = msg_data.get("customer_id", "")

    # Idempotency — Meta retries on non-200, so the same mid may arrive twice
    if mid and Message.objects.filter(mid=mid).exists():
        return None

    conv, created = Conversation.objects.get_or_create(
        user=user,
        platform=platform,
        customer_id=customer_id,
        defaults={"customer_name": msg_data.get("customer_name") or ""},
    )

    # Backfill name if we learned it and conversation was created without it
    if not created and msg_data.get("customer_name") and not conv.customer_name:
        Conversation.objects.filter(pk=conv.pk).update(
            customer_name=msg_data.get("customer_name")
        )

    attachments = msg_data.get("attachments")
    msg_text = msg_data.get("text") or ""
    att_type = (attachments or {}).get("type", "")

    # Download WhatsApp media while we're already in the background thread.
    # Other platforms serve public URLs directly — no extra step needed.
    if platform == "whatsapp" and attachments and attachments.get("media_id"):
        try:
            public_url = download_whatsapp_media(
                media_id=attachments["media_id"],
                access_token=access_token,
                mime_type=attachments.get("mime_type"),
            )
            attachments["url"] = public_url
            attachments["stored"] = True
        except Exception as exc:
            logger.warning("WA media download failed mid=%s: %s", mid, exc)
            attachments["download_error"] = str(exc)

    # --- Media understanding ---
    # For images: run vision analysis and fold the description into the message text.
    # For audio/voice: transcribe and use as the message text.
    # This runs synchronously here (already in a background thread) so the result
    # is available before the 5-second batch timer fires.
    media_url = (attachments or {}).get("url") or (attachments or {}).get("payload", {}).get("url", "")
    if att_type == "image" and media_url:
        try:
            from api.ai.media import analyze_image
            analysis = analyze_image(media_url)
            if analysis:
                attachments["analysis"] = analysis
                # Combine caption + analysis so the AI has full context
                caption = msg_text or ""
                msg_text = f"{caption}\n[Image: {analysis}]".strip()
        except Exception as exc:
            logger.warning("Image analysis failed mid=%s: %s", mid, exc)

    elif att_type in ("audio", "voice") and media_url:
        try:
            from api.ai.media import transcribe_audio
            mime = (attachments or {}).get("mime_type", "")
            transcription = transcribe_audio(media_url, mime)
            if transcription:
                attachments["transcription"] = transcription
                msg_text = transcription
        except Exception as exc:
            logger.warning("Audio transcription failed mid=%s: %s", mid, exc)
            msg_text = msg_text or "[Voice message received]"

    Message.objects.create(
        conversation=conv,
        mid=mid,
        sender="customer",
        text=msg_text or None,
        attachments=attachments if attachments else None,
        raw_payload=msg_data.get("raw"),
    )

    # Store enriched text in batch so _fire_batch_pipeline combines it correctly
    MessageBatch.objects.create(
        conversation=conv,
        message_text=msg_text,
        platform=platform,
    )

    return conv.id


# ---------------------------------------------------------------------------
# Base webhook view
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name="dispatch")
class _BaseWebhookView(View):
    platform = None

    def _get_integration(self, username):
        try:
            user = User.objects.get(username=username)
            return Integration.objects.filter(user=user, platform=self.platform).first()
        except User.DoesNotExist:
            return None

    def _submit(self, integration, messages):
        if messages:
            _executor.submit(
                _process_webhook,
                integration.user_id,
                self.platform,
                messages,
                integration.access_token or "",
            )


# ---------------------------------------------------------------------------
# Meta platforms (WhatsApp, Messenger, Instagram) — shared GET verification
# ---------------------------------------------------------------------------

class _MetaWebhookView(_BaseWebhookView):

    def get(self, request, username):
        """Respond to Meta webhook verification challenge."""
        integration = self._get_integration(username)
        if not integration:
            logger.warning("Webhook verification: no integration for user=%s platform=%s", username, self.platform)
            return HttpResponse(status=404)

        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token", "")
        challenge = request.GET.get("hub.challenge", "")

        configured_token = integration.verify_token or ""

        if not configured_token:
            logger.error(
                "Webhook verification failed: no verify_token configured for user=%s platform=%s",
                username, self.platform,
            )
            return HttpResponse(status=403)

        if mode == "subscribe" and token == configured_token:
            logger.info("Webhook verified for user=%s platform=%s", username, self.platform)
            return HttpResponse(challenge, content_type="text/plain")

        logger.warning(
            "Webhook verification failed: token mismatch for user=%s platform=%s mode=%s",
            username, self.platform, mode,
        )
        return HttpResponse(status=403)

    def _handle_post(self, request, username, parse_fn):
        body = request.body
        integration = self._get_integration(username)

        # Always return 200 to Meta — even on errors — so Meta doesn't retry
        # indefinitely. Processing happens in background.
        if not integration:
            return HttpResponse("EVENT_RECEIVED", status=200)

        if not _verify_meta_signature(body, integration.app_secret, request.headers.get("X-Hub-Signature-256", "")):
            logger.warning("Signature mismatch on %s webhook for user=%s", self.platform, username)
            return HttpResponse("EVENT_RECEIVED", status=200)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return HttpResponse("EVENT_RECEIVED", status=200)

        self._submit(integration, parse_fn(payload))
        return HttpResponse("EVENT_RECEIVED", status=200)


class WhatsAppWebhookView(_MetaWebhookView):
    platform = "whatsapp"

    def post(self, request, username):
        return self._handle_post(request, username, parse_whatsapp)


class MessengerWebhookView(_MetaWebhookView):
    platform = "messenger"

    def post(self, request, username):
        return self._handle_post(request, username, parse_messenger)


class InstagramWebhookView(_MetaWebhookView):
    platform = "instagram"

    def post(self, request, username):
        return self._handle_post(request, username, parse_instagram)


# ---------------------------------------------------------------------------
# Telegram — POST only, secret token header
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name="dispatch")
class TelegramWebhookView(_BaseWebhookView):
    platform = "telegram"

    def post(self, request, username):
        integration = self._get_integration(username)
        if not integration:
            return HttpResponse("OK", status=200)

        # Verify secret token if configured on the integration
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if integration.verify_token and secret != integration.verify_token:
            logger.warning("Telegram secret mismatch for user=%s", username)
            return HttpResponse("OK", status=200)

        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return HttpResponse("OK", status=200)

        self._submit(integration, parse_telegram(payload))
        return HttpResponse("OK", status=200)
