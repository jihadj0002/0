import json
import logging
import re

from django.core.files.storage import default_storage
from django.db.models import DecimalField, Q
from django.db.models.functions import Coalesce

from back.models import Conversation, Product, ProductImages, Sale

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "Search products by SKU/name/keyword; optional min_price/max_price (budget). Try up to 3 query variants (English, synonyms, transliteration). Call before quoting any price.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search term — SKU code, product name, or keyword"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                    "min_price": {"type": "number", "description": "Minimum price / budget floor — optional; omit if no lower bound"},
                    "max_price": {"type": "number", "description": "Maximum price / budget ceiling — optional; e.g. if customer says 'budget of 500 taka' pass 500"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_details",
            "description": "Fresh price/stock for a PID — only if it is NOT already listed in the context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pid": {"type": "string", "description": "Product PID e.g. sku_abc123"},
                },
                "required": ["pid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_images",
            "description": "Send product images. pid → images one-by-one; pids=[...] → card carousel. Cards already show name+price — do not repeat them in text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pid": {"type": "string", "description": "Single product PID (use when showing one product)"},
                    "pids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "One or more product PIDs to show as a card carousel (prefer this over calling send_images multiple times)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_order",
            "description": "Create a pending order (name, phone, address, city, zone required). Backend computes totals: first call customer_confirmed=false returns the summary to present; second call customer_confirmed=true ONLY after the customer's explicit yes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {"type": "string"},
                    "customer_phone": {"type": "string"},
                    "customer_address": {"type": "string"},
                    "customer_city": {"type": "string"},
                    "delivery_zone": {
                        "type": "string",
                        "enum": ["inside_dhaka", "outside_dhaka"],
                        "description": "Used to apply correct delivery charge",
                    },
                    "customer_confirmed": {
                        "type": "boolean",
                        "description": "MUST be true only after the customer explicitly confirmed the final order summary (items + total) with a clear yes. Otherwise leave false — the tool returns the summary to confirm.",
                    },
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "pid": {"type": "string"},
                                "quantity": {"type": "integer"},
                                "variation_id": {
                                    "type": "string",
                                    "description": "Required for products that have variations (size/color). Use the variation_id from search_products/get_product_details.",
                                },
                            },
                            "required": ["pid"],
                        },
                    },
                },
                "required": ["customer_name", "customer_phone", "customer_address", "items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_status",
            "description": "Look up an order by its oid.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "Order oid e.g. ord_abc123"},
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_customer",
            "description": "Save customer name/phone/city/address.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "phone": {"type": "string"},
                    "city": {"type": "string"},
                    "address": {"type": "string"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_ticket",
            "description": "Hand the conversation to a human: complaints, angry customer, explicit human request.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "Short summary of the issue"},
                    "description": {"type": "string", "description": "Detailed description of the issue"},
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "urgent"],
                        "description": "Issue priority (default medium)",
                    },
                },
                "required": ["subject", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": "Search policies/FAQs/returns/shipping/payment. NOT for product queries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look up (e.g. 'return policy', 'shipping time', 'payment methods')"},
                    "limit": {"type": "integer", "description": "Max results (default 3)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": "Private planning step before tools — never customer-facing text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "notes": {"type": "string", "description": "Short internal plan (1-3 lines)"},
                },
                "required": ["notes"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _search_result_instruction(total, query=""):
    """Return a tailored _instruction based on how many products were found.

    The customer's literal request is embedded so the model can reject
    keyword-matched-but-unrelated candidates (a 'dress' is NOT 'dress shoes').
    """
    asked = f' for "{query}"' if query else ""
    prefix = (
        f'These are catalog CANDIDATES from a keyword search{asked} — some may be unrelated. '
        "Reply ONLY about items whose NAME is genuinely what the customer asked for "
        "(a 'dress' request is NOT satisfied by 'dress shoes'; a 'bathtub' is NOT a 'mosquito net'). "
        "If none genuinely match, tell the customer it's currently unavailable and do NOT list "
        "unrelated items — do not invent a match.\n"
    )
    if total <= 1:
        return prefix + (
            "If this matches, send_images(pid=...) so the customer sees it, "
            "and mention the name + price in one short line."
        )
    return prefix + (
        "If several genuinely match and the customer is browsing, show them with "
        "send_images(pids=[...]) as a carousel and give a short text. "
        "If the customer asked about one specific item, focus on that one. "
        "Do NOT search for each product individually."
    )


# Generic English + transliterated-Bengali filler words that carry no product
# meaning. Used to keep keyword fan-out and relevance scoring focused on the
# words that actually describe the product the customer wants.
_STOPWORDS = frozenset({
    "a", "an", "the", "of", "for", "and", "or", "to", "is", "are", "was", "with",
    "my", "me", "i", "we", "this", "that", "these", "those", "it", "do", "does",
    "you", "your", "our", "please", "pls", "show", "need", "want", "looking",
    "have", "has", "had", "any", "some", "all", "new", "get", "give", "send",
    # transliterated Bengali fillers / connectors (not product words)
    "ache", "ase", "achi", "hobe", "hbe", "ki", "kichu", "ektu", "ata", "eita",
    "eta", "ei", "der", "jonno", "lagbe", "lagbe", "chai", "chaii", "chaai",
    "koto", "dam", "amar", "ami", "apnar", "apni", "ta", "tar", "ar", "o", "na",
})





def _content_tokens(text):
    """Tokenize text into meaningful lowercase tokens (no stopwords/pure digits)."""
    toks = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [t for t in toks if t not in _STOPWORDS and not t.isdigit() and len(t) >= 2]


def _normalize_bangla(text):
    """Strip Bangla vowel signs + juktakkhor to approximate a Latin search term.

    'বরইয়ের' → 'বরইর' — still Bangla, but matches tokens like 'বরই' more
    easily than the conjunct 'য়ের' form. Used to catch typo/transliteration
    variants without a full translator.
    """
    return re.sub(
        r"[\u09BE-\u09CD\u09D7\u09DC\u09DD\u09DF\u200C\u200D]+", "", text or ""
    )


# Rough Bangla → Latin map (only for search hints, not translation).
_BN_TO_LATIN = {
    # independent vowels
    "অ": "o", "আ": "a", "ই": "i", "ঈ": "i", "উ": "u", "ঊ": "u",
    "এ": "e", "ঐ": "oi", "ও": "o", "ঔ": "ou",
    # consonants
    "ক": "k", "খ": "kh", "গ": "g", "ঘ": "gh", "ঙ": "ng",
    "চ": "c", "ছ": "ch", "জ": "j", "ঝ": "jh", "ঞ": "n",
    "ট": "t", "ঠ": "th", "ড": "d", "ঢ": "dh", "ণ": "n",
    "ত": "t", "থ": "th", "দ": "d", "ধ": "dh", "ন": "n",
    "প": "p", "ফ": "ph", "ব": "b", "ভ": "bh", "ম": "m",
    "য": "j", "র": "r", "ল": "l", "শ": "sh", "ষ": "sh",
    "স": "s", "হ": "h", "ড়": "r", "ঢ়": "rh", "য়": "y",
    # dependent vowel signs
    "া": "a", "ি": "i", "ী": "i", "ু": "u", "ূ": "u",
    "ে": "e", "ৈ": "oi", "ো": "o", "ৌ": "ou", "ৃ": "ri",
    "ঃ": "h", "ং": "ng", "ঁ": "n",
    "়": "", "্": "", "\u200c": "", "\u200d": "",
}

_BN_CONSONANTS = frozenset(
    "কখগঘঙচছজঝঞটঠডঢণতথদধনপফবভমযরলশষসহড়ঢ়য়"
)

_BN_VOWEL_SIGNS = frozenset("া ি ী ু ূ ে ৈ ো ৌ ৃ".split())

_BN_LETTER_RE = re.compile(
    "[" + "".join(re.escape(ch) for ch in _BN_TO_LATIN.keys()) + "]", re.UNICODE
)


def _to_latin(text):
    """Very rough Bangla→Latin phonetic hint (for matching only).

    'বরইয়ের আচার' → 'boriyer achar' — enough to match 'boroi/borui achar'.
    """
    chars = text or ""
    out = []
    i = 0
    while i < len(chars):
        ch = chars[i]
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        # 'য়' is stored as 'য' + nukta (U+09AF + U+09BC) → 'y'
        if ch == "য" and nxt == "়":
            out.append("y")
            i += 2
            continue
        latin = _BN_TO_LATIN.get(ch)
        if latin is None:
            out.append(ch)
            i += 1
            continue
        if ch in _BN_CONSONANTS:
            # inherent 'o' (অ) when the consonant is NOT followed by a vowel
            # sign and NOT part of a conjunct (virama) — 'বরইয়ের' reads
            # 'boro' + 'iyer' ≈ 'boroi'/'borui', not 'briyer'.
            if nxt and (nxt in _BN_VOWEL_SIGNS or nxt == "্"):
                out.append(latin)
            else:
                out.append(latin + "o")
        else:
            out.append(latin)
        i += 1
    return "".join(out)


def _strip_trailing_vowels(latin):
    """Drop trailing a/e/i/o/u per word — customers write 'achar', not 'acaro'."""
    words = re.split(r"([^a-z0-9]+)", (latin or "").lower())
    out = []
    for w in words:
        if re.fullmatch(r"[a-z0-9]+", w):
            w = re.sub(r"[aeiou]+$", "", w)
        out.append(w)
    return "".join(out)


# Bangla grammatical particles that appear in every product name and pollute
# transliteration matching ('er' in 'আমের', 'আচার' in 'আমের আচার').
_BN_PARTICLES = frozenset({
    "er", "der", "ra", "ta", "te", "ke", "diye", "theke", "ar", "ebong",
    "or", "ir", "erir", "eririr",
})


def _matches_latin(query, name_latin):
    """True if any Latin query token is contained in the Latin transliteration."""
    if not query or not name_latin:
        return False
    toks = [t for t in re.findall(r"[a-z0-9]+", (query or "").lower())
            if len(t) >= 2 and t not in _STOPWORDS and t not in _BN_PARTICLES]
    compact = _strip_trailing_vowels(name_latin).replace(" ", "")
    if any(t in compact for t in toks):
        return True
    # Consonant-skeleton match: 'misri' ≈ 'mishro', 'tentul' ≈ 'tetul'.
    name_toks = [t for t in re.findall(r"[a-z0-9]+", compact) if len(t) >= 3]
    for qt in toks:
        q_sk = re.sub(r"[aeiouy]+", "", qt)
        if len(q_sk) < 3:
            continue
        for nt in name_toks:
            n_sk = re.sub(r"[aeiouy]+", "", nt)
            if q_sk and n_sk and (q_sk in n_sk or n_sk in q_sk):
                return True
    return False


def _bangla_matches(query, name):
    """True if any query token appears in the Bangla-normalized product name."""
    if not query or not name:
        return False
    q = re.findall(r"[a-z0-9]+", (query or "").lower())
    q = [t for t in q if len(t) >= 2 and t not in _STOPWORDS and t not in _BN_PARTICLES]
    if not q:
        return False
    compact = _normalize_bangla(name).lower().replace(" ", "")
    return any(t in compact for t in q)





def _image_url(path):
    try:
        return default_storage.url(str(path))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Focused products — a rolling list (most-recent-first, max 5) persisted on
# conversation.current_product as a JSON array. A single search can surface
# several products, so the AI keeps context on the last few it touched (and can
# pull a pid for send_images). The most recent product is kept in full detail
# (description + variations); older entries are compact to fit the field.
# Readers tolerate a legacy single-dict payload or raw-pid string, so the API
# SelectProductView and older rows keep working.
# ---------------------------------------------------------------------------

FOCUS_MAX = 5


def parse_focus_products(value):
    """Return the focused-product list (most-recent-first) from current_product."""
    if not value:
        return []
    value = value.strip()
    try:
        data = json.loads(value)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict) and d.get("pid")]
        if isinstance(data, dict) and data.get("pid"):
            return [data]
    except (json.JSONDecodeError, TypeError):
        pass
    return [{"pid": value}]  # legacy: the whole value is a raw pid


