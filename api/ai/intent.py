"""
IntentDetector (P0-3): Rule-based intent classification, with optional LLM fallback.
"""
import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Intent taxonomy
# ---------------------------------------------------------------------------

INTENT_GROUPS = {
    "SEARCH_PRODUCT": ["product search", "browse", "looking for"],
    "ASK_PRICE": ["price query", "cost inquiry"],
    "ASK_STOCK": ["stock inquiry"],
    "COMPARE_PRODUCTS": ["product comparison"],
    "CREATE_ORDER": ["place order", "checkout"],
    "CHECK_ORDER": ["order status"],
    "CANCEL_ORDER": ["cancel order"],
    "RETURN_PRODUCT": ["return", "exchange"],
    "ASK_DETAILS": ["product details"],
    "ASK_DELIVERY": ["delivery query"],
    "ASK_PAYMENT": ["payment query"],
    "ASK_FAQ": ["faq", "policy"],
    "GREETING": ["greeting"],
    "SMALL_TALK": ["chit-chat"],
    "HUMAN_SUPPORT": ["human handoff"],
    "ESCALATION": ["angry", "escalation"],
    "BILLING_QUERY": ["billing"],
    "UPGRADE_PLAN": ["upgrade"],
    "STORE_SYNC": ["sync"],
    "ANALYTICS_QUERY": ["analytics"],
    "CONTENT_REQUEST": ["content generation"],
    "RECOMMEND": ["recommendation"],
    "AFFIRM": ["confirmation", "affirmative answer"],
    "PROVIDE_QUANTITY": ["quantity answer"],
    "STORE_INFO": ["store identity", "contact info"],
}

# AFFIRM (F1): an affirmative answer to the CURRENT bot question. Detection is
# stage-dependent — inside an order workflow these tokens confirm the step;
# outside one they are plain chit-chat answers (never a product search).
_AFFIRM_PATTERN = re.compile(
    r"^(?:"
    r"j+i+|hm+|hmm+|mm+|ha+h*|hae+|yes|yeah|yep|yup|ok|okay|sure|done|"
    r"confirmed|confirm|correct|alright|hobe|hbe|hoy|hoi|thik|"
    r"ho re (?:bhai|vai|ভাই)|are re|re bhai|"
    r"হ্যাঁ|হ্যা|হুম|হুঁ|অবশ্যই|ঠিক আছে|ঠিক|করছি|করুন|নিব|নিন|দিবেন|দেবেন|"
    r"অর্ডার করুন|অর্ডার কনফার্ম|অর্ডার কনফার্ম করুন"
    r")(?:\s*[!.।]*\s*)$",
    re.IGNORECASE,
)

