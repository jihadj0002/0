import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import DecimalField, F, Q, Value
from django.db.models.functions import Coalesce

from back.models import Conversation, OrderItem, Product, ProductImages, Sale
from context.models import AgentIdentity, StoreConfig, BehaviorRules

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared Dataclasses (used across all components)
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    state: str = "success"  # "success" | "error" | "empty" | "permission_denied"
    tool: str = ""
    data: dict | None = None
    error: str | None = None
    execution_time_ms: int = 0
    cached: bool = False

    @classmethod
    def success(cls, data=None, tool="", execution_time_ms=0):
        return cls(state="success", tool=tool, data=data, execution_time_ms=execution_time_ms)

    @classmethod
    def as_error(cls, message, tool="", execution_time_ms=0):
        return cls(state="error", tool=tool, error=message, execution_time_ms=execution_time_ms)

    @classmethod
    def empty(cls, tool="", execution_time_ms=0):
        return cls(state="empty", tool=tool, execution_time_ms=execution_time_ms)

    @classmethod
    def permission_denied(cls, tool="", execution_time_ms=0):
        return cls(state="permission_denied", tool=tool, execution_time_ms=execution_time_ms)


# ---------------------------------------------------------------------------
# BaseTool — all tools inherit from this
# ---------------------------------------------------------------------------

class BaseTool:
    name: str = ""
    description: str = ""
    parameters: dict = {}
    permission: str = "public"  # "public" | "customer" | "staff" | "manager" | "owner"
    timeout_ms: int = 10000
    retry_count: int = 1

    def execute(self, args: dict, user, conversation) -> ToolResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# ToolRegistry — central registry for all tools
# ---------------------------------------------------------------------------

