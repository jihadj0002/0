from back.models import Message, Product

MAX_PROMPT_LENGTH = 8000


def build_system_prompt(user, conversation):
    """Assemble the full system prompt from AgentIdentity, StoreConfig, BehaviorRules, and live conversation state."""
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
            f"Always respond in the customer's detected language, defaulting to {identity.language}."
        )

    # --- Core task rules (placed EARLY so they survive truncation) ---
    tone = identity.tone if identity else "friendly"
    style = identity.style if identity else "concise"
    parts.append(
        "## Rules — Product & Tool Usage (follow exactly)\n"
        "- You do NOT have the catalog memorised. For ANY product question — by name, "
        "description, category, SKU o,r an image the customer sends — you MUST call "
        "`search_products` FIRST with the keyword(s) or identifier."
        "When using send_images(pids=[...]) with multiple PIDs they appear as a scrollable carousel "
        "(1 image per card, buttons include product name). "
        "For a single product, send_images(pid=...) sends all its images individually one by one — "
        "you may describe them in text. "
        "CRITICAL: When you find multiple products via search_products, NEVER list their names, "
        "prices, or descriptions in your text reply. Instead, immediately call send_images(pids=[...]) "
        "to show them as a carousel. Your text should only ask a follow-up question or guide the customer.\n"
        "When customer selects a product (e.g. SELECT_PRODUCT|...), the product's full data "
        "(name, price, stock, description, variations) is already in Focused Products in this prompt. "
        "Call send_images to show photos and describe the product from the Focused Products data. "
        "Do NOT call get_product_details — you already have everything you need.\n"
        "## Search Rules\n"
    )
    if external_catalog:
        parts.append(
            "## Search Rules\n"
            "`search_products` FIRST with the SKU(s). Example: customer Sends an image sku contain 39328BB \"cradle ache?\" "
            "→ call search_products(query=\"39328BB\").\n"
            "`pid` returned by search), then offer images with `send_images`. "
            "Multiple PIDs via pids=[...] → scrollable carousel (1 image per card, "
            "buttons include product name for selection). "
            "Single PID via pid=... → all product images sent individually one by one.\n"
            "- If no identifier is provided, search using short tailed keywords.\n"
            "- NEVER state a price, stock, or product name you have not just obtained from "
            "search_products / get_product_details. Never guess or invent products.\n"
            "- If a product has variations (size/color), show the options and pass the chosen "
            "`variation_id` into create_order.\n"
            "- Before create_order collect: customer name, phone, delivery address; confirm the "
            "items aloud first.\n"
            "use tool tool_get_order_status to check order status\n"
            "- Use create_ticket(subject, description, priority) when the customer is angry, or wants something else than products,"
            "Write a complaint, issue or wants something, or the issue is beyond your scope. The agent will take over.\n"
            "- Never paste image URLs in text — send images only via send_images (max 5).\n"
            "- No need to declare image name that you've sent. Send image internally\n"
            "Use tool search_knowledge_base to access the knowledge base.\n"
            "such as: FAQs, Policies, payment methods or any general information.\n"
            "But product info must come within context or via search product.\n"
            "If their product is not found say that product is not available instead of saying not found.\n"
            f"- Keep replies {tone} and {style} unless the customer asks for detail."
        )
    else:
        parts.append(
            "`search_products` FIRST with the keyword(s). Example: customer says \"cradle ache?\" "
        "→ call search_products(query=\"cradle\").\n"
            "- After searching, call `get_product_details` for the chosen product (use the exact "
            "`pid` returned by search), then offer images with `send_images`. "
            "Multiple PIDs via pids=[...] → scrollable carousel (1 image per card). "
            "Single PID via pid=... → all product images sent individually one by one.\n"
            "- NEVER state a price, stock, or product name you have not just obtained from "
            "search_products / get_product_details. Never guess or invent products.\n"
            "- If a product has variations (size/color), show the options and pass the chosen "
            "`variation_id` into create_order.\n"
            "- Before create_order collect: customer name, phone, delivery address; confirm the "
            "items aloud first.\n"
            "use tool tool_get_order_status to check order status\n"
            "- Use create_ticket(subject, description, priority) when the customer is angry, "
            "asks for a human, or the issue is beyond your scope. The agent will take over.\n"
            "- Never paste image URLs in text — send images only via send_images (max 5).\n"
            "- No need to declare image name that you've sent. Send image internally\n"
            "Use tool search_knowledge_base to access the knowledge base.\n"
            "such as: FAQs, Policies, payment methods or any general information.\n"
            "But product info must come within context or via search product.\n"
            "If their product is not found say that product is not available instead of saying not found.\n"
            f"- Keep replies {tone} and {style} unless the customer asks for detail."
        )

    # --- Image search & iterative search guidance ---
    parts.append(
        "## Image Search & Multiple Searches\n"
        "When a customer sends an image, the message shows structured data: "
        "SKU, product name, type, brand, color, capacity.\n"
        "Use search_products to find the matching product — you can call it "
        "MULTIPLE times with different queries:\n"
        "  1. First try the SKU (if available) as the exact search query.\n"
        "  2. If no good match, try the product_name.\n"
        "  3. Then try type + brand, or type + color, or brand alone.\n"
        "  4. Then try individual keywords (just the brand, just the type).\n"
        "Compare each search result against the image data (SKU, name, brand, "
        "type, color). When you find a confident match, use send_images.\n"
        "If nothing matches after several attempts, tell the customer and ask "
        "for more details (SKU number, brand name, etc.).\n"
        "CRITICAL: Only send product images when the match is reliable — "
        "do NOT send a product whose name/type/brand clearly doesn't match "
        "what the customer showed."
    )

    if rules.custom_instructions:
            parts.append(f"Custom instructions: {rules.custom_instructions}")

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
    if external_catalog:
        parts.append(
            "## END Of Focused Products\n"
            
        )
    else:
        available_products = list(Product.objects.filter(user=user, status=True)[:20])
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
    lines = ["## Focused Products (recent — what this conversation is about, newest first)"]

    primary = focus_list[0]
    p_pid = primary.get("pid", "")
    p_name = primary.get("name") or ""
    header = f"1. {p_name} (PID: {p_pid})" if p_name else f"1. PID: {p_pid}"
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

    for i, f in enumerate(focus_list[1:], start=2):
        name = f.get("name") or ""
        label = f"{name} (PID: {f.get('pid')})" if name else f"PID: {f.get('pid')}"
        price = f"{f['price']} {currency}" if f.get("price") is not None else ""
        stock_note = "" if f.get("in_stock", True) else " — out of stock"
        lines.append(f"{i}. {label}" + (f" — {price}" if price else "") + stock_note)

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
        role = "assistant" if m.sender == "bot" else "user"
        content = m.text or ""
        if not content and m.attachments:
            att_type = m.attachments.get("type", "")
            url = m.attachments.get("url") or m.attachments.get("payload", {}).get("url", "")
            content = f"[{att_type}: {url}]" if url else f"[{att_type}]"
        history.append({"role": role, "content": content})

    return history