# PROVIDE_QUANTITY (F1/F2): the customer answers with a quantity+unit ("2 pcs",
# "2 pack den", "৫টা"). Detected before any product-search re-interpretation.
_QUANTITY_TOKEN_RE = re.compile(
    r"(?:^|\s)(?P<qty>\d+|[০-৯]+|এক|দুই|তিন|চার|পাঁচ|one|two|three|four|five)"
    r"\s*(?:pcs|pc|pieces?|piece|pack|packs|টা|টি|ta|পিস|প্যাক|খানা|"
    r"kg|কেজি|gm|গ্রাম|কেজি)(?!\w)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

_INTENT_PATTERNS: list[tuple[str, re.Pattern, float]] = [
    # Small talk with a real request attached ("kemon achen bhai?"). Pure
    # chit-chat is handled by the early _SMALL_TALK_RE.fullmatch return.
    ("SMALL_TALK", re.compile(
        r"^(how are you|what('s|s) up|kemon|কেমন|ভাল|kmon|accho|আচ্ছা|"
        r"kmn achen|kemon achen|কেমন আছেন|valo achi|ভাল আছি|kemon acho|"
        r"ki khobor|কি খবর|তুমি কেমন|আপনি কেমন|"
        r"ok|okay|thanks|thank you|ধন্যবাদ|bye|goodbye|বিদায়)(?=\s|$)",
        re.IGNORECASE
    ), 0.75),

    # Greetings
    ("GREETING", re.compile(
        r"^(hi|hello|hey|hlw|helo|hllo|hy|yo|হাই|হ্যালো|হেলো|আসলামু|আসসালামু|সালাম|"
        r"hi there|good morning|good evening|good afternoon|howdy|wasup|"
        r"gm|gn)(?=\s|$)", re.IGNORECASE
    ), 1.0),

    # Bengali greeting (with suffix like আলাইকুম)
    ("GREETING", re.compile(
        r"^(আসসালামু আলাইকুম|সালামু আলাইকুম|আদাব|নমস্কার)", re.IGNORECASE
    ), 1.0),

    # Human support
    ("HUMAN_SUPPORT", re.compile(
        r"(human|agent|operator|real person|staff|ম্যানেজার|এজেন্ট|অপারেটর|"
        r"কথা বলব|মানুষের সাথে|সাপোর্ট|manush|manusher|মানুষ|"
        r"kotha bolte|কথা বলতে চাই)", re.IGNORECASE
    ), 0.9),

    # Escalation (angry / complaint)
    ("ESCALATION", re.compile(
        r"(complaint|angry|frustrated|terrible|worst|bad experience|"
        r"অভিযোগ|খুব খারাপ|ঠকানো|প্রতারিত)", re.IGNORECASE
    ), 0.8),

    # Order status (mentions of existing orders — must win over CREATE_ORDER)
    ("CHECK_ORDER", re.compile(
        r"(order status|where (is )?(my )?order|ট্র্যাক|ট্রাকিং|অর্ডার ট্র্যাক|"
        r"অর্ডার কোথায়|অর্ডার স্ট্যাটাস|order (id|number|tracking|ট্র্যাকিং)|"
        r"track (my )?order|order track|"
        r"(ager|purono|previous|old|amr|amar|my|last)\s+order|"
        r"order(s)?\s+(kothay|koi|sob|সব|কোথায়|কই)|order koto|order ki)",
        re.IGNORECASE
    ), 0.95),

    # Cancel order
    ("CANCEL_ORDER", re.compile(
        r"(cancel (my )?order|বাতিল|অর্ডার বাতিল|remove order|undo order)",
        re.IGNORECASE
    ), 0.9),

    # Return/Exchange
    ("RETURN_PRODUCT", re.compile(
        r"(return|exchange|refund|replace|ফেরত|রিটার্ন|রিফান্ড|এক্সচেঞ্জ|বদল)",
        re.IGNORECASE
    ), 0.85),

    # Delivery
    ("ASK_DELIVERY", re.compile(
        r"(shipping|delivery|ship|কুরিয়ার|ডেলিভারি|কবে পাব|কত দিন|শিপিং|"
        r"how long.*(take|arrive)|delivery (time|charge|cost|inside|outside))",
        re.IGNORECASE
    ), 0.9),

    # Payment
    ("ASK_PAYMENT", re.compile(
        r"(payment|pay|bkash|nagad|rocket|cash on delivery|COD|ক্যাশ অন|"
        r"পেমেন্ট|বিকাশ|নগদ|পেও|কিভাবে পেমেন্ট)", re.IGNORECASE
    ), 0.9),

    # Price
    ("ASK_PRICE", re.compile(
        r"(price|cost|how much|rate|দাম|কতো|কত টাকা|মূল্য|বাজেট|budget|"
        r"কম দামে|সস্তা|discount|discunt|ডিসকাউন্ট|কমান|negotiat|"
        r"টাকায়|টাকাই|takai|diben|কমে|দিবেন|কত(?!\s*স্টক)|"
        r"koto taka|koto takay|koto\b|kitne)", re.IGNORECASE
    ), 0.85),

    # Product details (higher priority than ASK_FAQ)
    ("ASK_DETAILS", re.compile(
        r"(tell me about|details? (of|about)|about the|describe|"
        r"what is|কি (হল|জিনিস)|বিস্তারিত|ডিটেইলস|সম্পর্কে বলুন|"
        r"কেমন|কীভাবে ব্যবহার)", re.IGNORECASE
    ), 0.85),

    # Stock
    ("ASK_STOCK", re.compile(
        r"(stock|available|in stock|out of stock|স্টক|স্টোক্‌|কত স্টক|"
        r"আছে কি|স্টোর আছে)", re.IGNORECASE
    ), 0.8),

    # Send product images ("photo pathan", "ছবি দেখান") — must win over
    # SEARCH_PRODUCT so the send_images tool actually runs.
    ("SEND_IMAGES", re.compile(
        r"\b(pics?|photos?|pictures?|images?|img)\b|ছবি|ফটো|photo|"
        r"picture", re.IGNORECASE
    ), 0.85),

    # Price negotiation / bargaining ("150 e den", "ektu komaw", "দাম কম").
    # Placed before CREATE_ORDER so a price offer ("150 e den") never becomes
    # an order flow.
    ("NEGOTIATE", re.compile(
        r"(\b\d{2,4}\b\s*(tk|taka|টাকা)?\s*(e|er|kore|diye|দিয়ে|করে)?\s*"
        r"(den|diben|din|dibena|দেন|দিন|দিবেন|দেবেন|দাও|দেয়া|দেওয়া|হবে|করা যাবে))|"
        r"(দাম কম|দামে কম|কমাও|কমান|কম দাম|dame kom|komaw|komo|ektu kom|আরেকটু কম|"
        r"discount (den|দেন|diben|দিবেন|দাও|koro)|loose|লুজ|barga|নিগোশিয়েট|"
        r"diben na|dibena)", re.IGNORECASE
    ), 0.88),

    # Frustration / abuse ("bal", "chudir vai", "dhat") — de-escalate, never argue.
    ("FRUSTRATION", re.compile(
        r"(bal|বাল|boka|বোকা|chudir|চুদির|gud|গুদ|magir|মাগির|madarchod|মাদারচোদ|"
        r"tor putki|তোর মাগি|তোর মায়ের|তোর বাপ|তোর মুখ|তোর গুদ|"
        r"dhat|ধাত|jhak|ঝাক|khoti|খটি|বেহুদা|fuck|shit|"
        r"nalayek|নালায়েক|pagan|পাগল|gali|গালি|মাইর|mair|idiot|ইডিয়ট|stupid|"
        r"wtf|wth|wt)", re.IGNORECASE
    ), 0.85),

    # Create order / buy — EXPRESSING buying intent only. Mere mentions of the
    # word "order" (e.g. "ager order", "order id", "order kothay") must NOT
    # trigger order creation — CHECK_ORDER above already wins those by
    # priority, but the negative lookahead protects against new phrasings.
    ("CREATE_ORDER", re.compile(
        r"(order(?!\s+(id|number|status|track|kothay|koi|ager|purono|previous|old|"
        r"sob|সব))\b|buy|purchase|অর্ডার|আর্ডার|"
        r"কিনতে চাই|কিনবো|কিনব|কিনি|নিতে চাই|নিবো|নেব|নিব|"
        r"অর্ডার দিতে|অর্ডার দিব|অর্ডার করব|অর্ডার করি|"
        r"(?<!দেখতে )(?<!জানতে )(?<!বলতে )(?<!শুনতে )(?<!পড়তে )(?<!বুঝতে )"
        r"চাই(?!\s*(দেখতে|জানতে|বলতে|শুনতে|পড়তে|বুঝতে))|লাগবে|দরকার|"
        r"academy|enroll)", re.IGNORECASE
    ), 0.8),

    # Compare
    ("COMPARE_PRODUCTS", re.compile(
        r"(compare|difference|vs |versus|কম্পেয়ার|তুলনা|difference between|"
        r"কোনটা ভাল)", re.IGNORECASE
    ), 0.85),

    # Catalog browse: "ki product ache?", "sob product dekhan" → full catalog
    # (search text + product cards). Placed before SEARCH_PRODUCT so generic
    # catalog phrasings don't collapse into a plain text-only search.
    ("CATALOG", re.compile(
        r"(catalog|catalogue|product list|all products|all items|sob product|"
        r"সব প্রোডাক্ট|সব পণ্য|সবগুলো|"
        r"ki (product|products|প্রোডাক্ট|পণ্য|জিনিস|জিনিসপত্র|মাল) (ache|আছে)|"
        r"ki ki (ache|আছে)|কি কি আছে|কি প্রোডাক্ট আছে|কি কি প্রোডাক্ট|"
        r"products? dekhan|প্রোডাক্ট দেখান|সব দেখান)", re.IGNORECASE
    ), 0.82),

    # Search product (generic product inquiry)
    ("SEARCH_PRODUCT", re.compile(
        r"(product|item|goods|show|have (any|some)|got|product|প্রোডাক্ট|পণ্য|"
        r"আছে|ache|দেখান|দেখতে চাই|dekhan|dekhao|dekhaben|dekha|products|items|"
        r"looking for|need|want|sell|sells|selling|খুঁজছি)", re.IGNORECASE
    ), 0.7),

    # FAQ / Knowledge base
    ("ASK_FAQ", re.compile(
        r"(policy|rule|rules|faq|how (does|can|do)|what (is|are)|"
        r"when (do|can|will)|where (can|do)|why (is|does)|can I|"
        r"tell me about|explain|"
        r"কোথায়|কথায়|kothay|কই|ঠিকানা)", re.IGNORECASE
    ), 0.6),

    # Billing / Plan
    ("BILLING_QUERY", re.compile(
        r"(bill|invoice|charge|credit|plan|price.*plan|monthly|subscription|"
        r"বিল|ইনভয়েস)", re.IGNORECASE
    ), 0.8),

    # Upgrade plan
    ("UPGRADE_PLAN", re.compile(
        r"(upgrade|downgrade|change plan|switch (to|plan)|আপগ্রেড|ডাউনগ্রেড|"
        r"প্ল্যান পরিবর্তন)", re.IGNORECASE
    ), 0.85),

    # Store sync
    ("STORE_SYNC", re.compile(
        r"(sync|syncing|connected store|woocommerce|shopify|"
        r"সিঙ্ক|সংযুক্ত দোকান)", re.IGNORECASE
    ), 0.8),

    # Analytics
    ("ANALYTICS_QUERY", re.compile(
        r"(sales (report|summary|today|yesterday)|revenue|analytics|"
        r"statistics|পরিসংখ্যান|বিক্রয়)", re.IGNORECASE
    ), 0.8),

    # Content request
    ("CONTENT_REQUEST", re.compile(
        r"(write|generate|create) (product )?(description|content|seo|meta|"
        r"বর্ণনা|বিষয়বস্তু)", re.IGNORECASE
    ), 0.8),

    # Recommendations
    ("RECOMMEND", re.compile(
        r"(recommend|suggest|popular|best.?seller|সাজেস্ট|রিকমেন্ডেড|"
        r"সাজেশন|popular|trending)", re.IGNORECASE
    ), 0.8),

    # Store identity ("nam ki apnar?", "phone number den", "address kothay?")
    # — must NOT fall into product search (R6/F5). Tight patterns only:
    # "Ei address e din" (order edit) must not match → address requires a
    # question/possession word ("kothay", "apnader", "your", "apnar").
    ("STORE_INFO", re.compile(
        r"(nam ki apnar|ki nam|ki nam tomader|apnader nam|apnar nam|"
        r"tumar nam|আপনার নাম|তোমার নাম|"
        r"phone number (den|ta)?|number (ta )?den|apnader (?:phone )?number|"
        r"apnar (?:phone )?number|phone (?:ta|no)?(?: ?den)?$|"
        r"hotline|হটলাইন|contact number|যোগাযোগ নম্বর|"
        r"apnader (?:delivery )?address|your (?:shop |store )?address|"
        r"address kothay|shop (?:er )?address|সম্পূর্ণ ঠিকানা কী|"
        r"দোকান কোথায়|স্টোর কোথায়|আপনাদের ঠিকানা|আপনার ঠিকানা কোথায়|"
        r"kothay achen|kothay acho|কোথায় আছেন)",
        re.IGNORECASE
    ), 0.9),
]

# Single-word/no-context small talk patterns
_SMALL_TALK_RE = re.compile(
    r"^(how are you|what('s|s) up|kemon|কেমন|ভাল|kmon|accho|আচ্ছা|"
    r"kmn achen|kemon achen|কেমন আছেন|valo achi|ভাল আছি|kemon acho|"
    r"ki khobor|কি খবর|তুমি কেমন|আপনি কেমন|"
    r"ঠিক আছে|ok|okay|thanks|thank you|ধন্যবাদ|bye|goodbye|বিদায়|"
    r"ha|hae|ho|hmm|na|no|yes|acha|accha|thik|hobe|hbe|jani|"
    r"thik ache|ঠিক|thikase)",
    re.IGNORECASE
)

_QUICK_FILLER_WORDS = frozenset({
    "ha", "hae", "ho", "na", "no", "yes", "acha", "accha", "thik", "ok",
    "okay", "hobe", "hbe", "jani", "ki", "kya", "why", "how", "so", "to",
    "the", "a", "an", "ache", "ase", "ei", "eta", "o", "ar", "are", "is",
    "it", "me", "i", "you",
})


# ---------------------------------------------------------------------------
# IntentDetector
# ---------------------------------------------------------------------------

class IntentDetector:

    @staticmethod
    def detect(text: str, context=None) -> str:
        """Rule-based intent detection. Returns an intent name string.
        Falls back to 'UNKNOWN' if no pattern matches."""
        return IntentDetector.detect_with_confidence(text, context)[0]

    @staticmethod
    def detect_with_confidence(text: str, context=None) -> tuple[str, float]:
        """
        Rule-based intent detection with a confidence score (new_ai_orchestrator
        Layer 5 / 'use confidence'). Returns (intent_name, confidence).

        Confidence drives the orchestrator's clarify/block-risky gate: high
        confidence intents proceed, low-confidence CREATE_ORDER etc. never
        auto-execute.
        """
        if not text or not text.strip():
            return "UNKNOWN", 0.0

        cleaned = text.strip()

        # Pure chit-chat ("kemon achen?", "ok", "thanks") — but only when the
        # ENTIRE message is small talk. A message that merely STARTS with a
        # small-talk word ("ok bhai, ami amer achar 2 pcs order korbo") must go
        # through full scoring so the real intent (order/product) is found.
        if _SMALL_TALK_RE.fullmatch(cleaned):
            return "SMALL_TALK", 0.75

        # F1: AFFIRM (ji/hm/ho re bhai/হ্যাঁ) — an answer to the current bot
        # question, never a product search. Inside an active order workflow
        # the orchestrator's first-strike routing consumes these via the
        # workflow; here we only tag them so the pipeline replies chat-style.
        stage = IntentDetector._stage_of(context)
        affirm = bool(_AFFIRM_PATTERN.match(cleaned))
        if affirm:
            if stage in ("order_collecting", "awaiting_confirmation"):
                return "AFFIRM", 0.95
            # Outside an order context a bare confirmation is chit-chat — the
            # "confirm right after an ORDER question" gate (2a, orchestrator)
            # still upgrades it to CREATE_ORDER when the last bot question
            # asked for an order.
            return "SMALL_TALK", 0.75

        # F1/F2: PROVIDE_QUANTITY ("2 pcs", "2 pack den", "৫টা") — captured
        # into the session's pre_collected before anything else interprets it
        # as a search ("pack" matches products like "Fly Glue Trap (10 Pcs)").
        if _QUANTITY_TOKEN_RE.search(cleaned):
            if stage in ("order_collecting", "awaiting_confirmation"):
                return "PROVIDE_QUANTITY", 0.9
            return "PROVIDE_QUANTITY", 0.9

        scores: dict[str, float] = {}

        for intent_name, pattern, confidence in _INTENT_PATTERNS:
            match = pattern.search(cleaned)
            if match:
                # Adjust confidence based on match position and length
                matched_text = match.group(0)
                position_boost = 1.0
                if match.start() == 0:
                    position_boost = 1.15  # matched at start of message
                if len(matched_text) / max(len(cleaned), 1) > 0.5:
                    position_boost *= 1.1  # match covers most of the message

                final_confidence = min(1.0, confidence * position_boost)
                if intent_name not in scores or final_confidence > scores[intent_name]:
                    scores[intent_name] = final_confidence

        if not scores:
            # Bare product-name messages ("cradle?", "bottle?", "dolna?") match
            # no pattern but are clearly product queries — search them instead
            # of falling to UNKNOWN (which answers from memory without tools).
            if IntentDetector._is_short_product_query(cleaned, context):
                return "SEARCH_PRODUCT", 0.7
            product_intent = IntentDetector._match_catalog_product(text, context)
            return (product_intent or "UNKNOWN", 0.65 if product_intent else 0.0)

        # Return the highest-confidence match
        best_intent = max(scores, key=scores.get)
        best_conf = scores[best_intent]

        # An explicit order request is more specific than chit-chat — a message
        # like "ok bhai, ami amer k 2 pcs order korbo" mentions BOTH "ok"
        # (small talk) and "order korbo"; the order intent must win.
        if best_intent in ("SMALL_TALK", "GREETING") and "CREATE_ORDER" in scores:
            best_intent = "CREATE_ORDER"
            best_conf = scores["CREATE_ORDER"]

        # Weak/generic matches that mention a real catalog product should be
        # treated as product searches (e.g. "jolpai" alone, "ok" style replies
        # after a product question).
        if best_intent in ("UNKNOWN", "SMALL_TALK", "GREETING"):
            product_intent = IntentDetector._match_catalog_product(text, context)
            if product_intent:
                return product_intent, 0.65

        logger.debug("IntentDetector: text=%r → %s (%.2f, scores=%s)", text[:50], best_intent, best_conf, scores)
        return best_intent, best_conf

    @staticmethod
    def _stage_of(context) -> str:
        """Coarse conversation stage (F1) — 'browsing' when unknown."""
        try:
            if context is None:
                return "browsing"
            conv = getattr(context, "conversation", None)
            if conv is None:
                return "browsing"
            from .state import get_stage
            return get_stage(conv)
        except Exception:
            return "browsing"

    @staticmethod
    def _is_short_product_query(text: str, context=None) -> bool:
        """True when a short message is likely a bare product query.

        "cradle?", "bottle ache?", "dolna?" — no intent pattern matched, but a
        short message containing a real content word should be searched, not
        answered from memory. Pure filler/acknowledgments ("na", "ok", "ha",
        "thik") are excluded — they're caught earlier as SMALL_TALK.

        F1 demotion: this must NEVER fire when
        - an order workflow is active (the customer is answering the flow:
          "5 pcs den" is a quantity, "Basundhara..." is an address),
        - the text carries ≥10 digits or address keywords ("R/A block Rd 04",
          "House 12, Bari") — those are details, not catalog queries,
        - the message is only pronouns/deictics ("eita", "koto", "ki") —
          leave those to deixis resolution / UNKNOWN extraction.
        """
        if not text or len(text.strip()) > 40:
            return False

        # F1: stage-aware demotion
        stage = IntentDetector._stage_of(context)
        if stage in ("order_collecting", "awaiting_confirmation", "post_order"):
            return False
        if len(re.sub(r"\D", "", text)) >= 10:
            return False
        if re.search(
            r"(road|block|r/a|house|bari|বাড়ি|বাসা|ঠিকানা|street|rd|"
            r"sector|bhaban|ভবন|thikana|flat|apartment)", text.lower()
        ):
            return False

        words = [w for w in re.split(r"[\s,.;:!?/]+", text.lower()) if w]
        if not words or len(words) > 4:
            return False
        if all(w in _QUICK_FILLER_WORDS for w in words):
            return False
        # Pronouns/deictics only ("eita", "koto", "ki", "eta", "সেটা") — no
        # content word, so nothing to search.
        if all(
            w in _QUICK_FILLER_WORDS or w in ("eita", "eta", "aita", "ei", "oi",
                                              "এইটা", "এটা", "ওটা", "সেটা", "ওই",
                                              "koto", "ki", "kono", "kon")
            for w in words
        ):
            return False
        return any(len(w) >= 3 for w in words)

    @staticmethod
    def _match_catalog_product(text: str, context=None) -> str:
        """Reclassify weak intents as SEARCH_PRODUCT when the message mentions
        a product from the user's catalog (by name token or substring).

        Returns "" (empty) when no product matches — the caller decides the
        fallback intent.
        """
        if not text or not context:
            return ""
        user = getattr(getattr(context, "conversation", None), "user", None)
        if not user:
            return ""

        try:
            from back.models import Product

            words = [w for w in re.split(r"[\s,.;:!?]+", text.lower()) if len(w) >= 3]
            if not words and len(text.strip()) < 3:
                return ""
            lowered = text.lower()
            for p in Product.objects.filter(user=user, status=True)[:50]:
                name = (p.name or "").strip().lower()
                if not name:
                    continue
                if name in lowered:
                    return "SEARCH_PRODUCT"
                if any(w in name for w in words):
                    return "SEARCH_PRODUCT"
        except Exception as exc:
            logger.warning("Catalog product match failed: %s", exc)
        return ""

    @staticmethod
    def detect_with_llm_fallback(text: str, context=None) -> str:
        """
        Two-tier: try rule-based first, fall back to LLM for UNKNOWN or low confidence.
        """
        intent = IntentDetector.detect(text, context)
        if intent != "UNKNOWN":
            return intent

        # LLM fallback
        try:
            from .providers import call_llm
            prompt = (
                "Classify the intent of this customer message into exactly one of these labels:\n"
                f"{', '.join(INTENT_GROUPS.keys())}\n\n"
                f"Message: {text}\n\n"
                "Return ONLY the label name, nothing else."
            )
            msg, _ = call_llm(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
                temperature=0.1,
                max_tokens=20,
            )
            result = (msg.content or "").strip().upper()
            if result in INTENT_GROUPS:
                return result
        except Exception as exc:
            logger.warning("LLM intent fallback failed: %s", exc)

        return "UNKNOWN"

    @staticmethod
    def is_actionable(intent: str) -> bool:
        """Return True if this intent requires tool execution (not just chit-chat)."""
        non_actionable = {"GREETING", "SMALL_TALK", "UNKNOWN"}
        return intent not in non_actionable
