import json
import logging

from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q

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
            "description": "Search the store's product catalog by name or description. Call this before quoting any price.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keywords to search"},
                    "limit": {"type": "integer", "description": "Max results (default 5)", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_details",
            "description": "Get full details and image URLs for a specific product by its PID.",
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
            "description": "Retrieve all image URLs for a product so they can be sent to the customer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pid": {"type": "string", "description": "Product PID"},
                },
                "required": ["pid"],
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
            "name": "transfer_chat",
            "description": "Disable AI and hand the conversation to a human agent. Use when: customer requests human, complaint escalation, or issue is beyond AI scope.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Short reason for the transfer"},
                },
                "required": ["reason"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _image_url(path):
    try:
        return default_storage.url(str(path))
    except Exception:
        return None


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


def tool_search_products(user, query, limit=5, conversation=None):
    # Live external source → query the provider in real time.
    from api.products.factory import get_active_source, get_provider, is_external
    try:
        source = get_active_source(user)
        if source and source.mode == "live" and is_external(user):
            provider = get_provider(user)
            if query and query.strip():
                rows = provider.search(query, limit)
            else:
                rows = provider.list_products(limit=limit)
            rows = rows or []
            
            current_conv = Conversation.objects.filter(user=user, pk=conversation.pk).first() if conversation else None
            current_conv.current_product = "rows[0]['external_id']" if rows else ""
            current_conv.save(update_fields=["current_product"])
            return {"products": [_external_row(r) for r in rows], "total": len(rows)}
    except Exception:
        logger.exception("Live search_products failed; falling back to local DB")

    # If a product is already selected for this conversation, return it directly
    if conversation and conversation.current_product:
        pid = conversation.current_product.strip()
        try:
            product = Product.objects.get(user=user, pid=pid, status=True)
            return {
                "products": [_product_row(product)],
                "total": 1,
                "selected_product": True,
            }
        except Product.DoesNotExist:
            Conversation.objects.filter(pk=conversation.pk).update(current_product="")
            conversation.current_product = ""

    # Generic / empty query → show featured first, then fill with anything
    generic = not query or query.strip().lower() in ("", "product", "products", "show", "list", "all")
    if generic:
        qs = Product.objects.filter(user=user, status=True).order_by("-featured_product", "name")
    else:
        qs = Product.objects.filter(
            user=user, status=True
        ).filter(
            Q(name__icontains=query) | Q(description__icontains=query)
        ).order_by("-featured_product", "name")

    products_list = list(qs[:limit])

    # Auto-select the top result so the next context includes full product details
    if products_list and conversation:
        top_pid = products_list[0].pid
        Conversation.objects.filter(pk=conversation.pk).update(current_product=top_pid)
        conversation.current_product = top_pid

    results = [_product_row(p) for p in products_list]
    return {"products": results, "total": len(results)}


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
    # Live external source → fetch from the provider (pid is the external_id).
    from api.products.factory import get_active_source, get_provider, is_external
    try:
        source = get_active_source(user)
        if source and source.mode == "live" and is_external(user):
            r = get_provider(user).get_product(pid)
            if not r:
                return {"error": f"Product '{pid}' not found"}
            details = {
                "pid": r["external_id"],
                "name": r["name"],
                "price": r["price"],
                "discounted_price": r.get("discounted_price"),
                "stock": r.get("stock", 0),
                "in_stock": r.get("in_stock", True),
                "description": r.get("description") or "",
                "upsell_enabled": False,
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
            current_conv = Conversation.objects.filter(user=user, pk=conversation.pk).first() if conversation else None
            current_conv.current_product = details
            current_conv.save(update_fields=["current_product"])
            return details
    except Exception:
        logger.exception("Live get_product_details failed; falling back to local DB")

    try:
        p = Product.objects.get(user=user, pid=pid)
    except Product.DoesNotExist:
        return {"error": f"Product '{pid}' not found"}

    extra_images = [
        _image_url(img)
        for img in ProductImages.objects.filter(product=p).values_list("images", flat=True)
        if _image_url(img)
    ]

    return {
        "pid": p.pid,
        "name": p.name,
        "price": str(p.price),
        "discounted_price": str(p.discounted_price) if p.discounted_price else None,
        "stock": p.stock_quantity,
        "in_stock": p.stock_quantity > 0,
        "description": p.description or "",
        # "main_image": _image_url(p.image),
        # "gallery": extra_images,
        "upsell_enabled": p.upsell_enabled,
    }


def tool_send_images(user, pid, conversation=None):
    # Live external source → fetch images from the provider.
    from api.products.factory import get_active_source, get_provider, is_external
    try:
        source = get_active_source(user)
        if source and source.mode == "live" and is_external(user):
            r = get_provider(user).get_product(pid)
            if not r:
                return {"error": f"Product '{pid}' not found", "images": []}
            images = r.get("images") or ([r["image"]] if r.get("image") else [])
            return {"pid": pid, "name": r["name"], "images": images}
    except Exception:
        logger.exception("Live send_images failed; falling back to local DB")

    try:
        p = Product.objects.get(user=user, pid=pid)
    except Product.DoesNotExist:
        return {"error": f"Product '{pid}' not found", "images": []}

    images = []
    main = _image_url(p.image)
    if main:
        images.append(main)
    for img in ProductImages.objects.filter(product=p).values_list("images", flat=True):
        url = _image_url(img)
        if url and url not in images:
            images.append(url)

    # Return image URLs only — the pipeline collects them via pending_images
    # and sends everything in one send_reply call at the end.
    return {"pid": pid, "name": p.name, "images": images}


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
                resolved.append((None, qty, unit_price, r.get("name") or pid, pid, vid))
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
    if updates:
        Conversation.objects.filter(pk=conversation.pk).update(**updates)
    return {"updated": list(updates.keys())}


def tool_transfer_chat(conversation, reason):
    conversation.disable_ai()
    return {
        "transferred": True,
        "reason": reason,
        "note": "AI disabled — human agent will take over",
    }




# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def execute_tool(name, arguments, user, conversation):
    try:
        args = arguments if isinstance(arguments, dict) else {}

        if name == "search_products":
            return tool_search_products(user, args.get("query", ""), int(args.get("limit", 5)), conversation=conversation)

        if name == "get_product_details":
            return tool_get_product_details(user, args.get("pid", ""), conversation=conversation)

        if name == "send_images":
            return tool_send_images(user, args.get("pid", ""), conversation)

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

        if name == "transfer_chat":
            return tool_transfer_chat(conversation, args.get("reason", "Customer requested"))

        return {"error": f"Unknown tool: {name}"}

    except Exception as exc:
        logger.exception("Tool '%s' raised an exception", name)
        return {"error": str(exc)}
