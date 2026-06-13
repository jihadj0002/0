import hashlib
import hmac
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.contrib.auth.models import User
from django.db import close_old_connections
from django.db.models import Q
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from back.models import Conversation, Integration, Message, MessageBatch

from .utils.parsers import parse_instagram, parse_messenger, parse_telegram, parse_whatsapp
from .utils.whatsapp import download_whatsapp_media

logger = logging.getLogger(__name__)

# Bounded thread pool — handles burst traffic without spawning unlimited threads.
_executor = ThreadPoolExecutor(max_workers=50)

# Per-conversation timers: {conversation_id: threading.Timer}
# When a new message arrives for a conversation, any existing timer is cancelled
# and a fresh 5-second timer is started. This ensures rapid bursts are combined
# into one AI turn rather than triggering a pipeline call per message.
_batch_timers: dict[int, threading.Timer] = {}
_batch_timers_lock = threading.Lock()

# Per-conversation pipeline locks — prevents overlapping run() calls for the
# same conversation. When a timer fires and the lock is already held (previous
# pipeline still running), this invocation skips; the unprocessed batches stay
# in the DB and will be picked up by the next timer.
_conv_locks: dict[int, threading.Lock] = {}
_conv_locks_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Signature helpers
# ---------------------------------------------------------------------------

