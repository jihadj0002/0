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
    """Remove image URLs from a reply and tidy the leftover whitespace.

    Also strips markdown bold markers and whitespace-only lines so a reply that
    embedded image URLs doesn't come out with ugly gaps like ``\\n     \\n   Price:``.
    """
    if not text:
        return text
    cleaned = _IMAGE_URL_RE.sub("", text)
    cleaned = cleaned.replace("**", "")
    # Drop whitespace-only lines; collapse multiple blank lines to one.
    lines = []
    for line in cleaned.split("\n"):
        if line.strip():
            lines.append(line)
        elif lines and lines[-1] != "":
            lines.append("")
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


# Detect when a reply *claims* to send/share photos — used to catch the common
# failure where the model says "ছবি পাঠাচ্ছি" / "sending the images" without ever
# calling send_images. Requires both an image noun and a sending cue.
_IMG_NOUN_RE = re.compile(r"(ছবি|chh?obi|image|photo|picture|\bpic\b)", re.IGNORECASE)
_SEND_CUE_RE = re.compile(
    r"(পাঠা|দিচ্ছি|দিলাম|পাঠালাম|পাঠাচ্ছি|শেয়ার|patha|dicchi|dilam|"
    r"send|sending|share|sharing|attach|here (are|is)|দেখা|দেখান|দেখাচ্ছি)",
    re.IGNORECASE,
)


def _promises_images(text):
    """True if the reply text implies images are being sent."""
    if not text:
        return False
    return bool(_IMG_NOUN_RE.search(text) and _SEND_CUE_RE.search(text))


# The catalog has no videos. Catch replies that claim to send/attach videos.
_VIDEO_NOUN_RE = re.compile(r"(video|vdo|ভিডিও|clipp?|reels?|reel|film)", re.IGNORECASE)


def _promises_videos(text):
    """True if the reply text implies videos are being sent."""
    if not text:
        return False
    return bool(_VIDEO_NOUN_RE.search(text) and _SEND_CUE_RE.search(text))


# When product cards/images are being sent, the reply text must stay to ONE short
# message — the visuals already carry names, prices, and photos.
_VERBOSE_LIST_RE = re.compile(r"(^|\n)\s*\d+[.)]", re.MULTILINE)
_LIST_MARKER_RE = re.compile(r"(^|\n)\s*\d+[.)]\s+")
_MAX_VISUAL_TEXT_LEN = 180


def _is_verbose(text):
    """True if a reply is too long to accompany product cards/images."""
    if not text:
        return False
    if len(text) > _MAX_VISUAL_TEXT_LEN:
        return True
    if "\n\n" in text:
        return True
    return bool(_VERBOSE_LIST_RE.search(text))


def _collapse_verbose_text(text):
    """Compress a long reply to its first + last chunk.

    Chunks split on sentence boundaries (., !, ?, ।) and on newlines — e.g. a
    numbered list without trailing periods. Numbered-list markers are stripped
    first, otherwise the period in "1." would split mid-list and truncate items.
    """
    text = _LIST_MARKER_RE.sub(r"\1", text)
    chunks = [
        p.strip()
        for p in re.split(r"(?<=[.!?।])\s+|\n+", text)
        if p.strip()
    ]
    if len(chunks) <= 2:
        return " ".join(chunks)
    return chunks[0] + " " + chunks[-1]


from .context import build_system_prompt, get_conversation_history
from .providers import call_llm
from .sender import send_reply
from .tools import TOOL_DEFINITIONS, execute_tool, parse_focus_products

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 7


def _split_text_messages(text, max_parts=3):
    if not text:
        return []
    parts = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(parts) <= 1:
        return [text] if text.strip() else []
    if len(parts) <= max_parts:
        return parts
    # Cap the number of bubbles — merge overflow into the last part.
    return parts[: max_parts - 1] + ["\n".join(parts[max_parts - 1:])]




