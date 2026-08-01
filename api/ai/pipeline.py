import json
import logging
import re
import time
import uuid

from back.models import Integration, Message, ToolCallLog, UsageLog

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


# Detect when a reply *claims* to send/share photos — used to catch the common
# failure where the model says "ছবি পাঠাচ্ছি" / "sending the images" without ever
# calling send_images. Requires both an image noun and a sending cue.
_IMG_NOUN_RE = re.compile(r"(ছবি|chh?obi|image|photo|picture|\bpic\b)", re.IGNORECASE)
_SEND_CUE_RE = re.compile(
    r"(পাঠা|দিচ্ছি|দিলাম|পাঠালাম|পাঠাচ্ছি|শেয়ার|patha|dicchi|dilam|"
    r"send|sending|share|sharing|attach|here (are|is))",
    re.IGNORECASE,
)


def _promises_images(text):
    """True if the reply text implies images are being sent."""
    if not text:
        return False
    return bool(_IMG_NOUN_RE.search(text) and _SEND_CUE_RE.search(text))


from .context import build_system_prompt, get_conversation_history
from .providers import call_llm
from .sender import send_reply
from .tools import TOOL_DEFINITIONS, execute_tool, parse_focus_products

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 7


def _split_text_messages(text):
    if not text:
        return []
    parts = [p.strip() for p in text.split("\n\n") if p.strip()]
    return parts if len(parts) > 1 else [text]




