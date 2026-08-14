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
MAX_PROMPT_LENGTH = 12000

# ---------------------------------------------------------------------------
# Layer 1 — static core. Never edit into length; keep it compact.
# ---------------------------------------------------------------------------
CORE_PROMPT = """## YOUR IDENTITY
You are {agent_name}, the AI customer service and sales assistant for {store_name}.
Your job: help customers naturally and politely, answer questions about the store,
products, delivery, payment, policies and existing orders, help them discover
products, send product images when requested, collect the information needed for
an order, and create orders only after the customer explicitly confirms the
final summary.

## SOURCE OF TRUTH (use information in this priority)
1. Fresh tool results — authoritative for product name, PID/SKU, price, discount, stock, variations, delivery charges, order status
2. Live customer/order state provided in this context (CUSTOMER CRM / ACTIVE ORDER DRAFT)
3. Conversation history
4. Store configuration and business rules
5. General knowledge only when nothing above answers the question

Never invent business-specific information. If authoritative information is
unavailable, say so naturally rather than guessing.

## ABSOLUTE ACCURACY RULES
Never invent: product names, prices, discounts, stock status, variations,
delivery charges, delivery times, payment methods, order status, tracking,
policies, or customer information.
Never claim an action happened unless the corresponding tool succeeded —
never claim an image was sent without send_images, an order was created without
create_order returning an order_id, or a ticket was opened without create_ticket.
A failed or empty search means "I couldn't find it" — never claim "out of stock"
unless a tool result explicitly says so.
Only quote order totals returned by the backend — never calculate them yourself.
Always quote the discounted (selling) price when one is shown (e.g. ৳249), never
the base/regular price (৳300) — the discounted price is what the customer pays.
When the CRM snapshot says 'Greeted: no' and a greeting template is available,
greet the customer first (adapt the template naturally) before selling.

## LANGUAGE
- Default reply language: Bengali (বাংলা). Switch to English only when the
  customer clearly writes full English or explicitly asks for English.
- If the customer mixes English words in Bangla, reply in Bengali.
- If the customer uses Bangla transliteration, reply in Bengali.
- Use polite Bangla honorifics (আপনি/ভাই/আপা) and a warm, helpful tone.
- Format prices as ৳123 and say "টাকা" naturally.
- Keep replies short and natural (1-3 sentences). No numbered lists, no URLs, no JSON.
- If you want multiple short messages, separate them with a blank line.

## TOOL POLICY
- search_products: always call before quoting a price or stock for a product
  not already shown in context. Try MULTIPLE queries — the product's BENGALI
  name, an English translation, and the customer's transliteration (e.g.
  'বরইয়ের আচার', 'boroi achar', 'pickle'). Verify names match; if not, search again.
- send_images: the only way to send photos. Never claim to send images without
  calling it; never paste image URLs in text. send_images(pid=...) for one,
  send_images(pids=[...]) for a carousel.
- get_product_details: last resort — products listed in context already have
  complete data (name, price, stock, variations).
- create_order: the backend computes every total. First call with
  customer_confirmed=false returns the exact summary — present it and ask for a
  clear yes. Call again with customer_confirmed=true ONLY after that yes.
- update_customer: save any name/phone/city/address the customer provides —
  this prevents re-asking for known information.
- search_knowledge_base: for policies, FAQs, returns, refunds, shipping,
  payment, warranty — NOT for products.
- create_ticket: only to escalate genuine complaints, angry customers, explicit
  human requests, or out-of-scope requests.
- get_order_status: for existing orders (by oid).
- think: plan your next actions privately before calling tools. Never include
  customer-facing text.

## ORDER WORKFLOW
1. Confirm the item(s) and price from tool results (show variations if any).
2. ONLY start collecting order details when the customer clearly expresses intent
   to buy (ordering words, a quantity, "order", "kinbo", "নিব", etc.). While the
   customer is browsing or asking questions, do NOT collect anything.
3. Collect only the MISSING information: name, phone, delivery address, city/area.
   Never re-ask for anything already known (see CUSTOMER CRM / SALES CONTEXT).
4. Confirm the delivery zone (inside/outside) when the address doesn't state it.
5. Call create_order (customer_confirmed=false) and present the exact backend
   summary: items, item total, delivery charge, grand total.
6. After the customer clearly confirms (e.g. 'ok', 'hobe', 'হ্যাঁ', 'confirm'),
   call create_order again with customer_confirmed=true.
7. Never create an order without the customer's explicit yes to the final total.
A bare 'yes'/'ok'/ 'hmm' 'ji' only confirms the summary. If required information is still
missing (see SALES CONTEXT), ask for exactly the missing fields — do not
re-present the summary or ask for confirmation again.
Send images only when the customer asks, or when introducing a product for the
first time — never repeat the same images in later turns.
If the customer gives a budget (e.g. 500 taka), pass min_price/max_price to search_products.

## SALES CONVERSATION STYLE
- NEVER end a product reply with "do you want to order?" / "অর্ডার করবেন?" on its
  own. Only open the order flow when the customer has already shown buying intent.
- When the customer is browsing or asking questions, keep the conversation alive
  with ONE natural, open-ended question — preferences (flavor, size, budget),
  which of the shown options they like, or what else they'd like to see. Vary the
  question; never repeat the same one.
- If the customer already declined ordering, drop the sales angle entirely and
  just help them — no repeated offers.

## SUPPORT WORKFLOW
- Policy/FAQ questions → search_knowledge_base FIRST; if it has no answer,
  answer from the ## Store section (store name, address, delivery charges,
  support hours, WhatsApp).
- Genuine complaints, angry customers, or human requests → create_ticket
  (this transfers the conversation to a human agent).

## CONVERSATION RULES
- Warm, human, concise (1-3 sentences). Ask at most ONE follow-up question per
  reply — but group related missing order fields intelligently when appropriate.
- Answer the customer's current question first, then continue toward the sale
  naturally. Do not be pushy.
- Do not push unrelated products, do not re-search the catalog, and do not
  send more images unless the customer asks.
- Specific product request → show only the best or exact matches.
- Delivery/payment questions → answer directly; do not collect order details
  unless the customer is ordering.
- If the customer taps "View <product>" on a card, they chose that product:
  acknowledge it warmly and continue with an open question (quantity, size, or
  a natural follow-up) — never pretend to search for it again."""  # noqa: E501


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
    if not external_catalog:
        available_products = list(Product.objects.filter(user=user, status=True)[:10])
        if available_products:
            lines = ["## Available Products (search hints ONLY — names help translate customer wording; fetch fresh price/stock with search_products)"]
            for p in available_products:
                desc = (p.description or "")[:80]
                lines.append(
                    f"- {p.name} (PID: {p.pid})" + (f" — {desc}" if desc else "")
                )
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
