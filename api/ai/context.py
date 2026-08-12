from back.models import Message, Product

# Keep headroom so the dynamic tail (focused products + live customer state,
# appended last) is never the part that gets truncated.
MAX_PROMPT_LENGTH = 10000


def build_system_prompt(user, conversation, image_analysis=None):
    """Assemble the full system prompt from AgentIdentity, StoreConfig, BehaviorRules, and live conversation state.

    ``image_analysis`` — optional dict from api.ai.media.analyze_image_structured
    (sku, product_name, brand, description) plus ``analysis_search`` for
    pre-search results.  When the current turn was triggered by an image, this
    tells the AI that the catalog was already searched so it doesn't re-search.
    """
    from context.models import AgentIdentity, StoreConfig, BehaviorRules
    from api.products.factory import get_active_source, is_external

    identity = AgentIdentity.objects.filter(user=user).first()
    store = StoreConfig.objects.filter(user=user).first()
    rules = BehaviorRules.objects.filter(user=user).first()

    source = get_active_source(user)
    external_catalog = bool(source) and is_external(user)

    parts = []

    # --- Agent identity ---
    if identity:
        parts.append(
            f"## Your Identity\n"
            f"Name: {identity.name}\n"
            f"Role: {identity.role or 'Sales & Support Agent'}\n"
            f"Tone: {identity.tone}  |  Style: {identity.style}  |  Language: {identity.language}\n"
            "Default reply language is Bengali; only switch if the customer clearly uses another language or asks for English."
        )

    if rules and rules.custom_instructions:
        parts.append(f"## Custom Instructions (user-defined)\n{rules.custom_instructions}")

    parts.append(
        "## LANGUAGE\n"
        "- Default reply language: Bengali (বাংলা).\n"
        "- Only reply in English when the customer clearly writes full English sentences or explicitly asks for English.\n"
        "- If the customer mixes English words in Bangla, still reply in Bengali.\n"
        "- If the customer uses Bangla transliteration, reply in Bengali.\n"
        "- Use polite Bangla honorifics (আপনি/ভাই/আপা) and a warm, helpful tone.\n"
        "- Format prices as ৳123 and say 'টাকা' naturally in Bangla.\n"
        "- Keep replies short and natural for chat.\n"
    )

    # --- Core task rules (these override custom instructions above) ---
    tone = identity.tone if identity else "friendly"
    style = identity.style if identity else "concise"
    parts.append(
        "## BEHAVIOR (follow exactly)\n"
        "- Warm, human, concise (1-3 sentences).\n"
        "- No numbered lists. No URLs. No JSON.\n"
        "- Specific product request → show only the best or exact matches.\n"
        "- If multiple options, use send_images(pids=[...]) to send carousel as names and price are already in the carousel.\n"
        "- If you want multiple short messages, separate with a blank line.\n"
        "- Delivery/payment questions: answer directly; don't collect details unless ordering.\n"
        "- Ask at most ONE follow-up question per reply.\n"
    )

    parts.append(
        "## SALES FLOW\n"
        "- If not greeted yet, start with a short greeting from the greeting template (if available).\n"
        "- For product interest: confirm the item + price, then ask one short follow-up (color/size/budget).\n"
        "- For photos: call send_images, then ask if they want to order or see another item.\n"
        "- Before create_order collect: name, phone, delivery address, and city/area.\n"
        "- If the customer is ready to order, summarize items and ask for missing info only.\n"
    )

    # --- Product discovery flow ---
    parts.append(
        "## WORKFLOW (product requests)\n"
        "1) think(): plan 2-3 queries.\n"
        "2) search_products with different keywords (Bengali → English).\n"
        "3) Verify names match; if not, search again.\n"
        "4) Respond with genuine matches or say out of stock.\n"
    )
    parts.append("- For images: search SKU first, then name.\n")
    parts.append(
        "- NEVER state a price or name you didn't just get from a tool.\n"
        "- If search results don't include any product relevant to the "
        "customer's query → say it's out of stock. Do NOT invent product "
        "names, prices, or descriptions.\n"
        "- If product has variations (size/color), show options before ordering.\n"
        "- If product not found → say out of stock (not 'not found').\n"
        "- Use get_order_status for existing orders.\n"
        "- If customer gives a budget (e.g. 500 taka), pass min_price/max_price to search_products.\n"
        "- Complaints/policy questions: check search_knowledge_base FIRST (return, "
        "refund, delivery, payment, warranty); answer from it. Use create_ticket only "
        "to escalate genuine complaints, angry customers, or out-of-scope requests.\n"
        "- Use search_knowledge_base for FAQs, policies, delivery info — not for products.\n"
        "- Never paste image URLs in text — use send_images only.\n"
        "- Before create_order collect: customer name, phone, delivery address.\n"
        f"- Keep replies {tone} and {style}."
    )

    # --- Image pre-search awareness ---
    # When the customer sent an image, the system already analyzed it and
    # searched the catalog. The results are in "Recent Searched Products"
    # below. Tell the AI so it doesn't re-search.
    if image_analysis:
        parts.append(
            "## Image Analysis (already processed)\n"
            "The customer sent an image. The system analyzed it and pre-searched "
            "the catalog for matching products (results are below in "
            "'Recent Searched Products'). You do NOT need to search again "
            "unless the customer asks for something different."
        )
        if image_analysis.get("sku"):
            parts[-1] += f"\nDetected SKU: {image_analysis['sku']}"
        if image_analysis.get("product_name"):
            parts[-1] += f"\nDetected name: {image_analysis['product_name']}"
        if image_analysis.get("brand"):
            parts[-1] += f"\nDetected brand: {image_analysis['brand']}"

    # --- Response flow ---
    parts.append(
        "## RESPONSE FLOW\n"
        "- For images: use analyzed SKU/name first, then search.\n"
        "- If the customer asks for photos (ছবি/pic), call send_images using focused products or search results.\n"
        "- If the customer provides a PID/SKU, search by that exact code or call get_product_details.\n"
        "- Never claim to send images without send_images.\n"
        "- send_images(pid=...) for one, send_images(pids=[...]) for many.\n"
        "- Keep reply short: name + price + one follow-up.\n"
        "- Multi-item order: confirm items, then create_order.\n"
    )

    # --- Store info ---
    if store:
        parts.append(
            f"## Store\n"
            f"Name: {store.store_name or 'Our Store'}\n"
            f"Address: {store.address or 'Not set'}\n"
            f"WhatsApp: {store.whatsapp_number or 'Not set'}\n"
            f"Support hours: {store.support_open_time} – {store.support_close_time} ({store.timezone})\n"
            f"Currency: {store.currency}\n"
            f"Delivery inside: {store.delivery_charge_inside} {store.currency}  |  "
            f"Outside: {store.delivery_charge_outside} {store.currency}"
        )

    # --- Behavior rules ---
    if rules:
        chit = f"{'on' if rules.chit_chat_enabled else 'off'} ({rules.chit_chat_style})"
        parts.append(
            f"## Behavior\n"
            f"Chit-chat: {chit}  |  Cross-sell: {'yes' if rules.cross_sell_enabled else 'no'}  |  "
            f"Ask open-ended questions: {'yes' if rules.ask_open_ended else 'no'}"
        )
        if rules.greeting_message:
            parts.append(f"Greeting template: {rules.greeting_message}")
        
        

    # --- Live customer state ---
    cust = []
    if conversation.customer_name:
        cust.append(f"Name: {conversation.customer_name}")
    if conversation.customer_phone:
        cust.append(f"Phone: {conversation.customer_phone}")
    if conversation.customer_city:
        cust.append(f"City: {conversation.customer_city}")
    if conversation.customer_address:
        cust.append(f"Address: {conversation.customer_address}")
    if conversation.greeted:
        cust.append("Already greeted: yes")
    if conversation.detected_intent:
        cust.append(f"Intent: {conversation.detected_intent}")

    currency = store.currency if store else "BDT"

    # Focused products (kept in context across turns). search_products /
    # get_product_details persist a rolling list (most-recent-first) of the last
    # few products this conversation touched, so the AI can act on any of them —
    # e.g. call send_images with a pid when the customer asks for photos, without
    # searching again.
    from .tools import parse_focus_products
    focus_list = parse_focus_products(conversation.current_product)
    if focus_list:
        cust.append(_render_focus_products(focus_list, currency))

    if cust:
        parts.append("## Current Customer\n" + "\n".join(cust))

    # --- Catalogue snapshot ---
    # For an external/live source, do NOT inline a list — the live catalog is
    # large and dynamic; the AI must use search_products (enforced in the rules).
    if not external_catalog and not focus_list:
        available_products = list(Product.objects.filter(user=user, status=True)[:10])
        if available_products:
            lines = ["## Available Products (sample — use search_products for the full catalog)"]
            for p in available_products:
                desc = (p.description or "")[:80]
                lines.append(
                    f"- {p.name} (PID: {p.pid}) — {p.price} {currency}"
                    + (f" — {desc}" if desc else "")
                )
            parts.append("\n".join(lines))
        else:
            parts.append("## Available Products\nNo products listed — use search_products.")

    # NOTE: conversation history is NOT embedded here — pipeline.py passes it as
    # real chat messages, so embedding it again would duplicate every turn.

    # Join all parts and truncate if too long
    system_prompt = "\n\n".join(parts)
    if len(system_prompt) > MAX_PROMPT_LENGTH:
        # Truncate and add indicator
        system_prompt = system_prompt[:MAX_PROMPT_LENGTH] + "\n\n[SYSTEM PROMPT TRUNCATED]"
    return system_prompt


