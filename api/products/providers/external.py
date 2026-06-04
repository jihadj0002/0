"""External / custom provider.

A generic store the user wires by hand. It has no standard product API, so
product reads fall back to the locally-synced Product cache for this source.
Its key job is ORDER PUSH: create_order POSTs the canonical order_payload to
``source.order_endpoint_url`` with optional headers from ``order_endpoint_auth``.
"""

import requests

from .base import ProductProvider
from .internal import normalize_product

TIMEOUT = 15

_ORDER_ID_KEYS = ("id", "order_id", "external_order_id", "orderId")


class ExternalProvider(ProductProvider):
    def _queryset(self):
        from back.models import Product
        return Product.objects.filter(user=self.user, source=self.source)

    def test_connection(self) -> dict:
        if self.source and self.source.order_endpoint_url:
            return {"ok": True, "message": "Custom order endpoint configured."}
        return {"ok": False, "message": "No order endpoint configured."}

    def list_products(self, limit=50, page=1) -> list:
        try:
            offset = max(0, (int(page) - 1) * int(limit))
            qs = self._queryset().order_by("-featured_product", "-id")[offset:offset + int(limit)]
            return [normalize_product(p) for p in qs]
        except Exception:
            return []

    def get_product(self, external_id):
        try:
            p = self._queryset().filter(external_id=external_id).first() or \
                self._queryset().filter(pid=external_id).first()
            return normalize_product(p) if p else None
        except Exception:
            return None

    def search(self, query, limit=5) -> list:
        from django.db.models import Q
        try:
            qs = self._queryset().filter(
                Q(name__icontains=query) | Q(description__icontains=query)
            ).order_by("-featured_product", "-id")[: int(limit)]
            return [normalize_product(p) for p in qs]
        except Exception:
            return []

    def create_order(self, order_payload: dict) -> dict:
        url = self.source.order_endpoint_url if self.source else None
        if not url:
            return {
                "ok": False,
                "external_order_id": None,
                "raw": {},
                "error": "No order endpoint configured.",
            }
        try:
            auth = self.source.order_endpoint_auth or {}
            headers = {"Content-Type": "application/json"}
            if isinstance(auth, dict):
                headers.update(auth.get("headers", {}) or {})
            r = requests.post(url, json=order_payload, headers=headers, timeout=TIMEOUT)
            if r.status_code in (200, 201, 202):
                try:
                    data = r.json()
                except Exception:
                    data = {}
                ext_id = None
                if isinstance(data, dict):
                    for k in _ORDER_ID_KEYS:
                        if data.get(k):
                            ext_id = str(data.get(k))
                            break
                return {"ok": True, "external_order_id": ext_id, "raw": data, "error": None}
            return {
                "ok": False,
                "external_order_id": None,
                "raw": {"status": r.status_code, "body": r.text[:500]},
                "error": f"HTTP {r.status_code}",
            }
        except Exception as e:
            return {"ok": False, "external_order_id": None, "raw": {}, "error": str(e)}

    def get_order_status(self, external_order_id) -> dict:
        return {
            "ok": False,
            "error": "Custom external source does not support order status lookup.",
        }
