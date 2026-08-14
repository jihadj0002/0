"""CRM derivation engine.

After each pipeline run (or inbound message) ``recompute`` is called to turn
raw signals + conversation state into derived values:

- purchase_intent_score / lead_score (0-100)
- buying_probability (0.0-1.0)
- engagement score
- lifecycle transitions (lead → prospect → customer → repeat_customer → vip)
- lazy inactivity detection

Derived values are explicitly *inferences* — they never overwrite customer
supplied facts (those live in ``CustomerProfile.facts``).
"""
import logging
import re

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# Affirmative/negative sentiment cues for a lightweight heuristic.
_POSITIVE_RE = re.compile(
    r"(ভালো|ভাল|দারুণ|চমৎকার|ঠিক আছে|thik ache|ok|okay|great|nice|good|love|পছন্দ|"
    r"dhonnobad|ধন্যবাদ|thanks|thank you|hobe|হবে|ham|হুম|yes|হ্যাঁ|confirm|নিশ্চিত)",
    re.IGNORECASE,
)
_NEGATIVE_RE = re.compile(
    r"(খারাপ|dhoroni|দুঃখিত|দামি|dam dami|বেশি দাম|expensive|costly|তাড়া|তাড়া|"
    r"late|deri|দেরি|problem|সমস্যা|complain|অভিযোগ|rubbish|bad|poor|no|না|"
    r"hobe na|হবে না|korte cai na|চাই না)",
    re.IGNORECASE,
)

INACTIVE_AFTER_DAYS = 30

# Signal weights (mirrors context/crm/signals.py).
_SIGNAL_WEIGHTS = {
    "asked_price": 5,
    "asked_stock": 5,
    "asked_photo": 10,
    "asked_delivery": 8,
    "asked_how_to_order": 10,
    "provided_address": 12,
    "confirmed_product": 15,
    "requested_discount": 3,
}


def _get_profile(conversation):
    from context.crm_models import CustomerProfile
    return CustomerProfile.objects.filter(conversation=conversation).first()


def _get_opportunity(conversation):
    from context.crm_models import SalesOpportunity
    return SalesOpportunity.objects.filter(conversation=conversation).first()


def purchase_intent_score(profile):
    """0-100 score from confirmed signals (raw count of `True` signals)."""
    signals = profile.buying_signals or {}
    total = 0
    for name, weight in _SIGNAL_WEIGHTS.items():
        if signals.get(name):
            total += weight
    return min(total, 100)


def engagement_score(profile):
    """0-100 based on message activity (bot+customer+agent) recency."""
    if not profile or not profile.last_contact_at:
        return 0
    days = (timezone.now() - profile.last_contact_at).days
    if days <= 1:
        return 90
    if days <= 3:
        return 60
    if days <= 7:
        return 30
    return 5


def sentiment_from_text(text):
    if not text:
        return "neutral"
    pos = len(_POSITIVE_RE.findall(text or ""))
    neg = len(_NEGATIVE_RE.findall(text or ""))
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def infer_lifecycle(profile, order_count):
    """Next lifecycle stage from order history (+score as tie-breaker)."""
    if order_count == 0:
        return "lead"
    if order_count == 1:
        return "customer"
    return "repeat_customer"


def maybe_reactivate(profile, order_count, base):
    """INACTIVE → reactivated stage if the customer just came back."""
    if profile.lifecycle_stage == "inactive" and order_count >= 1:
        return "customer" if order_count == 1 else "repeat_customer"
    return base


@transaction.atomic
def recompute(conversation, customer_text=None):
    """Derive scores/stage/opportunity updates for a conversation. Never raises."""
    try:
        from context.crm_models import CustomerProfile, CrmEvent, SalesOpportunity
        from back.models import Sale

        profile = _get_profile(conversation)
        if profile is None:
            return None

        # Order history (authoritative).
        orders = Sale.objects.filter(
            user=conversation.user,
            conversation=conversation,
        ).exclude(status="refunded")
        order_count = orders.count()
        total_spent = sum((row["amount"] or 0) for row in orders.values("amount"))
        first_order = orders.order_by("created_at").first()

        # Derived scores.
        lead_score = purchase_intent_score(profile)
        eng = engagement_score(profile)
        prob = round(min(0.95, lead_score / 100 * 0.7 + eng / 100 * 0.3), 2)

        # Lifecycle.
        base = infer_lifecycle(profile, order_count)
        if base == "customer" and total_spent >= 50000:
            base = "vip"
        base = maybe_reactivate(profile, order_count, base)

        if order_count != profile.order_count:
            profile.order_count = order_count
        if profile.total_spent != total_spent:
            profile.total_spent = total_spent
        if first_order and profile.first_order_at != first_order.created_at:
            profile.first_order_at = first_order.created_at

        if profile.lifecycle_stage != base:
            old = profile.lifecycle_stage
            profile.lifecycle_stage = base
            CrmEvent.objects.create(
                user=conversation.user,
                conversation=conversation,
                type="lifecycle_change",
                description=f"Lifecycle: {old} → {base}",
                data={"from": old, "to": base, "order_count": order_count},
            )
        if profile.lead_score != lead_score:
            profile.lead_score = lead_score
        if abs((profile.buying_probability or 0) - prob) > 0.001:
            profile.buying_probability = prob
        profile.save(
            update_fields=["order_count", "total_spent", "first_order_at",
                           "lifecycle_stage", "lead_score", "buying_probability",
                           "updated_at"]
        )

        # Opportunity updates.
        opp = _get_opportunity(conversation)
        if opp is None or opp.status != "open":
            return profile
        stage = opp.stage
        if lead_score >= 70 and stage in ("discovery", "product_interest", "considering"):
            stage = "ready_to_buy"
        elif lead_score >= 40 and stage == "discovery":
            stage = "product_interest"
        if profile.lifecycle_stage in ("customer", "repeat_customer", "vip") and stage not in ("won", "lost"):
            stage = "won"
            opp.status = "won"
            CrmEvent.objects.create(
                user=conversation.user,
                conversation=conversation,
                type="stage_change",
                description=f"Opportunity won: {opp.get_stage_display()} → Won",
                data={"stage": "won", "source": "lifecycle"},
            )
        if stage != opp.stage:
            CrmEvent.objects.create(
                user=conversation.user,
                conversation=conversation,
                type="stage_change",
                description=f"Stage: {opp.get_stage_display()} → {dict(SalesOpportunity.STAGE_CHOICES).get(stage, stage)}",
                data={"from": opp.stage, "to": stage, "lead_score": lead_score},
            )
            opp.stage = stage
        opp.buying_probability = prob
        if conversation.detected_intent and conversation.detected_intent != opp.intent:
            opp.intent = conversation.detected_intent
        opp.save(update_fields=["stage", "status", "intent", "buying_probability", "updated_at"])
        return profile
    except Exception:
        logger.exception("CRM recompute failed conv=%s", getattr(conversation, "pk", None))
        return None
