"""External / custom provider — talks to a remote product + order REST API.

Currently targets the monowamart ERP "AI" API. Key facts (confirmed live):
  * Base URL = ``https://<host>/api/v1/{business_id}/ai`` — the ``store_url`` on
    the source may point at ``.../ai`` or ``.../ai/products``; we normalise it.
  * Cloudflare blocks non-browser User-Agents → every request MUST send a
    browser ``User-Agent`` or it 403s.
  * Products are Laravel-paginated (``data[]`` + ``current_page``/``last_page``/
    ``next_page_url``). Each product carries a ``variations[]`` array; the order
    API needs ``product_id`` + ``variation_id``, so variations are surfaced in
    the normalized dict.
  * Orders are created with ``POST {base}/order`` (singular).
"""

import re

import requests

from .base import ProductProvider

TIMEOUT = 20

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

_TAG_RE = re.compile(r"<[^>]+>")

_ORDER_ID_KEYS = ("id", "order_id", "external_order_id", "orderId")
MAX_PAGES = 20


def _strip_html(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", text)).strip()


def _to_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


class ExternalProvider(ProductProvider):
    # ------------------------------------------------------------------ helpers
    def _base_url(self):
        """Return the ERP base ending in ``/ai`` (no trailing slash, no /products)."""
        url = (self.source.store_url or "").strip() if self.source else ""
        url = url.rstrip("/")
        for suffix in ("/products", "/product"):
            if url.endswith(suffix):
                url = url[: -len(suffix)]
                break
        return url.rstrip("/")

    def _headers(self):
        headers = dict(_BROWSER_HEADERS)
        token = getattr(self.source, "access_token", "") if self.source else ""
        if token:
            headers["Authorization"] = f"Bearer {token}"
        auth = getattr(self.source, "order_endpoint_auth", None) if self.source else None
        if isinstance(auth, dict):
            headers.update(auth.get("headers", {}) or {})
        return headers

    def _get(self, path, params=None):
        url = f"{self._base_url()}/{path.lstrip('/')}"
        r = requests.get(url, params=params, headers=self._headers(), timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    # ----------------------------------------------------------------- mapping
    def _media_urls(self, raw):
        urls = []
        main = raw.get("image_url")
        if main:
            urls.append(main)
        host = re.match(r"(https?://[^/]+)", self._base_url())
        host = host.group(1) if host else ""
        for m in raw.get("media", []) or []:
            try:
                if m.get("tag") == "thumbnail":
                    continue
                built = f"{host}/storage/{m['directory']}/{m['filename']}.{m['extension']}"
                if built not in urls:
                    urls.append(built)
            except Exception:
                continue
        return urls

    def _variations(self, raw):
        out = []
        for v in raw.get("variations", []) or []:
            qty = _to_int(v.get("qty_available"))
            out.append({
                "variation_id": v.get("id"),
                "name": v.get("name"),
                "sub_sku": v.get("sub_sku"),
                "price": str(v.get("sell_price") or "0"),
                "promotion_price": str(v["promotion_price"]) if v.get("promotion_price") else None,
                "qty": qty,
                "in_stock": qty > 0,
            })
        return out

    def _normalize(self, raw):
        variations = self._variations(raw)
        first = variations[0] if variations else {}
        images = self._media_urls(raw)
        stock = _to_int(raw.get("total_stock")) or sum(v["qty"] for v in variations)
        return {
            "external_id": str(raw.get("id")),
            "sku": raw.get("sku"),
            "name": raw.get("name") or "",
            "description": _strip_html(raw.get("product_description")),
            "price": first.get("price", "0"),
            "discounted_price": first.get("promotion_price"),
            "stock": stock,
            "in_stock": stock > 0,
            "image": images[0] if images else None,
            "images": images,
            "variations": variations,
            "raw": raw,
        }

    @staticmethod
    def _items(payload):
        """Pull the product list out of a Laravel-paginated or bare response."""
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            return payload.get("data", []) or []
        return []

    # --------------------------------------------------------------- interface
    def test_connection(self) -> dict:
        try:
            data = self._get("products", params={"page": 1})
            count = data.get("total") if isinstance(data, dict) else len(self._items(data))
            return {"ok": True, "message": f"Connected — {count} products available."}
        except Exception as e:
            return {"ok": False, "message": f"Connection failed: {e}"}

    def list_products(self, limit=50, page=1) -> list:
        try:
            data = self._get("products", params={"page": int(page)})
            return [self._normalize(p) for p in self._items(data)]
        except Exception:
            return []

    def get_product(self, external_id):
        try:
            data = self._get(f"products/{external_id}")
            raw = data.get("data", data) if isinstance(data, dict) and "data" in data else data
            if not raw:
                return None
            return self._normalize(raw)
        except Exception:
            return None

    def search(self, query, limit=5) -> list:
        # ?sku= is unreliable on the ERP — route everything through ?query=.
        try:
            data = self._get("products", params={"query": query})
            rows = [self._normalize(p) for p in self._items(data)]
            return rows[: int(limit)]
        except Exception:
            return []

    def create_order(self, order_payload: dict) -> dict:
        """``order_payload`` is the canonical dict built by orders.py.

        Translate it to the ERP shape and POST to ``{base}/order``.
        """
        cust = order_payload.get("customer", {})
        items = []
        for it in order_payload.get("items", []):
            entry = {"product_id": it.get("external_id"), "quantity": int(it.get("quantity") or 1)}
            if it.get("variation_id"):
                entry["variation_id"] = it["variation_id"]
            items.append(entry)

        erp_payload = {
            "address": {
                "name": cust.get("name", ""),
                "mobile": cust.get("phone", ""),
                "address": cust.get("address", ""),
            },
            "items": items,
            "delivered_to": order_payload.get("delivery_zone", "inside_dhaka"),
            "shipping_note": order_payload.get("note", ""),
            "source": "ai",
            "payment_method": "cod",
            "rp_redeemed": 0,
            "rp_redeemed_amount": 0,
        }

        url = f"{self._base_url()}/order"
        if self.source and self.source.order_endpoint_url:
            url = self.source.order_endpoint_url

        try:
            r = requests.post(url, json=erp_payload, headers=self._headers(), timeout=TIMEOUT)
            if r.status_code in (200, 201, 202):
                try:
                    data = r.json()
                except Exception:
                    data = {}
                ext_id = None
                container = data.get("data", data) if isinstance(data, dict) else {}
                if isinstance(container, dict):
                    for k in _ORDER_ID_KEYS:
                        if container.get(k):
                            ext_id = str(container[k])
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
        try:
            data = self._get(f"orders/{external_order_id}")
            raw = data.get("data", data) if isinstance(data, dict) and "data" in data else data
            if not raw:
                return {"ok": False, "error": "Order not found"}
            return {
                "ok": True,
                "external_order_id": str(raw.get("id")),
                "status": raw.get("status"),
                "payment_status": raw.get("payment_status"),
                "raw": raw,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
