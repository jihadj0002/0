"""Shopify Admin REST API provider.

Base URL: {store_url}/admin/api/2024-01
Auth header: X-Shopify-Access-Token: {access_token}
"""

import re

import requests

from .base import ProductProvider

TIMEOUT = 15
API_VERSION = "2024-01"

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text):
    if not text:
        return ""
    return _TAG_RE.sub("", text).strip()


def normalize_product(item):
    variants = item.get("variants") or []
    first_variant = variants[0] if variants else {}
    images = [img.get("src") for img in (item.get("images") or []) if img.get("src")]
    inv = first_variant.get("inventory_quantity")
    return {
        "external_id": str(item.get("id")),
        "name": item.get("title") or "",
        "description": _strip_html(item.get("body_html")),
        "price": str(first_variant.get("price") or "0"),
        "discounted_price": (
            str(first_variant.get("compare_at_price"))
            if first_variant.get("compare_at_price")
            and first_variant.get("price")
            and float(first_variant.get("price")) < float(first_variant.get("compare_at_price"))
            else None
        ),
        "stock": int(inv) if inv is not None else 0,
        "in_stock": (inv is None) or int(inv) > 0,
        "image": images[0] if images else None,
        "images": images,
        "raw": item,
        "_variant_id": str(first_variant.get("id")) if first_variant.get("id") else None,
    }


class ShopifyProvider(ProductProvider):
    @property
    def _base(self):
        return (self.source.store_url or "").rstrip("/") + f"/admin/api/{API_VERSION}"

    @property
    def _headers(self):
        return {
            "X-Shopify-Access-Token": self.source.access_token or "",
            "Content-Type": "application/json",
        }

    def _get(self, path, params=None):
        url = self._base + path
        return requests.get(url, headers=self._headers, params=params or {}, timeout=TIMEOUT)

    def test_connection(self) -> dict:
        try:
            r = self._get("/products.json", {"limit": 1})
            if r.status_code == 200:
                return {"ok": True, "message": "Connected to Shopify."}
            return {"ok": False, "message": f"HTTP {r.status_code}: {r.text[:200]}"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def list_products(self, limit=50, page=1) -> list:
        try:
            r = self._get("/products.json", {"limit": int(limit)})
            if r.status_code != 200:
                return []
            return [normalize_product(i) for i in (r.json().get("products") or [])]
        except Exception:
            return []

    def get_product(self, external_id):
        try:
            r = self._get(f"/products/{external_id}.json")
            if r.status_code != 200:
                return None
            return normalize_product(r.json().get("product") or {})
        except Exception:
            return None

    def search(self, query, limit=5) -> list:
        try:
            r = self._get("/products.json", {"title": query, "limit": int(limit)})
            if r.status_code != 200:
                return []
            products = [normalize_product(i) for i in (r.json().get("products") or [])]
            # Shopify REST title param is not a fuzzy search; filter client-side too.
            q = (query or "").lower()
            filtered = [p for p in products if q in p["name"].lower()]
            return (filtered or products)[: int(limit)]
        except Exception:
            return []

    def create_order(self, order_payload: dict) -> dict:
        try:
            customer = order_payload.get("customer", {}) or {}
            line_items = []
            for it in order_payload.get("items", []) or []:
                variant_id = (it.get("raw") or {}).get("_variant_id") if isinstance(it.get("raw"), dict) else None
                if variant_id:
                    line_items.append({"variant_id": int(variant_id), "quantity": int(it.get("quantity", 1))})
                else:
                    # Fallback to a custom line item with title + price.
                    line_items.append({
                        "title": it.get("name") or "Item",
                        "price": str(it.get("price") or "0"),
                        "quantity": int(it.get("quantity", 1)),
                    })

            name = (customer.get("name") or "").strip()
            first, _, last = name.partition(" ")
            body = {
                "order": {
                    "line_items": line_items,
                    "financial_status": "pending",
                    "customer": {"first_name": first or name, "last_name": last},
                    "shipping_address": {
                        "first_name": first or name,
                        "last_name": last,
                        "phone": customer.get("phone", ""),
                        "address1": customer.get("address", ""),
                        "city": customer.get("city", ""),
                    },
                    "note": order_payload.get("note", ""),
                }
            }
            r = requests.post(
                self._base + "/orders.json", headers=self._headers, json=body, timeout=TIMEOUT
            )
            if r.status_code in (200, 201):
                data = r.json().get("order", {})
                return {
                    "ok": True,
                    "external_order_id": str(data.get("id")),
                    "raw": data,
                    "error": None,
                }
            return {
                "ok": False,
                "external_order_id": None,
                "raw": {"status": r.status_code, "body": r.text[:500]},
                "error": f"HTTP {r.status_code}",
            }
        except Exception as e:
            return {"ok": False, "external_order_id": None, "raw": {}, "error": str(e)}

    def get_order_status(self, external_order_id) -> dict:
        try:
            r = self._get(f"/orders/{external_order_id}.json")
            if r.status_code != 200:
                return {"ok": False, "error": f"HTTP {r.status_code}"}
            data = r.json().get("order", {})
            return {
                "ok": True,
                "external_order_id": str(data.get("id")),
                "status": data.get("financial_status") or data.get("fulfillment_status"),
                "total": data.get("total_price"),
                "raw": data,
                "error": None,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
