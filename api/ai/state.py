"""
State Machine & Workflow Engine (P1-9 .. P1-13).

Persistence lives in context.models.SessionContext (one per conversation).
The engine consumes messages while a workflow is active, collects fields
turn-by-turn, validates transitions, and executes the final tool calls.
"""
import logging
import re

from django.utils import timezone

logger = logging.getLogger(__name__)

# Zombie-flow guard: an order workflow that has seen no activity for this long
# is abandoned on the next message so a stale session can never hijack a
# brand-new conversation (customer presence ≠ ordering intent).
WORKFLOW_TIMEOUT = timezone.timedelta(minutes=30)

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
            # Full product name mentioned verbatim → decisive.
            for p in products:
                name = (p.name or "").strip().lower()
                if name and name in content:
                    return {"pid": p.pid, "name": p.name, "price": str(p.price),
                            "discounted_price": str(p.discounted_price) if p.discounted_price else None,
                            "stock": p.stock_quantity, "in_stock": p.stock_quantity > 0}
            # Unique word hits only: "ninjar" isolates Ninjar Gel while the
            # shared word "gel" matches several products and stays ambiguous.
            words = [
                w for w in re.split(r"[\s,.;:!?]+", content)
                if len(w) >= 3 and w not in WorkflowEngine._QUICK_SEARCH_STOPWORDS
            ]
            unique = {}
            for w in words:
                hit = [p for p in products
                       if p.name and w in p.name.lower()]
                if len(hit) == 1 and hit[0].pid not in unique:
                    unique[hit[0].pid] = hit[0]
            if len(unique) == 1:
                p = next(iter(unique.values()))
                return {"pid": p.pid, "name": p.name, "price": str(p.price),
                        "discounted_price": str(p.discounted_price) if p.discounted_price else None,
                        "stock": p.stock_quantity, "in_stock": p.stock_quantity > 0}
    except Exception as exc:
        logger.warning("History product resolution failed: %s", exc)
    return None


def _resolve_deixis(context, text):
    """'eita / এইটা / this one' → the last product actually discussed in recent
    history (either role), newest mention first. A message naming several
    products (e.g. a catalog listing) is skipped — the customer's own
    single-product mention is more specific."""
    try:
        from back.models import Product

        user = getattr(getattr(context, "conversation", None), "user", None)
        history = getattr(context, "history", None) or []
        if not user or not history:
            return None
        products = list(Product.objects.filter(user=user, status=True)[:50])
        stop = WorkflowEngine._QUICK_SEARCH_STOPWORDS
        for h in reversed(history[-30:]):
            content = (h.get("content") or "").lower()
            if not content:
                continue
            # Full product name mentioned verbatim → decisive.
            for p in products:
                name = (p.name or "").strip().lower()
                if name and name in content:
                    return {"pid": p.pid, "name": p.name, "price": str(p.price),
                            "discounted_price": str(p.discounted_price) if p.discounted_price else None,
                            "stock": p.stock_quantity, "in_stock": p.stock_quantity > 0}
            # Unique word hits only: "ninjar" isolates Ninjar Gel while the
            # shared word "gel" matches several products and stays ambiguous.
            words = [
                w for w in re.split(r"[\s,.;:!?]+", content)
                if len(w) >= 3 and w not in stop
            ]
            unique = {}
            for w in words:
                hit = [p for p in products
                       if p.name and w in p.name.lower()]
                if len(hit) == 1 and hit[0].pid not in unique:
                    unique[hit[0].pid] = hit[0]
            if len(unique) == 1:
                p = next(iter(unique.values()))
                return {"pid": p.pid, "name": p.name, "price": str(p.price),
                        "discounted_price": str(p.discounted_price) if p.discounted_price else None,
                        "stock": p.stock_quantity, "in_stock": p.stock_quantity > 0}
        return None
    except Exception as exc:
        logger.warning("Deixis resolution failed: %s", exc)
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


