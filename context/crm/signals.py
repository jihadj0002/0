"""Buying-signal recording.

A vocabulary of boolean-ish signals observed during a conversation. Tools fire
these via :func:`record_signal`; the engine turns them into derived scores.
"""
import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

# Every signal we track. ``weight`` feeds purchase_intent_score.
SIGNALS = {
    "asked_price": {"weight": 5},
    "asked_stock": {"weight": 5},
    "asked_photo": {"weight": 10},
    "asked_delivery": {"weight": 8},
    "asked_how_to_order": {"weight": 10},
    "provided_address": {"weight": 12},
    "confirmed_product": {"weight": 15},
    "requested_discount": {"weight": 3},
}

SIGNAL_OBJECTIONS = {
    "asked_delivery": {"type": "delivery_cost", "text": "Asked about delivery cost"},
    "requested_discount": {"type": "price", "text": "Asked for a discount / price negotiation"},
    "asked_stock": {"type": "availability", "text": "Asked about stock availability"},
}


def record_signal(conversation, name, value=True, *, opportunity_hint=""):
    """Record a buying signal on the conversation's CustomerProfile.

    Safe to call from tools/background threads: never raises. Fires the
    objection list update for objection-typed signals.
    """
    try:
        from context.crm_models import CustomerProfile, CrmEvent

        if name not in SIGNALS:
            return None
        profile, _ = CustomerProfile.objects.get_or_create(
            user=conversation.user,
            conversation=conversation,
            defaults={
                "customer_id": conversation.customer_id or "",
                "platform": conversation.platform,
            },
        )
        signals = dict(profile.buying_signals or {})
        if bool(value):
            if not signals.get(name):
                signals[name] = True
                CrmEvent.objects.create(
                    user=conversation.user,
                    conversation=conversation,
                    type="signal",
                    description=f"Signal: {name}",
                    data={"signal": name},
                )
        else:
            signals[name] = False
        profile.buying_signals = signals

        # Objection bookkeeping (dedup by type, count occurrences).
        if value and name in SIGNAL_OBJECTIONS:
            obj = SIGNAL_OBJECTIONS[name]
            objections = list(profile.objections or [])
            found = next((o for o in objections if o.get("type") == obj["type"]), None)
            if found:
                found["count"] = int(found.get("count", 1)) + 1
            else:
                objections.append({"type": obj["type"], "text": obj["text"], "count": 1})
            profile.objections = objections

        profile.last_contact_at = timezone.now()
        profile.save(update_fields=["buying_signals", "objections", "last_contact_at", "updated_at"])
        return profile
    except Exception:
        logger.exception("record_signal failed conv=%s signal=%s", getattr(conversation, "pk", None), name)
        return None


def get_or_create_profile(conversation):
    """Return (profile, created) for a conversation — never raises."""
    try:
        from context.crm_models import CustomerProfile
        return CustomerProfile.objects.get_or_create(
            user=conversation.user,
            conversation=conversation,
            defaults={
                "customer_id": conversation.customer_id or "",
                "platform": conversation.platform,
                "name": conversation.customer_name or "",
                "phone": conversation.customer_phone or "",
                "city": conversation.customer_city or "",
                "address": conversation.customer_address or "",
            },
        )
    except Exception:
        logger.exception("get_or_create_profile failed conv=%s", getattr(conversation, "pk", None))
        return None, False


def record_fact(conversation, key, value, *, source="customer", confidence=1.0):
    """Store a fact the customer told us (source=customer) or the backend
    confirmed (source=system). Never overwrites with lower confidence."""
    try:
        from context.crm_models import CrmEvent

        profile, _ = get_or_create_profile(conversation)
        if profile is None:
            return None
        facts = dict(profile.facts or {})
        key = str(key).strip().lower().replace(" ", "_")
        if not key:
            return None
        existing = facts.get(key)
        if existing and float(existing.get("confidence", 0)) > confidence:
            return profile
        facts[key] = {"value": value, "source": source, "confidence": float(confidence)}
        profile.facts = facts
        profile.save(update_fields=["facts", "updated_at"])
        CrmEvent.objects.create(
            user=conversation.user,
            conversation=conversation,
            type="fact_recorded",
            description=f"Fact: {key} = {value}",
            data={"key": key, "value": value, "source": source},
        )
        return profile
    except Exception:
        logger.exception("record_fact failed conv=%s", getattr(conversation, "pk", None))
        return None


def record_product_view(conversation, name="", pid=""):
    """Track viewed/interested products on the CustomerProfile (dedup + count)."""
    try:
        profile, _ = get_or_create_profile(conversation)
        if profile is None or not pid:
            return None
        prefs = list(profile.preferences or [])
        found = next((p for p in prefs if p.get("pid") == pid), None)
        if found:
            found["count"] = int(found.get("count", 1)) + 1
            if name:
                found["name"] = name
        else:
            prefs.insert(0, {"pid": pid, "name": name or "", "count": 1})
        profile.preferences = prefs[:20]
        profile.save(update_fields=["preferences", "updated_at"])
        return profile
    except Exception:
        logger.exception("record_product_view failed conv=%s", getattr(conversation, "pk", None))
        return None


def set_current_product(conversation, pid):
    """Set the conversation's current product interest on the opportunity."""
    try:
        from context.crm_models import SalesOpportunity

        if not pid:
            return None
        opp, _ = SalesOpportunity.objects.get_or_create(
            user=conversation.user,
            conversation=conversation,
            defaults={"status": "open"},
        )
        if opp.status != "open":
            return opp
        if opp.current_product_pid != pid:
            opp.current_product_pid = pid
            if opp.stage == "discovery":
                opp.stage = "product_interest"
            opp.save(update_fields=["current_product_pid", "stage", "updated_at"])
        return opp
    except Exception:
        logger.exception("set_current_product failed conv=%s", getattr(conversation, "pk", None))
        return None
