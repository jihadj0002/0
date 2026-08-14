"""Order draft plumbing — the backend truth for order math.

Everything the AI needs about an in-flight order is computed here:

- :func:`resolve_order_items`  — product/variation/stock resolution (shared by
  the create_order tool and the pipeline auto-confirm guard)
- :func:`compute_order_totals` — authoritative item_total/delivery_charge/grand_total
- :func:`create_sale_from_resolved` — atomic Sale creation from resolved items
- :func:`save_draft` / :func:`draft_missing_fields` — OrderDraft persistence
- :func:`sync_session_state` — mirrors draft status into SessionContext

The LLM is never asked to do arithmetic; it only echoes backend totals.
"""
import json
import logging
from decimal import Decimal

from django.db import transaction

logger = logging.getLogger(__name__)


def fmt_amount(value):
    """Format a Decimal/BigDecimal as a clean string: 300 not 300.00."""
    try:
        text = f"{Decimal(value):f}"
    except Exception:
        return str(value)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


# ---------------------------------------------------------------------------
# Product resolution
# ---------------------------------------------------------------------------

def resolve_order_items(user, items):
    """Resolve and validate order items.

    Returns (resolved, errors, warnings) where each resolved entry is a dict::

        {pid, qty, unit_price (Decimal), name, external_id, variation_id, product}

    ``product`` is the local Product row when one exists, else None (live
    external item). Mirrors the resolution rules of the previous
    ``tool_create_order`` implementation.
    """
    from api.products.factory import get_active_source, get_provider, is_external
    from back.models import Product

    source = get_active_source(user)
    external_active = bool(source) and is_external(user)
    live_mode = bool(source) and source.mode == "live" and external_active
    provider = None
    if live_mode:
        try:
            provider = get_provider(user)
        except Exception:
            logger.exception("Could not load provider for order resolution; treating as non-live")
            provider = None

    resolved = []
    errors = []
    warnings = []

    for item in items:
        pid = item.get("pid", "")
        raw_qty = item.get("quantity") or item.get("qty")  # tool args vs draft JSON
        try:
            qty = max(int(raw_qty), 1)
        except (TypeError, ValueError):
            qty = 1
        requested_vid = item.get("variation_id")

        product = Product.objects.filter(user=user, pid=pid, status=True).first()
        if product is None and external_active:
            product = Product.objects.filter(user=user, external_id=pid).first()

        if product is not None:
            if product.stock_quantity < qty:
                errors.append(f"{product.name}: only {product.stock_quantity} left in stock")
                continue
            if requested_vid:
                warnings.append(f"{product.name}: variation_id ignored for local product")
            unit_price = product.discounted_price or product.price
            resolved.append({
                "pid": pid,
                "qty": qty,
                "unit_price": unit_price,
                "name": product.name,
                "external_id": product.external_id or None,
                "variation_id": None,
                "product": product,
            })
            continue

        if live_mode and provider is not None:
            r = None
            try:
                r = provider.get_product(pid)
            except Exception:
                logger.exception("Live get_product failed for pid=%s during order resolution", pid)
            if not r:
                try:
                    results = provider.search(pid, limit=1)
                    r = results[0] if results else None
                except Exception:
                    pass
            if r:
                variations = r.get("variations") or []
                chosen = None
                if variations:
                    if requested_vid:
                        chosen = next(
                            (v for v in variations
                             if str(v.get("variation_id")) == str(requested_vid)),
                            None,
                        )
                        if chosen is None:
                            errors.append(f"{r.get('name') or pid}: variation '{requested_vid}' not found")
                            continue
                    elif len(variations) == 1:
                        chosen = variations[0]
                    else:
                        opts = ", ".join(
                            f"{v.get('name')} (variation_id={v.get('variation_id')})"
                            for v in variations
                        )
                        errors.append(
                            f"{r.get('name') or pid}: choose a variation before ordering — options: {opts}"
                        )
                        continue
                raw_price = (
                    (chosen or {}).get("promotion_price")
                    or (chosen or {}).get("price")
                    or r.get("discounted_price") or r.get("price") or "0"
                )
                try:
                    unit_price = Decimal(str(raw_price))
                except Exception:
                    unit_price = Decimal("0")
                resolved.append({
                    "pid": pid,
                    "qty": qty,
                    "unit_price": unit_price,
                    "name": r.get("name") or pid,
                    "external_id": r["external_id"],
                    "variation_id": chosen.get("variation_id") if chosen else (requested_vid or None),
                    "product": None,
                })
                continue

        errors.append(f"Product '{pid}' not found")

    return resolved, errors, warnings


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------