def _summarize_tool_result(tool_name, result):
    """Produce a short human-readable summary of a tool result for ToolCallLog."""
    if not isinstance(result, dict):
        return str(result)[:500]

    if "error" in result:
        return f"Error: {result['error']}"

    if tool_name == "search_products":
        products = result.get("products", [])
        names = [p.get("name", p.get("pid", "?"))[:40] for p in products[:5]]
        extra = f" (+{len(products)-5} more)" if len(products) > 5 else ""
        return f"Found {result.get('total', len(products))} products: {', '.join(names)}{extra}"

    if tool_name == "send_images":
        products = result.get("products", [])
        if products:
            names = []
            for p in products[:3]:
                imgs = len(p.get("images", []))
                names.append(f"{p.get('name', p.get('pid', '?'))} ({imgs} imgs)")
            extra = f" (+{len(products)-3} more)" if len(products) > 3 else ""
            return f"Sent images for {len(products)} product(s): {', '.join(names)}{extra}"
        if result.get("images"):
            return f"Sent {len(result['images'])} image(s) for pid={result.get('pid', '?')}"
        return "No images available"

    if tool_name == "create_order":
        if result.get("order_id"):
            return (
                f"Order {result['order_id']} created, "
                f"status={result['status']}, total={result.get('total', '?')}"
            )
        return f"Error: {result.get('error', 'unknown')}"

    if tool_name == "get_product_details":
        if result.get("name"):
            return (
                f"Product: {result['name']} "
                f"(price={result.get('price', '?')}, stock={result.get('stock', '?')})"
            )
        return f"Error: {result.get('error', 'not found')}"

    if tool_name == "get_order_status":
        if result.get("status"):
            return (
                f"Order {result.get('order_id', '?')}: "
                f"{result['status']}, total={result.get('total', '?')}"
            )
        return f"Error: {result.get('error', 'not found')}"

    if tool_name == "create_ticket":
        if result.get("ticket_id"):
            return (
                f"Ticket #{result['ticket_id']} created, "
                f"priority={result.get('priority', 'medium')}"
            )
        return f"Error: {result.get('error', 'unknown')}"

    if tool_name == "update_customer":
        updated = result.get("updated", [])
        if updated:
            return f"Updated customer fields: {', '.join(updated)}"
        return "No updates"

    if tool_name == "search_knowledge_base":
        count = result.get("total", 0)
        return f"Found {count} knowledge base result(s)"

    keys = list(result.keys())[:5]
    return f"Result keys: {', '.join(keys)}"


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

    # Integration-level guard: don't proceed if AI is disabled at the platform
    # level or if there's no access token to send replies with.
    integration = Integration.get_active(user, conversation.platform)
    if not integration or not integration.is_enabled:
        logger.info("Integration AI disabled — skipping pipeline conv=%s", conversation.pk)
        return
    if not integration.access_token:
        logger.info("No access token for platform=%s — skipping pipeline conv=%s", conversation.platform, conversation.pk)
        return
    model = (integration.ai_model or None) if integration else None

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

    customer_text = incoming_message.text or ""

    # Check if the triggering message has image analysis data to pass to context.
    # Look at the latest unprocessed customer message with image analysis in attachments.
    image_analysis = None
    if customer_text and "[Image:" in customer_text:
        try:
            latest_img_msg = Message.objects.filter(
                conversation=conversation, sender="customer",
            ).exclude(attachments=None).order_by("-timestamp").first()
            if latest_img_msg and latest_img_msg.attachments:
                att = latest_img_msg.attachments
                ia = att.get("analysis_data") or {}
                asearch = att.get("analysis_search") or []
                if ia:
                    image_analysis = ia
                    if asearch:
                        image_analysis["analysis_search"] = asearch
        except Exception:
            pass

    system_prompt = build_system_prompt(user, conversation, image_analysis=image_analysis)
    history = get_conversation_history(conversation, limit=12)

    # Ensure the triggering message is the last user turn
    if not history or history[-1].get("content") != customer_text or history[-1].get("role") != "user":
        history.append({"role": "user", "content": customer_text})

    messages = [{"role": "system", "content": system_prompt}] + history

    final_text = None
    pending_images = []
    product_cards = []
    transferred = False
    image_promise_corrected = False
    search_called = False
    focus_hinted = False
    _product_keywords = re.compile(
        r"(price|dam|দাম|dokan|দোকান|product|প্রোডাক্ট|পণ্য|item|"
        r"কিনতে|n?e?ed?|order|অর্ডার|available|stock|photo|image|ছবি|"
        r"ki.?ki.?ache|কি কি আছে|show|দেখান|want)",
        re.IGNORECASE,
    )

    for iteration in range(MAX_TOOL_ITERATIONS):
        try:
            llm_msg, usage = call_llm(
                messages=messages,
                tools=TOOL_DEFINITIONS,
                model=model,
                temperature=0.6,
                max_tokens=700,
            )
        except Exception:
            logger.exception("LLM call failed reply_id=%s iter=%d", reply_id, iteration)
            break

        _log(user, reply_id, usage, call_type=f"call_{iteration + 1}")

        # Track which tools were called
        if llm_msg.tool_calls:
            for tc in llm_msg.tool_calls:
                fn = tc.function.name
                if fn == "search_products":
                    search_called = True

        # No tool calls → LLM is done (guard: force think or search first)
        if not llm_msg.tool_calls:
            candidate = llm_msg.content or ""

            # GUARD: force search_products if the query looks like a product request
            is_product_query = bool(_product_keywords.search(customer_text or ""))
            if is_product_query and not search_called:
                has_focus = bool(parse_focus_products(conversation.current_product))
                if has_focus and not focus_hinted:
                    focus_hinted = True
                    messages.append({"role": "assistant", "content": candidate})
                    messages.append({
                        "role": "system",
                        "content": (
                            "Relevant products are already listed in "
                            "'## Recent Searched Products' above. Use that data. "
                            "Only call search_products if the customer asks for "
                            "something not already there."
                        ),
                    })
                    continue
                elif not has_focus:
                    messages.append({"role": "assistant", "content": candidate})
                    messages.append({
                        "role": "system",
                        "content": (
                            "The customer is asking about products. You MUST call "
                            "search_products before replying. Use different keywords "
                            "(Bengali → English, synonyms). Do NOT rely on focused products "
                            "alone — search the catalog first."
                        ),
                    })
                    continue
                # has_focus + already hinted → fall through to final_text

            # Safety net for the "promised images but never sent them" failure:
            # if the reply claims to send photos but send_images was never called
            # (no images/cards collected) and we have focused products to show,
            # force ONE corrective pass to either actually send or drop the claim.
            if (not image_promise_corrected
                    and _promises_images(candidate)
                    and not pending_images and not product_cards
                    and parse_focus_products(conversation.current_product)):
                image_promise_corrected = True
                messages.append({"role": "assistant", "content": candidate})
                messages.append({
                    "role": "system",
                    "content": (
                        "You told the customer you would send photos but did NOT call "
                        "send_images. Either call send_images now with the correct product "
                        "pid(s) from the focused products, or resend your reply WITHOUT "
                        "mentioning photos. Never claim to send images without the tool."
                    ),
                })
                continue
            final_text = candidate
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

            t0 = time.time()
            result = execute_tool(fn_name, fn_args, user, conversation)
            elapsed = int((time.time() - t0) * 1000)

            # Log every tool call to ToolCallLog for audit trail
            try:
                ToolCallLog.objects.create(
                    conversation=conversation,
                    user=user,
                    reply_id=reply_id,
                    iteration=iteration,
                    tool_name=fn_name,
                    arguments=fn_args,
                    result_summary=_summarize_tool_result(fn_name, result),
                    execution_time_ms=elapsed,
                )
            except Exception as exc:
                logger.warning("ToolCallLog write failed: %s", exc)

            # Relevance guard: for send_images with multiple products,
            # drop products that share <2 content tokens with the customer query.
            # Prevents unrelated items (e.g. Similac when customer asked about NAN 2).
            if fn_name == "send_images" and isinstance(result, dict) and customer_text:
                prods = result.get("products") or []
                if len(prods) > 1:
                    from .tools import _content_tokens as _tok
                    q_toks = set(_tok(customer_text))
                    if q_toks:
                        kept = []
                        for p in prods:
                            p_toks = set(_tok(p.get("name", "")))
                            overlap = len(q_toks & p_toks)
                            # Keep if ≥2 tokens overlap, or a longer query token matches exactly
                            if overlap >= 2 or any(
                                qt in p.get("name", "").lower()
                                for qt in q_toks if len(qt) > 3
                            ):
                                kept.append(p)
                        if kept and len(kept) < len(prods):
                            result["products"] = kept
                            result["total"] = len(kept)

            # Collect images and structured product cards for delivery.
            # NEVER expose raw URLs back to the LLM — the model only gets
            # a confirmation; visuals are sent separately by send_reply.
            tool_content = result
            if fn_name == "send_images" and isinstance(result, dict):
                products = result.get("products")
                if products:
                    if len(products) > 1:
                        product_cards.extend(products)
                        if conversation.platform in ("whatsapp", "telegram"):
                            for p in products:
                                imgs = p.get("images") or []
                                if imgs:
                                    pending_images.append(imgs[0])
                        tool_content = {
                            "products": [
                                {"pid": p.get("pid"), "name": p.get("name"),
                                 "price": p.get("price"), "sku": p.get("sku")}
                                for p in products
                            ],
                            "products_count": len(products),
                            "status": "products sent as a scrollable carousel — briefly mention names and prices in your reply",
                        }
                    else:
                        for p in products:
                            pending_images.extend(p.get("images", []))
                        p = products[0]
                        tool_content = {
                            "pid": p.get("pid"),
                            "name": p.get("name"),
                            "price": p.get("price"),
                            "sku": p.get("sku"),
                            "images_sent": len(pending_images),
                            "status": "product images sent to the customer one by one — you may describe what is shown",
                        }
                else:
                    imgs = result.get("images", []) or []
                    pending_images.extend(imgs)
                    tool_content = {
                        "pid": result.get("pid"),
                        "name": result.get("name"),
                        "images_sent": len(imgs),
                        "status": "images sent to the customer" if imgs else "no images available",
                    }
                if result.get("error"):
                    tool_content["error"] = result["error"]

            if fn_name == "create_ticket":
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
    attachment = {}
    if unique_images:
        attachment["images"] = unique_images
    if product_cards:
        attachment["cards"] = product_cards
        attachment["type"] = "product_cards" if len(product_cards) > 1 else "product_card"

    msg = Message.objects.create(
        conversation=conversation,
        sender="bot",
        text=final_text,
        attachments=attachment or None,
    )

    # Send via platform — pass product_cards only for multi-product carousel;
    # single-product images are sent individually (avoids duplicate image messages).
    delivery = send_reply(
        conversation,
        _split_text_messages(final_text),
        image_urls=unique_images or None,
        product_cards=product_cards if len(product_cards) > 1 else None,
    )

    # Record the delivery outcome on the message so "saved but not delivered"
    # failures are visible instead of silent.
    try:
        raw = msg.raw_payload or {}
        if not isinstance(raw, dict):
            raw = {}
        raw["delivery"] = delivery
        msg.raw_payload = raw
        msg.save(update_fields=["raw_payload"])
    except Exception:
        logger.warning("Delivery-status write failed reply_id=%s", reply_id)

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
