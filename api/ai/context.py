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
        "description, category, SKU, or an image the customer sends — you MUST call "
        "`search_products` FIRST with the keyword(s). Example: customer says \"cradle ache?\" "
        "→ call search_products(query=\"cradle\").\n"
        "- After searching, call `get_product_details` for the chosen product (use the exact "
        "`pid` returned by search), then offer images with `send_images`.\n"
        "- NEVER state a price, stock, or product name you have not just obtained from "
        "search_products / get_product_details. Never guess or invent products.\n"
        "- If a product has variations (size/color), show the options and pass the chosen "
        "`variation_id` into create_order.\n"
        "- Before create_order collect: customer name, phone, delivery address; confirm the "
        "items aloud first.\n"
        "- Use transfer_chat when the customer is angry, asks for a human, or the issue is "
        "beyond your scope.\n"
        "- Never paste image URLs in text — send images only via send_images (max 5).\n"
        f"- Keep replies {tone} and {style} unless the customer asks for detail."
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
        if rules.out_of_hours_message:
            parts.append(f"Out-of-hours reply: {rules.out_of_hours_message}")
        if rules.knowledge_base and rules.knowledge_base.strip():
            parts.append(f"## Knowledge Base\n{rules.knowledge_base.strip()}")
        if rules.sample_questions_answers and rules.sample_questions_answers.strip():
            parts.append(f"## Sample Training Q&A\n{rules.sample_questions_answers.strip()}")

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

    # Current selected product (kept in context across turns). Resolve from the
    # active source so it works for both internal and external/live catalogs.
    pid = (conversation.current_product or "").strip()
    if pid:
        snap = _selected_product_snapshot(user, pid, currency, external_catalog)
        if snap:
            cust.append(snap)

    if cust:
        parts.append("## Current Customer\n" + "\n".join(cust))

    # --- Catalogue snapshot ---
    # For an external/live source, do NOT inline a list — the live catalog is
    # large and dynamic; the AI must use search_products (enforced in the rules).
    if external_catalog:
        parts.append(
            "## Catalog\n"
            "Products live in a connected external store and are NOT listed here. "
            "Always use search_products to find anything the customer asks about."
        )
    else:
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


def _selected_product_snapshot(user, pid, currency, external_catalog):
    """Return a short text block describing the currently-selected product, or None.

    Resolves via the live provider for external catalogs, else the local DB.
    """
    if external_catalog:
        try:
            from api.products.factory import get_provider
            r = get_provider(user).get_product(pid)
            if not r:
                return None
            disc = f"  Discounted: {r['discounted_price']} {currency}" if r.get("discounted_price") else ""
            return (
                f"Selected product: {r['name']} (PID: {r['external_id']})\n"
                f"Price: {r.get('price')} {currency}{disc}\n"
                f"Stock: {r.get('stock', 0)} units\n"
                f"Description: {(r.get('description') or 'N/A')[:200]}"
            )
        except Exception:
            return None

    try:
        product = Product.objects.get(user=user, pid=pid, status=True)
    except Product.DoesNotExist:
        return None
    disc = f"  Discounted: {product.discounted_price} {currency}" if product.discounted_price else ""
    return (
        f"Selected product: {product.name} (PID: {product.pid})\n"
        f"Price: {product.price} {currency}{disc}\n"
        f"Stock: {product.stock_quantity} units\n"
        f"Description: {(product.description or 'N/A')[:200]}"
    )


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