def _focus_pid(conversation):
    items = parse_focus_products(getattr(conversation, "current_product", "") if conversation else "")
    return items[0].get("pid", "") if items else ""


def _build_focus_payload(product, full=True):
    """Snapshot a product row/details dict. ``full`` keeps description + variations."""
    payload = {"pid": str(product.get("pid", "")), "name": product.get("name") or ""}
    for key in ("price", "discounted_price", "stock", "in_stock", "sku", "external_id"):
        val = product.get(key)
        if val is not None:
            payload[key] = val
    if not full:
        return payload
    desc = (product.get("description") or "").strip()
    if desc:
        payload["description"] = desc[:150]
    variations = product.get("variations") or []
    if variations:
        payload["variations"] = [
            {
                "variation_id": v.get("variation_id"),
                "name": v.get("name"),
                "price": v.get("price"),
                "in_stock": v.get("in_stock", True),
            }
            for v in variations[:6]
        ]
    return payload


def _focus_products(conversation, products):
    """Prepend ``products`` (row/details dicts) to the rolling focus list.

    Dedups by pid, keeps most-recent-first, caps at FOCUS_MAX. The newest entry
    is stored in full detail; the rest are compact to respect the field size.
    """
    if not conversation or not products:
        return
    incoming = [p for p in products if p and p.get("pid")]
    if not incoming:
        return

    existing = parse_focus_products(conversation.current_product)
    ordered, seen = [], set()
    for p in incoming + existing:
        pid = str(p.get("pid"))
        if pid and pid not in seen:
            seen.add(pid)
            ordered.append(p)
        if len(ordered) >= FOCUS_MAX:
            break

    items = [_build_focus_payload(p, full=(i == 0)) for i, p in enumerate(ordered)]

    payload = json.dumps(items)
    # current_product is CharField(max_length=5000) — drop oldest until it fits.
    while len(payload) > 4900 and len(items) > 1:
        items.pop()
        payload = json.dumps(items)
    Conversation.objects.filter(pk=conversation.pk).update(current_product=payload)
    conversation.current_product = payload


