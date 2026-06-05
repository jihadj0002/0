import json
import logging
import re
import uuid

from back.models import Message, UsageLog

# Image URLs (storage links, or any http(s) link ending in an image extension).
_IMAGE_URL_RE = re.compile(
    r"https?://\S+?\.(?:jpg|jpeg|png|gif|webp|bmp|svg)(?:\?\S*)?",
    re.IGNORECASE,
)


def _strip_image_urls(text):
    """Remove image URLs from a reply and tidy the leftover whitespace."""
    if not text:
        return text
    cleaned = _IMAGE_URL_RE.sub("", text)
    # Collapse blank lines / stray spaces left behind by removed URLs.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()

from .context import build_system_prompt, get_conversation_history
from .providers import call_llm
from .sender import send_reply
from .tools import TOOL_DEFINITIONS, execute_tool

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 5


def run(conversation, incoming_message):
    """
    Entry point for the AI pipeline.

    Called from the post_save signal (inside a webhook background thread) after
    a customer message is persisted. Runs synchronously — already off the main
    request thread.

    Flow:
        pre-flight credit check → build context → LLM call →
        [tool loop up to MAX_TOOL_ITERATIONS] →
        final text → save bot Message → send via platform → log all LLM calls
    """
    if not conversation.is_ai_enabled:
        return

    user = conversation.user

    # Pre-flight: ensure the user has a balance and still has credits
    try:
        from billing.models import Plan, UserBalance
        balance = UserBalance.objects.filter(user=user).first()
        if balance is None:
            # Auto-provision a free balance for users created before billing was added
            plan = Plan.objects.filter(name="free", is_active=True).first()
            if plan:
                balance, _ = UserBalance.objects.get_or_create(
                    user=user,
                    defaults={
                        "plan": plan,
                        "credits_remaining": plan.monthly_credits,
                        "credits_total": plan.monthly_credits,
                        "renewal_date": UserBalance.next_renewal_date(),
                    },
                )
        if balance and balance.credits_remaining <= 0:
            logger.warning("Credits exhausted for user=%s — skipping pipeline conv=%s", user.pk, conversation.pk)
            return
    except Exception:
        logger.warning("Pre-flight credit check failed for user=%s — proceeding anyway", user.pk)
    reply_id = uuid.uuid4().hex

    system_prompt = build_system_prompt(user, conversation)
    history = get_conversation_history(conversation, limit=20)

    # Ensure the triggering message is the last user turn
    customer_text = incoming_message.text or ""
    if not history or history[-1].get("content") != customer_text or history[-1].get("role") != "user":
        history.append({"role": "user", "content": customer_text})

    messages = [{"role": "system", "content": system_prompt}] + history

    # Per-integration model override (falls back to provider default)
    integration = user.integrations.filter(platform=conversation.platform).first()
    model = (integration.ai_model or None) if integration else None

    final_text = None
    pending_images = []
    transferred = False

    for iteration in range(MAX_TOOL_ITERATIONS):
        try:
            llm_msg, usage = call_llm(messages=messages, tools=TOOL_DEFINITIONS, model=model)
        except Exception:
            logger.exception("LLM call failed reply_id=%s iter=%d", reply_id, iteration)
            break

        _log(user, reply_id, usage, call_type=f"call_{iteration + 1}")

        # No tool calls → LLM is done
        if not llm_msg.tool_calls:
            final_text = llm_msg.content or ""
            break

        # Append assistant turn (with tool_calls) so the thread stays coherent
        messages.append(llm_msg)

        # Execute every tool call in this turn
        for tc in llm_msg.tool_calls:
            fn_name = tc.function.name
            try:
                fn_args = json.loads(tc.function.arguments or "{}")
            except (json.JSONDecodeError, TypeError):
                fn_args = {}

            result = execute_tool(fn_name, fn_args, user, conversation)

            # Collect images for delivery, but NEVER expose the raw URLs back to
            # the LLM — otherwise it echoes them into the text reply. The model
            # only gets a confirmation; images are sent separately by send_reply.
            tool_content = result
            if fn_name == "send_images" and isinstance(result, dict):
                imgs = result.get("images", []) or []
                pending_images.extend(imgs)
                tool_content = {
                    "pid": result.get("pid"),
                    "name": result.get("name"),
                    "images_sent": len(imgs),
                    "status": "images sent to the customer" if imgs else "no images available for this product",
                }
                if result.get("error"):
                    tool_content["error"] = result["error"]

            if fn_name == "transfer_chat":
                transferred = True

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(tool_content),
            })

        if transferred:
            final_text = "I'm connecting you with a human agent now. Please wait a moment."
            break

    if not final_text:
        logger.warning("Pipeline produced no reply reply_id=%s conv=%s", reply_id, conversation.pk)
        return

    # Safety net: the AI must never put image URLs in text — images go only via
    # send_images. Strip any that slipped through before saving/sending.
    final_text = _strip_image_urls(final_text)

    # Deduplicate image URLs, cap at 5
    seen = set()
    unique_images = []
    for img in pending_images:
        if img not in seen:
            seen.add(img)
            unique_images.append(img)
            if len(unique_images) == 5:
                break

    # Persist bot reply
    Message.objects.create(
        conversation=conversation,
        sender="bot",
        text=final_text,
        attachments={"images": unique_images} if unique_images else None,
    )

    # Send via platform
    send_reply(conversation, final_text, image_urls=unique_images or None)

    # Deduct credits after reply is confirmed sent
    try:
        from billing.deductions import deduct_for_reply
        deduct_for_reply(user, reply_id)
    except Exception:
        logger.exception("Credit deduction failed reply_id=%s", reply_id)


def _log(user, reply_id, usage, call_type):
    try:
        UsageLog.objects.create(
            user=user,
            reply_id=reply_id,
            model=usage.get("model", "unknown"),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            call_type=call_type,
        )
    except Exception:
        logger.warning("UsageLog write failed reply_id=%s", reply_id)
