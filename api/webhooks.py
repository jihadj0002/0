import hashlib
import hmac
import json
import logging
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
# Each slot may block on DB writes + media downloads (I/O bound), so 20 workers
# is appropriate. Requests always return 200 immediately; processing queues here.
_executor = ThreadPoolExecutor(max_workers=20)


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

def _check_and_process_batches(user_id, platform):
    """
    Check if we should process a batch of messages for a user/platform combination.
    Processes when we have 3+ messages or when oldest message is >2 seconds old.
    """
    from django.utils import timezone
    from datetime import timedelta
    
    # Get unprocessed batches for this user/platform
    cutoff_time = timezone.now() - timedelta(seconds=5)  # Use 5 seconds to account for processing delays and ensure we don't process too early. Adjust as needed.
    
    # Find conversations with unprocessed batches
    unprocessed_batches = MessageBatch.objects.filter(
        conversation__user_id=user_id,
        platform=platform,
        processed=False
    ).order_by('timestamp')
    
    if not unprocessed_batches.exists():
        return
    
    # Group by conversation
    conversations_to_process = set()
    for batch in unprocessed_batches:
        conversations_to_process.add(batch.conversation_id)
    
    # Process each conversation that meets criteria
    for conversation_id in conversations_to_process:
        batches = MessageBatch.objects.filter(
            conversation_id=conversation_id,
            platform=platform,
            processed=False
        ).order_by('timestamp')
        
        # Process if we have 3+ messages OR oldest is >2 seconds old
        if batches.count() >= 3 or (batches.first().timestamp < cutoff_time and batches.exists()):
            _process_message_batch(conversation_id, platform)
            # Mark as processed
            batches.update(processed=True)


def _process_message_batch(conversation_id, platform):
    """
    Process a batch of messages for a conversation as a single unit.
    """
    from back.models import Conversation, MessageBatch
    from back.models import Message  # For creating bot messages later if needed
    from api.ai.pipeline import run  # Import the pipeline
    from back.models import Message  # For getting customer messages
    
    try:
        conversation = Conversation.objects.get(id=conversation_id)
        # Get all unprocessed messages for this conversation/platform
        message_batches = MessageBatch.objects.filter(
            conversation=conversation,
            platform=platform,
            processed=False
        ).order_by('timestamp')
        
        if not message_batches.exists():
            return
            
        # Combine messages into a single context
        combined_text = " ".join([mb.message_text for mb in message_batches])
        
        # Create a temporary unified message for the pipeline
        from types import SimpleNamespace
        unified_message = SimpleNamespace()
        unified_message.text = combined_text
        
        # Run the pipeline with the combined message
        run(conversation, unified_message)
        
    except Conversation.DoesNotExist:
        pass  # Conversation was deleted
    except Exception as e:
        logger.exception("_process_message_batch failed for conversation=%s: %s", conversation_id, e)


def _process_webhook(user_id, platform, unified_messages, access_token):
    """
    Runs in a thread pool worker. Persists each message in unified_messages.
    Django DB connections are per-thread — must call close_old_connections()
    at start to ensure a clean connection is checked out from the pool.
    """
    close_old_connections()
    try:
        user = User.objects.get(id=user_id)
        for msg_data in unified_messages:
            try:
                _persist_message(user, platform, msg_data, access_token)
            except Exception:
                logger.exception(
                    "Failed to persist message mid=%s user=%s platform=%s",
                    msg_data.get("message_id"), user_id, platform,
                )
        # Check if we should process a batch for any conversations
        _check_and_process_batches(user_id, platform)
    except Exception:
        logger.exception("_process_webhook crashed user_id=%s platform=%s", user_id, platform)
    finally:
        close_old_connections()


def _persist_message(user, platform, msg_data, access_token):
    mid = msg_data.get("message_id") or None
    customer_id = msg_data.get("customer_id", "")

    # Idempotency — Meta retries on non-200, so the same mid may arrive twice
    if mid and MessageBatch.objects.filter(message_text=msg_data.get("text", ""), processed=False).exists():
        return

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

    # Store message in batch for later processing
    MessageBatch.objects.create(
        conversation=conv,
        message_text=msg_data.get("text", ""),
        platform=platform
    )

    # Backfill name if we learned it and conversation was created without it
    if not created and msg_data.get("customer_name") and not conv.customer_name:
        Conversation.objects.filter(pk=conv.pk).update(
            customer_name=msg_data["customer_name"]
        )

    attachments = msg_data.get("attachments")

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

    Message.objects.create(
        conversation=conv,
        mid=mid,
        sender="customer",
        text=msg_data.get("text"),
        attachments=attachments if attachments else None,
        raw_payload=msg_data.get("raw"),
    )


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