def _clear_focus_product(conversation):
    if not conversation:
        return
    Conversation.objects.filter(pk=conversation.pk).update(current_product="")
    conversation.current_product = ""


def _external_row(r):
    row = {
        "pid": r["external_id"],
        "name": r["name"],
        "price": r["price"],
        "discounted_price": r.get("discounted_price"),
        "in_stock": r.get("in_stock", True),
        "stock": r.get("stock", 0),
        "description": (r.get("description") or "")[:200],
        "featured": False,
        "external_id": r["external_id"],
    }
    if r.get("sku"):
        row["sku"] = r["sku"]
    # Surface variations so the AI can present options and pass a variation_id
    # back into create_order (the external order API requires it).
    variations = r.get("variations") or []
    if variations:
        row["variations"] = [
            {
                "variation_id": v.get("variation_id"),
                "name": v.get("name"),
                "price": v.get("price"),
                "in_stock": v.get("in_stock", True),
            }
            for v in variations
        ]
    return row


def _generate_search_queries(original):
    """Yield deduplicated query variations for multi-strategy search."""
    seen = set()
    cleaned = original.strip()
    if not cleaned:
        return

    # 1. Original query
    if cleaned not in seen:
        seen.add(cleaned)
        yield cleaned

    # 2. Remove non-alphanumeric (keep spaces)
    stripped = re.sub(r"[^\w\s]", "", cleaned).strip()
    if stripped and stripped not in seen:
        seen.add(stripped)
        yield stripped

    words = stripped.split()

    # 3. First 2 words
    if len(words) > 2:
        first_two = " ".join(words[:2])
        if first_two not in seen:
            seen.add(first_two)
            yield first_two

    # 4. Last 2 words
    if len(words) > 2:
        last_two = " ".join(words[-2:])
        if last_two not in seen:
            seen.add(last_two)
            yield last_two

    # 5. Each individual MEANINGFUL word (skip stopwords/fillers so we don't
    #    fan out to terms like 'girl' or 'baby' that surface unrelated products).
    for w in words:
        if w and w not in seen and w.lower() not in _STOPWORDS and len(w) >= 3:
            seen.add(w)
            yield w


def _dedup_external(results):
    """Deduplicate a list of external result dicts by external_id (stored as pid)."""
    seen = set()
    out = []
    for r in results:
        pid = r.get("pid") or r.get("external_id")
        if pid and pid not in seen:
            seen.add(pid)
            out.append(r)
    return out