def compute_order_totals(user, items, delivery_zone="inside_dhaka"):
    """Backend-computed order math. Returns dict:

    ``{ok, errors, warnings, resolved, item_total, delivery_charge, grand_total}``
    """
    from context.models import StoreConfig

    resolved, errors, warnings = resolve_order_items(user, items)
    item_total = sum((r["unit_price"] or 0) * r["qty"] for r in resolved)

    store_config = StoreConfig.objects.filter(user=user).first()
    if delivery_zone == "inside_dhaka":
        delivery_charge = store_config.delivery_charge_inside if store_config else Decimal("0")
    else:
        delivery_charge = store_config.delivery_charge_outside if store_config else Decimal("0")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "resolved": resolved,
        "item_total": item_total,
        "delivery_charge": delivery_charge or Decimal("0"),
        "grand_total": item_total + (delivery_charge or Decimal("0")),
    }


# ---------------------------------------------------------------------------
# Sale creation
# ---------------------------------------------------------------------------

@transaction.atomic
def create_sale_from_resolved(user, conversation, resolved, item_total, delivery_charge,
                              delivery_zone, customer_name, customer_phone,
                              customer_address, customer_city=""):
    """Create the Sale + OrderItems inside one transaction. Returns (sale, push_result)."""
    from back.models import OrderItem, Sale

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
    for r in resolved:
        OrderItem.objects.create(
            order=sale,
            product=r["product"],
            product_name=r["name"],
            price=r["unit_price"],
            quantity=r["qty"],
            action="base",
            external_product_id=r["external_id"] or None,
            external_variation_id=str(r["variation_id"]) if r["variation_id"] else None,
        )
        if r["product"] is not None:
            p = r["product"]
            p.stock_quantity -= r["qty"]
            p.save(update_fields=["stock_quantity"])

    sale.amount = item_total + delivery_charge
    sale.save(update_fields=["amount"])

    Conversation_backfill = None  # noqa — backfill done by callers
    push_result = {}
    try:
        from api.products.orders import push_order_to_source
        push_result = push_order_to_source(sale) or {}
    except Exception:
        logger.exception("push_order_to_source failed for order %s", sale.oid)
        push_result = {}

    return sale, push_result


def backfill_conversation_customer(conversation, *, name=None, phone=None, city=None, address=None):
    from back.models import Conversation

    updates = {}
    if name:
        updates["customer_name"] = name
    if phone:
        updates["customer_phone"] = phone
    if city:
        updates["customer_city"] = city
    if address:
        updates["customer_address"] = address
    if updates:
        Conversation.objects.filter(pk=conversation.pk).update(**updates)
        for k, v in updates.items():
            setattr(conversation, k, v or getattr(conversation, k))
    return updates


# ---------------------------------------------------------------------------
# OrderDraft persistence
# ---------------------------------------------------------------------------

def save_draft(user, conversation, *, resolved, item_total, delivery_charge, grand_total,
               delivery_zone, confirmation_status="awaiting_confirmation", missing_fields=None,
               opportunity=None):
    """Upsert the OrderDraft row for a conversation."""
    from context.crm_models import CrmEvent, OrderDraft

    items = [
        {
            "pid": r["pid"],
            "name": r["name"],
            "qty": r["qty"],
            "unit_price": str(r["unit_price"]),
            "variation_id": r["variation_id"] or "",
        }
        for r in resolved
    ]
    draft, _ = OrderDraft.objects.update_or_create(
        user=user,
        conversation=conversation,
        defaults={
            "opportunity": opportunity,
            "items": items,
            "delivery_zone": delivery_zone,
            "item_total": item_total,
            "delivery_charge": delivery_charge,
            "grand_total": grand_total,
            "confirmation_status": confirmation_status,
            "missing_fields": missing_fields or [],
        },
    )
    CrmEvent.objects.create(
        user=user,
        conversation=conversation,
        type="order_draft",
        description=f"Draft: {confirmation_status} — total ৳{grand_total}",
        data={
            "confirmation_status": confirmation_status,
            "item_total": str(item_total),
            "delivery_charge": str(delivery_charge),
            "grand_total": str(grand_total),
            "items": items,
        },
    )
    return draft


def draft_missing_fields(conversation, draft=None):
    """Which order fields are still missing for this conversation (backend truth)."""
    missing = []
    if not (conversation.customer_name or "").strip():
        missing.append("customer_name")
    if not (conversation.customer_phone or "").strip():
        missing.append("customer_phone")
    if not (conversation.customer_address or "").strip():
        missing.append("customer_address")
    if not (conversation.customer_city or "").strip():
        missing.append("customer_city")
    if draft and draft.confirmation_status != "confirmed":
        missing.append("confirmation")
    return missing


def clear_draft(conversation):
    from context.crm_models import OrderDraft
    OrderDraft.objects.filter(conversation=conversation).update(confirmation_status="no_order")


# ---------------------------------------------------------------------------
# SessionContext sync (the workflow state machine)
# ---------------------------------------------------------------------------

def sync_session_state(conversation, state, *, pending_confirmation=None, step=None):
    """Mirror draft/order status into SessionContext (get_or_create)."""
    from context.models import SessionContext

    session, _ = SessionContext.objects.get_or_create(conversation=conversation)
    updates = {"state": state}
    if pending_confirmation is not None:
        updates["pending_confirmation"] = pending_confirmation
    if step is not None:
        updates["workflow_step"] = step
    for key, value in updates.items():
        setattr(session, key, value)
    session.save(update_fields=list(updates.keys()) + ["updated_at"])
    return session


