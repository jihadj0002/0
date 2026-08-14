"""System prompt assembly — three layers + priority-based fitting.

Layer 1  STATIC core (module constant, never truncated): identity, mission,
         truth hierarchy, anti-hallucination, tool policy, order/support
         workflows, conversation rules.
Layer 2  Store + business configuration.
Layer 3  Live state: CRM snapshot, focused products, image analysis,
         catalog hints (lowest priority).

:func:`fit_prompt` drops whole low-priority sections when the budget is
exceeded — it never slices an instruction in half.
"""
from back.models import Message, Product

# Budget in characters (≈ tokens*4 heuristic). The core prompt is written to
# fit comfortably inside this even when every other section is dropped.
MAX_PROMPT_LENGTH = 9000

# ---------------------------------------------------------------------------
# Layer 1 — static core. Never edit into length; keep it compact.
# ---------------------------------------------------------------------------
CORE_PROMPT = """## ROLE
You are {agent_name}, AI sales & support assistant of {store_name}. Help politely with products, prices, delivery, payment, policies and existing orders; send product images when asked; create orders ONLY after the customer explicitly confirms the final summary.

## TRUTH (priority order)
1. Fresh tool results (authoritative: name/PID/SKU, price, discount, stock, variations, delivery, order status)
2. Live state in this context (CUSTOMER CRM / SALES CONTEXT / ACTIVE ORDER DRAFT)
3. Conversation history
4. Store config + business rules
5. General knowledge only as last resort.

## ACCURACY
Never invent names, prices, discounts, stock, variations, delivery fees/times, payment methods, order status, policies or customer info. Never claim an action without its tool succeeding (send_images/create_order/create_ticket). Empty search = "not found", never "out of stock" unless stated. Quote only backend order totals, never self-calculated. Quote the DISCOUNTED selling price when shown (৳249, not ৳300). If CRM says "Greeted: no" and a greeting template exists, greet first (adapted naturally).

## LANGUAGE
Bengali (বাংলা) by default — including mixed/transliterated English — polite honorifics (আপনি/ভাই/আপা), warm tone, prices as ৳123. English only when the customer writes full English. Replies: 1-3 short sentences, no lists/URLs/JSON; separate multiple short messages with a blank line.

## TOOLS (call before quoting anything not shown in context)
- search_products: before any price/stock; try ≤3 queries (Bengali name, English, transliteration).
- send_images: the ONLY way to send photos. Cards/images show name+price — never re-list them in text.
- get_product_details: only for products NOT already listed in context (listed ones have complete data).
- create_order: backend computes totals — first call customer_confirmed=false returns the summary to present; second call customer_confirmed=true ONLY after explicit yes.
- update_customer: save any name/phone/city/address given.
- search_knowledge_base: policies/FAQs/returns/shipping/payment — NOT products.
- create_ticket: complaints, angry customers, explicit human request.
- get_order_status: existing order by oid.
- think: private planning before tools; never customer-facing text.

## ORDER FLOW
Start collecting order details ONLY after clear buying intent (quantity, "order", "kinbo", "নিব"…); while browsing, collect nothing. Collect only MISSING fields (see SALES CONTEXT) — never re-ask known info. Confirm zone (inside/outside) if the address doesn't say. Present the exact backend summary, then create_order(confirmed=true) after a clear yes. A bare 'yes'/'ok' only confirms the summary. No order without explicit yes to the final total. Budget given → pass min/max_price to search_products. Send images only when asked or when first introducing a product — never repeat them.

## SALES STYLE
NEVER end a product reply with "do you want to order?" / "অর্ডার করবেন?" on its own — only open the order flow after buying intent. While browsing, keep it alive with ONE natural, varied open-ended question (preferences, size, budget, which option they like). After a decline, stop selling and just help.

## SUPPORT
Policy questions → search_knowledge_base first; if no answer, use the ## Store block below. Complaints/angry/human request → create_ticket.

## RULES
Answer the current question first. Max ONE follow-up question per reply. No unrelated products, no re-searching, no unsolicited images. Specific request → exact/best match only. Delivery/payment questions → answer directly, no order details. Card "View X" tap = customer chose X → acknowledge + open question, never re-search for it. Products in "Recent Searched Products" are COMPLETE data — never refetch them."""  # noqa: E501