def _ask_for_missing_fields(missing, lang):
    """Ask for every still-missing order field in one message (never
    re-asks a name/phone/address the customer already provided)."""
    if lang == "bn":
        parts = " ও ".join(FIELD_LABELS[f] for f in missing)
        return f"অর্ডারটি সম্পন্ন করতে {parts} জানাবেন? 🛒"
    parts = ", ".join(FIELD_LABELS_EN[f] for f in missing)
    return f"To complete your order, could you tell me {parts}?"


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
    # Bot questions that actually ask the customer to order ("নিতে চান?",
    # "অর্ডার করব?"). A bare "ok" after ANY other question ("আরেকটা দেখতে
    # চান?", "ছবি পাঠাব?") must NOT start an order flow.
    ORDER_QUESTION_RE = re.compile(
        r"(অর্ডার|নিতে চান|আগ্রহী|নিবেন|নেবেন|নিন কি|কিনবেন|কিনতে চান|"
        r"interested|order|want (it|this|that|one)|দরকার|কোনটা নিবেন|"
        r"নেবেন কি|কিনবেন কি|order korbo|নিবো কি|নিব কি)", re.IGNORECASE
    )
    # Browse-y verbs — a short message containing one of these ("tetuler achar
    # den", "bedbug spray dekhan") is browsing, never a name/address answer.
    BROWSE_VERB_RE = re.compile(
        r"(den|diben|din|দেন|দিন|দিবেন|দেবেন|দাও|দেখান|dekhan|dekhao|"
        r"dekhaben|dekha|দেখা|ছবি|pic|photo|pathan|পাঠান|দাম|dam|koto|"
        r"কত\b|আছে|ache|stock|দেখতে|দেখুন)", re.IGNORECASE
    )
    # Narrow visual-browse signal for the flow gates: "pic den", "ছবি দেন",
    # "dekhan" mean the customer is browsing, NOT answering the order step.
    # Plain "5 pcs den" must NOT match ("den" alone is overloaded — a quantity
    # answer), or the quantity silently gets lost to the LLM.
    _BROWSE_PAUSE_RE = re.compile(
        r"(pic|photo|images?|ছবি|dekhi|দেখি|dekha|দেখা|dekhan|দেখান|"
        r"dekhao|দেখাও|dekhaben|দেখাবেন|dekhte|দেখতে|দেখুন|catalog|"
        r"ক্যাটালগ)", re.IGNORECASE
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
        r"\s*(?:pcs|pc|pieces?|piece|kg|কেজি|পিস|টা|টি|ta|খানা)(?!\w)", re.IGNORECASE
    )
    _BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

    # ------------------------------------------------------------------
    # Entry point: called by the orchestrator for every incoming message
    # ------------------------------------------------------------------

    @classmethod
    def handle_message(cls, conversation, text, context) -> dict | None:
        """If an order workflow is active, consume ONLY order-ish messages.

        The workflow is a pause-able step — it must never hijack unrelated
        messages (a customer asking for pictures, prices or the catalog is
        NOT answering the phone field). Returns a response dict {"text": ...}
        when the message is consumed by the flow, or None when the normal
        orchestrator pipeline should answer it instead.
        """
        session = get_session(conversation)
        if session.state == "idle":
            cls._remember_order_hint(conversation, text, context)
            return None

        # Zombie flows: a workflow idle for too long is abandoned so a session
        # started hours/days ago can never swallow the next conversation.
        if session.updated_at and (timezone.now() - session.updated_at) > WORKFLOW_TIMEOUT:
            reset_session(conversation)
            return None

        intent = getattr(getattr(context, "intent", None), "name", "")
        text_n = (text or "").strip()

        # A question while collecting data is NOT a field answer — let the AI
        # answer it; the pending field stays pending for the next message.
        if cls.QUESTION_RE.search(text_n):
            return None

        digits = re.sub(r"\D", "", text_n)

        if session.current_workflow == "create_order":
            if intent == "FRUSTRATION":
                reset_session(conversation)
                return None
            # The flow owns its decision states: a product-selection answer, a
            # confirmation, or a size answer is ALWAYS consumed by the flow —
            # even when the 5-second batch timer merged several rapid messages
            # into one longer text ("Bait powder ta\n5 pcs"). Letting the LLM
            # improvise these steps is how orders lose their product/quantity
            # ("didn't understand the product" loops).
            if session.state in (
                "awaiting_product_selection",
                "awaiting_confirmation",
                "awaiting_variation",
            ):
                # Visual-browse requests ("tetuler achar er pic den") pause the
                # flow — the customer is browsing, not answering the order step;
                # the pending step stays for the next order-ish message.
                if cls._BROWSE_PAUSE_RE.search(text_n):
                    return None
                return cls._handle_order_step(conversation, session, text_n, context)
            # Details-looking answers ("01712345678", "Mirpur 10, 017...") always
            # belong to the flow — even if the classifier called them a product
            # search. Names/addresses are short bare answers without browse
            # verbs ("amar nam Jubayer", "Mirpur 10 kajipara").
            if len(digits) >= 10:
                return cls._handle_order_step(conversation, session, text_n, context)
            if intent in ("UNKNOWN", "SMALL_TALK", "SEARCH_PRODUCT") and (
                len(text_n.split()) <= 4 and not cls._BROWSE_PAUSE_RE.search(text_n)
            ):
                return cls._handle_order_step(conversation, session, text_n, context)
            # Longer messages can still be order answers when they carry a
            # product or quantity signal ("Bait powder ta 5 pcs") — only
            # browse requests ("tetuler achar er pic den") stay paused.
            if session.state == "awaiting_details" and not cls._BROWSE_PAUSE_RE.search(text_n):
                if cls._parse_quantity(text_n) or cls._product_mention(conversation, text_n):
                    return cls._handle_order_step(conversation, session, text_n, context)

        # Intents that are clearly NOT order-field answers — let the pipeline
        # answer (catalog, images, prices, stock, complaints...). The pending
        # flow stays paused for the next order-ish message. Frustration drops
        # the pending order entirely so we never keep nagging a vexed customer.
        BYPASS_INTENTS = {
            "SEND_IMAGES", "CATALOG", "SEARCH_PRODUCT", "ASK_STOCK",
            "CHECK_ORDER", "CANCEL_ORDER", "RETURN_PRODUCT", "ASK_DELIVERY",
            "ASK_PAYMENT", "ASK_PRICE", "ASK_DETAILS", "COMPARE_PRODUCTS",
            "NEGOTIATE", "FRUSTRATION", "HUMAN_SUPPORT", "ESCALATION",
            "ASK_FAQ", "BILLING_QUERY", "UPGRADE_PLAN", "STORE_SYNC",
            "ANALYTICS_QUERY", "CONTENT_REQUEST", "RECOMMEND",
            "GREETING", "SMALL_TALK",
        }
        if intent in BYPASS_INTENTS:
            if intent == "FRUSTRATION":
                reset_session(conversation)
            return None

        if session.current_workflow == "create_order":
            return cls._handle_order_step(conversation, session, text_n, context)
        return None

    # Tokens that carry no product meaning — an order message made ONLY of
    # these ("ami toh 2 pcs order korlam") must never resolve to a catalog
    # product via quick search ("pcs" matches "Fly Glue Trap (10 Pcs)").
    _QUICK_SEARCH_STOPWORDS = frozenset({
        "ami", "amar", "amr", "tumi", "apni", "toh", "tah", "but", "and",
        "the", "er", "or", "ar", "ki", "koto", "korte", "kore", "korbo",
        "korlam", "korbe", "kori", "করব", "করলাম", "করবে", "চাই", "chai",
        "den", "diben", "দেন", "দিবেন", "bhai", "vai", "ভাই", "bhaiya",
        "ভাইয়া", "eita", "eta", "ei", "এটা", "এইটা", "oid", "ota", "na",
        "না", "ok", "okay", "hobe", "hbe", "হবে", "order", "অর্ডার", "pcs",
        "pc", "piece", "pieces", "পিস", "টা", "টি", "ta", "kg", "কেজি",
        "2", "3", "4", "5", "amar", "jodi", "jadi", "যা", "dite", "দিতে",
        "eita", "eta", "aita", "ei", "oi", "এইটা", "এটা", "ওটা", "সেটা",
    })

    _DEIXIS_RE = re.compile(
        r"(eita|aita|eta|ei|oi|এইটা|এটা|ওটা|সেটা|ওই|the one|this one|"
        r"that one|same|একই)", re.IGNORECASE
    )

    @classmethod
    def _remember_order_hint(cls, conversation, text, context):
        """Capture (product, qty) from NON-order messages ("5 pcs bedbug spray
        ta?", "400 kore rakhen") so a later "ok order korbo" carries the
        product AND quantity the customer was discussing. Only fresh,
        uniquely-resolved hints are kept — never guesses from stopwords."""
        try:
            from .tools import parse_focus_products

            session = get_session(conversation)
            focus = parse_focus_products(getattr(conversation, "current_product", "") or "")
            selected = resolve_product_reference(text, focus) if focus else None
            if selected is None:
                found = cls._quick_catalog_search(conversation.user, text)
                if len(found) == 1 and found[0].get("pid"):
                    selected = found[0]
                elif len(found) > 1:
                    tokens = [
                        t for t in re.split(r"[\s,.;:!?]+", text.lower())
                        if len(t) >= 3 and t not in cls._QUICK_SEARCH_STOPWORDS
                    ]
                    unique = {}
                    for t in tokens:
                        hits = [p for p in found if t in (p.get("name") or "").lower()]
                        if len(hits) == 1 and hits[0].get("pid") not in unique:
                            unique[hits[0]["pid"]] = hits[0]
                    if len(unique) == 1:
                        selected = next(iter(unique.values()))
            qty = cls._parse_quantity(text)
            if not (selected or qty):
                return
            # Merge with the previous hint: "bedbug spray ta?" (product) then
            # "5pcs" (qty) then "400 kore" (nothing) must accumulate into one
            # {product, qty} — never overwrite the product with an empty hint.
            old = (session.collected_data or {}).get("_hint") or {}
            hint = {}
            if selected:
                hint.update(pid=selected.get("pid", ""),
                            product_name=selected.get("name", ""))
            elif old.get("pid"):
                hint.update(pid=old.get("pid", ""),
                            product_name=old.get("product_name", ""))
            if qty:
                hint["quantity"] = qty
            elif old.get("quantity"):
                hint["quantity"] = old["quantity"]
            hint["ts"] = timezone.now().isoformat()
            collected = dict(session.collected_data or {})
            collected["_hint"] = hint
            session.collected_data = collected
            session.save(update_fields=["collected_data", "updated_at"])
            logger.info(
                "Order hint captured conv=%s product=%s qty=%s",
                conversation.pk, hint.get("product_name"), hint.get("quantity"),
            )
        except Exception as exc:
            logger.warning("Order hint capture failed conv=%s: %s", conversation.pk, exc)

    @classmethod
    def _quick_catalog_search(cls, user, text) -> list:
        """Best-effort catalog lookup for order messages that name no prior
        focus product ("আমের আচার এক কেজি অর্ডার দিব"). Reuses the full
        search pipeline (variations, Bengali transliteration, prefixes).

        Messages containing only generic order-speak ("ami toh 2 pcs order
        korlam") resolve to NOTHING — guessing a product from stopwords is
        how wrong-product orders happen."""
        if not text or not user:
            return []
        tokens = [
            t for t in re.split(r"[\s,.;:!?/]+", text.lower())
            if t and t not in cls._QUICK_SEARCH_STOPWORDS
        ]
        if not tokens or all(len(t) <= 2 or t.isdigit() for t in tokens):
            return []
        try:
            from .tools import tool_search_products
            out = tool_search_products(user, text, limit=8)
            return list(out.get("products") or [])
        except Exception:
            logger.exception("quick catalog search failed")
            return []

    @classmethod
    def _product_mention(cls, conversation, text) -> dict | None:
        """Resolve a product reference in the message (focus first, then
        catalog quick-search) — used by the flow gates to decide whether a
        longer message is an order answer or a browse request."""
        try:
            from .tools import parse_focus_products
            focus = parse_focus_products(getattr(conversation, "current_product", "") or "")
            sel = resolve_product_reference(text, focus) if focus else None
            if sel is None:
                found = cls._quick_catalog_search(conversation.user, text)
                if len(found) == 1:
                    sel = found[0]
            return sel
        except Exception:
            return None

    @classmethod
    def start_order_flow(cls, conversation, context) -> dict | None:
        """Start the CREATE_ORDER workflow (called when intent==CREATE_ORDER).

        Resolution order for the product:
          1. explicit reference in the message ("jolpai", "আমের আচার")
          2. deixis ("eita", "এইটা") → last product actually discussed
          3. repeat-order signal ("আবার", "again") → last order's product
          4. last product mentioned by the customer in conversation history
          5. exactly one focused product
          6. ambiguous focus (several products) → ask which one
          7. nothing resolvable → ask for a product name (never falls through
             to LLM improvisation — orders are created only via this flow)

        If all required customer fields are already known, shows the order
        summary and asks for confirmation before creating it.
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

        # Fresh (product, qty) hint from a recent non-order message ("5 pcs
        # bedbug spray ta?") — a later "ok order korbo" carries them over.
        hint = None
        try:
            _session = get_session(conversation)
            _hint = (_session.collected_data or {}).get("_hint") or {}
            if _hint.get("ts"):
                from datetime import datetime as _dt
                ts = _dt.fromisoformat(_hint["ts"])
                if ts.tzinfo is None:
                    ts = timezone.make_aware(ts, timezone.get_current_timezone())
                if timezone.now() - ts <= timezone.timedelta(minutes=20):
                    hint = _hint
            cd = dict(_session.collected_data or {})
            if cd.pop("_hint", None) is not None:
                _session.collected_data = cd
                _session.save(update_fields=["collected_data"])
        except Exception:
            hint = None

        # 1. Explicit reference in the message
        selected = resolve_product_reference(text, focus) if focus else None
        # 1b. The message NAMES a product but it isn't in focus ("ami Ninjar Gel
        #     order korbo" while focus holds other search results) — quick-search
        #     the catalog; only a unique hit is decisive.
        if selected is None:
            found = cls._quick_catalog_search(conversation.user, text)
            if len(found) == 1 and found[0].get("pid"):
                selected = found[0]
            elif len(found) > 1:
                # A shared word ("gel") inflates the result set; a message token
                # that matches exactly ONE returned product ("ninjar") decides.
                tokens = [
                    t for t in re.split(r"[\s,.;:!?]+", text.lower())
                    if len(t) >= 3 and t not in cls._QUICK_SEARCH_STOPWORDS
                ]
                unique = {}
                for t in tokens:
                    hits = [p for p in found if t in (p.get("name") or "").lower()]
                    if len(hits) == 1 and hits[0].get("pid") not in unique:
                        unique[hits[0]["pid"]] = hits[0]
                if len(unique) == 1:
                    selected = next(iter(unique.values()))
        # 2. Deixis ("eita order korbo", "এইটা") → the last product actually
        #    discussed, before repeat/history so a stale focus never wins.
        if selected is None and cls._DEIXIS_RE.search(text):
            selected = _resolve_deixis(context, text)
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
        # 4. Last product discussed in history
        if selected is None and focus:
            selected = _resolve_from_history(context)
        # 4.5. Order hint — the product the customer was just negotiating about
        #      ("bedbug spray ta? … 5pcs … 400 kore") is more current than
        #      older focus items.
        if selected is None and hint and hint.get("pid"):
            selected = {"pid": hint["pid"], "name": hint.get("product_name", "")}
        if not quantity and hint and hint.get("quantity"):
            quantity = int(hint["quantity"])

        # 5/6. Focus products — exactly one focused product, or several (ask)
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
            # 7. Nothing resolvable — ask for a product name.
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

        # Multi-variation product (e.g. baby clothing sizes): capture the
        # size BEFORE delivery details — the external order API requires a
        # variation_id and the customer knows their size best at this point.
        variations = cls._product_variations(conversation, pid)
        if len(variations) >= 2:
            cls._apply_variation(collected, text, variations)
            if not collected.get("variation_id"):
                session = get_session(conversation)
                session.collected_data = collected
                session.current_workflow = "create_order"
                session.state = "awaiting_variation"
                session.save()
                opts = ", ".join(str(v.get("name") or v.get("variation_id", "?")) for v in variations)
                return {"text": (
                    f"{selected.get('name', '')} — কোন সাইজটা নেবেন? বিকল্প: {opts}"
                    if lang == "bn" else
                    f"Which {selected.get('name', '')} size would you like? Options: {opts}"
                )}

        missing = [f for f in ORDER_FIELDS if not collected.get(f)]

        session = get_session(conversation)
        if not missing:
            # Everything known -> show the summary and confirm anyway. A silent
            # one-turn create is how wrong quantities/products slip through
            # ("2 ta" parsed as 1, stale focus picked the wrong product).
            session.collected_data = collected
            session.current_workflow = "create_order"
            session.workflow_step = 0
            session.state = "awaiting_confirmation"
            session.pending_confirmation = collected
            session.verified = True
            session.save()
            return {"text": cls._order_summary_text(collected, context, lang)}

        # Start collection
        session.collected_data = collected
        session.current_workflow = "create_order"
        session.workflow_step = 0
        session.state = "awaiting_details"
        session.save()
        return {"text": _ask_for_missing_fields(missing, _lang(conversation))}

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
    def _product_variations(cls, conversation, pid) -> list:
        """Return the variation list for a product (from focused products, or a
        live external lookup when the product isn't in focus — e.g. repeat
        orders). Empty list when the product has no variations."""
        try:
            from .tools import parse_focus_products
            for fp in parse_focus_products(getattr(conversation, "current_product", "") or ""):
                if str(fp.get("pid")) == str(pid):
                    return cls._dedupe_variations(fp.get("variations") or [])
        except Exception:
            logger.exception("variation lookup failed conv=%s", getattr(conversation, "pk", "?"))
        try:
            from api.products.factory import get_active_source, get_provider, is_external
            source = get_active_source(conversation.user)
            if source and source.mode == "live" and is_external(conversation.user):
                r = get_provider(conversation.user).get_product(pid)
                if r:
                    return cls._dedupe_variations(r.get("variations") or [])
        except Exception:
            logger.exception("live variation lookup failed conv=%s", getattr(conversation, "pk", "?"))
        return []

    @staticmethod
    def _dedupe_variations(variations) -> list:
        out, seen = [], set()
        for v in variations or []:
            vid = v.get("variation_id")
            if vid is not None and str(vid) not in seen:
                seen.add(str(vid))
                out.append(v)
        return out

    @classmethod
    def _select_variation(cls, text, variations) -> dict | None:
        """Match a variation (size/color) from free text. Returns the variation
        dict or None. Matches exact/substring name and compact forms with
        Bengali digits normalised ("2-3 years" ↔ "২-৩", "0-6m", "6-12 months")."""
        if not text or not variations:
            return None
        t = (text or "").lower()
        t_compact = re.sub(r"[\s\-–—()./]+", "", t).translate(cls._BN_DIGITS)
        for v in variations:
            name = str(v.get("name") or "").strip().lower()
            if not name:
                continue
            if name in t:
                return v
            compact = re.sub(r"\b(months|years|month|year|mths|ages?)\b", "", name)
            compact = re.sub(r"[\s\-–—()./]+", "", compact).translate(cls._BN_DIGITS)
            if compact and compact in t_compact:
                return v
        return None

    @classmethod
    def _apply_variation(cls, collected, text, variations) -> bool:
        """Match a variation name in ``text`` and store it on ``collected``.
        Returns True when a variation was chosen."""
        if not variations or collected.get("variation_id"):
            return False
        var = cls._select_variation(text, variations)
        if var is None:
            return False
        collected["variation_id"] = str(var.get("variation_id") or "")
        collected["variation_name"] = str(var.get("name") or "")
        return True

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

            # Multi-variation product chosen here → ask for the size first.
            variations = cls._product_variations(conversation, selected.get("pid", ""))
            if len(variations) >= 2:
                cls._apply_variation(collected, text, variations)
                if not collected.get("variation_id"):
                    session.collected_data = collected
                    session.state = "awaiting_variation"
                    session.save()
                    opts = ", ".join(str(v.get("name") or v.get("variation_id", "?")) for v in variations)
                    return {"text": (
                        f"{selected.get('name', '')} — কোন সাইজটা নেবেন? বিকল্প: {opts}"
                        if lang == "bn" else
                        f"Which {selected.get('name', '')} size would you like? Options: {opts}"
                    )}

            missing = [f for f in ORDER_FIELDS if not collected.get(f)]
            if missing:
                session.workflow_step = 0
                session.state = "awaiting_details"
                session.save()
                if lang == "bn":
                    return {"text": (
                        f"ঠিক আছে, {selected.get('name', '')}! "
                        f"{_ask_for_missing_fields(missing, lang)}"
                    )}
                return {"text": (
                    f"Great, {selected.get('name', '')}! "
                    f"{_ask_for_missing_fields(missing, lang)}"
                )}
            summary = cls._order_summary_text(collected, context, lang)
            session.pending_confirmation = collected
            session.state = "awaiting_confirmation"
            session.verified = True
            session.save()
            return {"text": summary}

        if session.state == "awaiting_variation":
            # Size/color collection for multi-variation products.
            if cls.CANCEL_RE.search(text or ""):
                reset_session(conversation)
                return {"text": "অর্ডারটি বাতিল করা হয়েছে। আরও কিছুতে সাহায্য করতে পারি?" if lang == "bn"
                        else "Order cancelled. Can I help with anything else?"}

            qty = cls._parse_quantity(text)
            if qty:
                collected["quantity"] = qty
                session.collected_data = collected
                session.save()
                variations = cls._product_variations(conversation, collected.get("pid", ""))
                opts = ", ".join(str(v.get("name") or v.get("variation_id", "?")) for v in variations)
                return {"text": (
                    f"ঠিক আছে, {qty} পিস! এখন সাইজটা বলুন — {opts}"
                    if lang == "bn" else
                    f"Great, {qty} pieces! Now the size — options: {opts}"
                )}

            variations = cls._product_variations(conversation, collected.get("pid", ""))
            if not cls._apply_variation(collected, text, variations):
                opts = ", ".join(str(v.get("name") or v.get("variation_id", "?")) for v in variations)
                return {"text": (
                    f"সাইজটা বুঝতে পারিনি। বিকল্পগুলো: {opts}"
                    if lang == "bn" else
                    f"Sorry, I didn't catch the size. Options: {opts}"
                )}

            session.collected_data = collected
            missing = [f for f in ORDER_FIELDS if not collected.get(f)]
            if missing:
                session.workflow_step = ORDER_FIELDS.index(missing[0])
                session.state = "awaiting_details"
                session.save()
                return {"text": _ask_for_missing_fields(missing, lang)}
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
                # Product changed → the old variation is invalid; re-ask the
                # size when the new product has multiple variations.
                variations = cls._product_variations(conversation, swap.get("pid", ""))
                if len(variations) >= 2:
                    collected.pop("variation_id", None)
                    collected.pop("variation_name", None)
                    session.collected_data = collected
                    session.state = "awaiting_variation"
                    session.save()
                    opts = ", ".join(str(v.get("name") or v.get("variation_id", "?")) for v in variations)
                    return {"text": (
                        f"ঠিক আছে, এখন {swap.get('name')} অর্ডার করছি! কোন সাইজটা নেবেন? বিকল্প: {opts}"
                        if lang == "bn" else
                        f"Sure, {swap.get('name')} it is! Which size would you like? Options: {opts}"
                    )}
                collected.pop("variation_id", None)
                collected.pop("variation_name", None)
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
                        return {"text": _ask_for_missing_fields(missing, lang)}
                    summary = cls._order_summary_text(collected, context, lang)
                    session.pending_confirmation = collected
                    session.state = "awaiting_confirmation"
                    session.verified = True
                    session.save()
                    return {"text": summary}

            # Validate the collected value so "ok"/"yes" isn't stored as a name
            problem = cls._validate_field_value(field, value, lang)
            if problem:
                retries = int((collected or {}).get("_field_retries", 0))
                if retries >= 2:
                    # Escape hatch: after repeated failed attempts for the same
                    # field, stop the loop instead of nagging forever.
                    reset_session(conversation)
                    if lang == "bn":
                        return {"text": (
                            "ঠিক আছে, অর্ডারটা পরে নিতে পারি। আগে প্রোডাক্ট দেখে নিন — "
                            "'ছবি দেন' লিখলে ছবি পাঠাই, আবার অর্ডার করতেই চাইলে বলুন!"
                        )}
                    return {"text": (
                        "No problem — we can order later. Browse the catalog first, "
                        "and just tell me when you're ready to order!"
                    )}
                collected["_field_retries"] = retries + 1
                session.collected_data = collected
                session.save()
                return {"text": problem}

            collected[field] = value
            collected["_field_retries"] = 0
            session.collected_data = collected

            missing = [f for f in ORDER_FIELDS if not collected.get(f)]
            if missing:
                session.workflow_step += 1
                session.state = "awaiting_details"
                session.save()
                return {"text": _ask_for_missing_fields(missing, lang)}

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
            # The customer is refining the pending order instead of confirming
            # ("5 pcs", "Bait powder ta", "eta 2ta den") — update the pending
            # data and re-show the summary so what gets confirmed is what the
            # customer just said. Never silently confirm a stale summary.
            pending = dict(session.pending_confirmation or collected)
            changed = False
            sel = cls._product_mention(conversation, text)
            if sel and sel.get("pid") and sel.get("pid") != pending.get("pid"):
                pending["pid"] = sel.get("pid", "")
                pending["product_name"] = sel.get("name", "")
                pending.pop("variation_id", None)
                pending.pop("variation_name", None)
                changed = True
            qty = cls._parse_quantity(text)
            if qty:
                pending["quantity"] = qty
                changed = True
            if changed:
                session.pending_confirmation = pending
                session.collected_data = pending
                session.verified = True
                session.save()
                return {"text": cls._order_summary_text(pending, context, lang)}
            if lang == "bn":
                return {"text": "আপনি কি অর্ডারটি নিশ্চিত করতে চান? (হ্যাঁ / না)"}
            return {"text": "Do you want to confirm the order? (yes / no)"}

        return {"text": "দুঃখিত, আমি বুঝতে পারিনি। আবার বলবেন?" if lang == "bn"
                else "Sorry, I didn't catch that. Could you repeat?"}

    @classmethod
    def _order_totals(cls, conversation, collected) -> tuple:
        """(unit_price, delivery_charge, total) computed exactly like the
        create_order tool computes them — the confirm summary always matches
        the order that will actually be created."""
        from decimal import Decimal
        from back.models import Product
        from context.models import StoreConfig

        unit = Decimal("0")
        pid = collected.get("pid", "")
        # External/live products aren't in the local Product table — resolve
        # the effective price from the focus-products cache (carries the same
        # price create_order will charge) without a slow live ERP call.
        if not unit and pid:
            try:
                from .tools import parse_focus_products
                focus = parse_focus_products(getattr(conversation, "current_product", "") or "")
                for fp in focus:
                    if fp.get("pid") == pid:
                        unit = Decimal(str(fp.get("price") or 0))
                        break
            except Exception:
                pass
        if not unit:
            try:
                product = Product.objects.filter(
                    user=conversation.user, pid=pid, status=True
                ).first()
                if product:
                    unit = product.discounted_price or product.price or Decimal("0")
            except Exception:
                pass
        delivery = Decimal("0")
        try:
            store = StoreConfig.objects.filter(user=conversation.user).first()
            if store:
                zone = collected.get("delivery_zone", "inside_dhaka")
                delivery = (
                    store.delivery_charge_inside if zone == "inside_dhaka"
                    else store.delivery_charge_outside
                ) or Decimal("0")
        except Exception:
            pass
        qty = int(collected.get("quantity") or 1)
        return unit, delivery, (unit * qty) + delivery

    @classmethod
    def _order_summary_text(cls, collected, context, lang) -> str:
        name = collected.get("customer_name", "")
        phone = collected.get("customer_phone", "")
        address = collected.get("customer_address", "")
        product = collected.get("product_name") or ""
        variation = collected.get("variation_name") or ""
        if variation:
            product = f"{product} ({variation})"
        qty = int(collected.get("quantity") or 1)
        unit, delivery, total = cls._order_totals(context.conversation, collected)
        conv = getattr(context, "conversation", None)
        if lang == "bn":
            prod_line = f"🛒 পণ্য: {qty} × {product}"
            if unit:
                prod_line += f" ({unit:,.0f} টাকা/পিস)"
            lines = [
                f"আপনার অর্ডারের সারসংক্ষেপ:",
                f"👤 নাম: {name}",
                f"📞 মোবাইল: {phone}",
                f"📍 ঠিকানা: {address}",
                prod_line,
                f"ডেলিভারি চার্জ: {delivery:,.0f} টাকা",
                f"মোট: {total:,.0f} টাকা",
                "",
                "অর্ডারটি নিশ্চিত করতে চান? (হ্যাঁ / না)",
            ]
            return "\n".join(lines)
        prod_line = f"Product: {qty} × {product}"
        if unit:
            prod_line += f" ({unit:,.2f} each)"
        return (
            f"Order summary:\nName: {name}\nPhone: {phone}\nAddress: {address}\n"
            f"{prod_line}\nDelivery: {delivery:,.2f}\nTotal: {total:,.2f}\n\n"
            f"Confirm the order? (yes / no)"
        )

    @classmethod
    def _execute_create_order(cls, conversation, session, collected, context) -> dict:
        """Run the create_order tool with the collected data."""
        from .tools import tool_create_order

        pid = collected.get("pid", "")
        quantity = int(collected.get("quantity") or 1)

        # Duplicate guard: a still-pending order for the SAME product placed
        # moments ago is almost always a mis-click or a correction attempt —
        # creating a second one silently doubles the order. Surface the
        # existing order instead and let the customer decide.
        try:
            from back.models import Sale
            recent = Sale.objects.filter(
                conversation=conversation,
                status="pending",
                created_at__gte=timezone.now() - timezone.timedelta(minutes=30),
            ).prefetch_related("items")
            for s in recent:
                if s.items.filter(product_name__iexact=(collected.get("product_name") or "")).exists():
                    reset_session(conversation)
                    return {
                        "error": "duplicate",
                        "duplicate_of": s.oid,
                        "duplicate_amount": str(s.amount),
                    }
        except Exception:
            logger.exception("Duplicate-order check failed conv=%s", conversation.pk)

        try:
            result = tool_create_order(
                user=conversation.user,
                conversation=conversation,
                customer_name=collected.get("customer_name", ""),
                customer_phone=collected.get("customer_phone", ""),
                customer_address=collected.get("customer_address", ""),
                customer_city=getattr(conversation, "customer_city", "") or "",
                delivery_zone="inside_dhaka",
                items=[{
                    "pid": pid,
                    "quantity": quantity,
                    "variation_id": collected.get("variation_id") or None,
                }],
            )
            if not result.get("error"):
                # Remember the order so later turns ("আবার অর্ডার", "আমার অর্ডারটা")
                # answer from memory instead of a blank "didn't understand".
                try:
                    from .memory import MemoryManager
                    MemoryManager.store_fact(
                        conversation.user,
                        "last_order",
                        {
                            "oid": result.get("oid", ""),
                            "product": collected.get("product_name", ""),
                            "quantity": quantity,
                            "amount": str(result.get("total") or result.get("amount") or ""),
                        },
                        memory_type="preference",
                        confidence=1.0,
                        conversation=conversation,
                    )
                except Exception as exc:
                    logger.warning("Order memory store failed conv=%s: %s", conversation.pk, exc)
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
            # Product-resolution failures ("Product 'X' not found" — ERP hiccup,
            # stale pid) must NOT kill the flow: drop back to product selection
            # so the customer can re-pick and retry instead of losing the order.
            msg = str(result.get("details") or result.get("error", ""))
            if "not found" in msg.lower():
                lang = _lang(conversation)
                session.state = "awaiting_product_selection"
                session.save()
                return {
                    "error": "product_not_found",
                    "text": (
                        "দুঃখিত, পণ্যটি এই মুহূর্তে পাওয়া যাচ্ছে না। আবার কোন পণ্যটা নেবেন, একটার নাম বলুন?"
                        if lang == "bn" else
                        "Sorry, that product is unavailable right now. Which product would you like?"
                    ),
                }
            reset_session(conversation)
            return result
        except Exception as exc:
            logger.exception("create_order tool failed conv=%s", conversation.pk)
            reset_session(conversation)
            return {"error": str(exc)}
    @classmethod
    def _order_created_response(cls, result, context, lang) -> dict:
        if result.get("error") == "duplicate":
            oid = result.get("duplicate_of", "?")
            amount = result.get("duplicate_amount", "")
            if lang == "bn":
                return {"text": (
                    f"এই পণ্যটির একটি অর্ডার (আইডি: {oid}, মোট {amount} টাকা) আগেই "
                    f"পেন্ডিং আছে। ওটাই চূড়ান্ত করব, নাকি বাতিল করে নতুন করব? "
                    f"('বাতিল করো' লিখলেই বাতিল করে নতুন অর্ডার নেব)"
                )}
            return {"text": (
                f"You already have a pending order for this item (ID: {oid}, "
                f"total {amount}). Should I finalize that one, or cancel it and "
                f"place a new one?"
            )}
        if result.get("error") == "product_not_found":
            return {"text": result.get("text", "")}
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