def _filter_by_budget(results, min_price, max_price):
    """Filter a list of product dicts by effective price (discounted if available, else price)."""
    if min_price is None and max_price is None:
        return results
    out = []
    for r in results:
        try:
            eff = float(r.get("discounted_price") or r.get("price") or 0)
        except (ValueError, TypeError):
            eff = 0
        if min_price is not None and eff < min_price:
            continue
        if max_price is not None and eff > max_price:
            continue
        out.append(r)
    return out


def tool_search_products(user, query, limit=10, conversation=None, min_price=None, max_price=None):
    max_external_attempts = 4

    # Live external source → query the provider in real time.
    from api.products.factory import get_active_source, get_provider, is_external
    try:
        source = get_active_source(user)
        if source and source.mode == "live" and is_external(user):
            provider = get_provider(user)
            if not (query and query.strip()):
                rows = provider.list_products(limit=limit)
                rows = rows or []
                results = [_external_row(r) for r in rows]
                results = _filter_by_budget(results, min_price, max_price)
                if results:
                    _focus_products(conversation, results[:FOCUS_MAX])
                    out = {"products": results, "total": len(results)}
                    out["_instruction"] = _search_result_instruction(len(results))
                    return out

            # Multi-strategy: try ALL successive query variations (don't break on
            # first batch — the first variation can return irrelevant results
            # while individual words match perfectly). Dedup by external_id.
            all_results = []
            seen_ids = set()
            for variation in _generate_search_queries(query):
                if len(seen_ids) >= max_external_attempts * limit:
                    break
                try:
                    rows = provider.search(variation, limit)
                    rows = rows or []
                    for r in rows:
                        eid = r.get("external_id")
                        if eid and eid not in seen_ids:
                            seen_ids.add(eid)
                            all_results.append(_external_row(r))
                except Exception:
                    logger.exception("External search failed for variation=%s", variation)

            all_results = _filter_by_budget(all_results, min_price, max_price)
            all_results = all_results[:limit]
            if all_results:
                _focus_products(conversation, all_results[:FOCUS_MAX])
                out = {"products": all_results, "total": len(all_results)}
                out["_instruction"] = _search_result_instruction(len(all_results), query)
                return out
            # Nothing matched the query — tell the model so it can say unavailable
            # or try a genuinely different keyword (don't fall through to junk).
            if query and query.strip():
                return {
                    "products": [],
                    "total": 0,
                    "_instruction": (
                        f'No catalog items matched "{query}". Either try ONE more search with a '
                        "different/simpler keyword or synonym, or tell the customer it's currently "
                        "unavailable. Do NOT present unrelated products."
                    ),
                }
            # Empty query — fall through to local DB / featured handling.
    except Exception:
        logger.exception("Live search_products failed; falling back to local DB")

    # If a product is already selected for this conversation, keep it but only
    # as the FIRST result — never short-circuit the search with it. A stale
    # focus (e.g. "আমের আচার" selected earlier) must not hijack a NEW query
    # ("বরইয়ের আচার pic den"); the full search still runs and the LLM sees
    # which results genuinely match the customer's words.
    focus_pid = _focus_pid(conversation)
    focus_product = None
    if focus_pid:
        try:
            product = Product.objects.get(user=user, pid=focus_pid, status=True)
            if min_price is not None or max_price is not None:
                eff = float(product.discounted_price or product.price or 0)
                in_budget = True
                if min_price is not None and eff < min_price:
                    in_budget = False
                if max_price is not None and eff > max_price:
                    in_budget = False
                if in_budget:
                    focus_product = _product_row(product)
                else:
                    _clear_focus_product(conversation)
            else:
                focus_product = _product_row(product)
        except Product.DoesNotExist:
            _clear_focus_product(conversation)

    # Generic / empty query → show featured first, then fill with anything
    generic = not query or query.strip().lower() in (
        "", "product", "products", "show", "list", "all",
        "ki", "ki ki", "ache", "ki ache", "ki ki ache", "কি কি আছে", "কি আছে",
    )
    if generic:
        qs = Product.objects.filter(user=user, status=True).order_by("-featured_product", "name")
        products_list = list(qs[:limit])
    else:
        # Multi-strategy: build one combined OR query across all variations and fields
        combined_q = Q()
        for variation in _generate_search_queries(query):
            combined_q |= (
                Q(name__icontains=variation)
                | Q(description__icontains=variation)
                | Q(pid__icontains=variation)
                | Q(external_id__icontains=variation)
                | Q(name__regex=_normalize_bangla(variation))
            )
        qs = Product.objects.filter(user=user, status=True).filter(combined_q).order_by("-featured_product", "name")

        # Budget filter — use effective price (discounted if available, else price)
        if min_price is not None or max_price is not None:
            qs = qs.annotate(
                _eff_price=Coalesce("discounted_price", "price", output_field=DecimalField())
            )
            if min_price is not None:
                qs = qs.filter(_eff_price__gte=min_price)
            if max_price is not None:
                qs = qs.filter(_eff_price__lte=max_price)

        # Dedup by pid (the combined query can return the same product via different variations)
        seen = set()
        products_list = []
        for p in qs:
            if p.pid not in seen:
                seen.add(p.pid)
                products_list.append(p)
                if len(products_list) >= limit:
                    break

        # Latin transliteration fallback — Bangla product names can't match
        # 'borui er achar' directly, so ALSO match the rough Latin transliteration.
        if query and query.strip():
            kept = []
            seen2 = {p.pid for p in products_list}
            for p in Product.objects.filter(user=user, status=True).order_by("-featured_product", "name"):
                latin = _to_latin(p.name or "")
                if p.pid not in seen2 and _matches_latin(query, latin):
                    seen2.add(p.pid)
                    kept.append(p)
                    if len(products_list) + len(kept) >= limit:
                        break
            products_list.extend(kept)

    results = [_product_row(p) for p in products_list]

    # Merge the focused product as the FIRST candidate — only if it actually
    # relates to what the customer asked (matches query terms). Keeps context
    # ("the product we were just looking at") without hijacking new searches.
    if focus_product and not generic and query and query.strip():
        pname = focus_product.get("name") or ""
        if not (_bangla_matches(query, pname) or _matches_latin(query, _to_latin(pname))):
            focus_product = None
    if focus_product:
        merged = [focus_product]
        seen_pid = {focus_product.get("pid")}
        for r in results:
            if r.get("pid") not in seen_pid:
                seen_pid.add(r.get("pid"))
                merged.append(r)
        results = merged

    if results and conversation:
        _focus_products(conversation, results[:FOCUS_MAX])

    out = {"products": results, "total": len(results)}
    if results:
        out["_instruction"] = _search_result_instruction(len(results), query)
    elif not generic and query and query.strip():
        out["_instruction"] = (
            f'No catalog items matched "{query}". Either try ONE more search with a '
            "different/simpler keyword or synonym, or tell the customer it's currently "
            "unavailable. Do NOT present unrelated products."
        )
    return out