def _summarize_tool_result(tool_name, result):
    """Produce a short human-readable summary of a tool result for ToolCallLog."""
    if not isinstance(result, dict):
        return str(result)[:500]

    if "error" in result:
        return f"Error: {result['error']}"

    if tool_name == "search_products":
        products = result.get("products", [])
        if result.get("matched") is False:
            return (
                f"No match for query — {len(products)} product(s) shown as "
                "conversation history only"
            )
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

    Legacy: the Orchestrator is now the single canonical AI path (webhooks route
    through ``run_via_orchestrator``). This shim keeps any stray caller (tests,
    back-office scripts, future integrations) behaviorally identical.
    """
    from .orchestrator import run_via_orchestrator
    run_via_orchestrator(conversation, incoming_message)
    return

    # The legacy monolithic pipeline body below is intentionally unreachable
    # (the Orchestrator is the single AI path). Kept for reference only.
    if not conversation.is_ai_enabled:
        try:
            if not conversation.auto_enable_ai():
                return
        except Exception:
            logger.exception("auto_enable_ai failed conv=%s", conversation.pk)
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
    video_promise_corrected = False
    verbose_corrected = False
    search_called = False
    focus_hinted = False
    # PIDs whose images were already collected this turn — the model sometimes
    # calls send_images repeatedly for the same product.
    _turn_sent_pids = set()
    _product_keywords = re.compile(
        r"(price|dam|দাম|dokan|দোকান|product|প্রোডাক্ট|পণ্য|item|"
        r"কিনতে|n?e?ed?|order|অর্ডার|available|stock|photo|image|ছবি|"
        r"ki.?ki.?ache|কি কি আছে|show|দেখান|want)",
        re.IGNORECASE,
    )
    _video_keywords = re.compile(
        r"(video|vdo|ভিডিও|clipp?|reels?|reel|film|dekhano|দেখানো)",
        re.IGNORECASE,
    )

    # Cross-turn: images sent in the previous bot message must NOT be resent
    # unless the customer explicitly asks for more.
    _requesting_more = bool(
        re.search(r"(more|আরো|আরও|আবার|again|all|সব|dup|extra)", customer_text or "", re.IGNORECASE)
    )
    prev_images = set()
    try:
        last_bot = (
            Message.objects.filter(conversation=conversation, sender="bot")
            .exclude(attachments=None)
            .order_by("-timestamp")
            .first()
        )
        if last_bot and last_bot.attachments:
            prev_images.update(last_bot.attachments.get("images", []) or [])
            for _card in last_bot.attachments.get("cards", []) or []:
                _ci = _card.get("images") or []
                if _ci:
                    prev_images.add(_ci[0])
    except Exception:
        pass

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
            is_video_request = bool(_video_keywords.search(customer_text or ""))
            if is_product_query and not is_video_request and not search_called:
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
            # Safety net: never let the model claim it sent a video — the catalog
            # has no videos. Force ONE corrective pass to retract the claim.
            if (not video_promise_corrected
                    and _promises_videos(candidate)):
                video_promise_corrected = True
                messages.append({"role": "assistant", "content": candidate})
                messages.append({
                    "role": "system",
                    "content": (
                        "The catalog has NO videos — you must NOT claim to send "
                        "videos. Resend your reply politely stating videos are not "
                        "available and offer to send product pictures instead."
                    ),
                })
                continue
            # Safety net: with cards/images already being sent, the text must stay
            # to ONE short message — cards already show names/prices/photos.
            if (not verbose_corrected
                    and (pending_images or product_cards)
                    and _is_verbose(candidate)):
                verbose_corrected = True
                messages.append({"role": "assistant", "content": candidate})
                messages.append({
                    "role": "system",
                    "content": (
                        "Product cards/images are already being sent and show the "
                        "names, prices, and photos. Reply with ONE short message: a "
                        "brief intro sentence and one short follow-up question. Do "
                        "NOT list product names, prices, or details already visible "
                        "in the cards."
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
                    from .tools import _content_tokens as _tok, _latinize_bn
                    q_toks = set(_tok(customer_text))
                    if q_toks:
                        kept = []
                        for p in prods:
                            p_toks = set(_tok(p.get("name", "")))
                            overlap = len(q_toks & p_toks)
                            # Keep if ≥2 tokens overlap, or a longer query token matches
                            # the name — or its romanized form, since Bengali names
                            # ("লেবুর আচার") never share latin tokens with the query.
                            p_name = (p.get("name", "") or "").lower()
                            p_latin = _latinize_bn(p_name).lower() if p_name else ""
                            if overlap >= 2 or any(
                                qt in p_name or (p_latin and qt in p_latin)
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
                products = result.get("products") or []

                # Within-turn dedup: skip products whose images were already
                # collected earlier in this same turn (model sometimes calls
                # send_images repeatedly with the same pids).
                fresh = []
                for p in products:
                    pid = p.get("pid")
                    if pid and pid in _turn_sent_pids:
                        continue
                    if pid:
                        _turn_sent_pids.add(pid)
                    fresh.append(p)

                # Cross-turn dedup: never resend images that were already
                # delivered in the previous bot message — unless the customer
                # explicitly asked for more.
                if not _requesting_more:
                    kept = []
                    for p in fresh:
                        imgs = p.get("images") or []
                        if imgs and imgs[0] in prev_images:
                            continue
                        kept.append(p)
                    fresh = kept

                if not fresh:
                    tool_content = {
                        "status": "images_already_sent",
                        "note": (
                            "You already sent these product images (this turn or the "
                            "previous one). Do NOT resend. Briefly remind the customer "
                            "the pictures were already shared, or suggest other products."
                        ),
                    }
                elif len(fresh) > 1:
                    # Multi-product: ONE image per product, max 4 products.
                    capped = fresh[:4]
                    for p in capped:
                        imgs = p.get("images") or []
                        if imgs:
                            p["images"] = [imgs[0]]
                    product_cards.extend(capped)
                    tool_content = {
                        "products": [
                            {"pid": p.get("pid"), "name": p.get("name"),
                             "price": p.get("price"), "sku": p.get("sku")}
                            for p in capped
                        ],
                        "products_count": len(capped),
                        "status": "products sent as a scrollable carousel — briefly mention names and prices in your reply",
                    }
                elif products:
                    # Single product: up to 4 images, sent one-by-one.
                    p = fresh[0]
                    imgs = (p.get("images") or [])[:4]
                    pending_images.extend(imgs)
                    tool_content = {
                        "pid": p.get("pid"),
                        "name": p.get("name"),
                        "price": p.get("price"),
                        "sku": p.get("sku"),
                        "images_sent": len(imgs),
                        "status": "product images sent to the customer one by one — you may describe what is shown",
                    }
                else:
                    # Flat images payload (no product dicts) — cap at 4.
                    imgs = (result.get("images", []) or [])[:4]
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

    # Deduplicate image URLs, cap at 4 (one per product, max 4 products)
    seen = set()
    unique_images = []
    for img in pending_images:
        if img not in seen:
            seen.add(img)
            unique_images.append(img)
            if len(unique_images) == 4:
                break

    # Deduplicate cards by pid (model can ask for the same products repeatedly)
    seen_pids = set()
    unique_cards = []
    for card in product_cards:
        pid = card.get("pid")
        if pid and pid in seen_pids:
            continue
        if pid:
            seen_pids.add(pid)
        unique_cards.append(card)
    product_cards = unique_cards[:4]

    # Deterministic guard: with cards/images present, collapse a still-verbose
    # reply to first + last sentence — the model can ignore the corrective pass.
    has_visuals = bool(unique_images or product_cards)
    if has_visuals and _is_verbose(final_text):
        final_text = _collapse_verbose_text(final_text)

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
        _split_text_messages(final_text, max_parts=1 if has_visuals else 3),
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
