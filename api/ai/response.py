"""
ResponseGenerator (P0-6): Final LLM call that generates natural-language reply
from verified tool results. Product intents get an AGENTIC loop (the model can
call think / search_products / get_product_details / send_images iteratively,
like the legacy gemini setup); everything else is single-call generation.
"""
import json
import logging
import re
import time

from .context import ConversationContext, Response
from .tools import ToolResult

logger = logging.getLogger(__name__)

_IMAGE_URL_RE = re.compile(
    r"https?://\S+?\.(?:jpg|jpeg|png|gif|webp|bmp|svg)(?:\?\S*)?",
    re.IGNORECASE,
)

# Photo-request tokens — send_images is only offered when one of these appears
# ("pic", "ছবি", "dekhi"...). Plain quantity answers like "5 pcs den" must NOT
# match (overloaded "den"), so only visual-browse words are listed.
_PHOTO_ASK_RE = re.compile(
    r"(pic|photo|images?|ছবি|dekhi|দেখি|dekha|দেখা|dekhan|দেখান|"
    r"dekhao|দেখাও|dekhaben|দেখাবেন|dekhte|দেখতে|দেখুন)", re.IGNORECASE
)

# LLMs sometimes render images as markdown in text ("![Name](url)") even though
# images are sent separately — strip the whole construct so customers never see
# a raw "![Product]()" line.
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def _clean_media_markdown(text: str) -> str:
    """Remove image URLs and image-markdown remnants from reply text."""
    if not text:
        return text
    return _MD_IMAGE_RE.sub("", _IMAGE_URL_RE.sub("", text)).strip()

# Product intents where the model may drive iterative searches. Everything else
# (orders, KB, tickets…) stays deterministic.
_AGENTIC_INTENTS = frozenset({
    "SEARCH_PRODUCT", "ASK_PRICE", "ASK_STOCK", "ASK_DETAILS",
    "COMPARE_PRODUCTS", "RECOMMEND", "SEND_IMAGES", "CATALOG",
})

# Safe tool subset for the agentic loop — read/search/send only. Order creation
# stays exclusively in the deterministic WorkflowEngine.
_AGENT_TOOL_NAMES = frozenset({
    "think", "search_products", "get_product_details", "send_images",
    "search_knowledge_base",
})

MAX_AGENT_ITERATIONS = 6


def _detect_lang(context) -> str:
    """Language = conversation detection hint (set by the pipeline when it
    detects the customer's language) or the agent's configured default."""
    conv = getattr(context, "conversation", None)
    detected = (getattr(conv, "language_detected", "") or "").lower()
    if detected in ("bn", "en"):
        return detected
    return (context.settings.agent_language or "bn") or "bn"


