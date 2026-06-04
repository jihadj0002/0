"""Product sync — pull normalized products from a source and UPSERT locally."""

from decimal import Decimal, InvalidOperation

from django.utils import timezone

from .factory import get_provider_for_source

MAX_PAGES = 20         # safety cap (a few hundred products at per_page=50)
PER_PAGE = 50


def _to_decimal(value):
    if value in (None, "", "0", 0):
        try:
            return Decimal(str(value or "0"))
        except (InvalidOperation, ValueError):
            return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def sync_products(source) -> dict:
    """Pull products from ``source`` and upsert into local Product rows.

    Returns {"created": int, "updated": int, "errors": [str]}.
    """
    from back.models import Product

    result = {"created": 0, "updated": 0, "errors": []}
    provider = get_provider_for_source(source, user=source.user)

    try:
        all_items = []
        for page in range(1, MAX_PAGES + 1):
            batch = provider.list_products(limit=PER_PAGE, page=page)
            if not batch:
                break
            all_items.extend(batch)
            if len(batch) < PER_PAGE:
                break

        for item in all_items:
            external_id = item.get("external_id")
            if not external_id:
                result["errors"].append("Skipped product with no external_id")
                continue
            try:
                discounted = item.get("discounted_price")
                defaults = {
                    "name": item.get("name") or "Unnamed",
                    "description": item.get("description") or "",
                    "price": _to_decimal(item.get("price")),
                    "discounted_price": _to_decimal(discounted) if discounted else None,
                    "stock_quantity": int(item.get("stock") or 0),
                    "status": bool(item.get("in_stock", True)),
                }
                obj, created = Product.objects.update_or_create(
                    user=source.user,
                    source=source,
                    external_id=str(external_id),
                    defaults=defaults,
                )
                if created:
                    result["created"] += 1
                else:
                    result["updated"] += 1
            except Exception as e:
                result["errors"].append(f"{external_id}: {e}")

        source.last_synced = timezone.now()
        source.status = "connected"
        source.last_error = ""
        source.save()
    except Exception as e:
        result["errors"].append(str(e))
        source.status = "error"
        source.last_error = str(e)
        source.save()

    return result