def _render_focus_products(focus_list, currency):
    """Render the rolling focused-products list for the system prompt.

    The most recent product (index 0) is shown in full (description +
    variations); the rest are listed compactly. The AI can call send_images /
    get_product_details with any of these PIDs.
    """
    # Dedup by pid — keep first occurrence (newest)
    seen = set()
    deduped = []
    for f in focus_list:
        pid = f.get("pid")
        if pid and pid not in seen:
            seen.add(pid)
            deduped.append(f)
    focus_list = deduped
    if not focus_list:
        return ""

    lines = ["## Recent Searched Products (recent — what this conversation is about, newest first)"]

    primary = focus_list[0]
    p_pid = primary.get("pid", "")
    p_name = primary.get("name") or ""
    header = f"- {p_name} (PID: {p_pid})" if p_name else f"- PID: {p_pid}"
    if primary.get("sku"):
        header += f"  SKU: {primary['sku']}"
    lines.append(header)
    if primary.get("price") is not None:
        price_line = f"   Price: {primary['price']} {currency}"
        if primary.get("discounted_price"):
            price_line += f"  Discounted: {primary['discounted_price']} {currency}"
        lines.append(price_line)
    if primary.get("stock") is not None:
        lines.append(f"   Stock: {primary['stock']} ({'in stock' if primary.get('in_stock', True) else 'out of stock'})")
    if primary.get("description"):
        lines.append(f"   Description: {primary['description']}")
    for v in primary.get("variations") or []:
        stock_note = "" if v.get("in_stock", True) else " — out of stock"
        lines.append(
            f"   • {v.get('name')} — {v.get('price')} {currency} "
            f"(variation_id={v.get('variation_id')}){stock_note}"
        )

    for f in focus_list[1:]:
        name = f.get("name") or ""
        label = f"{name} (PID: {f.get('pid')})" if name else f"PID: {f.get('pid')}"
        price = f"{f['price']} {currency}" if f.get("price") is not None else ""
        stock_note = "" if f.get("in_stock", True) else " — out of stock"
        lines.append(f"- {label}" + (f" — {price}" if price else "") + stock_note)

    lines.append(
        "All data above is COMPLETE — you already have name, price, stock, "
        "description, and variations. Call send_images(pid=...) to send photos "
        "and describe the product from this data. Do NOT call get_product_details "
        "for any product listed above — the data is already here."
    )
    return "\n".join(lines)


def get_conversation_history(conversation, limit=20):
    """Return the last `limit` messages as an OpenAI-format list."""
    msgs = list(
        Message.objects
        .filter(conversation=conversation)
        .order_by("-timestamp")[:limit]
    )
    msgs.reverse()

    history = []
    for m in msgs:
        if m.sender == "agent":
            continue
        role = "assistant" if m.sender == "bot" else "user"
        content = m.text or ""
        if not content and m.attachments:
            att_type = m.attachments.get("type", "")
            url = m.attachments.get("url") or m.attachments.get("payload", {}).get("url", "")
            content = f"[{att_type}: {url}]" if url else f"[{att_type}]"
        history.append({"role": role, "content": content})

    return history
