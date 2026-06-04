"""Order push — send a local Sale to the user's active external source."""

from .factory import get_active_source, get_provider_for_source, is_external


def _item_external_id(item):
    if item.external_product_id:
        return item.external_product_id
    product = item.product
    if product is not None:
        return product.external_id or product.pid
    return None


def push_order_to_source(sale) -> dict:
    """Push a Sale to the active external source.

    Returns {"ok": bool, "external_order_id": ..., "error": ...} or, for an
    internal active source, {"ok": True, "skipped": "internal"}.
    """
    user = sale.user

    if not is_external(user):
        return {"ok": True, "skipped": "internal"}

    source = get_active_source(user)
    provider = get_provider_for_source(source, user=user)

    items = []
    for item in sale.items.all():
        items.append({
            "external_id": _item_external_id(item),
            "name": item.product_name or (item.product.name if item.product else ""),
            "quantity": int(item.quantity or 1),
            "price": str(item.price or "0"),
            "raw": item.raw_product_data if isinstance(item.raw_product_data, dict) else {},
        })

    payload = {
        "customer": {
            "name": sale.customer_name or "",
            "phone": sale.customer_phone or "",
            "address": sale.customer_address or "",
            "city": sale.customer_city or "",
        },
        "items": items,
        "total": str(sale.amount or "0"),
        "delivery_zone": sale.delivered_to or "inside_dhaka",
        "note": "",
    }

    res = provider.create_order(payload)

    if res.get("ok"):
        if res.get("external_order_id"):
            sale.external_order_id = res["external_order_id"]
        sale.source = "external"
        sale.updated_to_web = "updated"
    else:
        sale.source = "external"
        sale.updated_to_web = "failed"
    sale.save()

    return {
        "ok": bool(res.get("ok")),
        "external_order_id": res.get("external_order_id"),
        "error": res.get("error"),
    }
