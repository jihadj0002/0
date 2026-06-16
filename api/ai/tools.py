import json
import logging
import re

from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import DecimalField, F, Q, Value
from django.db.models.functions import Coalesce

from back.models import Conversation, OrderItem, Product, ProductImages, Sale
from context.models import AgentIdentity, StoreConfig, BehaviorRules

logger = logging.getLogger(__name__)


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
                    "limit": {"type": "integer", "description": "Max results (default 10)", "default": 10},
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
                    "customer_city": {"type": "string", "default": ""},
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
                                "quantity": {"type": "integer", "default": 1},
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
                        "default": "medium",
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
                    "limit": {"type": "integer", "description": "Max results (default 3)", "default": 3},
                },
                "required": ["query"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _search_result_instruction(total):
    """Return a tailored _instruction based on how many products were found."""
    prefix = (
        "Verify these products match what the customer asked by checking their names. "
        "If NONE match, call search_products again with different keywords.\n"
    )
    if total <= 1:
        return prefix + (
            "Describe the product briefly in text. "
            "Only send images if the customer asks."
        )
    return prefix + (
        "If customer is browsing broadly, you may show these via "
        "send_images(pids=[...]) as a carousel. If customer asked about "
        "a specific item, describe it in text. "
        "Do NOT search for each product individually."
    )


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

    # 5. Each individual word
    for w in words:
        if w and w not in seen:
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
            # Sort by relevance: products whose name contains exact query words come first
            query_words = set(query.lower().split())
            all_results.sort(
                key=lambda r: sum(
                    1 for w in query_words if w in (r.get("name", "").lower().split())
                ),
                reverse=True,
            )
            all_results = all_results[:limit]
            if all_results:
                _focus_products(conversation, all_results[:FOCUS_MAX])
                out = {"products": all_results, "total": len(all_results)}
                # If the best result matches NONE of the query words, the results
                # are clearly wrong — tell the LLM to try different keywords.
                best_score = sum(
                    1 for w in query_words if w in (all_results[0].get("name", "").lower().split())
                ) if all_results else 0
                if query_words and best_score == 0:
                    out["_instruction"] = (
                        "These products don't match your query. Try searching AGAIN "
                        "with different keywords."
                    )
                else:
                    out["_instruction"] = _search_result_instruction(len(all_results))
                return out
            # External search returned nothing — fall through to local DB
    except Exception:
        logger.exception("Live search_products failed; falling back to local DB")

    # If a product is already selected for this conversation, return it directly
    # (but check budget — if the focused product doesn't fit, fall through to search).
    focus_pid = _focus_pid(conversation)
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
    generic = not query or query.strip().lower() in ("", "product", "products", "show", "list", "all")
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

    # Focus on this search's results so the next context lists the recent products
    if products_list and conversation:
        _focus_products(conversation, [_product_row(p) for p in products_list[:FOCUS_MAX]])

    results = [_product_row(p) for p in products_list]
    out = {"products": results, "total": len(results)}
    if len(results) > 0:
        out["_instruction"] = _search_result_instruction(len(results))
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

        return {"error": f"Unknown tool: {name}"}

    except Exception as exc:
        logger.exception("Tool '%s' raised an exception", name)
        return {"error": str(exc)}