def _verify_meta_signature(body_bytes, app_secret, signature_header):
    """Verify X-Hub-Signature-256.

    SECURITY: Returns True in "skip mode" when ``app_secret`` is empty or no
    signature header is present. This is intentional for per-user webhooks
    during manual setup (an Integration may not yet have an app_secret), but it
    means signature checks SILENTLY PASS when the secret is unset. Callers that
    serve multi-tenant traffic (e.g. the app-level webhook) MUST require a
    configured secret + present signature themselves before calling this and
    fail closed if either is missing — do not rely on skip mode in production.
    """
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

    The timer callback submits work to the shared thread pool rather than
    running directly in a timer thread — this avoids spawning unlimited
    database connections and keeps pipeline work managed by the same pool.
    """
    with _batch_timers_lock:
        existing = _batch_timers.get(conversation_id)
        if existing is not None:
            existing.cancel()
        t = threading.Timer(5.0, lambda cid: _executor.submit(_fire_batch_pipeline, cid), args=(conversation_id,))
        t.daemon = True
        _batch_timers[conversation_id] = t
        t.start()


def _fire_batch_pipeline(conversation_id):
    """
    Called (via executor) after 5 seconds of silence for a conversation.
    Combines all unprocessed MessageBatch rows into a single AI turn.

    Locks per-conversation so overlapping runs never happen.  Batch rows are
    pinned by primary key to prevent concurrent runs from stealing each other's
    batches.
    """
    # Acquire per-conversation lock (non-blocking) — skip if already running.
    with _conv_locks_lock:
        conv_lock = _conv_locks.get(conversation_id)
        if conv_lock is None:
            conv_lock = threading.Lock()
            _conv_locks[conversation_id] = conv_lock

    if not conv_lock.acquire(blocking=False):
        # A pipeline run is already in flight for this conversation. The batches
        # that triggered THIS timer would otherwise be stranded: the customer may
        # have stopped sending, so no future message would ever create a new
        # timer to pick them up. Reschedule a fresh timer so the in-flight run's
        # leftovers get drained once it releases the lock.
        logger.info("Pipeline already running for conv=%s — rescheduling to drain remaining batches", conversation_id)
        _schedule_batch_pipeline(conversation_id)
        return

    try:
        with _batch_timers_lock:
            _batch_timers.pop(conversation_id, None)

        close_old_connections()
        from types import SimpleNamespace
        from api.ai.pipeline import run

        conversation = Conversation.objects.get(id=conversation_id)

        # Guard: don't fire if AI was disabled since the timer was scheduled.
        ai_still_enabled = (
            conversation.is_ai_enabled
            and Integration.objects.filter(
                user=conversation.user,
                platform=conversation.platform,
                is_enabled=True,
            ).exists()
        )
        if not ai_still_enabled:
            MessageBatch.objects.filter(
                conversation=conversation, processed=False
            ).update(processed=True)
            return

        # Pin batch primary keys so concurrent invocations don't interfere.
        batch_pks = list(
            MessageBatch.objects.filter(
                conversation=conversation,
                processed=False,
            ).order_by("timestamp").values_list("pk", flat=True)
        )
        if not batch_pks:
            return

        combined_text = "\n".join(
            b.message_text
            for b in MessageBatch.objects.filter(pk__in=batch_pks)
            if b.message_text.strip()
        )

        if not combined_text.strip():
            MessageBatch.objects.filter(pk__in=batch_pks).update(processed=True)
            return

        unified = SimpleNamespace(text=combined_text)
        try:
            run(conversation, unified)
        except Exception:
            logger.exception(
                "Pipeline crashed conv=%s — batches preserved for retry", conversation_id
            )
            return

        # Only mark consumed AFTER pipeline completes successfully, and only
        # our pinned batches — never touch batches that arrived in the meantime.
        MessageBatch.objects.filter(pk__in=batch_pks).update(processed=True)

        # New messages may have arrived DURING this run. Their timer could have
        # fired while we held the lock (it reschedules itself), but cover the race
        # explicitly: if unprocessed batches remain, drain them on a fresh timer
        # so the tail of a burst is never left without an AI reply.
        if MessageBatch.objects.filter(
            conversation=conversation, processed=False
        ).exists():
            _schedule_batch_pipeline(conversation_id)

    except Conversation.DoesNotExist:
        pass
    except Exception:
        logger.exception("_fire_batch_pipeline failed for conversation=%s", conversation_id)
    finally:
        conv_lock.release()
        # Clean up the lock entry so the dict doesn't grow unbounded.
        with _conv_locks_lock:
            if _conv_locks.get(conversation_id) is conv_lock:
                del _conv_locks[conversation_id]
        close_old_connections()


def _process_webhook(user_id, platform, unified_messages, access_token):
    """
    Runs in a thread pool worker. Persists each message and (re)starts the
    per-conversation 5-second batch timer so rapid bursts are collapsed into
    one AI pipeline call. When AI is disabled for this platform, messages
    are still stored (for human agent view) but media analysis and pipeline
    scheduling are skipped.
    """
    close_old_connections()
    try:
        user = User.objects.get(id=user_id)
        ai_enabled = Integration.objects.filter(
            user=user, platform=platform, is_enabled=True
        ).exists()

        conversation_ids = set()
        for msg_data in unified_messages:
            try:
                conv_id = _persist_message(user, platform, msg_data, access_token, ai_enabled)
                if conv_id:
                    conversation_ids.add(conv_id)
            except Exception:
                logger.exception(
                    "Failed to persist message mid=%s user=%s platform=%s",
                    msg_data.get("message_id"), user_id, platform,
                )
        # Only schedule the AI pipeline when AI is active for this platform
        if ai_enabled:
            for cid in conversation_ids:
                _schedule_batch_pipeline(cid)
    except Exception:
        logger.exception("_process_webhook crashed user_id=%s platform=%s", user_id, platform)
    finally:
        close_old_connections()


def _persist_message(user, platform, msg_data, access_token, ai_enabled):
    """Persist a single incoming message. Returns the conversation id, or None if dropped."""
    mid = msg_data.get("message_id") or None
    customer_id = msg_data.get("customer_id", "")

    conv, created = Conversation.objects.get_or_create(
        user=user,
        platform=platform,
        customer_id=customer_id,
        defaults={
            "customer_name": msg_data.get("customer_name") or "",
            "is_ai_enabled": ai_enabled,
        },
    )

    # Using API to fetch User's name and profile picture for Messenger conversations, since the webhook payload doesn't include them. This enriches the conversation context for the AI and allows us to display the customer's name and profile image in the UI. We only do this on creation to avoid unnecessary API calls on every message, but we backfill the name if we learn it later and it was missing at creation time.
    if created and platform == "messenger":
        try:
            from api.utils.get_msngr_profile import get_messenger_profile
            profile = get_messenger_profile(customer_id, access_token)
            conv.customer_name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
            conv.profile_image = profile.get("profile_pic", "")
            conv.save(update_fields=["customer_name", "profile_image"])
        except Exception as exc:
            logger.warning("Failed to fetch Messenger profile for customer_id=%s: %s", customer_id, exc)

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
    # Skip analysis when AI is disabled for this platform — saves API costs.
    media_url = (attachments or {}).get("url") or ""
    if not media_url and isinstance((attachments or {}).get("payload"), dict):
        media_url = (attachments or {}).get("payload", {}).get("url", "")
    if att_type == "image" and media_url and ai_enabled:
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

    elif att_type in ("audio", "voice") and media_url and ai_enabled:
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

    # Idempotent message creation — use get_or_create on mid to prevent the
    # TOCTOU race between the old "check, then create" pattern.
    if mid:
        _, msg_created = Message.objects.get_or_create(
            mid=mid,
            defaults={
                "conversation": conv,
                "sender": "customer",
                "text": msg_text or None,
                "attachments": attachments if attachments else None,
                "raw_payload": msg_data.get("raw"),
            },
        )
        if not msg_created:
            # Duplicate mid — Meta retried a message we already stored.
            # The conversation already exists, so just return its id.
            return conv.id
    else:
        try:
            Message.objects.create(
                conversation=conv,
                sender="customer",
                text=msg_text or None,
                attachments=attachments if attachments else None,
                raw_payload=msg_data.get("raw"),
            )
        except Exception:
            logger.exception("Failed to create message (no mid) conv=%s", conv.pk)
            return conv.id

    # Store enriched text in batch so _fire_batch_pipeline combines it correctly.
    # Only create batch rows when AI is active — no point batching for disabled-AI convos.
    if ai_enabled:
        try:
            MessageBatch.objects.create(
                conversation=conv,
                message_text=msg_text,
                platform=platform,
            )
        except Exception:
            logger.warning("MessageBatch creation failed conv=%s", conv.pk)

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
            return Integration.get_active(user, self.platform)
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


# ---------------------------------------------------------------------------
# App-level webhook — single endpoint for ALL OAuth-connected Meta pages
# ---------------------------------------------------------------------------
# Configured ONCE in the Meta App dashboard. Meta sends every connected page's
# events here. We attribute each entry to an Integration by its page/IG id and
# fan out to the existing background processor. This is purely additive — the
# per-user webhook views above are untouched.

# object -> (platform, parser)
_OBJECT_MAP = {
    "page": "messenger",
    "instagram": "instagram",
    "whatsapp_business_account": "whatsapp",
}


@method_decorator(csrf_exempt, name="dispatch")
class MetaAppWebhookView(View):

    def get(self, request):
        """Meta webhook verification challenge (app-level)."""
        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token", "")
        challenge = request.GET.get("hub.challenge", "")

        configured = settings.META_WEBHOOK_VERIFY_TOKEN or ""
        if mode == "subscribe" and configured and token == configured:
            return HttpResponse(challenge, content_type="text/plain")
        return HttpResponse(status=403)

    def post(self, request):
        # Meta MUST always receive a 200 — never let this raise.
        try:
            body = request.body

            # The app-level webhook receives events for EVERY connected tenant.
            # If META_APP_SECRET is unset, _verify_meta_signature runs in skip
            # mode (returns True) and forged payloads could inject messages into
            # any tenant's conversations by guessing a page id. Refuse to process
            # unsigned traffic on this shared endpoint — fail closed.
            app_secret = settings.META_APP_SECRET or ""
            signature = request.headers.get("X-Hub-Signature-256", "")
            if not app_secret:
                logger.error(
                    "App-level webhook: META_APP_SECRET not configured — "
                    "dropping event (cannot verify signature)."
                )
                return HttpResponse("EVENT_RECEIVED", status=200)
            if not signature or not _verify_meta_signature(body, app_secret, signature):
                logger.warning("App-level webhook: signature mismatch")
                return HttpResponse("EVENT_RECEIVED", status=200)

            try:
                payload = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                return HttpResponse("EVENT_RECEIVED", status=200)

            platform = _OBJECT_MAP.get(payload.get("object"))
            if not platform:
                return HttpResponse("EVENT_RECEIVED", status=200)

            parser = {
                "messenger": parse_messenger,
                "instagram": parse_instagram,
                "whatsapp": parse_whatsapp,
            }[platform]

            for entry in payload.get("entry", []):
                try:
                    self._route_entry(platform, parser, payload.get("object"), entry)
                except Exception:
                    logger.exception("App-level webhook: failed routing an entry")

        except Exception:
            logger.exception("App-level webhook POST crashed")

        return HttpResponse("EVENT_RECEIVED", status=200)

    def _route_entry(self, platform, parser, object_type, entry):
        entry_id = entry.get("id")
        if not entry_id:
            return

        # Attribute the event to the Integration registered for this page/IG id.
        qs = Integration.objects.filter(platform=platform, integration_id=entry_id)
        if platform == "instagram":
            qs = Integration.objects.filter(
                Q(platform=platform)
                & (Q(integration_id=entry_id) | Q(ig_account_id=entry_id))
            )
        integration = qs.first()
        if not integration:
            logger.info(
                "App-level webhook: no integration for platform=%s entry_id=%s",
                platform, entry_id,
            )
            return

        single_payload = {"object": object_type, "entry": [entry]}
        messages = parser(single_payload)
        if not messages:
            return

        _executor.submit(
            _process_webhook,
            integration.user_id,
            platform,
            messages,
            integration.access_token or "",
        )


# ---------------------------------------------------------------------------
# Zombie recovery — process MessageBatch rows left behind after a restart
# ---------------------------------------------------------------------------

def recover_zombie_batches():
    """Fire the pipeline for any MessageBatch rows left behind after a crash/restart."""
    close_old_connections()
    orphan = MessageBatch.objects.filter(processed=False)
    if not orphan.exists():
        return
    cids = set(orphan.values_list("conversation_id", flat=True))
    logger.info("Recovering %d zombie batches across %d conversations", orphan.count(), len(cids))
    for cid in cids:
        try:
            conv = Conversation.objects.get(id=cid)
            if not conv.is_ai_enabled:
                orphan.filter(conversation_id=cid).update(processed=True)
                continue
            _fire_batch_pipeline(cid)
        except Conversation.DoesNotExist:
            orphan.filter(conversation_id=cid).delete()
        except Exception:
            logger.exception("Zombie recovery failed for conversation=%s", cid)