def _product_row(p):
    return {
        "pid": p.pid,
        "name": p.name,
        "price": str(p.price),
        "discounted_price": str(p.discounted_price) if p.discounted_price else None,
        "in_stock": p.stock_quantity > 0,
        "stock": p.stock_quantity,
        "description": (p.description or "")[:200],
        "featured": p.featured_product,
    }


def tool_get_product_details(user, pid, conversation=None):
    from api.products.factory import get_active_source, get_provider, is_external

    # 0) Focused products cache — avoids API calls when data is already in hand.
    if conversation:
        focus_list = parse_focus_products(conversation.current_product)
        for fp in focus_list:
            if fp.get("pid") == pid or fp.get("sku") == pid:
                details = {
                    "pid": fp.get("pid", ""),
                    "name": fp.get("name", ""),
                    "price": str(fp.get("price", "")),
                    "discounted_price": str(fp.get("discounted_price") or ""),
                    "stock": fp.get("stock", 0),
                    "in_stock": fp.get("in_stock", True),
                    "description": (fp.get("description") or "")[:300],
                    "upsell_enabled": False,
                }
                if fp.get("sku"):
                    details["sku"] = fp["sku"]
                if fp.get("external_id"):
                    details["external_id"] = fp["external_id"]
                variations = fp.get("variations") or []
                if variations:
                    details["variations"] = variations
                _focus_products(conversation, [details])
                return details

    # 1) Live external source.
    fallback_to_db = False
    try:
        source = get_active_source(user)
        if source and source.mode == "live" and is_external(user):
            provider = get_provider(user)
            r = provider.get_product(pid)
            if not r:
                try:
                    results = provider.search(pid, limit=1)
                    r = results[0] if results else None
                except Exception:
                    pass
            if not r:
                fallback_to_db = True
            else:
                details = {
                    "pid": r["external_id"],
                    "name": r["name"],
                    "price": r["price"],
                    "discounted_price": r.get("discounted_price"),
                    "stock": r.get("stock", 0),
                    "in_stock": r.get("in_stock", True),
                    "description": r.get("description") or "",
                    "upsell_enabled": False,
                    "external_id": r["external_id"],
                }
                if r.get("sku"):
                    details["sku"] = r["sku"]
                variations = r.get("variations") or []
                if variations:
                    details["variations"] = [
                        {
                            "variation_id": v.get("variation_id"),
                            "name": v.get("name"),
                            "price": v.get("price"),
                            "in_stock": v.get("in_stock", True),
                        }
                        for v in variations
                    ]
                _focus_products(conversation, [details])
                return details
    except Exception:
        logger.exception("Live get_product_details failed; falling back to local DB")
        fallback_to_db = True

    if not fallback_to_db:
        pass
    # Local DB fallback: search by pid first, then by external_id
    p = Product.objects.filter(Q(user=user, pid=pid) | Q(user=user, external_id=pid)).first()
    if p is not None:
        extra_images = [
            _image_url(img)
            for img in ProductImages.objects.filter(product=p).values_list("images", flat=True)
            if _image_url(img)
        ]

        details = {
            "pid": p.pid,
            "name": p.name,
            "price": str(p.price),
            "discounted_price": str(p.discounted_price) if p.discounted_price else None,
            "stock": p.stock_quantity,
            "in_stock": p.stock_quantity > 0,
            "description": p.description or "",
            "upsell_enabled": p.upsell_enabled,
        }
        _focus_products(conversation, [details])
        return details

    return {"error": f"Product '{pid}' not found"}


