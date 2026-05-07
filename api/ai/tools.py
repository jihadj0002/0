import json
import logging

from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q

from back.models import Conversation, OrderItem, Product, ProductImages, Sale

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


def tool_search_products(user, query, limit=5, conversation=None):
    # If we have a conversation and a current_product is set, prioritize that
    if conversation and conversation.current_product:
        try:
            # Try to get the specific product by pid
            product = Product.objects.get(user=user, pid=conversation.current_product, status=True)
            # Get image URLs
            image_urls = []
            if product.image and hasattr(product.image, 'url'):
                image_urls.append(product.image.url)
            
            # Get additional images
            additional_images = ProductImages.objects.filter(product=product)
            for img in additional_images:
                if img.images and hasattr(img.images, 'url'):
                    image_urls.append(img.images.url)
            
            # Build product info with image URLs as string
            product_info = f"Viewing: {product.name} (PK: {product.pid})\n"
            product_info += f"Description: {product.description or 'No description available'}\n"
            product_info += f"Price: {product.price}\n"
            if product.discounted_price:
                product_info += f"Discounted Price: {product.discounted_price}\n"
            product_info += f"Stock: {product.stock_quantity} units\n"
            if image_urls:
                product_info += f"Image URLs: {', '.join(image_urls)}\n"
            else:
                product_info += "Image URLs: No images available\n"
            
            # Save the product info with image URLs to current_product
            conversation.current_product = product_info
            conversation.save(update_fields=['current_product'])
            
            results = [{
                "pid": product.pid,
                "name": product.name,
                "price": str(product.price),
                "discounted_price": str(product.discounted_price) if product.discounted_price else None,
                "in_stock": product.stock_quantity > 0,
                "stock": product.stock_quantity,
                "description": (product.description or "")[:200],
                "featured": product.featured_product,
            }]
            return {"products": results, "total": len(results), "selected_product": True}
        except Product.DoesNotExist:
            # If the current_product doesn't exist, clear it and continue with normal search
            conversation.current_product = ""
            conversation.save(update_fields=['current_product'])
            pass
    
    # If query is empty or very generic, prioritize featured products
    if not query or query.strip() in ['', 'product', 'products', 'show', 'list']:
        # First get featured products
        featured_products = Product.objects.filter(
            user=user, 
            status=True, 
            featured_product=True
        ).order_by('?')[:limit//2] if limit > 1 else Product.objects.filter(
            user=user, 
            status=True, 
            featured_product=True
        ).first()
        
        # Then get regular products matching the query
        regular_products = Product.objects.filter(
            user=user, 
            status=True
        ).filter(
            Q(name__icontains=query) | Q(description__icontains=query)
        ).exclude(
            featured_product=True  # Exclude featured to avoid duplicates
        ).order_by("name")[:limit - len(featured_products) if isinstance(featured_products, list) else limit - 1]
        
        # Combine results
        products_list = []
        if isinstance(featured_products, list):
            products_list.extend(featured_products)
        else:
            if featured_products:
                products_list.append(featured_products)
        products_list.extend(regular_products)
    else:
        # Normal search with featured product boost
        products = Product.objects.filter(
            user=user, 
            status=True
        ).filter(
            Q(name__icontains=query) | Q(description__icontains=query)
        ).order_by(
            "-featured_product",  # Featured products first
            "name"
        )[:limit]
        products_list = list(products)
    
    results = []
    for p in products_list:
        results.append({
            "pid": p.pid,
            "name": p.name,
            "price": str(p.price),
            "discounted_price": str(p.discounted_price) if p.discounted_price else None,
            "in_stock": p.stock_quantity > 0,
            "stock": p.stock_quantity,
            "description": (p.description or "")[:200],
            "featured": p.featured_product,
        })
    
    return {"products": results, "total": len(results)}


def tool_get_product_details(user, pid):
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
        "main_image": _image_url(p.image),
        "gallery": extra_images,
        "upsell_enabled": p.upsell_enabled,
    }


def tool_send_images(user, pid):
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

    return {"pid": pid, "name": p.name, "images": images}


def tool_create_order(user, conversation, customer_name, customer_phone, customer_address, items,
                      customer_city="", delivery_zone="inside_dhaka"):
    resolved = []
    errors = []

    for item in items:
        pid = item.get("pid", "")
        qty = max(int(item.get("quantity", 1)), 1)
        try:
            product = Product.objects.get(user=user, pid=pid, status=True)
            if product.stock_quantity < qty:
                errors.append(f"{product.name}: only {product.stock_quantity} left in stock")
            else:
                resolved.append((product, qty))
        except Product.DoesNotExist:
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
        for product, qty in resolved:
            unit_price = product.discounted_price or product.price
            OrderItem.objects.create(
                order=sale,
                product=product,
                product_name=product.name,
                price=unit_price,
                quantity=qty,
                action="base",
            )
            product.stock_quantity -= qty
            product.save(update_fields=["stock_quantity"])
            total += unit_price * qty
            line_items.append({"name": product.name, "qty": qty, "unit_price": str(unit_price)})

        sale.amount = total
        sale.save(update_fields=["amount"])

    # Backfill conversation customer fields
    Conversation.objects.filter(pk=conversation.pk).update(
        customer_name=customer_name or conversation.customer_name,
        customer_phone=customer_phone or conversation.customer_phone,
        customer_city=customer_city or conversation.customer_city,
    )

    return {
        "order_id": sale.oid,
        "status": sale.status,
        "total": str(sale.amount),
        "items": line_items,
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
            return tool_search_products(user, args.get("query", ""), int(args.get("limit", 5)))

        if name == "get_product_details":
            return tool_get_product_details(user, args.get("pid", ""))

        if name == "send_images":
            return tool_send_images(user, args.get("pid", ""))

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
