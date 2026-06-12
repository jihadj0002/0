"""Internal provider — reads the local Product ORM scoped to a user.

Order creation for internal sources is handled directly in api/ai/tools.py,
so create_order / get_order_status are no-op stubs here.
"""

from .base import ProductProvider


def _img_url(field):
    try:
        if field and getattr(field, "name", ""):
            return field.url
    except Exception:
        pass
    return None


def normalize_product(product):
    """Normalize a local Product row into the canonical dict."""
    image = _img_url(product.image)
    images = [image] if image else []
    for pi in product.productimages_set.all() if hasattr(product, "productimages_set") else []:
        u = _img_url(pi.images)
        if u:
            images.append(u)
    price = product.price
    discounted = product.discounted_price
    return {
        "external_id": product.external_id or product.pid,
        "name": product.name,
        "description": product.description or "",
        "price": str(price) if price is not None else "0",
        "discounted_price": str(discounted) if discounted is not None else None,
        "stock": int(product.stock_quantity or 0),
        "in_stock": bool(product.status) and (product.stock_quantity or 0) > 0,
        "image": images[0] if images else None,
        "images": images,
        "raw": {"pid": product.pid, "id": product.id},
    }


class InternalProvider(ProductProvider):
    def _queryset(self):
        from back.models import Product
        qs = Product.objects.filter(user=self.user)
        return qs

    def test_connection(self) -> dict:
        return {"ok": True, "message": "Internal product source."}

    def list_products(self, limit=50, page=1) -> list:
        try:
            offset = max(0, (int(page) - 1) * int(limit))
            qs = self._queryset().order_by("-featured_product", "-id")[offset:offset + int(limit)]
            return [normalize_product(p) for p in qs]
        except Exception:
            return []

    def get_product(self, external_id):
        from back.models import Product
        try:
            p = Product.objects.filter(user=self.user).filter(
                pid=external_id
            ).first() or Product.objects.filter(
                user=self.user, external_id=external_id
            ).first()
            return normalize_product(p) if p else None
        except Exception:
            return None

    def search(self, query, limit=5) -> list:
        from django.db.models import Q
        try:
            qs = self._queryset().filter(
                Q(name__icontains=query)
                | Q(description__icontains=query)
                | Q(pid__icontains=query)
                | Q(external_id__icontains=query)
            ).order_by("-featured_product", "-id")[: int(limit)]
            return [normalize_product(p) for p in qs]
        except Exception:
            return []

    def create_order(self, order_payload: dict) -> dict:
        # Internal order creation stays in api/ai/tools.py.
        return {
            "ok": False,
            "external_order_id": None,
            "raw": {},
            "error": "internal source handled by AI tools directly",
        }

    def get_order_status(self, external_order_id) -> dict:
        return {
            "ok": False,
            "error": "internal source handled by AI tools directly",
        }
