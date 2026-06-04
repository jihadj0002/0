"""Resolver — the public API other agents import.

    from api.products.factory import get_provider, get_active_source, is_external

Resolves the correct ProductProvider for a user based on their active
ProductSource. If no active source exists, the InternalProvider is returned.
"""

from .providers.base import ProductProvider
from .providers.external import ExternalProvider
from .providers.internal import InternalProvider
from .providers.shopify import ShopifyProvider
from .providers.woocommerce import WooCommerceProvider

_PROVIDER_MAP = {
    "woocommerce": WooCommerceProvider,
    "shopify": ShopifyProvider,
    "external": ExternalProvider,
    "custom": ExternalProvider,
    "internal": InternalProvider,
}


def get_provider_for_source(source, user=None) -> ProductProvider:
    """Return a provider instance for the given ProductSource (or internal)."""
    if source is None:
        return InternalProvider(None, user=user)
    cls = _PROVIDER_MAP.get(source.provider, InternalProvider)
    return cls(source, user=user if user is not None else source.user)


def get_active_source(user):
    """Return the user's active ProductSource, or None."""
    from back.models import ProductSource
    return ProductSource.get_active_for(user)


def get_provider(user) -> ProductProvider:
    """Return the provider for the user's active source (internal if none)."""
    source = get_active_source(user)
    if source is None:
        return InternalProvider(None, user=user)
    return get_provider_for_source(source, user=user)


def is_external(user) -> bool:
    """True if the user has an active source whose provider is not internal."""
    source = get_active_source(user)
    return bool(source) and source.provider != "internal"