def order_summary_dict(*, resolved, item_total, delivery_charge, grand_total, delivery_zone):
    return {
        "items": [
            {"name": r["name"], "qty": r["qty"], "unit_price": fmt_amount(r["unit_price"]),
             "variation_id": r["variation_id"] or ""}
            for r in resolved
        ],
        "item_total": fmt_amount(item_total),
        "delivery_charge": fmt_amount(delivery_charge),
        "grand_total": fmt_amount(grand_total),
        "delivery_zone": delivery_zone,
    }


# ---------------------------------------------------------------------------
# Confirm-draft flow (shared by the tool + the pipeline auto-confirm guard)
# ---------------------------------------------------------------------------

@transaction.atomic
def confirm_draft_order(conversation):
    """Create the order from the persisted draft + conversation customer data.

    Returns the order result dict, or an error dict. Used by the pipeline
    auto-confirm guard so "হ্যাঁ" in the awaiting-confirmation state creates
    the order with the exact same backend logic as the tool.
    """
    from context.crm_models import CrmEvent, CustomerProfile, OrderDraft

    user = conversation.user
    draft = OrderDraft.objects.filter(conversation=conversation).first()
    if not draft or draft.confirmation_status != "awaiting_confirmation":
        return {"error": "No pending order confirmation", "confirmable": False}
    if not draft.items:
        return {"error": "Order draft has no items", "confirmable": False}

    # Merge draft + conversation customer data.
    name = (conversation.customer_name or "").strip()
    phone = (conversation.customer_phone or "").strip()
    address = (conversation.customer_address or "").strip()
    city = (conversation.customer_city or "").strip()
    if not (name and phone and address):
        return {
            "error": "Missing customer information for the order",
            "missing_fields": draft_missing_fields(conversation, draft),
            "confirmable": False,
        }

    # Re-validate stock/prices at confirmation time.
    totals = compute_order_totals(user, draft.items, draft.delivery_zone)
    if not totals["ok"]:
        return {"error": "Cannot create order", "details": totals["errors"], "confirmable": False}

    sale, push_result = create_sale_from_resolved(
        user=user,
        conversation=conversation,
        resolved=totals["resolved"],
        item_total=totals["item_total"],
        delivery_charge=totals["delivery_charge"],
        delivery_zone=draft.delivery_zone,
        customer_name=name,
        customer_phone=phone,
        customer_address=address,
        customer_city=city,
    )

    draft.confirmation_status = "confirmed"
    draft.converted_order = sale
    draft.item_total = totals["item_total"]
    draft.delivery_charge = totals["delivery_charge"]
    draft.grand_total = totals["grand_total"]
    draft.missing_fields = []
    draft.save(update_fields=["confirmation_status", "converted_order", "item_total",
                              "delivery_charge", "grand_total", "missing_fields", "updated_at"])

    from context.crm.signals import record_signal
    from context.crm.engine import recompute
    from context.crm_models import SalesOpportunity
    from context.models import SessionContext

    record_signal(conversation, "confirmed_product")
    opp = SalesOpportunity.objects.filter(conversation=conversation).first()
    if opp and opp.status == "open":
        opp.stage = "won"
        opp.status = "won"
        opp.save(update_fields=["stage", "status", "updated_at"])
        CrmEvent.objects.create(
            user=user,
            conversation=conversation,
            type="stage_change",
            description="Opportunity won (order created)",
            data={"stage": "won", "order_id": sale.oid},
        )
    profile = CustomerProfile.objects.filter(conversation=conversation).first()
    if profile:
        profile.order_count += 1
        profile.total_spent = (profile.total_spent or 0) + sale.amount
        if not profile.first_order_at:
            profile.first_order_at = sale.created_at
        profile.lifecycle_stage = "customer" if profile.order_count == 1 else "repeat_customer"
        if profile.total_spent >= 50000:
            profile.lifecycle_stage = "vip"
        profile.save(update_fields=["order_count", "total_spent", "first_order_at",
                                    "lifecycle_stage", "updated_at"])

    CrmEvent.objects.create(
        user=user,
        conversation=conversation,
        type="order_created",
        description=f"Order {sale.oid} created (total ৳{sale.amount})",
        data={"order_id": sale.oid, "total": str(sale.amount)},
    )
    sync_session_state(conversation, "completed", pending_confirmation=None)

    return {
        "order_id": sale.oid,
        "status": sale.status,
        "total": fmt_amount(sale.amount),
        "items": [
            {"name": r["name"], "qty": r["qty"], "unit_price": fmt_amount(r["unit_price"])}
            for r in totals["resolved"]
        ],
        "synced_to_store": bool(push_result.get("ok") and not push_result.get("skipped")),
        "external_order_id": sale.external_order_id,
        "confirmable": True,
    }
