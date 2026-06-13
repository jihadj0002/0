"""WooCommerce REST API v3 provider.

Base URL: {store_url}/wp-json/wc/v3
Auth: HTTP Basic (consumer_key, consumer_secret) over HTTPS.
"""

import re

import requests

from .base import ProductProvider

TIMEOUT = 15

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text):
    if not text:
        return ""
    return _TAG_RE.sub("", text).strip()


def normalize_product(item):
    images = [img.get("src") for img in (item.get("images") or []) if img.get("src")]
    sale = item.get("sale_price")
    stock_qty = item.get("stock_quantity")
    return {
        "external_id": str(item.get("id")),
        "name": item.get("name") or "",
        "description": _strip_html(item.get("short_description") or item.get("description")),
        "price": str(item.get("regular_price") or item.get("price") or "0"),
        "discounted_price": str(sale) if sale else None,
        "stock": int(stock_qty) if stock_qty is not None else 0,
        "in_stock": item.get("stock_status") == "instock",
        "image": images[0] if images else None,
        "images": images,
        "sku": item.get("sku") or None,
        "raw": item,
    }


class WooCommerceProvider(ProductProvider):
    @property
    def _base(self):
        return (self.source.store_url or "").rstrip("/") + "/wp-json/wc/v3"

    @property
    def _auth(self):
        return (self.source.consumer_key or "", self.source.consumer_secret or "")

    def _get(self, path, params=None):
        url = self._base + path
        return requests.get(url, auth=self._auth, params=params or {}, timeout=TIMEOUT)

    def test_connection(self) -> dict:
        try:
            r = self._get("/products", {"per_page": 1})
            if r.status_code == 200:
                return {"ok": True, "message": "Connected to WooCommerce."}
            return {"ok": False, "message": f"HTTP {r.status_code}: {r.text[:200]}"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def list_products(self, limit=50, page=1) -> list:
        try:
            r = self._get("/products", {"per_page": int(limit), "page": int(page)})
            if r.status_code != 200:
                return []
            return [normalize_product(i) for i in (r.json() or [])]
        except Exception:
            return []

    def get_product(self, external_id):
        try:
            r = self._get(f"/products/{external_id}")
            if r.status_code != 200:
                return None
            return normalize_product(r.json())
        except Exception:
            return None

    def search(self, query, limit=5) -> list:
        try:
            r = self._get("/products", {"search": query, "per_page": int(limit)})
            if r.status_code != 200:
                return []
            return [normalize_product(i) for i in (r.json() or [])]
        except Exception:
            return []

    def create_order(self, order_payload: dict) -> dict:
        try:
            customer = order_payload.get("customer", {}) or {}
            line_items = []
            for it in order_payload.get("items", []) or []:
                ext = it.get("external_id")
                try:
                    pid = int(ext)
                except (TypeError, ValueError):
                    continue
                line_items.append({"product_id": pid, "quantity": int(it.get("quantity", 1))})

            name = (customer.get("name") or "").strip()
            first, _, last = name.partition(" ")
            billing = {
                "first_name": first or name,
                "last_name": last,
                "phone": customer.get("phone", ""),
                "address_1": customer.get("address", ""),
                "city": customer.get("city", ""),
            }
            body = {
                "status": "pending",
                "billing": billing,
                "shipping": dict(billing),
                "line_items": line_items,
                "customer_note": order_payload.get("note", ""),
            }
            r = requests.post(
                self._base + "/orders", auth=self._auth, json=body, timeout=TIMEOUT
            )
            if r.status_code in (200, 201):
                data = r.json()
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
            r = self._get(f"/orders/{external_order_id}")
            if r.status_code != 200:
                return {"ok": False, "error": f"HTTP {r.status_code}"}
            data = r.json()
            return {
                "ok": True,
                "external_order_id": str(data.get("id")),
                "status": data.get("status"),
                "total": data.get("total"),
                "raw": data,
                "error": None,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