# ---------------------------------------------------------------------------
# Priority-based fitting
# ---------------------------------------------------------------------------

def fit_prompt(sections, max_chars=MAX_PROMPT_LENGTH):
    """Assemble ``sections`` (list of ``(priority, text)``) within the budget.

    Higher priority = never dropped. When the budget is exceeded, whole
    low-priority sections are removed — an individual instruction is never
    sliced in half. Returns the joined prompt.
    """
    kept = [(p, t) for p, t in sections if t and t.strip()]
    total = sum(len(t) + 2 for _, t in kept)
    while total > max_chars and len(kept) > 1:
        # Drop the lowest-priority section.
        lowest = min(kept, key=lambda s: s[0])
        kept.remove(lowest)
        total = sum(len(t) + 2 for _, t in kept)
    if total > max_chars and kept:
        # Even the core alone doesn't fit — hard cut (should never happen).
        p, text = kept[0]
        kept = [(p, text[:max_chars])]
    kept.sort(key=lambda s: -s[0])  # restore output order: highest priority first
    joined = "\n\n".join(t for _, t in kept)
    if len(kept) < len(sections):
        joined += "\n\n[CONTEXT TRUNCATED — low-priority sections omitted]"
    return joined


# ---------------------------------------------------------------------------
# Layer 2 + 3 assembly
# ---------------------------------------------------------------------------

def build_system_prompt(user, conversation, image_analysis=None):
    """Assemble the full system prompt from the static core, StoreConfig /
    BehaviorRules, and live state (CRM snapshot, focused products, image
    analysis, catalog hints).

    ``image_analysis`` — optional dict from api.ai.media.analyze_image_structured
    (sku, product_name, brand, description) plus ``analysis_search`` for
    pre-search results. When the current turn was triggered by an image, this
    tells the AI that the catalog was already searched so it doesn't re-search.
    """
    from context.models import AgentIdentity, BehaviorRules, StoreConfig
    from api.products.factory import get_active_source, is_external

    identity = AgentIdentity.objects.filter(user=user).first()
    store = StoreConfig.objects.filter(user=user).first()
    rules = BehaviorRules.objects.filter(user=user).first()

    source = get_active_source(user)
    external_catalog = bool(source) and is_external(user)

    sections = []

    # --- Layer 1: static core (priority 100) ---
    sections.append((
        100,
        CORE_PROMPT.format(
            agent_name=(identity.name if identity else "Assistant") or "Assistant",
            store_name=(store.store_name if store else "our store") or "our store",
        ),
    ))

    # --- Layer 2: store config (priority 95) ---
    if store:
        sections.append((
            95,
            f"## Store\n"
            f"Name: {store.store_name or 'Our Store'}\n"
            f"Address: {store.address or 'Not set'}\n"
            f"WhatsApp: {store.whatsapp_number or 'Not set'}\n"
            f"Support hours: {store.support_open_time} – {store.support_close_time} ({store.timezone})\n"
            f"Currency: {store.currency}\n"
            f"Delivery inside: {store.delivery_charge_inside} {store.currency}  |  "
            f"Outside: {store.delivery_charge_outside} {store.currency}\n"
            "- Customer asks shop name / delivery charge / hours / address / payment "
            "→ answer directly from this block. Do NOT collect an address or push an "
            "order for these questions."
        ))

    # --- Layer 2: business rules (priority 90) ---
    biz = []
    if rules:
        chit = f"{'on' if rules.chit_chat_enabled else 'off'} ({rules.chit_chat_style})"
        biz.append(
            f"Chit-chat: {chit}  |  Cross-sell: {'yes' if rules.cross_sell_enabled else 'no'}  |  "
            f"Ask open-ended questions: {'yes' if rules.ask_open_ended else 'no'}"
        )
        if rules.greeting_message:
            biz.append(f"Greeting template: {rules.greeting_message}")
        if rules.out_of_hours_message:
            biz.append(f"Out-of-hours message: {rules.out_of_hours_message}")
        if rules.custom_instructions:
            biz.append(f"Custom instructions (user-defined): {rules.custom_instructions}")
    if biz:
        sections.append((90, "## BUSINESS RULES\n" + "\n".join(biz)))

    # --- Layer 3: CRM snapshot (priority 85) ---
    crm_snapshot = _crm_snapshot_for(conversation)
    if crm_snapshot:
        sections.append((85, crm_snapshot))

    # --- Layer 3: rolling chat summary (priority 80) — lets history shrink
    # from 12 to 6 raw messages on long conversations without losing facts. ---
    summary = (getattr(conversation, "chat_summary", "") or "").strip()
    if summary:
        sections.append((80, f"## CHAT SUMMARY (older messages, condensed)\n{summary[:1500]}"))

    # --- Layer 3: image pre-search awareness (priority 70) ---
    if image_analysis:
        img = (
            "## Image Analysis (already processed)\n"
            "The customer sent an image. The system analyzed it and pre-searched "
            "the catalog for matching products (results in 'Recent Searched Products' "
            "below). Do NOT search again unless the customer asks for something different."
        )
        if image_analysis.get("sku"):
            img += f"\nDetected SKU: {image_analysis['sku']}"
        if image_analysis.get("product_name"):
            img += f"\nDetected name: {image_analysis['product_name']}"
        if image_analysis.get("brand"):
            img += f"\nDetected brand: {image_analysis['brand']}"
        sections.append((70, img))

    # --- Layer 3: focused products (priority 75) ---
    from .tools import parse_focus_products
    focus_list = parse_focus_products(conversation.current_product)
    if focus_list:
        currency = store.currency if store else "BDT"
        sections.append((75, _render_focus_products(focus_list, currency)))

    # --- Layer 3: catalog hints (priority 50 — first to be dropped) ---
    # Hints only — never authoritative; prices/stock always come from tools.
    # Kept small: 5 name-only entries (they exist to translate customer wording).
    if not external_catalog:
        available_products = list(Product.objects.filter(user=user, status=True)[:5])
        if available_products:
            lines = ["## Available Products (hint only — names help translate customer wording; fetch fresh price/stock with search_products)"]
            for p in available_products:
                lines.append(f"- {p.name} (PID: {p.pid})")
            sections.append((50, "\n".join(lines)))
        else:
            sections.append((50, "## Available Products\nNo products listed — use search_products."))

    # NOTE: conversation history is NOT embedded here — pipeline.py passes it as
    # real chat messages, so embedding it again would duplicate every turn.

    return fit_prompt(sections)