class ResponseGenerator:

    @staticmethod
    def generate(
        context: ConversationContext,
        tool_results: list[ToolResult],
        reply_id: str | None = None,
        dry_run: bool = False,
    ) -> Response:
        """Generate a natural-language reply from tool results.

        Single LLM call with no tool access — prevents hallucination.
        For cases with empty/known data, uses template responses to save cost.
        """
        if not tool_results:
            # UNKNOWN messages get an LLM interpretation pass with full context
            # (memory + history + store) instead of a canned "didn't understand".
            if getattr(getattr(context, "intent", None), "name", "") == "UNKNOWN":
                return ResponseGenerator._unknown_llm_reply(context, reply_id, dry_run)
            return Response(text=ResponseGenerator._greeting_or_fallback(context))

        # Check for ticket creation (human handoff)
        for r in tool_results:
            if r.tool == "create_ticket" and r.state == "success":
                return Response(
                    text="I'm connecting you with a human agent now. Please wait a moment.",
                    transferred=True,
                )

        # Permission denied — respond without retrying
        for r in tool_results:
            if r.state == "permission_denied":
                lang = _detect_lang(context)
                if lang == "bn":
                    text = "দুঃখিত, এই কাজটি করার অনুমতি আমার নেই। অন্য কিছুতে সাহায্য করতে পারি?"
                else:
                    text = "Sorry, I don't have permission to do that. Can I help with something else?"
                return Response(text=text)

        # Template-based responses for empty KB results (avoids hallucination)
        kb_result = None
        search_result = None
        for r in tool_results:
            if r.tool == "search_knowledge_base" and r.state == "success":
                kb_result = r
            if r.tool == "search_products" and r.state == "success":
                search_result = r

        if kb_result and kb_result.data:
            total = kb_result.data.get("total", 0) if isinstance(kb_result.data, dict) else 0
            if total == 0:
                intent_name = context.intent.name if context.intent else ""
                # Delivery info lives in StoreConfig (rendered in the prompt), so
                # let the LLM answer from there — only FAQ/payment get the canned
                # "no info" response when the knowledge base is empty.
                if intent_name in ("ASK_PAYMENT", "ASK_FAQ"):
                    lang = _detect_lang(context)
                    if lang == "bn":
                        return Response(text="আমার কাছে এই বিষয়ে কোনো তথ্য নেই। একজন এজেন্টের সাথে যোগাযোগ করিয়ে দিতে পারি?")
                    return Response(text="I don't have that information available. Would you like me to connect you with an agent?")

        # Product intents: let the model drive iterative searches (like the
        # legacy setup) — it can search with better keywords, fetch details,
        # and send images before replying.
        intent_name = context.intent.name if context.intent else ""
        if intent_name in _AGENTIC_INTENTS:
            return ResponseGenerator._agentic_loop(context, tool_results, reply_id, dry_run)

        # Build prompt from tool results
        prompt = ResponseGenerator._build_prompt(context, tool_results)

        try:
            from .providers import call_llm

            msg, usage = call_llm(
                messages=[{"role": "system", "content": prompt}],
                tools=None,
                model=context.model,
                temperature=0.3,
                max_tokens=512,
            )
            text = (msg.content or "").strip()
            # Log usage for billing (only when a reply_id is provided by the orchestrator)
            if reply_id:
                from back.models import UsageLog
                try:
                    UsageLog.objects.create(
                        user=context.user,
                        reply_id=reply_id,
                        model=usage.get("model", ""),
                        input_tokens=usage.get("input_tokens", 0),
                        output_tokens=usage.get("output_tokens", 0),
                        call_type="bot_preview" if dry_run else "response_generation",
                    )
                except Exception as exc:
                    logger.warning("UsageLog write failed reply_id=%s: %s", reply_id, exc)
        except Exception as exc:
            logger.error("ResponseGenerator LLM call failed: %s", exc)
            text = ResponseGenerator._fallback_text(tool_results)

        # Extract images and card data from tool results
        images, cards = ResponseGenerator._extract_media(tool_results)
        images, cards = ResponseGenerator._media_for_intent(context, images, cards)

        # Safety: strip any image URLs / markdown images that slipped through
        text = _clean_media_markdown(text)

        return Response(
            text=text,
            images=images,
            cards=cards,
        )

    @staticmethod
    def _agentic_loop(
        context: ConversationContext,
        seed_results: list[ToolResult],
        reply_id: str | None = None,
        dry_run: bool = False,
    ) -> Response:
        """Agentic response generation for product intents.

        The model gets the planner's VERIFIED seed results plus read-only tools
        (think / search_products / get_product_details / send_images) and may
        iterate: refine keywords, search synonyms, fetch details, send images —
        then produce the final reply. This restores the legacy behavior where
        the model searched the catalog itself ("Baby feeder price?" → multiple
        searches → images → rich answer).

        Guards:
        - seed search empty & the model never searched → force one more search
        - reply claims to send images but send_images never ran → force fix
        - hard cap MAX_AGENT_ITERATIONS LLM calls per turn
        """
        from .providers import call_llm
        from .tools import ToolRegistry

        system_prompt = ResponseGenerator._build_prompt(context, seed_results, agentic=True)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context.incoming_text or ""},
        ]
        tool_defs = ResponseGenerator._loop_tools(context)

        seed_has_products = any(
            r.tool == "search_products" and r.state == "success"
            and (r.data or {}).get("products")
            for r in seed_results
        )
        seed_sent_images = any(
            r.tool == "send_images" and r.state == "success"
            and (r.data or {}).get("products")
            for r in seed_results
        )

        all_results = list(seed_results)
        search_forced = False
        image_promise_corrected = False
        final_text = None

        for iteration in range(MAX_AGENT_ITERATIONS):
            try:
                msg, usage = call_llm(
                    messages=messages,
                    tools=tool_defs,
                    model=context.model,
                    temperature=0.5,
                    max_tokens=700,
                )
            except Exception as exc:
                logger.error("Agentic loop LLM call failed iter=%d: %s", iteration, exc)
                break

            ResponseGenerator._log_loop_usage(context, reply_id, usage, dry_run)

            if not getattr(msg, "tool_calls", None):
                candidate = (msg.content or "").strip()

                # Guard 1: the seed search found nothing and the model replied
                # without searching — force one more search attempt.
                loop_searched = any(
                    r.tool == "search_products"
                    for r in all_results[len(seed_results):]
                )
                if not seed_has_products and not loop_searched and not search_forced:
                    search_forced = True
                    messages.append({"role": "assistant", "content": candidate})
                    messages.append({"role": "system", "content": (
                        "The initial search found nothing. Before replying you MUST call "
                        "search_products again with a different keyword, spelling or synonym "
                        "(products may be listed under English names; try the brand name, "
                        "singular/plural, or a related category)."
                    )})
                    continue

                # Guard 2: reply claims to send images but send_images never ran.
                loop_sent_images = any(
                    r.tool == "send_images" and r.state == "success"
                    and (r.data or {}).get("products")
                    for r in all_results[len(seed_results):]
                )
                if (not image_promise_corrected and not seed_sent_images
                        and not loop_sent_images
                        and ResponseGenerator._photo_requested(context)
                        and ResponseGenerator._promises_images(candidate)):
                    image_promise_corrected = True
                    messages.append({"role": "assistant", "content": candidate})
                    messages.append({"role": "system", "content": (
                        "You told the customer you would send photos but send_images was "
                        "never called. Either call send_images now with the focused product "
                        "pid(s), or rewrite the reply WITHOUT mentioning photos."
                    )})
                    continue

                final_text = candidate
                break

            messages.append(msg)
            for tc in msg.tool_calls:
                fn = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                t0 = time.time()
                result = ToolRegistry.execute(fn, args, context.user, context.conversation)
                result.execution_time_ms = int((time.time() - t0) * 1000)
                all_results.append(result)

                try:
                    from back.models import ToolCallLog
                    from .pipeline import _summarize_tool_result
                    ToolCallLog.objects.create(
                        conversation=context.conversation,
                        user=context.user,
                        reply_id=reply_id,
                        iteration=len(seed_results) + iteration,
                        tool_name=fn,
                        arguments=args,
                        result_summary=_summarize_tool_result(fn, result.data),
                        execution_time_ms=result.execution_time_ms,
                    )
                except Exception as exc:
                    logger.warning("Agentic ToolCallLog write failed: %s", exc)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(
                        ResponseGenerator._tool_content_for_model(result),
                        ensure_ascii=False,
                    ),
                })

        if final_text is None:
            final_text = ResponseGenerator._fallback_text(all_results)

        images, cards = ResponseGenerator._extract_media(all_results)
        images, cards = ResponseGenerator._media_for_intent(context, images, cards)
        final_text = _clean_media_markdown(final_text or "")

        return Response(text=final_text, images=images, cards=cards)

    @staticmethod
    def _tool_content_for_model(result: ToolResult) -> dict:
        """Tool data as seen by the model — image URLs are stripped (media is
        sent out-of-band by send_reply), replaced with a count."""
        data = result.data
        if not isinstance(data, dict):
            return {"state": result.state, "error": result.error}
        data = dict(data)
        if isinstance(data.get("images"), list):
            data["image_count"] = len(data["images"])
            data.pop("images", None)
        return data

    @staticmethod
    def _log_loop_usage(context, reply_id, usage, dry_run) -> None:
        if not reply_id:
            return
        try:
            from back.models import UsageLog
            UsageLog.objects.create(
                user=context.user,
                reply_id=reply_id,
                model=usage.get("model", ""),
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                call_type="bot_preview" if dry_run else "response_generation",
            )
        except Exception as exc:
            logger.warning("UsageLog write failed reply_id=%s: %s", reply_id, exc)

    @staticmethod
    def _loop_tools(context) -> list:
        """Agent-loop tool definitions for this turn. send_images is offered
        ONLY when the customer asked for photos — otherwise the model would
        auto-send product images on every search/price reply ("sending images
        only when asked for")."""
        from .tools import ToolRegistry
        tool_defs = [
            d for d in ToolRegistry.get_definitions()
            if d["function"]["name"] in _AGENT_TOOL_NAMES
        ]
        if not ResponseGenerator._photo_requested(context):
            tool_defs = [
                d for d in tool_defs
                if d["function"]["name"] != "send_images"
            ]
        return tool_defs

    @staticmethod
    def _photo_requested(context) -> bool:
        """True when the customer explicitly asked for photos — the only time
        send_images may run. "Cockroach marar jnno?" must NOT auto-send
        images, "pic den dekhi bait powder er" must."""
        intent_name = context.intent.name if context.intent else ""
        if intent_name == "SEND_IMAGES":
            return True
        if not (context.incoming_text or ""):
            return False
        return bool(_PHOTO_ASK_RE.search(context.incoming_text))

    @staticmethod
    def _promises_images(text: str) -> bool:
        """True if the reply text implies images are being sent."""
        if not text:
            return False
        from .pipeline import _IMG_NOUN_RE, _SEND_CUE_RE
        return bool(_IMG_NOUN_RE.search(text) and _SEND_CUE_RE.search(text))

    @staticmethod
    def _unknown_llm_reply(
        context: ConversationContext,
        reply_id: str | None = None,
        dry_run: bool = False,
    ) -> Response:
        """UNKNOWN messages get an LLM interpretation pass with full context —
        long-term memory, conversation history, store identity/context — so the
        bot can understand typos, fragments, and unclear requests instead of
        repeating "didn't understand". No tools, so nothing can be invented or
        executed. Falls back to the canned text when the LLM is unavailable."""
        try:
            from .providers import call_llm

            prompt = ResponseGenerator._build_prompt(context, [], unclear=True)
            msg, usage = call_llm(
                messages=[{"role": "system", "content": prompt}],
                tools=None,
                model=context.model,
                temperature=0.3,
                max_tokens=256,
            )
            text = (msg.content or "").strip()
            if reply_id:
                from back.models import UsageLog
                try:
                    UsageLog.objects.create(
                        user=context.user,
                        reply_id=reply_id,
                        model=usage.get("model", ""),
                        input_tokens=usage.get("input_tokens", 0),
                        output_tokens=usage.get("output_tokens", 0),
                        call_type="bot_preview" if dry_run else "response_generation",
                    )
                except Exception as exc:
                    logger.warning("UsageLog write failed reply_id=%s: %s", reply_id, exc)
            if text:
                return Response(text=text)
        except Exception as exc:
            logger.error("UNKNOWN LLM reply failed: %s", exc)
        return Response(text=ResponseGenerator._greeting_or_fallback(context))

    @staticmethod
    def _build_prompt(
        context: ConversationContext,
        tool_results: list[ToolResult],
        unclear: bool = False,
        agentic: bool = False,
    ) -> str:
        """Build the response generation prompt.

        Rich context (identity, store, focus products, available products,
        custom instructions, greeting template) + memory + conversation
        history + tool results from THIS turn.
        """
        from .context import _build_system_prompt_from_ctx

        settings = context.settings
        tone = settings.agent_tone or "friendly"
        style = settings.agent_style or "concise"
        language = _detect_lang(context)

        # Format tool results for the prompt
        results_summary = []
        for r in tool_results:
            entry = {"tool": r.tool, "state": r.state, "data": r.data}
            if r.error:
                entry["error"] = r.error
            # Strip URLs from tool data for the prompt
            if isinstance(entry.get("data"), dict):
                if "images" in entry["data"]:
                    entry["data"]["image_count"] = len(entry["data"]["images"])
                    entry["data"].pop("images", None)
            results_summary.append(entry)

        # 1. Rich static context (identity, store, focus products, catalog, rules)
        parts = [_build_system_prompt_from_ctx(context)]

        # 2. Long-term memory (persisted facts/preferences)
        if context.memory.text:
            parts.append(f"## Long-term Memory about this customer\n{context.memory.text}")

        # 3. Conversation history (what was said before this turn)
        if context.history:
            lines = ["## Conversation so far (most recent last)"]
            for h in context.history[-12:]:
                role_label = "Customer" if h.get("role") == "user" else "Bot"
                content = (h.get("content") or "").strip()
                if content:
                    lines.append(f"{role_label}: {content}")
            parts.append("\n".join(lines))

        # 4. This turn — for UNKNOWN messages no tools ran; instead the LLM is
        # asked to INTERPRET the message using memory + history + store context.
        if unclear:
            parts.extend([
                "## This turn",
                f"The customer just asked: {context.incoming_text or ''}",
                "",
                "This message is unclear and no tools were run. Interpret it using the",
                "conversation history, long-term memory, and store context above —",
                "customers often type typos, fragments, or transliterated Bengali",
                '("dilivery kobe?", "char somoy", "ager moto").',
                "- If you can figure out what they mean (product, price, order,",
                "  delivery, complaint, repeat of an earlier question), respond",
                "  helpfully — answer from history/memory/store data only.",
                "- If it's genuinely unclear, ask ONE short clarifying question",
                "  with concrete examples (products, prices, delivery).",
                "- NEVER invent products, prices, stock, discounts, or order",
                "  details that are not in this prompt.",
            ])
        else:
            parts.extend([
                "## This turn",
                f"The customer just asked: {context.incoming_text or ''}",
                "",
                "Here are the VERIFIED results from the tools you requested. Only use this data.",
                "Do NOT invent any product names, prices, stock, or availability.",
                "",
                json.dumps(results_summary, indent=2, ensure_ascii=False),
            ])
        if context.customer.name:
            parts.append(f"\nCustomer: {context.customer.name}")

        # Agentic loop guidance — the model drives further searches itself.
        if agentic:
            parts.append("""
## Tools available to you now
You can call: think (private reasoning), search_products, get_product_details,
send_images, search_knowledge_base. Use them BEFORE replying when the customer
asks about products:
- The 'Here are the VERIFIED results' block above comes from the initial
  search. If it already contains the right products, reply using it — and if
  the customer wants photos (or it helps the answer), call send_images with the
  product pid(s).
- If that block is empty or the matches look weak/unrelated, call
  search_products AGAIN with different keywords: synonyms, English names,
  simpler spellings, brand names, singular/plural, or a related category
  ("feeder" → "feeding bottle" / "cleanser"). Do at most 3 searches in total;
  combine related terms into one query.
- If the customer names several products or categories, search each one.
- Once you have the best matches: if the customer asked for photos or to browse,
  call send_images for the top 1-3 products; otherwise just describe the best
  match(es) with prices from the results.
- Prefer the product that best matches what the customer described — never dump
  every search hit into the reply.
- think() is private — never put customer-facing text in it.
- When you have everything you need, reply with ONLY the final customer-facing
  message text (no tool calls, no JSON).
""")

        # P1-14..21: specialist role fragment based on intent (no extra LLM call)
        specialist = ResponseGenerator._specialist_fragment(context)
        if specialist:
            parts.append(specialist)

        parts.append(f"""
Write a natural, {style} reply in the customer's language (default: {language}).
- Use the conversation history above to stay consistent (e.g. which product the
  customer has been discussing, past prices quoted, previous questions asked).
- If 'Conversation so far' is non-empty, do NOT greet again or reintroduce
  yourself — continue the conversation naturally (no "কেমন আছেন" repeat).
- Max 3 sentences unless the customer asked for detailed information.
- Mention prices and stock only if they were in the tool results.
- EXACT rules (FOLLOW STRICTLY):
  * Do NOT invent ANY number that isn't in the tool results above
  * Total product count = the "total" field in search_products results
  * Product price = the "price" or "discounted_price" field in the results
  * Product stock = the "stock" field
  * If "total" is 1, there is exactly 1 product — do NOT say "3 products" etc.
- Discount questions: a product HAS a discount when its "discounted_price" is
  present and lower than its "price". Say so clearly even if the customer's
  question was phrased as a negative ("discount nai?" = "is there no discount?").
- If the customer asked to see the catalog ("what do you have", "ki ache")
  and total > 1: list ALL products with prices, never just one.
- If no products were found, say it's unavailable — do NOT suggest alternatives
  that weren't in the search results.
- If the exact item the customer asked for was NOT found, but the search results
  contain RELATED products (e.g. asked for "Vicks candy" → found "Vicks BabyRub"),
  say the exact item is unavailable AND offer the related product as an
  alternative with its price. Never claim the related product is what they asked for.
- NEVER say an order was created, confirmed, or "will be delivered" unless a
  create_order tool result in THIS turn shows an order_id. If the customer
  confirms an order and no create_order result exists, say you're processing it.
- Quote an order id ONLY if it appears in THIS turn's tool results — never from
  memory or old conversation history.
- Never say "your order" or imply an existing order unless a create_order or
  get_order_status tool result is in THIS turn.
- Bargaining ("150 e den", "দাম কম"): politely hold the current price — the
  discounted_price in the results IS the best price. NEVER quote or invent a
  lower price; if the customer insists, offer to place the order at the current
  price.
- Do NOT end every reply with a question or an order pitch ("আপনি কি অর্ডার
  করতে চান?"). Answer what was asked and stop. Ask a question only when it
  truly helps the conversation: which product the customer wants, or when they
  are actively choosing between options.
- Do NOT append a generic "আর কিছু জানতে চাইলে বলুন / anything else?" sign-off
  to every reply — use it sparingly (e.g. after a full catalog listing), never
  as the default ending.
- Be warm and helpful.
- If the customer asked about delivery, answer from the 'Store' section in the
  system prompt (delivery charges, support hours) — no knowledge base needed.
- For payment/policy questions, answer only from knowledge base results; if
  none were found, say you don't have that information.
- If multiple products, briefly describe the best match or ask what they're looking for.
- Never include URLs, JSON, or tool names in your reply.
- Respond in ONE language consistently (default: {language}). Do NOT mix languages.
""")

        # Final instruction — highest weight at the end of the prompt
        parts.append(f"FINAL INSTRUCTION: The customer is writing in {language}. "
                     f"Your ENTIRE reply must be in {language} — even if the store "
                     "data or instructions above are in another language.\n"
                     "After answering, STOP. Do not add a follow-up question, a "
                     "sales pitch, or an 'anything else?' sign-off. Exception: you "
                     "may ask which product they prefer ONLY when the customer "
                     "asked to browse the catalog or listed products.")

        return "\n".join(parts)

    _SPECIALIST_FRAGMENTS: dict[str, str] = {
        "SALES": (
            "## ROLE: Sales Specialist\n"
            "- You are the store's product expert: guide discovery, compare options, "
            "suggest complementary items when the store allows cross-selling.\n"
            "- When the customer is deciding, gently ask which product they like best "
            "or offer the best match. Never pressure.\n"
            "- If stock is low on the requested item, mention it warmly and offer a "
            "genuine alternative only if it appears in the tool results."
        ),
        "SUPPORT": (
            "## ROLE: Customer Support Specialist\n"
            "- You handle policies, delivery, payment, returns, and complaints.\n"
            "- Answer ONLY from knowledge base results. If nothing was found, say you "
            "don't have that information and offer to connect the customer to a human.\n"
            "- Stay calm and empathetic with complaints; do not argue with the customer.\n"
            "- Escalate (create_ticket) only for genuine issues beyond your scope."
        ),
        "BILLING": (
            "## ROLE: Billing Specialist\n"
            "- You explain plans, pricing, and billing questions.\n"
            "- Only quote plan/price details present in the tool results.\n"
            "- For upgrade/downgrade requests, summarize the difference and ask for "
            "confirmation before any action."
        ),
    }

    _INTENT_TO_SPECIALIST: dict[str, str] = {
        "SEARCH_PRODUCT": "SALES",
        "ASK_PRICE": "SALES",
        "ASK_STOCK": "SALES",
        "ASK_DETAILS": "SALES",
        "COMPARE_PRODUCTS": "SALES",
        "RECOMMEND": "SALES",
        "CREATE_ORDER": "SALES",
        "CANCEL_ORDER": "SUPPORT",
        "RETURN_PRODUCT": "SUPPORT",
        "ASK_DELIVERY": "SUPPORT",
        "ASK_PAYMENT": "SUPPORT",
        "ASK_FAQ": "SUPPORT",
        "HUMAN_SUPPORT": "SUPPORT",
        "ESCALATION": "SUPPORT",
        "BILLING_QUERY": "BILLING",
        "UPGRADE_PLAN": "BILLING",
    }

    @staticmethod
    def _specialist_fragment(context: ConversationContext) -> str:
        """Return the specialist role fragment for the current intent."""
        intent_name = context.intent.name if context.intent else ""
        specialist = ResponseGenerator._INTENT_TO_SPECIALIST.get(intent_name)
        if not specialist:
            return ""
        return ResponseGenerator._SPECIALIST_FRAGMENTS.get(specialist, "")

    @staticmethod
    def _extract_media(tool_results: list[ToolResult]) -> tuple[list[str], list[dict]]:
        """Extract image URLs and product cards from tool results.

        Cards come from search/catalog results (one carousel element per
        product); standalone images come from send_images only. The caller
        gates cards to catalog intents and images to photo requests, so a
        single-product or price reply never re-sends the whole carousel."""
        images: list[str] = []
        cards: list[dict] = []

        for r in tool_results:
            if r.tool == "search_products" and r.state == "success" and r.data:
                prods = r.data.get("products") or []
                images_by_pid = {}
                pids = [p.get("pid") for p in prods if p.get("pid")]
                if pids:
                    try:
                        from back.models import Product, ProductImages
                        from .tools import _image_url
                        for p in Product.objects.filter(pid__in=pids):
                            imgs = []
                            main = _image_url(p.image)
                            if main:
                                imgs.append(main)
                            for row in ProductImages.objects.filter(product=p).values_list("images", flat=True):
                                url = _image_url(row)
                                if url and url not in imgs:
                                    imgs.append(url)
                            images_by_pid[p.pid] = imgs
                    except Exception:
                        images_by_pid = {}
                for p in prods:
                    imgs = images_by_pid.get(p.get("pid")) or []
                    if imgs:
                        cards.append({**p, "images": imgs})
            elif r.tool == "send_images" and r.state == "success" and r.data:
                for p in (r.data.get("products") or []):
                    images.extend((p.get("images") or [])[:5])

        # Deduplicate
        seen = set()
        unique = []
        for img in images:
            if img not in seen:
                seen.add(img)
                unique.append(img)

        return unique[:5], cards[:10]

    @staticmethod
    def _media_for_intent(context, images, cards):
        """Intent-level media rules:
        - cards (carousel) only for catalog browsing ("ki product ache?") —
          never for single-product details, price, qty or order replies
          ("sends the whole carousel again and again" was exactly this)
        - images only for photo requests (send_images is additionally gated by
          tool access, so images cannot appear here unless the customer asked)"""
        intent_name = context.intent.name if context.intent else ""
        if intent_name != "CATALOG":
            cards = []
        return images, cards

    @staticmethod
    def _greeting_or_fallback(context: ConversationContext) -> str:
        """Return a greeting or empty response when no tools were needed."""
        intent_name = context.intent.name if context.intent else ""
        lang = _detect_lang(context)

        if intent_name in ("GREETING",):
            if lang == "bn":
                msgs = [
                    "হ্যালো! কিভাবে সাহায্য করতে পারি?",
                    "হ্যালো ভাইয়া, কেমন আছেন? কিভাবে সাহায্য করতে পারি?",
                    "বলুন ভাই, কিভাবে সাহায্য করতে পারি?",
                ]
            elif lang == "en":
                msgs = [
                    "Hello! How can I help you today?",
                    "Hi there! What can I do for you?",
                ]
            else:
                msgs = ["Hello! How can I help you today?"]
            import random
            return random.choice(msgs)
        if intent_name == "SMALL_TALK":
            if lang == "bn":
                return "ঠিক আছে ভাই! কিভাবে সাহায্য করতে পারি?"
            return "I'm here to help! What can I assist you with?"
        if intent_name == "FRUSTRATION":
            # Customer is upset/using abusive language — de-escalate warmly,
            # never argue, never take offense. Offer a concrete next step.
            if lang == "bn":
                return (
                    "দুঃখিত ভাই, অসুবিধার জন্য আন্তরিক দুঃখিত। আমি আপনাকে সাহায্য করার "
                    "জন্য এখানে আছি — প্রোডাক্টের দাম, অর্ডার বা ডেলিভারি — যেটা জানতে "
                    "চান বলুন, সাথে সাথে দেখছি।"
                )
            return (
                "Sorry about that — I'm here to help. Price, order, or delivery — "
                "just tell me what you need and I'll sort it out."
            )
        if lang == "bn":
            return (
                "দুঃখিত, আমি বুঝতে পারিনি। যেমন বলতে পারেন: 'আমের আচার কত?' "
                "বা 'অর্ডার করব' বা 'ডেলিভারি কতদিন লাগে?'"
            )
        return "Sorry, I didn't catch that. You could ask e.g. 'how much is Amer Achar?' or 'I want to order'."

    @staticmethod
    def _fallback_text(tool_results: list[ToolResult]) -> str:
        """Generate a simple text summary when LLM is unavailable."""
        parts = []
        for r in tool_results:
            if r.state == "success" and r.data:
                products = r.data.get("products", [])
                if products:
                    for p in products[:3]:
                        price = p.get("discounted_price") or p.get("price", "")
                        name = p.get("name", "Product")
                        parts.append(f"{name} — {price}" if price else name)

                order = r.data if r.tool == "create_order" else None
                if order and order.get("order_id"):
                    parts.append(f"Order {order['order_id']} created! Total: {order.get('total', '')}")

                kb = r.data.get("results", [])
                if kb:
                    for item in kb[:2]:
                        content = item if isinstance(item, str) else item.get("content", "")
                        if content:
                            parts.append(content)

        return "\n".join(parts) if parts else "How can I help you?"
