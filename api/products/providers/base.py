"""Abstract base for product/order providers.

All provider methods return NORMALIZED data structures so callers (AI tools,
sync, frontend) never need to know which backend store is in use.

Normalized product dict shape:
    {
        "external_id": str,
        "name": str,
        "description": str,
        "price": str,
        "discounted_price": str | None,
        "stock": int,
        "in_stock": bool,
        "image": str | None,
        "images": [str, ...],
        "raw": {...},          # original provider payload for debugging
    }

create_order return shape:
    {"ok": bool, "external_order_id": str | None, "raw": {...}, "error": str | None}

test_connection return shape:
    {"ok": bool, "message": str}

The canonical order_payload passed to create_order:
    {
        "customer": {"name", "phone", "address", "city"},
        "items": [{"external_id", "name", "quantity", "price"}],
        "total": "123.00",
        "delivery_zone": "inside_dhaka",
        "note": "",
    }
"""

import abc


class ProductProvider(abc.ABC):
    def __init__(self, source, user=None):
        # ``source`` is a ProductSource instance (or None for internal).
        # ``user`` is always available for ORM scoping.
        self.source = source
        self.user = user if user is not None else (getattr(source, "user", None))

    @abc.abstractmethod
    def test_connection(self) -> dict:
        """Return {"ok": bool, "message": str}."""

    @abc.abstractmethod
    def list_products(self, limit=50, page=1) -> list:
        """Return a list of normalized product dicts."""

    @abc.abstractmethod
    def get_product(self, external_id):
        """Return a normalized product dict or None."""

    @abc.abstractmethod
    def search(self, query, limit=5) -> list:
        """Return a list of normalized product dicts matching ``query``."""

    @abc.abstractmethod
    def create_order(self, order_payload: dict) -> dict:
        """Return {"ok", "external_order_id", "raw", "error"}."""

    @abc.abstractmethod
    def get_order_status(self, external_order_id) -> dict:
        """Return a normalized order-status dict."""