def _crm_snapshot_for(conversation):
    """Render the live CRM snapshot (empty string when nothing to show)."""
    from context.crm.snapshot import build_crm_snapshot
    return build_crm_snapshot(conversation)


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
        if primary.get("discounted_price") is not None:
            price_line = f"   Selling price: {primary['discounted_price']} {currency}  (regular: {primary['price']} {currency})"
        else:
            price_line = f"   Price: {primary['price']} {currency}"
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
        price = None
        if f.get("discounted_price") is not None:
            price = f"Selling: {f['discounted_price']} {currency}"
        elif f.get("price") is not None:
            price = f"{f['price']} {currency}"
        stock_note = "" if f.get("in_stock", True) else " — out of stock"
        lines.append(f"- {label}" + (f" — {price}" if price else "") + stock_note)

    lines.append(
        "All data above is COMPLETE — you already have name, price, stock, "
        "description, and variations. Call send_images(pid=...) to send photos "
        "and describe the product from this data. Do NOT call get_product_details "
        "for any product listed above — the data is already here."
    )
    return "\n".join(lines)


def get_conversation_history(conversation, limit=15):
    """Return the last `limit` messages as an OpenAI-format list.

    Human-agent messages are included (marked ``[human agent]``) so the AI
    knows what a human already told the customer.
    """
    msgs = list(
        Message.objects
        .filter(conversation=conversation)
        .order_by("-timestamp")[:limit]
    )
    msgs.reverse()

    history = []
    for m in msgs:
        if m.sender == "agent":
            role = "assistant"
            content = f"[human agent] {m.text or ''}".strip()
        else:
            role = "assistant" if m.sender == "bot" else "user"
            content = m.text or ""
        if not content and m.attachments:
            att_type = m.attachments.get("type", "")
            url = m.attachments.get("url") or m.attachments.get("payload", {}).get("url", "")
            content = f"[{att_type}: {url}]" if url else f"[{att_type}]"
        history.append({"role": role, "content": content})

    return history