def tool_send_images(user, pid="", pids=None, conversation=None):
    from api.products.factory import get_active_source, get_provider, is_external

    # Collect all requested PIDs.
    requested = []
    if pids:
        requested.extend(pids)
    if pid:
        requested.append(pid)
    if not requested:
        fallback = _focus_pid(conversation)
        if fallback:
            requested.append(fallback)
    if not requested:
        return {"error": "No product selected — search for a product first", "products": []}

    source = get_active_source(user)
    external_active = bool(source) and is_external(user)
    live_mode = bool(source) and source.mode == "live" and external_active

    products = []
    seen_pids = set()

    for raw_pid in requested:
        if raw_pid in seen_pids:
            continue
        seen_pids.add(raw_pid)

        name = ""
        images = []
        price = ""
        discounted_price = ""
        sku = ""

        if live_mode:
            try:
                provider = get_provider(user)
                r = provider.get_product(raw_pid)
                if not r:
                    try:
                        results = provider.search(raw_pid, limit=1)
                        r = results[0] if results else None
                    except Exception:
                        pass
                if r:
                    name = r.get("name") or ""
                    images = r.get("images") or ([r["image"]] if r.get("image") else [])
                    price = str(r.get("price") or "")
                    discounted_price = str(r.get("discounted_price") or "")
                    sku = r.get("sku") or ""
            except Exception:
                logger.exception("Live send_images failed for %s; falling back to local DB", raw_pid)

        if not name:
            try:
                p = Product.objects.get(user=user, pid=raw_pid)
                name = p.name
                price = str(p.price) if p.price else ""
                discounted_price = str(p.discounted_price) if p.discounted_price else ""
                main = _image_url(p.image)
                if main:
                    images.append(main)
                for img in ProductImages.objects.filter(product=p).values_list("images", flat=True):
                    url = _image_url(img)
                    if url and url not in images:
                        images.append(url)
            except Product.DoesNotExist:
                continue

        if name:
            products.append({
                "pid": raw_pid,
                "name": name,
                "images": images,
                "price": price,
                "discounted_price": discounted_price,
                "sku": sku,
            })

    if not products:
        return {"error": "No products found", "products": []}

    return {"products": products, "total": len(products)}


# Known BDT city/district names — used to derive `city` from a free-text address.
_BDT_CITIES = {
    "ঢাকা", "চট্টগ্রাম", "সিলেট", "খুলনা", "রাজশাহী", "বরিশাল", "রংপুর",
    "ময়মনসিংহ", "কুমিল্লা", "নারায়ণগঞ্জ", "গাজীপুর", "বগুড়া", "দিনাজপুর",
    "জামালপুর", "টাঙ্গাইল", "ফরিদপুর", "পাবনা", "কুষ্টিয়া", "যশোর",
    "কক্সবাজার", "ব্রাহ্মণবাড়িয়া", "সিরাজগঞ্জ", "রাঙ্গামাটি", "খাগড়াছড়ি",
    "বান্দরবান", "ফেনী", "নোয়াখালী", "লক্ষ্মীপুর", "চাঁদপুর", "শেরপুর",
    "নেত্রকোনা", "কিশোরগঞ্জ", "মানিকগঞ্জ", "মুন্সিগঞ্জ", "গোপালগঞ্জ",
    "মাদারীপুর", "শরীয়তপুর", "সাতক্ষীরা", "ঝিনাইদহ", "মাগুরা", "নড়াইল",
    "বাগেরহাট", "পিরোজপুর", "ঝালকাঠি", "পটুয়াখালী", "ভোলা", "সুনামগঞ্জ",
    "হবিগঞ্জ", "মৌলভীবাজার", "গাইবান্ধা", "কুড়িগ্রাম", "লালমনিরহাট",
    "নীলফামারী", "পঞ্চগড়", "ঠাকুরগাঁও", "জয়পুরহাট", "নওগাঁ", "চাঁপাইনবাবগঞ্জ",
    "Dhaka", "Chittagong", "Sylhet", "Khulna", "Rajshahi", "Barishal",
    "Rangpur", "Mymensingh", "Cumilla", "Narayanganj", "Gazipur", "Bogra",
    "Cox's Bazar", "Coxs Bazar", "Feni", "Noakhali",
}


def derive_city_from_address(address):
    """Return a known BDT city name found at the end of an address, else ''."""
    address = (address or "").strip()
    if not address:
        return ""
    # Check the last comma-separated segment first ("মিরপুর ১০, ঢাকা" → ঢাকা).
    segments = [s.strip() for s in address.split(",") if s.strip()]
    for seg in reversed(segments):
        if seg in _BDT_CITIES:
            return seg
    # Fall back to the last token (covers "মিরপুর ঢাকা" style addresses).
    last_token = segments[-1].split()[-1] if segments else address.split()[-1]
    if last_token in _BDT_CITIES:
        return last_token
    return ""


