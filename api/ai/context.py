from back.models import Message, Product

MAX_PROMPT_LENGTH = 4000


def build_system_prompt(user, conversation):
    """Assemble the full system prompt from AgentIdentity, StoreConfig, BehaviorRules, and live conversation state."""
    from context.models import AgentIdentity, StoreConfig, BehaviorRules

    identity = AgentIdentity.objects.filter(user=user).first()
    store = StoreConfig.objects.filter(user=user).first()
    rules = BehaviorRules.objects.filter(user=user).first()

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

    # Current selected product or top-5 catalogue snapshot
    pid = (conversation.current_product or "").strip()
    if pid:
        try:
            product = Product.objects.get(user=user, pid=pid, status=True)
            currency = store.currency if store else "BDT"
            lines = [
                f"Selected product: {product.name} (PID: {product.pid})",
                f"Price: {product.price} {currency}" + (
                    f"  Discounted: {product.discounted_price} {currency}" if product.discounted_price else ""
                ),
                f"Stock: {product.stock_quantity} units",
                f"Description: {(product.description or 'N/A')[:200]}",
            ]
            cust.append("\n".join(lines))
        except Product.DoesNotExist:
            pass  # stale PID — context builder simply omits it
    else:
        available_products = list(Product.objects.filter(user=user, status=True)[:5])
        if available_products:
            lines = ["Available products (sample):"]
            for p in available_products:
                desc = (p.description or "")[:80]
                lines.append(f"- {p.name} (PID: {p.pid}) — {p.price} {store.currency if store else 'BDT'}" +
                              (f" — {desc}" if desc else ""))
            cust.append("\n".join(lines))
        else:
            cust.append("No products available in the store")

    if cust:
        parts.append("## Current Customer\n" + "\n".join(cust))

    # --- Conversation history (last 20 messages) ---
    history = get_conversation_history(conversation, limit=20)
    if history:
        history_text = []
        for msg in history:
            role = msg["role"].upper()
            content = msg["content"]
            history_text.append(f"{role}: {content}")
        if history_text:
            parts.append("## Recent Conversation\n" + "\n".join(history_text))

    # --- Core task rules ---
    parts.append(
        "## Rules\n"
        "- Never invent product prices or stock — always call search_products first.\n"
        "- Before creating an order collect: customer name, phone, delivery address.\n"
        "- Confirm order items aloud before calling create_order.\n"
        "- Use transfer_chat when the customer is angry, asks for a human, or issue is beyond your scope.\n"
        "- Keep replies short and conversational unless the customer asks for detail.\n"
        "- When suggesting products, always offer to send images with send_images.\n"
        "- Never send more than 5 images at once.\n"
        "- Never Send urls as outputs image urls should be sent via send_images.\n"
    )

    # Join all parts and truncate if too long
    system_prompt = "\n\n".join(parts)
    if len(system_prompt) > MAX_PROMPT_LENGTH:
        # Truncate and add indicator
        system_prompt = system_prompt[:MAX_PROMPT_LENGTH] + "\n\n[SYSTEM PROMPT TRUNCATED]"
    return system_prompt


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