class ToolRegistry:
    _tools: dict[str, BaseTool] = {}

    @classmethod
    def register(cls, tool):
        if isinstance(tool, type):
            tool = tool()
        cls._tools[tool.name] = tool

    @classmethod
    def get(cls, name) -> BaseTool | None:
        return cls._tools.get(name)

    @classmethod
    def execute(cls, name, args, user, conversation, timeout=None, retries=None):
        tool = cls.get(name)
        if not tool:
            return ToolResult.as_error(f"Unknown tool: {name}", tool=name)
        t0 = time.time()
        try:
            result = tool.execute(args, user, conversation)
            result.execution_time_ms = int((time.time() - t0) * 1000)
            return result
        except Exception as exc:
            elapsed = int((time.time() - t0) * 1000)
            return ToolResult.as_error(str(exc), tool=name, execution_time_ms=elapsed)

    @classmethod
    def get_definitions(cls):
        """Return OpenAI-compatible tool definitions for all registered tools."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in cls._tools.values()
        ]

    @classmethod
    def get_all_tools(cls) -> list[BaseTool]:
        return list(cls._tools.values())

    @classmethod
    def reset(cls):
        """Clear all registered tools (for testing)."""
        cls._tools = {}


# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "Search products by SKU, name, or keyword. You can optionally specify min_price and/or max_price to narrow results by budget. Try calling this MULTIPLE times with different keywords (try English, synonyms, simpler terms) until you find what the customer wants. Call this before quoting any price.",
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
            "description": "Get fresh price/stock for a product by PID. Only use as last resort — focused products (in system prompt) already have complete data including price, stock, description, variations. For focused products just call send_images.",
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
            "description": "Send product images to the customer. Returns name and price. For a single PID, all product images are sent one-by-one. For multiple PIDs via pids=[...], a scrollable carousel is shown. Mention name and price briefly in your reply after sending.",
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
            "description": "Create a new pending order. Only call after you have confirmed the items with the customer and collected name, phone, and address.",
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
            "description": "Look up an existing order by its order ID.",
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
            "description": "Save or update customer contact details in the conversation record.",
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
            "description": "Create a support ticket and hand the conversation to a human agent. Use when: customer requests human, complaint escalation, or issue is beyond AI scope.",
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
            "description": "Search business knowledge: policies, FAQs, return/exchange info, shipping, payment methods, company info, and training Q&A. Do NOT use for product queries — use search_products.",
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
            "description": "Private thinking step. Use to outline your next actions before calling tools. Do NOT include customer-facing text. This does not message the customer.",
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
            "If this matches, mention the name + price in one short line. "
            "If the tool results include images, tell the customer the image is attached."
        )
    return prefix + (
        "If several genuinely match and the customer is browsing, present them as a "
        "short list with prices; if the tool results include images, mention the images "
        "are attached. If the customer asked about one specific item, focus on that one. "
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
        payload["description"] = desc[:300]
    variations = product.get("variations") or []
    if variations:
        payload["variations"] = [
            {
                "variation_id": v.get("variation_id"),
                "name": v.get("name"),
                "price": v.get("price"),
                "in_stock": v.get("in_stock", True),
            }
            for v in variations[:12]
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


_GENERIC_QUERY_RE = re.compile(
    r"^(ki|কি|কী|kono|কোনো|kon|কোন|apnader|আপনাদের|tumar|তোমার)"
    r".*(ache|আছে|product|পণ্য|dekhan|দেখান|kis|কিস|ki|কি)",
    re.IGNORECASE,
)
_GENERIC_PHRASES = {
    "product", "products", "show", "show products", "show all", "list",
    "all", "all products", "product list", "catalog", "catalogue",
    "inventory", "stock", "ache", "dekhan", "দেখান", "সব", "সবগুলো",
    "what do you have", "what products", "what you got", "everything",
}


def _is_generic_catalog_query(query: str) -> bool:
    """True when the query asks to see the catalog rather than a specific item.

    Handles Bengali, romanized Bengali ("ki ache", "ki product ache",
    "dekhan"), and English ("what do you have", "show products").
    """
    if not query or not query.strip():
        return True
    q = query.strip()
    lowered = q.lower()
    if lowered in _GENERIC_PHRASES:
        return True
    if "show" in lowered and "product" in lowered:
        return True
    if "ache" in lowered and ("ki " in lowered or lowered.startswith("ki")
                              or "apnader" in lowered or "product" in lowered):
        return True
    return bool(_GENERIC_QUERY_RE.search(q))


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

    # 2. Remove non-alphanumeric (keep spaces). Bengali vowel signs (া ি ী …)
    #    are combining marks and NOT matched by \w — they must be kept, or
    #    every Bengali query is corrupted before latinization.
    stripped = re.sub(r"[^\w\s\u0980-\u09FF]", "", cleaned).strip()
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

    # 6. Bengali → romanized latin (e.g. "জলপাইয়ের আচার" → "jolpaiyer achar").
    #    Customers often type Bengali words in latin script ("jolpai achar"),
    #    so each Bengali query is also searched in its romanized form.
    latin = _latinize_bn(cleaned)
    if latin and latin not in seen:
        seen.add(latin)
        yield latin
    for w in stripped.split():
        wl = _latinize_bn(w)
        if wl and len(wl) >= 3 and wl not in seen:
            seen.add(wl)
            yield wl
        # 7. Prefix truncations: Bengali inflections ("জলপাইয়ের" → "jolpaiyer")
        #    carry a suffix that blocks substring matches against the base name
        #    ("Jolpaia achar"). Matching on a leading prefix recovers them.
        if wl and len(wl) > 6:
            for pre in (wl[:6], wl[:4]):
                if len(pre) >= 4 and pre not in seen:
                    seen.add(pre)
                    yield pre


_BN_LATIN_MAP = {
    "আ": "a", "অ": "o", "ই": "i", "ঈ": "i", "উ": "u", "ঊ": "u",
    "এ": "e", "ঐ": "oi", "ও": "o", "ঔ": "ou",
    "ক": "k", "খ": "kh", "গ": "g", "ঘ": "gh", "ঙ": "ng",
    "চ": "ch", "ছ": "ch", "জ": "j", "ঝ": "jh", "ঞ": "n",
    "ট": "t", "ঠ": "th", "ড": "d", "ঢ": "dh", "ণ": "n",
    "ত": "t", "থ": "th", "দ": "d", "ধ": "dh", "ন": "n",
    "প": "p", "ফ": "ph", "ব": "b", "ভ": "bh", "ম": "m",
    "য": "j", "র": "r", "ল": "l", "শ": "sh", "ষ": "sh", "স": "s", "হ": "h",
    "ড়": "r", "ঢ়": "rh", "য়": "y", "ৎ": "t", "ং": "ng", "ঃ": "h", "ঁ": "n",
    "া": "a", "ি": "i", "ী": "i", "ু": "u", "ূ": "u",
    "ৃ": "ri", "ে": "e", "ৈ": "oi", "ো": "o", "ৌ": "ou", "ৗ": "o",
}

# Consonants get an inherent vowel ("o") unless followed by a vowel sign,
# virama (conjunct marker), or the end of a word.
_BN_CONSONANTS = set("কখগঘঙচছজঝঞটঠডঢণতথদধনপফবভমযরলশষসহড়ঢ়য়ৎ")
_BN_VOWEL_SIGNS = set("ািীুূৃেৈোৌৗঁংঃ")

_BN_VIROMA = "্"

# Nukta pairs must be handled before the letter loop ("য়" is two codepoints)
_BN_NUKTA_PAIRS = {"ড়": "r", "ঢ়": "rh", "য়": "y"}


def _latinize_bn(text: str) -> str:
    """Best-effort Bengali → romanized latin transliteration.

    Letter-by-letter with a single inherent vowel ("o") per word:
    "জলপাইয়ের আচার" → "jolpaiyer achar", "আমের" → "amer", "আচার" → "achar".
    Approximate, but sufficient for substring search against latin product
    names ("jolpai" ⊂ "jolpaiyer achar").
    """
    if not text or not any("\u0980" <= c <= "\u09FF" for c in text):
        return ""
    out = []
    i = 0
    n = len(text)
    word_has_inherent = False
    while i < n:
        c = text[i]
        if c.isspace():
            out.append(c)
            word_has_inherent = False
            i += 1
            continue
        pair = text[i:i + 2]
        if pair in _BN_NUKTA_PAIRS:
            out.append(_BN_NUKTA_PAIRS[pair])
            if pair[0] in _BN_CONSONANTS:
                nxt = text[i + 2] if i + 2 < n else ""
                if not word_has_inherent and nxt not in _BN_VOWEL_SIGNS \
                        and nxt != _BN_VIROMA and nxt and "\u0980" <= nxt <= "\u09FF":
                    out.append("o")
                    word_has_inherent = True
            i += 2
            continue
        if c == _BN_VIROMA:
            i += 1
            continue
        if c in _BN_LATIN_MAP:
            out.append(_BN_LATIN_MAP[c])
            if c in _BN_CONSONANTS:
                nxt = text[i + 1] if i + 1 < n else ""
                if not word_has_inherent and nxt not in _BN_VOWEL_SIGNS \
                        and nxt != _BN_VIROMA and nxt and "\u0980" <= nxt <= "\u09FF":
                    out.append("o")
                    word_has_inherent = True
        else:
            out.append(c)
        i += 1
    return re.sub(r"\s+", " ", "".join(out)).strip()


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
                        "unavailable. The customer may be referring to a product discussed earlier "
                        "in the conversation (see 'Conversation so far'). Do NOT present unrelated products."
                    ),
                }
            # Empty query — fall through to local DB / featured handling.
    except Exception:
        logger.exception("Live search_products failed; falling back to local DB")

    # If a product is already focused for this conversation, return it directly
    # — but ONLY when the query actually refers to it. Without this guard the
    # shortcut hijacks every later search ("jolpai" would return the stale
    # "Amer Achar" focus). Generic catalog queries skip the shortcut entirely.
    if not _is_generic_catalog_query(query):
        focus_pid = _focus_pid(conversation)
        if focus_pid:
            try:
                product = Product.objects.get(user=user, pid=focus_pid, status=True)
                if _query_matches_product(query, product):
                    if min_price is not None or max_price is not None:
                        eff = float(product.discounted_price or product.price or 0)
                        in_budget = True
                        if min_price is not None and eff < min_price:
                            in_budget = False
                        if max_price is not None and eff > max_price:
                            in_budget = False
                        if not in_budget:
                            _clear_focus_product(conversation)
                        else:
                            return {
                                "products": [_product_row(product)],
                                "total": 1,
                                "selected_product": True,
                            }
                    else:
                        return {
                            "products": [_product_row(product)],
                            "total": 1,
                            "selected_product": True,
                        }
            except Product.DoesNotExist:
                _clear_focus_product(conversation)

    # Generic / empty query → show featured first, then fill with anything
    generic = _is_generic_catalog_query(query)
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

    results = [_product_row(p) for p in products_list]

    if results and conversation:
        _focus_products(conversation, results[:FOCUS_MAX])

    out = {"products": results, "total": len(results)}
    if results:
        out["_instruction"] = _search_result_instruction(len(results), query)
    elif not generic and query and query.strip():
        # No match — but the conversation may have discussed products before.
        # Return those as candidates so the LLM can answer from real data
        # (e.g. price-negotiation messages that name no product).
        focus_list = parse_focus_products(getattr(conversation, "current_product", "") if conversation else "")
        if focus_list:
            out["products"] = focus_list
            out["total"] = len(focus_list)
            out["_instruction"] = (
                f'No catalog items matched "{query}". The products above are the ones '
                "discussed earlier in THIS conversation — if the customer is referring to "
                "one of them, use its real price/stock data above. Do NOT invent data."
            )
        else:
            out["_instruction"] = (
                f'No catalog items matched "{query}". Either try ONE more search with a '
                "different/simpler keyword or synonym, or tell the customer it's currently "
                "unavailable. The customer may be referring to a product discussed earlier "
                "in the conversation (see 'Conversation so far'). Do NOT present unrelated products."
            )
    return out


def _query_matches_product(query, product):
    """True when the query directly names the product (name substring either
    way). Token matching is deliberately NOT used here — a token like "achar"
    matches several products, and the full search handles those cases."""
    if not query or not query.strip():
        return True
    lowered = query.strip().lower()
    name = (product.name or "").strip().lower()
    if not name:
        return False
    return name in lowered or lowered in name


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
                    "pid": r.get("sku") or r["external_id"],
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
    from back.models import Product

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
        # Catalog browse: "sob product er photo pathan" with no product in
        # focus — send cards for the whole (active) catalog instead of erroring.
        all_pids = list(
            Product.objects.filter(user=user, status=True)
            .values_list("pid", flat=True)[:8]
        )
        if all_pids:
            requested.extend(all_pids)
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


def tool_create_order(user, conversation, customer_name, customer_phone, customer_address, items,
                      customer_city="", delivery_zone="inside_dhaka"):
    from decimal import Decimal

    from api.products.factory import get_active_source, get_provider, is_external

    # Determine external context once.
    source = get_active_source(user)
    external_active = bool(source) and is_external(user)
    live_mode = bool(source) and source.mode == "live" and external_active
    provider = None
    if live_mode:
        try:
            provider = get_provider(user)
        except Exception:
            logger.exception("Could not load provider for create_order; treating as non-live")
            provider = None

    # Each resolved entry:
    #   (product_or_None, qty, unit_price, product_name, external_id, variation_id)
    resolved = []
    errors = []

    for item in items:
        pid = item.get("pid", "")
        qty = max(int(item.get("quantity", 1)), 1)
        requested_vid = item.get("variation_id")

        # 1) Local product by pid (system of record / internal / sync).
        product = Product.objects.filter(user=user, pid=pid, status=True).first()

        # 2) Synced external product matched by external_id.
        if product is None and external_active:
            product = Product.objects.filter(user=user, external_id=pid).first()

        if product is not None:
            if product.stock_quantity < qty:
                errors.append(f"{product.name}: only {product.stock_quantity} left in stock")
                continue
            unit_price = product.discounted_price or product.price
            resolved.append((product, qty, unit_price, product.name,
                             product.external_id or None, requested_vid or None))
            continue

        # 3) Live external product with no local row — look up via provider.
        if live_mode and provider is not None:
            r = None
            try:
                r = provider.get_product(pid)
            except Exception:
                logger.exception("Live get_product failed for pid=%s during create_order", pid)
            if not r:
                try:
                    results = provider.search(pid, limit=1)
                    r = results[0] if results else None
                except Exception:
                    pass
            if r:
                variations = r.get("variations") or []
                chosen = None
                if variations:
                    if requested_vid:
                        chosen = next(
                            (v for v in variations
                             if str(v.get("variation_id")) == str(requested_vid)),
                            None,
                        )
                        if chosen is None:
                            errors.append(f"{r.get('name') or pid}: variation '{requested_vid}' not found")
                            continue
                    elif len(variations) == 1:
                        chosen = variations[0]
                    else:
                        opts = ", ".join(
                            f"{v.get('name')} (variation_id={v.get('variation_id')})"
                            for v in variations
                        )
                        errors.append(
                            f"{r.get('name') or pid}: choose a variation before ordering — options: {opts}"
                        )
                        continue
                raw_price = (
                    (chosen or {}).get("promotion_price")
                    or (chosen or {}).get("price")
                    or r.get("discounted_price") or r.get("price") or "0"
                )
                try:
                    unit_price = Decimal(str(raw_price))
                except Exception:
                    unit_price = Decimal("0")
                vid = chosen.get("variation_id") if chosen else (requested_vid or None)
                resolved.append((None, qty, unit_price, r.get("name") or pid, r["external_id"], vid))
                continue

        errors.append(f"Product '{pid}' not found")

    if errors:
        return {"error": "Cannot create order", "details": errors}

    with transaction.atomic():
        sale = Sale.objects.create(
            user=user,
            conversation=conversation,
            customer_id=conversation.customer_id,
            customer_name=customer_name,
            customer_phone=customer_phone,
            customer_address=customer_address,
            customer_city=customer_city,
            delivered_to=delivery_zone,
            status="pending",
            amount=0,
        )
        total = 0
        line_items = []
        for product, qty, unit_price, product_name, external_id, variation_id in resolved:
            OrderItem.objects.create(
                order=sale,
                product=product,
                product_name=product_name,
                price=unit_price,
                quantity=qty,
                action="base",
                external_product_id=external_id or None,
                external_variation_id=str(variation_id) if variation_id else None,
            )
            # Only adjust stock for local rows.
            if product is not None:
                product.stock_quantity -= qty
                product.save(update_fields=["stock_quantity"])
            total += unit_price * qty
            line_items.append({"name": product_name, "qty": qty, "unit_price": str(unit_price)})

         # Add delivery charge
        store_config = StoreConfig.objects.filter(user=user).first()
        if delivery_zone == "inside_dhaka":
            delivery_charge = store_config.delivery_charge_inside if store_config else 0
        else:
            delivery_charge = store_config.delivery_charge_outside if store_config else 0

        sale.amount = total + delivery_charge
        sale.save(update_fields=["amount"])

    # Backfill conversation customer fields
    Conversation.objects.filter(pk=conversation.pk).update(
        customer_name=customer_name or conversation.customer_name,
        customer_phone=customer_phone or conversation.customer_phone,
        customer_city=customer_city or conversation.customer_city,
    )

    # Push to the user's external source (safe to call always; no-op when internal).
    push_result = {}
    try:
        from api.products.orders import push_order_to_source
        push_result = push_order_to_source(sale) or {}
    except Exception:
        logger.exception("push_order_to_source failed for order %s", sale.oid)
        push_result = {}

    return {
        "order_id": sale.oid,
        "status": sale.status,
        "total": str(sale.amount),
        "items": line_items,
        "synced_to_store": bool(push_result.get("ok") and not push_result.get("skipped")),
        "external_order_id": sale.external_order_id,
    }


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
    results = search_chunks(user, query, top_k=limit, min_score=0.0)
    if not results:
        return {"results": [], "total": 0, "note": "No matching knowledge found"}
    return {"results": results, "total": len(results)}


def tool_think(notes):
    return {"ok": True, "notes": notes}


def tool_check_inventory(user, sku_or_name="", min_stock=None):
    """P2-1: Stock level by SKU/name with optional low-stock threshold alerts."""
    qs = Product.objects.filter(user=user, status=True)
    if sku_or_name and sku_or_name.strip():
        qs = qs.filter(
            Q(name__icontains=sku_or_name)
            | Q(pid__icontains=sku_or_name)
            | Q(external_id__icontains=sku_or_name)
        )
    products = list(qs[:20])
    if not products:
        return {"error": "No products found", "items": []}

    items = []
    low = []
    for p in products:
        row = {
            "pid": p.pid,
            "name": p.name,
            "stock": p.stock_quantity,
            "in_stock": p.stock_quantity > 0,
            "price": str(p.price),
        }
        items.append(row)
        threshold = min_stock if min_stock is not None else 5
        if p.stock_quantity <= threshold:
            low.append({"pid": p.pid, "name": p.name, "stock": p.stock_quantity})

    return {"items": items, "total": len(items), "low_stock_alerts": low}


def tool_find_previous_tickets(user, conversation=None, keyword="", status=""):
    """P2-4: Search customer ticket history by keyword/status."""
    from back.models import SupportTicket

    qs = SupportTicket.objects.filter(conversation__user=user)
    if conversation:
        qs = qs.filter(conversation=conversation)
    if status and status.strip():
        qs = qs.filter(status=status.strip())
    if keyword and keyword.strip():
        qs = qs.filter(Q(subject__icontains=keyword) | Q(description__icontains=keyword))

    tickets = list(qs.order_by("-created_at")[:10])
    return {
        "tickets": [
            {
                "id": t.pk,
                "subject": t.subject,
                "status": t.status,
                "priority": t.priority,
                "created_at": t.created_at.isoformat() if t.created_at else "",
            }
            for t in tickets
        ],
        "total": len(tickets),
    }


def tool_get_payment_link(user, order_id):
    """P2-6: Generate a payment link for a pending order."""
    try:
        sale = Sale.objects.get(user=user, oid=order_id)
    except Sale.DoesNotExist:
        return {"error": f"Order '{order_id}' not found"}

    if sale.status not in ("pending", "draft"):
        return {"error": f"Order '{order_id}' is {sale.status} — only pending orders can be paid"}

    # Payment link is deterministic per order (placeholder for real gateway)
    from django.conf import settings

    base = getattr(settings, "PAYMENT_BASE_URL", "")
    token = sale.oid  # simple token — replace with signed token when a gateway ships
    link = f"{base}/pay/{token}" if base else f"/db/orders/pay/{sale.oid}"
    return {
        "order_id": sale.oid,
        "payment_link": link,
        "amount": str(sale.amount),
        "status": sale.status,
    }


def tool_track_shipment(user, order_id):
    """P2-7: Tracking info for an order by ID."""
    try:
        sale = Sale.objects.select_related("conversation").get(user=user, oid=order_id)
    except Sale.DoesNotExist:
        return {"error": f"Order '{order_id}' not found"}

    # Orders have no courier integration yet — derive status text from Sale status
    status_map = {
        "draft": "not placed yet",
        "pending": "pending — awaiting confirmation",
        "delivering": "out for delivery",
        "completed": "delivered",
        "refunded": "refunded",
    }
    return {
        "order_id": sale.oid,
        "status": sale.status,
        "status_text": status_map.get(sale.status, sale.status),
        "address": sale.customer_address or "",
        "city": sale.customer_city or "",
        "carrier": "pending assignment",
        "tracking_number": None,
        "estimated_delivery": None,
    }


def tool_get_sales_summary(user, period="today", limit=5):
    """P2-11: Revenue, order count, AOV, top products by period.

    period: today | yesterday | week | month | all
    """
    from datetime import timedelta

    from django.db.models import Sum
    from django.utils import timezone

    now = timezone.now()
    if period == "today":
        start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "yesterday":
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start = now - timedelta(days=7)
    elif period == "month":
        start = now - timedelta(days=30)
    else:
        start = None

    qs = Sale.objects.filter(user=user)
    if start:
        qs = qs.filter(created_at__gte=start)

    sales = list(qs)
    revenue = sum((float(s.amount) for s in sales if s.amount), 0.0)
    count = len(sales)
    aov = round(revenue / count, 2) if count else 0

    # Top products by units sold (from order items)
    top = []
    if sales:
        from back.models import OrderItem

        item_rows = (
            OrderItem.objects.filter(order__in=sales)
            .values("product_name")
            .annotate(units=Sum("quantity"))
            .order_by("-units")[:limit]
        )
        for r in item_rows:
            top.append({"name": r["product_name"] or "Unknown", "units": r["units"]})

    return {
        "period": period,
        "orders": count,
        "revenue": str(round(revenue, 2)),
        "average_order_value": str(aov),
        "top_products": top,
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def execute_tool(name, arguments, user, conversation):
    try:
        args = arguments if isinstance(arguments, dict) else {}

        if name == "search_products":
            return tool_search_products(
                user,
                args.get("query", ""),
                int(args.get("limit", 5)),
                conversation=conversation,
                min_price=args.get("min_price"),
                max_price=args.get("max_price"),
            )

        if name == "get_product_details":
            return tool_get_product_details(user, args.get("pid", ""), conversation=conversation)

        if name == "send_images":
            return tool_send_images(user, pid=args.get("pid", ""), pids=args.get("pids"), conversation=conversation)

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
            return tool_create_ticket(
                conversation=conversation,
                subject=args.get("subject", ""),
                description=args.get("description", ""),
                priority=args.get("priority", "medium"),
            )

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


# ---------------------------------------------------------------------------
# BaseTool subclasses (wrap old-style functions for ToolRegistry)
# ---------------------------------------------------------------------------

class SearchProductsTool(BaseTool):
    name = "search_products"
    description = "Search products by SKU, name, or keyword. You can optionally specify min_price and/or max_price to narrow results by budget. Try calling this MULTIPLE times with different keywords (try English, synonyms, simpler terms) until you find what the customer wants. Call this before quoting any price."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search term — SKU code, product name, or keyword"},
            "limit": {"type": "integer", "description": "Max results (default 10)"},
            "min_price": {"type": "number", "description": "Minimum price / budget floor — optional; omit if no lower bound"},
            "max_price": {"type": "number", "description": "Maximum price / budget ceiling — optional; e.g. if customer says 'budget of 500 taka' pass 500"},
        },
        "required": ["query"],
    }
    permission = "public"

    def execute(self, args, user, conversation):
        result = tool_search_products(
            user,
            args.get("query", ""),
            int(args.get("limit", 5)),
            conversation=conversation,
            min_price=args.get("min_price"),
            max_price=args.get("max_price"),
        )
        if isinstance(result, dict) and "error" in result:
            return ToolResult.as_error(result["error"], tool=self.name)
        return ToolResult.success(result, tool=self.name)


class GetProductDetailsTool(BaseTool):
    name = "get_product_details"
    description = "Get fresh price/stock for a product by PID. Only use as last resort — focused products (in system prompt) already have complete data including price, stock, description, variations. For focused products just call send_images."
    parameters = {
        "type": "object",
        "properties": {
            "pid": {"type": "string", "description": "Product PID e.g. sku_abc123"},
        },
        "required": ["pid"],
    }
    permission = "public"

    def execute(self, args, user, conversation):
        result = tool_get_product_details(user, args.get("pid", ""), conversation=conversation)
        if isinstance(result, dict) and "error" in result:
            return ToolResult.as_error(result["error"], tool=self.name)
        return ToolResult.success(result, tool=self.name)


class SendImagesTool(BaseTool):
    name = "send_images"
    description = "Send product images to the customer. Returns name and price. For a single PID, all product images are sent one-by-one. For multiple PIDs via pids=[...], a scrollable carousel is shown. Mention name and price briefly in your reply after sending."
    parameters = {
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
    }
    permission = "public"

    def execute(self, args, user, conversation):
        result = tool_send_images(user, pid=args.get("pid", ""), pids=args.get("pids"), conversation=conversation)
        if isinstance(result, dict) and "error" in result:
            return ToolResult.as_error(result["error"], tool=self.name)
        return ToolResult.success(result, tool=self.name)


class CreateOrderTool(BaseTool):
    name = "create_order"
    description = "Create a new pending order. Only call after you have confirmed the items with the customer and collected name, phone, and address."
    parameters = {
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
    }
    permission = "customer"

    def execute(self, args, user, conversation):
        result = tool_create_order(
            user=user,
            conversation=conversation,
            customer_name=args.get("customer_name", ""),
            customer_phone=args.get("customer_phone", ""),
            customer_address=args.get("customer_address", ""),
            customer_city=args.get("customer_city", ""),
            delivery_zone=args.get("delivery_zone", "inside_dhaka"),
            items=args.get("items", []),
        )
        if isinstance(result, dict) and "error" in result:
            return ToolResult.as_error(str(result.get("details", result["error"])), tool=self.name)
        return ToolResult.success(result, tool=self.name)


class GetOrderStatusTool(BaseTool):
    name = "get_order_status"
    description = "Look up an existing order by its order ID."
    parameters = {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "Order oid e.g. ord_abc123"},
        },
        "required": ["order_id"],
    }
    permission = "customer"

    def execute(self, args, user, conversation):
        result = tool_get_order_status(user, args.get("order_id", ""))
        if isinstance(result, dict) and "error" in result:
            return ToolResult.as_error(result["error"], tool=self.name)
        return ToolResult.success(result, tool=self.name)


class UpdateCustomerTool(BaseTool):
    name = "update_customer"
    description = "Save or update customer contact details in the conversation record."
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "phone": {"type": "string"},
            "city": {"type": "string"},
            "address": {"type": "string"},
        },
        "required": [],
    }
    permission = "public"

    def execute(self, args, user, conversation):
        result = tool_update_customer(
            conversation=conversation,
            name=args.get("name"),
            phone=args.get("phone"),
            city=args.get("city"),
            address=args.get("address"),
        )
        return ToolResult.success(result, tool=self.name)


class CreateTicketTool(BaseTool):
    name = "create_ticket"
    description = "Create a support ticket and hand the conversation to a human agent. Use when: customer requests human, complaint escalation, or issue is beyond AI scope."
    parameters = {
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
    }
    permission = "public"

    def execute(self, args, user, conversation):
        result = tool_create_ticket(
            conversation=conversation,
            subject=args.get("subject", ""),
            description=args.get("description", ""),
            priority=args.get("priority", "medium"),
        )
        return ToolResult.success(result, tool=self.name)


class SearchKnowledgeBaseTool(BaseTool):
    name = "search_knowledge_base"
    description = "Search business knowledge: policies, FAQs, return/exchange info, shipping, payment methods, company info, and training Q&A. Do NOT use for product queries — use search_products."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to look up (e.g. 'return policy', 'shipping time', 'payment methods')"},
            "limit": {"type": "integer", "description": "Max results (default 3)"},
        },
        "required": ["query"],
    }
    permission = "public"

    def execute(self, args, user, conversation):
        result = tool_search_knowledge_base(
            user=user,
            query=args.get("query", ""),
            limit=int(args.get("limit", 3)),
        )
        return ToolResult.success(result, tool=self.name)


class ThinkTool(BaseTool):
    name = "think"
    description = "Private thinking step. Use to outline your next actions before calling tools. Do NOT include customer-facing text. This does not message the customer."
    parameters = {
        "type": "object",
        "properties": {
            "notes": {"type": "string", "description": "Short internal plan (1-3 lines)"},
        },
        "required": ["notes"],
    }
    permission = "public"

    def execute(self, args, user, conversation):
        result = tool_think(args.get("notes", ""))
        return ToolResult.success(result, tool=self.name)


class CheckInventoryTool(BaseTool):
    name = "check_inventory"
    description = "Check stock levels by product name or SKU. Returns current stock, availability, and low-stock alerts."
    parameters = {
        "type": "object",
        "properties": {
            "sku_or_name": {"type": "string", "description": "Product name, SKU (pid), or external id. Empty = all products."},
            "min_stock": {"type": "integer", "description": "Optional threshold — products at or below this stock are flagged as low"},
        },
    }
    permission = "customer"

    def execute(self, args, user, conversation):
        result = tool_check_inventory(user, args.get("sku_or_name", ""), args.get("min_stock"))
        if isinstance(result, dict) and "error" in result:
            return ToolResult.as_error(result["error"], tool=self.name)
        return ToolResult.success(result, tool=self.name)


class FindPreviousTicketsTool(BaseTool):
    name = "find_previous_tickets"
    description = "Search the customer's previous support tickets by keyword or status."
    parameters = {
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "Keyword to match against ticket subject/description"},
            "status": {"type": "string", "description": "Filter by status (e.g. open, resolved)"},
        },
    }
    permission = "customer"

    def execute(self, args, user, conversation):
        result = tool_find_previous_tickets(user, conversation, args.get("keyword", ""), args.get("status", ""))
        return ToolResult.success(result, tool=self.name)


class GetPaymentLinkTool(BaseTool):
    name = "get_payment_link"
    description = "Generate a payment link for a pending order so the customer can pay."
    parameters = {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "Order oid e.g. ord_abc123"},
        },
        "required": ["order_id"],
    }
    permission = "customer"

    def execute(self, args, user, conversation):
        result = tool_get_payment_link(user, args.get("order_id", ""))
        if isinstance(result, dict) and "error" in result:
            return ToolResult.as_error(result["error"], tool=self.name)
        return ToolResult.success(result, tool=self.name)


class TrackShipmentTool(BaseTool):
    name = "track_shipment"
    description = "Get tracking / delivery status for an order by order ID."
    parameters = {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "Order oid e.g. ord_abc123"},
        },
        "required": ["order_id"],
    }
    permission = "customer"

    def execute(self, args, user, conversation):
        result = tool_track_shipment(user, args.get("order_id", ""))
        if isinstance(result, dict) and "error" in result:
            return ToolResult.as_error(result["error"], tool=self.name)
        return ToolResult.success(result, tool=self.name)


class GetSalesSummaryTool(BaseTool):
    name = "get_sales_summary"
    description = "Get sales metrics: order count, revenue, average order value, and top products for a period."
    parameters = {
        "type": "object",
        "properties": {
            "period": {"type": "string", "enum": ["today", "yesterday", "week", "month", "all"], "description": "Time window. Default: today"},
            "limit": {"type": "integer", "description": "Max number of top products. Default: 5"},
        },
    }
    permission = "owner"

    def execute(self, args, user, conversation):
        result = tool_get_sales_summary(user, args.get("period", "today"), args.get("limit", 5))
        return ToolResult.success(result, tool=self.name)


# ---------------------------------------------------------------------------
# Register all tools
# ---------------------------------------------------------------------------

ToolRegistry.register(SearchProductsTool)
ToolRegistry.register(GetProductDetailsTool)
ToolRegistry.register(SendImagesTool)
ToolRegistry.register(CreateOrderTool)
ToolRegistry.register(GetOrderStatusTool)
ToolRegistry.register(UpdateCustomerTool)
ToolRegistry.register(CreateTicketTool)
ToolRegistry.register(SearchKnowledgeBaseTool)
ToolRegistry.register(ThinkTool)
ToolRegistry.register(CheckInventoryTool)
ToolRegistry.register(FindPreviousTicketsTool)
ToolRegistry.register(GetPaymentLinkTool)
ToolRegistry.register(TrackShipmentTool)
ToolRegistry.register(GetSalesSummaryTool)