def tool_create_order(user, conversation, customer_name, customer_phone, customer_address, items,
                      customer_city="", delivery_zone="inside_dhaka", customer_confirmed=False):
    """Backend-enforced order creation.

    Flow:
      1. Resolve items + compute totals (authoritative backend math).
      2. Persist an OrderDraft + sync SessionContext to awaiting_confirmation.
      3. Without customer confirmation → return the exact summary to show.
      4. With confirmation → the draft is confirmed through the same code path
         as the pipeline auto-confirm guard (context.crm.drafts.confirm_draft_order).
    """
    from context.crm.drafts import (
        backfill_conversation_customer,
        compute_order_totals,
        confirm_draft_order,
        draft_missing_fields,
        order_summary_dict,
        save_draft,
        sync_session_state,
    )
    from context.crm.signals import record_signal

    # Merge supplied info with what the conversation already knows.
    name = (customer_name or "").strip() or (conversation.customer_name or "").strip()
    phone = (customer_phone or "").strip() or (conversation.customer_phone or "").strip()
    address = (customer_address or "").strip() or (conversation.customer_address or "").strip()
    city = (customer_city or "").strip() or (conversation.customer_city or "").strip()

    # Derive city from the address when the customer didn't give one separately
    # (e.g. "ঠিকানা মিরপুর ১০, ঢাকা" → city = ঢাকা). Matches known BDT city /
    # district names so "মিরপুর" is never mistaken for a city.
    if not city and address:
        city = derive_city_from_address(address)

    totals = compute_order_totals(user, items, delivery_zone)
    if not totals["ok"]:
        return {"error": "Cannot create order", "details": totals["errors"]}

    # Backfill the conversation so the auto-confirm guard and prompt see it.
    backfill_conversation_customer(
        conversation,
        name=name or None,
        phone=phone or None,
        city=city or None,
        address=address or None,
    )

    missing = []
    if not name:
        missing.append("customer_name")
    if not phone:
        missing.append("customer_phone")
    if not address:
        missing.append("customer_address")
    if not city:
        missing.append("customer_city")

    summary = order_summary_dict(
        resolved=totals["resolved"],
        item_total=totals["item_total"],
        delivery_charge=totals["delivery_charge"],
        grand_total=totals["grand_total"],
        delivery_zone=delivery_zone,
    )

    if missing:
        save_draft(
            user, conversation,
            resolved=totals["resolved"],
            item_total=totals["item_total"],
            delivery_charge=totals["delivery_charge"],
            grand_total=totals["grand_total"],
            delivery_zone=delivery_zone,
            confirmation_status="draft",
            missing_fields=missing,
        )
        sync_session_state(conversation, "awaiting_details", pending_confirmation=summary)
        return {
            "error": "Missing required customer information",
            "missing_fields": missing,
            "order_summary": summary,
            "note": "Collect only the missing fields — never re-ask for information already known.",
        }

    if not customer_confirmed:
        # Draft the order, sync the state machine, and hand back the summary.
        save_draft(
            user, conversation,
            resolved=totals["resolved"],
            item_total=totals["item_total"],
            delivery_charge=totals["delivery_charge"],
            grand_total=totals["grand_total"],
            delivery_zone=delivery_zone,
            confirmation_status="awaiting_confirmation",
            missing_fields=[],
        )
        sync_session_state(conversation, "awaiting_confirmation", pending_confirmation=summary)
        out = {
            "confirmation_required": True,
            "order_summary": summary,
        }
        if totals["warnings"]:
            out["warnings"] = totals["warnings"]
        out["note"] = (
            "Present this exact summary (items, item total, delivery charge, grand total, "
            "delivery zone) to the customer and ask for a clear yes. Do NOT create the order "
            "until the customer confirms."
        )
        return out

    # Customer confirmed → go through the single shared confirm path.
    if address:
        record_signal(conversation, "provided_address")
    result = confirm_draft_order(conversation)
    if isinstance(result, dict) and result.get("order_id"):
        if totals["warnings"]:
            result["warnings"] = totals["warnings"]
        return result
    # Draft may have drifted from these args (e.g. items changed between calls).
    # Re-persist this exact payload and retry once — but never resurrect an
    # already-confirmed draft (that would double-create the order).
    from context.crm_models import OrderDraft
    existing = OrderDraft.objects.filter(conversation=conversation).first()
    if existing and existing.confirmation_status == "confirmed" and existing.converted_order_id:
        sale = existing.converted_order
        out = {
            "order_id": sale.oid,
            "status": sale.status,
            "total": str(sale.amount),
            "items": [
                {"name": i.product_name, "qty": i.quantity, "price": str(i.price)}
                for i in sale.items.all()
            ],
        }
        if totals["warnings"]:
            out["warnings"] = totals["warnings"]
        return out
    save_draft(
        user, conversation,
        resolved=totals["resolved"],
        item_total=totals["item_total"],
        delivery_charge=totals["delivery_charge"],
        grand_total=totals["grand_total"],
        delivery_zone=delivery_zone,
        confirmation_status="awaiting_confirmation",
        missing_fields=[],
    )
    sync_session_state(conversation, "awaiting_confirmation", pending_confirmation=summary)
    return confirm_draft_order(conversation)


def tool_get_order_status(user, order_id):
    try:
        sale = Sale.objects.prefetch_related("items").get(user=user, oid=order_id)
    except Sale.DoesNotExist:
        return {"error": f"Order '{order_id}' not found"}

    return {
        "order_id": sale.oid,
        "status": sale.status,
        "total": str(sale.amount),
        "customer": sale.customer_name,
        "phone": sale.customer_phone,
        "address": sale.customer_address,
        "items": [
            {"name": i.product_name, "qty": i.quantity, "price": str(i.price)}
            for i in sale.items.all()
        ],
        "created_at": sale.created_at.isoformat(),
    }


def tool_update_customer(conversation, name=None, phone=None, city=None, address=None):
    updates = {}
    if name:
        updates["customer_name"] = name
    if phone:
        updates["customer_phone"] = phone
    if city:
        updates["customer_city"] = city
    if address:
        updates["customer_address"] = address
    if updates:
        Conversation.objects.filter(pk=conversation.pk).update(**updates)
        for k, v in updates.items():
            setattr(conversation, k, v)

    # CRM: sync identity + record confirmed facts + signals.
    try:
        from context.crm.signals import (
            get_or_create_profile, record_fact, record_signal,
        )
        profile, _ = get_or_create_profile(conversation)
        if profile is not None:
            profile.name = (name or "").strip() or profile.name
            profile.phone = (phone or "").strip() or profile.phone
            profile.city = (city or "").strip() or profile.city
            profile.address = (address or "").strip() or profile.address
            profile.save(update_fields=["name", "phone", "city", "address", "updated_at"])
        if name:
            record_fact(conversation, "customer_name", name)
        if phone:
            record_fact(conversation, "customer_phone", phone)
        if city:
            record_fact(conversation, "customer_city", city)
        if address:
            record_signal(conversation, "provided_address")
            record_fact(conversation, "customer_address", address)
    except Exception:
        logger.exception("CRM update in update_customer failed conv=%s", conversation.pk)

    return {"updated": list(updates.keys())}


