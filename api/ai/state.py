"""
State Machine & Workflow Engine (P1-9 .. P1-13).

Persistence lives in context.models.SessionContext (one per conversation).
The engine consumes messages while a workflow is active, collects fields
turn-by-turn, validates transitions, and executes the final tool calls.
"""
import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State transitions (P1-10)
# ---------------------------------------------------------------------------

WORKFLOW_STATES = [
    "idle", "browsing", "product_selected", "awaiting_details",
    "awaiting_confirmation", "checkout", "payment", "completed", "escalated",
]

STATE_TRANSITIONS: dict[str, set[str]] = {
    "idle": set(WORKFLOW_STATES) - {"idle"},
    "browsing": {"product_selected", "idle", "escalated"},
    "product_selected": {"awaiting_details", "idle", "escalated"},
    "awaiting_details": {"awaiting_confirmation", "awaiting_details", "idle", "escalated"},
    "awaiting_confirmation": {"checkout", "awaiting_details", "idle", "escalated"},
    "checkout": {"payment", "completed", "idle", "escalated"},
    "payment": {"completed", "idle", "escalated"},
    "completed": {"idle", "escalated"},
    "escalated": {"idle"},
}

# Order fields in collection order (P1-6: delivery zone derived, not re-asked)
ORDER_FIELDS = ["customer_name", "customer_phone", "customer_address"]
FIELD_LABELS = {
    "customer_name": "আপনার নাম",
    "customer_phone": "আপনার মোবাইল নম্বর",
    "customer_address": "আপনার সম্পূর্ণ ঠিকানা",
}
FIELD_LABELS_EN = {
    "customer_name": "your name",
    "customer_phone": "your phone number",
    "customer_address": "your full delivery address",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_session(conversation):
    """Get (or create) the SessionContext for a conversation (P1-9 wiring)."""
    from context.models import SessionContext
    session, _ = SessionContext.objects.get_or_create(conversation=conversation)
    return session


def transition(conversation, new_state, **updates):
    """Validate and apply a state transition. Returns the session."""
    session = get_session(conversation)
    allowed = STATE_TRANSITIONS.get(session.state, set())
    if new_state not in allowed:
        logger.warning(
            "Invalid transition %s -> %s for conv=%s",
            session.state, new_state, conversation.pk,
        )
        return session
    for key, value in updates.items():
        setattr(session, key, value)
    session.state = new_state
    session.save(update_fields=["state", "current_workflow", "workflow_step",
                                "collected_data", "pending_confirmation",
                                "verified", "verification_method", "updated_at"])
    return session


def reset_session(conversation):
    """Reset workflow state back to idle."""
    session = get_session(conversation)
    session.state = "idle"
    session.current_workflow = ""
    session.workflow_step = 0
    session.collected_data = {}
    session.pending_confirmation = None
    session.verified = False
    session.save(update_fields=["state", "current_workflow", "workflow_step",
                                "collected_data", "pending_confirmation",
                                "verified", "updated_at"])
    return session


def mark_escalated(conversation):
    """Called when a human handoff happens (create_ticket success)."""
    try:
        return transition(conversation, "escalated", current_workflow="human_support")
    except Exception as exc:
        logger.warning("mark_escalated failed conv=%s: %s", conversation.pk, exc)
        return None


def _resolve_from_history(context):
    """Scan recent conversation history (newest first) for the last product the
    customer mentioned, by matching catalog product names against message text.
    Returns {"pid", "name"} or None."""
    try:
        from back.models import Product

        user = getattr(getattr(context, "conversation", None), "user", None)
        if not user:
            return None
        history = getattr(context, "history", None) or []
        if not history:
            return None
        products = list(Product.objects.filter(user=user, status=True)[:50])
        for h in reversed(history[-12:]):
            content = (h.get("content") or "").lower()
            if not content or h.get("role") == "assistant":
                continue
            words = [w for w in re.split(r"[\s,.;:!?]+", content) if len(w) >= 3]
            if not words:
                continue
            for p in products:
                name = (p.name or "").strip().lower()
                if not name:
                    continue
                if name in content or any(w in name for w in words):
                    return {"pid": p.pid, "name": p.name, "price": str(p.price),
                            "discounted_price": str(p.discounted_price) if p.discounted_price else None,
                            "stock": p.stock_quantity, "in_stock": p.stock_quantity > 0}
    except Exception as exc:
        logger.warning("History product resolution failed: %s", exc)
    return None


def resolve_product_reference(text, focus_products, default_index=0):
    """P1-13 disambiguation: 'the second one' / 'দ্বিতীয়টা' / name mention
    ('jolpai') -> focus item."""
    if not focus_products:
        return None
    lowered = (text or "").strip().lower()

    # Ordinal references
    m = re.search(r"(?:the\s+)?(first|second|third|fourth|1st|2nd|3rd|4th|one|two|three)\s+one?", lowered)
    if m:
        word = m.group(1)
        idx_map = {"first": 0, "1st": 0, "one": 0,
                   "second": 1, "2nd": 1, "two": 1,
                   "third": 2, "3rd": 2, "three": 2,
                   "fourth": 3, "4th": 3}
        idx = idx_map.get(word)
        if idx is not None and idx < len(focus_products):
            return focus_products[idx]
    m = re.search(r"(১ম|২য়|৩য়|দ্বিতীয়|তৃতীয়|একটা|দুটো|প্রথম)", text or "")
    if m:
        word = m.group(1)
        idx_map = {"প্রথম": 0, "একটা": 0, "১ম": 0,
                   "দ্বিতীয়": 1, "২য়": 1, "দুটো": 1,
                   "তৃতীয়": 2, "৩য়": 2}
        idx = idx_map.get(word)
        if idx is not None and idx < len(focus_products):
            return focus_products[idx]

    # Name-token match: any significant word of the message appears in a
    # focus product's name ("jolpai" -> "Jolpaia achar", "আমের" -> "Amer Achar").
    # A word is only decisive when it uniquely resolves to ONE product — a
    # generic token like "আচার"/"achar" matches all three achar products and
    # must stay ambiguous (the selection prompt handles it).
    words = [w for w in re.split(r"[\s,.;:!?]+", lowered) if len(w) >= 3]
    if words:
        best = None
        best_count = None
        for w in words:
            matched = [fp for fp in focus_products
                       if fp.get("name") and (fp["name"] in lowered or w in fp["name"].lower())]
            if not matched:
                continue
            if best_count is None or len(matched) < best_count:
                best, best_count = matched[0], len(matched)
        if best_count == 1:
            return best
        # Bengali words → romanized latin so "আমের" matches "Amer Achar".
        # Only words that actually changed are considered (never latin words),
        # and only when the word uniquely resolves to ONE product — a generic
        # token like "আচার" matches all three achar products and must stay
        # ambiguous (the selection prompt handles it).
        from .tools import _latinize_bn
        latin_words = []
        for w in words:
            lt = _latinize_bn(w)
            if lt and len(lt) >= 3 and lt != w:
                latin_words.append(lt)
        if latin_words:
            best = None
            best_count = None
            for lt in latin_words:
                matched = [fp for fp in focus_products
                           if _latin_token_matches(lt, fp.get("name") or "")]
                if not matched:
                    continue
                if best_count is None or len(matched) < best_count:
                    best, best_count = matched[0], len(matched)
            if best_count == 1:
                return best
    return None


def _latin_token_matches(lt: str, name: str) -> bool:
    """Token ↔ product-name match with prefix tolerance: Bengali inflections
    ("জলপাইয়ের" → "jolpaiyer") must still match the base name ("Jolpaia
    achar") via their leading 6 chars ("jolpai")."""
    lt = (lt or "").lower()
    name = (name or "").lower()
    if not lt or not name:
        return False
    if lt in name or name in lt:
        return True
    pre = lt[:6] if len(lt) > 6 else lt
    return len(pre) >= 4 and pre in name


def _lang(conversation):
    """Return 'bn' or 'en' based on the conversation language hint, falling
    back to the agent's configured language (the pipeline persists the hint
    on the conversation, but a fresh conversation has none yet)."""
    detected = getattr(conversation, "language_detected", "") or ""
    if detected in ("bn", "en"):
        return detected
    try:
        from context.models import AgentIdentity
        agent = AgentIdentity.objects.filter(user=conversation.user).first()
        return (agent.language or "bn") if agent else "bn"
    except Exception:
        return "bn"


def _ask_for_field(field, lang):
    labels = FIELD_LABELS if lang == "bn" else FIELD_LABELS_EN
    if lang == "bn":
        return f"ঠিক আছে! অর্ডারটি সম্পন্ন করতে {labels[field]} জানাবেন? 🛒"
    return f"Great! To complete your order, could you tell me {labels[field]}?"


# ---------------------------------------------------------------------------
# Order workflow (P1-11/P1-12)
# ---------------------------------------------------------------------------


class WorkflowEngine:
    """Drives multi-turn workflows persisted in SessionContext."""

    # Intents that start a workflow when one is not active
    INTENT_WORKFLOWS = {"CREATE_ORDER": "create_order"}

    CONFIRM_RE = re.compile(
        r"^(yes|yep|yeah|confirm|ok|okay|sure|done|correct|ha|han|hmm|hm|"
        r"ji|jii|hobe|ঠিক|হ্যাঁ|হ্যা|হুম|হুঁ|অবশ্যই|ঠিক আছে|অর্ডার করুন|confirm|ship it|"
        r"হবে|করছি|করুন|নিব|নিন|দিবেন|দেবেন)",
        re.IGNORECASE
    )
    CANCEL_RE = re.compile(
        r"^(no|nope|na|nah|cancel|never ?mind|stop|না|বাতিল|থামুন|লাগবে না|প্রয়োজন নেই)",
        re.IGNORECASE
    )
    QUESTION_RE = re.compile(
        r"[?؟]|^(what|which|why|how|when|who|কি|কী|কোন|কেন|কেমন)\b", re.IGNORECASE
    )
    # "আবার অর্ডার করব", "order again", "একই জিনিস", "আগের মতো" → repeat last order
    _REPEAT_ORDER_RE = re.compile(
        r"(আবার|আবারো|abar|again|একই|same|আগের মতো|আগের মতোই|আরেকবার|"
        r"repeat|আরেকটা|আরো এক|ager moto|ager motoi|ager order|like last time|"
        r"like before|purono|previous|last order)", re.IGNORECASE
    )
    _QUANTITY_RE = re.compile(
        r"(?P<qty>\d+|[০-৯]+|এক|দুই|তিন|চার|পাঁচ|one|two|three|four|five)"
        r"\s*(?:pcs|pc|pieces?|kg|কেজি|পিস|টা|টি|খানা)(?!\w)", re.IGNORECASE
    )
    _BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

    # ------------------------------------------------------------------
    # Entry point: called by the orchestrator for every incoming message
    # ------------------------------------------------------------------

    @classmethod
    def handle_message(cls, conversation, text, context) -> dict | None:
        """If a workflow is active, consume the message.

        Returns a response dict {"text": ...} or None when the message
        should be processed by the normal orchestrator pipeline instead.
        """
        session = get_session(conversation)
        if session.state == "idle":
            return None

        # Price negotiation or frustration mid-flow: let the pipeline answer
        # (price hold / de-escalation); the pending field stays for the next
        # message. "150 e den" must not become a customer name or quantity.
        if getattr(getattr(context, "intent", None), "name", "") in ("NEGOTIATE", "FRUSTRATION"):
            return None

        # A question while collecting data is NOT a field answer — let the AI
        # answer it; the pending field stays pending for the next message.
        if cls.QUESTION_RE.search(text or ""):
            return None

        if session.current_workflow == "create_order":
            return cls._handle_order_step(conversation, session, text, context)
        return None

    @classmethod
    def _quick_catalog_search(cls, user, text) -> list:
        """Best-effort catalog lookup for order messages that name no prior
        focus product ("আমের আচার এক কেজি অর্ডার দিব"). Reuses the full
        search pipeline (variations, Bengali transliteration, prefixes)."""
        if not text or not user:
            return []
        try:
            from .tools import tool_search_products
            out = tool_search_products(user, text, limit=8)
            return list(out.get("products") or [])
        except Exception:
            logger.exception("quick catalog search failed")
            return []

    @classmethod
    def start_order_flow(cls, conversation, context) -> dict | None:
        """Start the CREATE_ORDER workflow (called when intent==CREATE_ORDER).

        Resolution order for the product:
          1. explicit reference in the message ("jolpai", "আমের আচার")
          2. last product discussed in conversation history
          3. repeat-order signal ("আবার", "again") → last order's product
          4. exactly one focused product
          5. ambiguous focus (several products) → ask which one
          6. nothing resolvable → ask for a product name (never falls through
             to LLM improvisation — orders are created only via this flow)

        If all required customer fields are already known, creates the order
        immediately. Otherwise begins turn-by-turn collection.
        """
        from .tools import parse_focus_products, _focus_products, FOCUS_MAX

        lang = _lang(conversation)
        text = (context.incoming_text or "").strip()

        focus = parse_focus_products(getattr(conversation, "current_product", "") or "")
        if not focus:
            # No product in focus yet (e.g. "আমের আচার এক কেজি অর্ডার দিব" with no
            # prior product search). Quick-search the catalog from the message text
            # so the order flow can resolve a real product instead of dropping it.
            found = cls._quick_catalog_search(conversation.user, text)
            if found:
                _focus_products(conversation, found[:FOCUS_MAX])
                focus = parse_focus_products(conversation.current_product or "")

        quantity = cls._parse_quantity(text)

        # 1. Explicit reference in the message
        selected = resolve_product_reference(text, focus) if focus else None
        # 3. Repeat-order signal → reuse the previous order's product (checked
        #    before history so "আবার অর্ডার করব" reuses the LAST ORDER's
        #    product AND quantity instead of whatever product was discussed).
        repeat_from = None
        if cls._REPEAT_ORDER_RE.search(text):
            repeat_from = cls._previous_order_for(conversation)
            if selected is None and repeat_from:
                if repeat_from.get("in_stock") is False:
                    reset_session(conversation)
                    return {"text": (
                        f"দুঃখিত, {repeat_from.get('name', '')} বর্তমানে স্টকে নেই। "
                        "অন্য কোনো প্রোডাক্ট দেখতে চাইলে জানাবেন।"
                        if lang == "bn" else
                        f"Sorry, {repeat_from.get('name', '')} is currently out of stock. "
                        "Let me know if you'd like to see something else."
                    )}
                selected = repeat_from
        # 2. Last product discussed in history
        if selected is None and focus:
            selected = _resolve_from_history(context)

        # 4/5. Focus products — exactly one focused product, or several (ask)
        if selected is None and focus:
            if len(focus) == 1:
                selected = focus[0]
            else:
                session = get_session(conversation)
                session.state = "awaiting_product_selection"
                session.current_workflow = "create_order"
                session.collected_data = {
                    "pending_pids": [f.get("pid", "") for f in focus[:4]],
                    "quantity": quantity,
                }
                session.save()
                names = ", ".join(f.get("name", f.get("pid", "?")) for f in focus[:4])
                return {"text": (
                    f"আপনি কোন প্রোডাক্টটা অর্ডার করতে চান? {names} — একটার নাম বলুন।"
                    if lang == "bn" else
                    f"Which product would you like to order? We have: {names}. Please name one."
                )}
        if selected is None:
            # 6. Nothing resolvable — ask for a product name.
            reset_session(conversation)
            return {"text": (
                "কোন প্রোডাক্টটা অর্ডার করতে চান? আমাদের ক্যাটালগ থেকে একটার নাম বলুন।"
                if lang == "bn" else
                "Which product would you like to order? Please name one from our catalog."
            )}

        pid = selected.get("pid", "")
        if repeat_from and not quantity:
            quantity = int(repeat_from.get("quantity") or 1)

        collected = {
            "customer_name": (context.customer.name or "").strip()
                             or (repeat_from or {}).get("customer_name", "") or "",
            "customer_phone": (context.customer.phone or "").strip()
                              or (repeat_from or {}).get("customer_phone", "") or "",
            "customer_address": (context.customer.address or "").strip()
                                or (repeat_from or {}).get("customer_address", "") or "",
            "pid": pid,
            "quantity": quantity or 1,
            "product_name": selected.get("name", ""),
        }

        missing = [f for f in ORDER_FIELDS if not collected.get(f)]

        session = get_session(conversation)
        if not missing:
            # Everything known -> create order in one turn
            result = cls._execute_create_order(conversation, session, collected, context)
            return cls._order_created_response(result, context, lang=_lang(conversation))

        # Start collection
        session.collected_data = collected
        session.current_workflow = "create_order"
        session.workflow_step = 0
        session.state = "awaiting_details"
        session.save()
        return {"text": _ask_for_field(missing[0], _lang(conversation))}

    @classmethod
    def _previous_order_for(cls, conversation):
        """Last order placed in this conversation (for repeat orders).

        Returns a product-like dict (+ quantity + customer fields) or None.
        """
        try:
            from back.models import Sale
            sale = Sale.objects.filter(conversation=conversation).order_by("-created_at").first()
            if not sale:
                return None
            item = sale.items.filter(action="base").first() or sale.items.first()
            if not item or not item.product:
                return None
            product = item.product
            return {
                "pid": product.pid,
                "name": product.name,
                "price": str(product.price),
                "discounted_price": str(product.discounted_price) if product.discounted_price else None,
                "stock": product.stock_quantity,
                "in_stock": product.stock_quantity > 0,
                "quantity": item.quantity,
                "customer_name": sale.customer_name or "",
                "customer_phone": sale.customer_phone or "",
                "customer_address": sale.customer_address or "",
            }
        except Exception as exc:
            logger.warning("Previous-order lookup failed conv=%s: %s", getattr(conversation, "pk", "?"), exc)
            return None

    @classmethod
    def _parse_quantity(cls, text) -> int:
        """Extract a quantity from "৪ পিস", "4 pcs", "এক কেজি", "২টা" → int (0 if none)."""
        m = cls._QUANTITY_RE.search(text or "")
        if not m:
            return 0
        raw = m.group("qty").strip().lower()
        words = {"এক": 1, "দুই": 2, "তিন": 3, "চার": 4, "পাঁচ": 5,
                 "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
        if raw in words:
            return words[raw]
        try:
            return max(1, int(raw.translate(cls._BN_DIGITS)))
        except ValueError:
            return 0

    @classmethod
    def _parse_details(cls, text) -> dict:
        """Best-effort name/phone/address extraction from a free-form details
        message ("Rafiul alam, Mirpur 10 kajipara, 01793504010")."""
        parts = [p.strip() for p in re.split(r"[,;|\n]+", text or "") if p.strip()]
        phone = ""
        name = ""
        address = ""
        named_parts = []
        for p in parts:
            digits = re.sub(r"\D", "", p)
            if len(digits) >= 10:
                phone = digits
                rest = re.sub(r"\d", "", p).strip(" ,;-")
                if rest and not name:
                    name = rest
            else:
                named_parts.append(p)
        if not name and named_parts:
            name = named_parts[0]
            named_parts = named_parts[1:]
        if named_parts:
            address = ", ".join(named_parts)
        return {
            "customer_name": name,
            "customer_phone": phone,
            "customer_address": address,
        }

    # ------------------------------------------------------------------
    # Order step handler
    # ------------------------------------------------------------------

    @classmethod
    def _validate_field_value(cls, field, value, lang) -> str | None:
        """Return an error message when the collected value is invalid, else None."""
        import re as _re

        if field == "customer_name":
            if not value or len(value) < 3 or cls.CONFIRM_RE.search(value) or cls.CANCEL_RE.search(value):
                return ("দুঃখিত, নামটা বুঝতে পারিনি। আপনার নাম জানাবেন? 🛒" if lang == "bn"
                        else "Sorry, I didn't catch that. What is your name? 🛒")
            # A name shouldn't look like a phone number or a quantity
            # ("2 pcs", "01711112222", "৩ পিস") — reject so it isn't stored.
            if len(_re.sub(r"\D", "", value)) >= 4:
                return ("দুঃখিত, নামটা বুঝতে পারিনি — ওটা নম্বর মনে হচ্ছে। আপনার নাম জানাবেন? 🛒" if lang == "bn"
                        else "Sorry, that looks like a number. What is your name? 🛒")
        if field == "customer_phone":
            digits = _re.sub(r"\D", "", value)
            if len(digits) < 10:
                return ("মোবাইল নম্বরটা যাচাই করা যায়নি। ১১ ডিজিটের নম্বর জানাবেন (যেমন 01712345678)? 📱"
                        if lang == "bn" else
                        "I couldn't verify that number. Please share an 11-digit number (e.g. 01712345678)? 📱")
        if field == "customer_address":
            if not value or len(value) < 5:
                return ("ঠিকানাটা একটু বিস্তারিত জানাবেন (এলাকা, বাসা নম্বর)? 📍"
                        if lang == "bn" else
                        "Could you give a fuller delivery address (area, house number)? 📍")
        return None

    @classmethod
    def _handle_order_step(cls, conversation, session, text, context) -> dict:
        lang = _lang(conversation)
        collected = dict(session.collected_data or {})

        if session.state == "awaiting_product_selection":
            from .tools import parse_focus_products

            # Cancel takes priority over a product answer
            if cls.CANCEL_RE.search(text or ""):
                reset_session(conversation)
                return {"text": "অর্ডারটি বাতিল করা হয়েছে। আরও কিছুতে সাহায্য করতে পারি?" if lang == "bn"
                        else "Order cancelled. Can I help with anything else?"}

            focus = parse_focus_products(getattr(conversation, "current_product", "") or "")
            selected = resolve_product_reference(text, focus)
            if selected is None and len(focus) == 1:
                selected = focus[0]

            # The customer answered with their details ("Rafiul alam, Mirpur 10,
            # 01793504010") instead of a product name — accept the details and
            # continue with the first product; the confirmation step at the end
            # still shows the product so the customer can correct it.
            digits = re.sub(r"\D", "", text or "")
            if selected is None and len(digits) >= 10:
                collected.update(cls._parse_details(text))
                collected["_selection_retries"] = 0
                if focus:
                    selected = focus[0]

            if selected is None:
                # Don't loop forever: after two unresolved replies (e.g. the
                # customer answered name/phone instead of a product), fall back
                # to the first listed product and continue the order flow.
                retries = int((collected or {}).get("_selection_retries", 0))
                if retries >= 1 and focus:
                    selected = focus[0]
            if selected is None:
                collected["_selection_retries"] = retries + 1
                session.collected_data = collected
                session.save()
                return {"text": (
                    "দুঃখিত, কোন প্রোডাক্টটা বুঝতে পারিনি। একটার নাম বলুন: "
                    + ", ".join(f.get("name", f.get("pid", "?")) for f in focus[:4])
                    if lang == "bn" else
                    "Sorry, I didn't catch which product. Please name one: "
                    + ", ".join(f.get("name", f.get("pid", "?")) for f in focus[:4])
                )}

            qty = cls._parse_quantity(text)
            # Prefill returning-customer fields (session collected_data from
            # start_order_flow only carries pending_pids/quantity).
            for f in ORDER_FIELDS:
                if not collected.get(f):
                    v = (context.customer.name if f == "customer_name"
                         else context.customer.phone if f == "customer_phone"
                         else context.customer.address)
                    if v:
                        collected[f] = v
            collected.update({
                "pid": selected.get("pid", ""),
                "product_name": selected.get("name", ""),
                "quantity": qty or int(collected.get("quantity") or 1),
            })
            session.collected_data = collected
            missing = [f for f in ORDER_FIELDS if not collected.get(f)]
            if missing:
                session.workflow_step = 0
                session.state = "awaiting_details"
                session.save()
                if lang == "bn":
                    return {"text": (
                        f"ঠিক আছে, {selected.get('name', '')}! "
                        f"অর্ডারটি সম্পন্ন করতে {FIELD_LABELS[missing[0]]} জানাবেন? 🛒"
                    )}
                return {"text": (
                    f"Great, {selected.get('name', '')}! To complete your order, "
                    f"could you tell me {FIELD_LABELS_EN[missing[0]]}?"
                )}
            summary = cls._order_summary_text(collected, context, lang)
            session.pending_confirmation = collected
            session.state = "awaiting_confirmation"
            session.verified = True
            session.save()
            return {"text": summary}

        if session.state == "awaiting_details":
            # Cancel takes priority over collecting a field value
            if cls.CANCEL_RE.search(text or ""):
                reset_session(conversation)
                return {"text": "অর্ডারটি বাতিল করা হয়েছে। আরও কিছুতে সাহায্য করতে পারি?" if lang == "bn"
                        else "Order cancelled. Can I help with anything else?"}

            field = ORDER_FIELDS[session.workflow_step] if session.workflow_step < len(ORDER_FIELDS) else None
            if field is None:
                return {"text": "আমি অর্ডারটির তথ্য নিয়ে নিচ্ছি, একটু অপেক্ষা করুন…" if lang == "bn"
                        else "Let me process your order…"}

            value = (text or "").strip()

            # The customer named a product instead of answering a field question
            # (e.g. "tetuler achar" while we asked for their name) — swap the
            # order's product and keep collecting; never store it as a name.
            from .tools import parse_focus_products
            focus = parse_focus_products(getattr(conversation, "current_product", "") or "")
            swap = resolve_product_reference(value, focus) if focus else None
            if swap is None:
                found = cls._quick_catalog_search(conversation.user, value)
                if len(found) == 1:
                    swap = found[0]
            if swap and (
                swap.get("pid") != collected.get("pid")
                or value.lower() == (swap.get("name") or "").lower()
            ):
                collected["pid"] = swap.get("pid", "")
                collected["product_name"] = swap.get("name", "")
                q = cls._parse_quantity(value)
                if q:
                    collected["quantity"] = q
                session.collected_data = collected
                session.save()
                return {"text": (
                    f"ঠিক আছে, এখন {swap.get('name')} অর্ডার করছি! {FIELD_LABELS[field]} জানাবেন?"
                    if lang == "bn" else
                    f"Sure, {swap.get('name')} it is! Could you tell me {FIELD_LABELS_EN[field]}?"
                )}

            # Quantity instead of a name/address ("2 pcs", "এক কেজি") — capture
            # it and keep collecting (never store it as the customer's name).
            qty = cls._parse_quantity(value)
            if qty and field != "customer_phone":
                collected["quantity"] = qty
                session.collected_data = collected
                session.save()
                return {"text": (
                    f"ঠিক আছে, {qty} পিস! এখন {FIELD_LABELS[field]} জানাবেন? 🛒"
                    if lang == "bn" else
                    f"Great, {qty} pieces! Now, could you tell me {FIELD_LABELS_EN[field]}?"
                )}

            # Multi-field answer: "Rafiul alam, Mirpur 10 kajipara, 01793504010"
            # fills name+phone+address in one turn when a phone number is present.
            if len(re.sub(r"\D", "", value)) >= 10:
                parsed = cls._parse_details(value)
                filled = False
                for f in ORDER_FIELDS:
                    if not collected.get(f) and parsed.get(f):
                        collected[f] = parsed[f]
                        filled = True
                if filled:
                    session.collected_data = collected
                    missing = [f for f in ORDER_FIELDS if not collected.get(f)]
                    if missing:
                        session.workflow_step = ORDER_FIELDS.index(missing[0])
                        session.state = "awaiting_details"
                        session.save()
                        return {"text": _ask_for_field(missing[0], lang)}
                    summary = cls._order_summary_text(collected, context, lang)
                    session.pending_confirmation = collected
                    session.state = "awaiting_confirmation"
                    session.verified = True
                    session.save()
                    return {"text": summary}

            # Validate the collected value so "ok"/"yes" isn't stored as a name
            problem = cls._validate_field_value(field, value, lang)
            if problem:
                return {"text": problem}

            collected[field] = value
            session.collected_data = collected

            missing = [f for f in ORDER_FIELDS if not collected.get(f)]
            if missing:
                session.workflow_step += 1
                session.state = "awaiting_details"
                session.save()
                return {"text": _ask_for_field(missing[0], lang)}

            # All fields collected -> confirm
            summary = cls._order_summary_text(collected, context, lang)
            session.pending_confirmation = collected
            session.state = "awaiting_confirmation"
            session.verified = True
            session.save()
            return {"text": summary}

        if session.state == "awaiting_confirmation":
            if cls.CONFIRM_RE.search(text or ""):
                pending = session.pending_confirmation or collected
                result = cls._execute_create_order(conversation, session, pending, context)
                return cls._order_created_response(result, context, lang)
            if cls.CANCEL_RE.search(text or ""):
                reset_session(conversation)
                return {"text": "অর্ডারটি বাতিল করা হয়েছে। আরও কিছুতে সাহায্য করতে পারি?" if lang == "bn"
                        else "Order cancelled. Can I help with anything else?"}
            if lang == "bn":
                return {"text": "আপনি কি অর্ডারটি নিশ্চিত করতে চান? (হ্যাঁ / না)"}
            return {"text": "Do you want to confirm the order? (yes / no)"}

        return {"text": "দুঃখিত, আমি বুঝতে পারিনি। আবার বলবেন?" if lang == "bn"
                else "Sorry, I didn't catch that. Could you repeat?"}

    @classmethod
    def _order_summary_text(cls, collected, context, lang) -> str:
        name = collected.get("customer_name", "")
        phone = collected.get("customer_phone", "")
        address = collected.get("customer_address", "")
        product = collected.get("product_name") or ""
        if lang == "bn":
            return (
                f"আপনার অর্ডারের সারসংক্ষেপ:\n"
                f"👤 নাম: {name}\n📞 মোবাইল: {phone}\n📍 ঠিকানা: {address}\n"
                f"🛒 পণ্য: {product}\n\n"
                f"অর্ডারটি নিশ্চিত করতে চান? (হ্যাঁ / না)"
            )
        return (
            f"Order summary:\nName: {name}\nPhone: {phone}\nAddress: {address}\n"
            f"Product: {product}\n\nConfirm the order? (yes / no)"
        )

    @classmethod
    def _execute_create_order(cls, conversation, session, collected, context) -> dict:
        """Run the create_order tool with the collected data."""
        from .tools import tool_create_order

        pid = collected.get("pid", "")
        quantity = int(collected.get("quantity") or 1)

        try:
            result = tool_create_order(
                user=conversation.user,
                conversation=conversation,
                customer_name=collected.get("customer_name", ""),
                customer_phone=collected.get("customer_phone", ""),
                customer_address=collected.get("customer_address", ""),
                customer_city=getattr(conversation, "customer_city", "") or "",
                delivery_zone="inside_dhaka",
                items=[{"pid": pid, "quantity": quantity}],
            )
            if not result.get("error"):
                # Persist the collected customer fields on the conversation so
                # future (repeat) orders prefill correctly.
                try:
                    from .tools import tool_update_customer
                    tool_update_customer(
                        conversation,
                        name=collected.get("customer_name") or None,
                        phone=collected.get("customer_phone") or None,
                        address=collected.get("customer_address") or None,
                    )
                except Exception as exc:
                    logger.warning("update_customer persist failed conv=%s: %s", conversation.pk, exc)
            # Workflow completed -> reset for the next flow
            reset_session(conversation)
            return result
        except Exception as exc:
            logger.exception("create_order tool failed conv=%s", conversation.pk)
            reset_session(conversation)
            return {"error": str(exc)}

    @classmethod
    def _order_created_response(cls, result, context, lang) -> dict:
        if result.get("error"):
            details = result.get("details")
            msg = str(details if details else result["error"])
            if lang == "bn":
                return {"text": f"দুঃখিত, অর্ডারটি তৈরি করা যায়নি। কারণ: {msg[:200]}"}
            return {"text": f"Sorry, I couldn't create the order: {msg[:200]}"}

        order_id = result.get("order_id", "")
        total = result.get("total", "")
        if lang == "bn":
            return {"text": f"✅ আপনার অর্ডারটি তৈরি হয়েছে!\nঅর্ডার আইডি: {order_id}\nমোট: {total} টাকা"}
        return {"text": f"✅ Your order has been created!\nOrder ID: {order_id}\nTotal: {total}"}