def tool_create_ticket(conversation, subject, description, priority="medium"):
    from back.models import SupportTicket
    ticket, created = SupportTicket.objects.get_or_create(
        conversation=conversation,
        defaults={"subject": subject, "description": description, "priority": priority},
    )
    if not created:
        ticket.subject = subject
        ticket.description = description
        ticket.priority = priority
        ticket.status = "open"
        ticket.resolved_at = None
        ticket.save()
    conversation.disable_ai()
    return {
        "ticket_id": ticket.pk,
        "subject": subject,
        "priority": priority,
        "transferred": True,
        "note": f"Ticket #{ticket.pk} created — AI disabled, human agent will take over",
    }




def tool_search_knowledge_base(user, query, limit=3):
    """Search RAG chunks (sample Q&A, knowledge base) via vector similarity."""
    from context.search import search_chunks
    results = search_chunks(user, query, top_k=limit, min_score=0.3)
    if not results:
        return {"results": [], "total": 0, "note": "No matching knowledge found"}
    return {"results": results, "total": len(results)}


def tool_think(notes):
    return {"ok": True, "notes": notes}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def _crm_search_hook(conversation, query, result):
    """CRM bookkeeping after a catalog search: signals + viewed products."""
    try:
        if not conversation:
            return
        from context.crm.signals import record_product_view, record_signal

        q = (query or "").lower()
        if re.search(r"(price|dam|দাম|koto|কত|taka|টাকা|বাজেট|budget)", q):
            record_signal(conversation, "asked_price")
        if re.search(r"(stock|স্টক|stock ache|আছে\?|কি আছে)", q):
            record_signal(conversation, "asked_stock")
        for p in ((result or {}).get("products") or [])[:5]:
            record_product_view(conversation, name=p.get("name", ""), pid=p.get("pid", ""))
    except Exception:
        logger.exception("CRM search hook failed conv=%s", getattr(conversation, "pk", None))


def _crm_product_detail_hook(conversation, result):
    try:
        if not conversation or not isinstance(result, dict):
            return
        from context.crm.signals import record_product_view, set_current_product

        pid = result.get("pid") or ""
        if pid:
            record_product_view(conversation, name=result.get("name", ""), pid=pid)
            set_current_product(conversation, pid)
    except Exception:
        logger.exception("CRM product detail hook failed conv=%s", getattr(conversation, "pk", None))


def _crm_send_images_hook(conversation, result):
    try:
        if not conversation or not isinstance(result, dict):
            return
        from context.crm.signals import record_signal, set_current_product

        products = result.get("products") or []
        if products:
            record_signal(conversation, "asked_photo")
            set_current_product(conversation, products[0].get("pid", ""))
    except Exception:
        logger.exception("CRM send_images hook failed conv=%s", getattr(conversation, "pk", None))


def _crm_ticket_hook(conversation, result):
    """Escalation bookkeeping: mark the opportunity lost + log the event."""
    try:
        if not conversation:
            return
        from context.crm_models import CrmEvent, SalesOpportunity

        opp = SalesOpportunity.objects.filter(conversation=conversation).first()
        if opp and opp.status == "open":
            opp.stage = "lost"
            opp.status = "lost"
            opp.save(update_fields=["stage", "status", "updated_at"])
        ticket_id = (result or {}).get("ticket_id")
        CrmEvent.objects.create(
            user=conversation.user,
            conversation=conversation,
            type="ticket_created",
            description=f"Support ticket created (opportunity lost)",
            data={"ticket_id": ticket_id},
        )
    except Exception:
        logger.exception("CRM ticket hook failed conv=%s", getattr(conversation, "pk", None))


def execute_tool(name, arguments, user, conversation):
    try:
        args = arguments if isinstance(arguments, dict) else {}

        if name == "search_products":
            result = tool_search_products(
                user,
                args.get("query", ""),
                min(int(args.get("limit", 5)), 5),
                conversation=conversation,
                min_price=args.get("min_price"),
                max_price=args.get("max_price"),
            )
            _crm_search_hook(conversation, args.get("query", ""), result)
            return result

        if name == "get_product_details":
            result = tool_get_product_details(user, args.get("pid", ""), conversation=conversation)
            _crm_product_detail_hook(conversation, result)
            return result

        if name == "send_images":
            result = tool_send_images(user, pid=args.get("pid", ""), pids=args.get("pids"), conversation=conversation)
            _crm_send_images_hook(conversation, result)
            return result

        if name == "create_order":
            return tool_create_order(
                user=user,
                conversation=conversation,
                customer_name=args.get("customer_name", ""),
                customer_phone=args.get("customer_phone", ""),
                customer_address=args.get("customer_address", ""),
                customer_city=args.get("customer_city", ""),
                delivery_zone=args.get("delivery_zone", "inside_dhaka"),
                items=args.get("items", []),
                customer_confirmed=bool(args.get("customer_confirmed", False)),
            )

        if name == "get_order_status":
            return tool_get_order_status(user, args.get("order_id", ""))

        if name == "update_customer":
            return tool_update_customer(
                conversation=conversation,
                name=args.get("name"),
                phone=args.get("phone"),
                city=args.get("city"),
                address=args.get("address"),
            )

        if name == "create_ticket":
            result = tool_create_ticket(
                conversation=conversation,
                subject=args.get("subject", ""),
                description=args.get("description", ""),
                priority=args.get("priority", "medium"),
            )
            _crm_ticket_hook(conversation, result)
            return result

        if name == "search_knowledge_base":
            return tool_search_knowledge_base(
                user=user,
                query=args.get("query", ""),
                limit=int(args.get("limit", 3)),
            )

        if name == "think":
            return tool_think(args.get("notes", ""))

        return {"error": f"Unknown tool: {name}"}

    except Exception as exc:
        logger.exception("Tool '%s' raised an exception", name)
        return {"error": str(exc)}
