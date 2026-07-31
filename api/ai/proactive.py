"""
Proactive monitor (P2-18..20): periodic checks against user-configured
ProactiveRule rows, dispatching proactive alerts outside the message flow.
"""
import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from back.models import Conversation, Integration, Message, Product, Sale
from billing.models import UserBalance
from context.models import ProactiveRule

logger = logging.getLogger(__name__)

# P2-20: specialized prompt for proactive messages — short, action-oriented
PROACTIVE_SYSTEM_PROMPT = (
    "You are sending a proactive alert to a store owner. Rules:\n"
    "1. Keep it under 2 short sentences — owners read these on mobile.\n"
    "2. State the problem first, then one clear action the owner can take.\n"
    "3. Do NOT use greetings, emojis, or sales talk.\n"
    "4. Respond in the same language as the input payload if provided, otherwise English."
)

# Per-event fallback templates (used when the LLM call fails or is disabled)
_FALLBACK_TEMPLATES = {
    "sync_failure": "Order sync to your web store failed for {count} order(s) in the last 24h. Please check your store connection settings.",
    "low_stock": "Low stock alert: {name} has only {stock} unit(s) left (threshold {threshold}). Restock soon to avoid losing sales.",
    "token_expiry": "Your {platform} connection token expires in {days} day(s) on {date}. Reconnect now to keep the AI assistant running.",
    "subscription_expiring": "Your credit plan renews in {days} day(s) on {date}. Top up credits to avoid interruptions.",
}


def _days_left(expiry_date):
    if not expiry_date:
        return None
    if hasattr(expiry_date, "date"):  # datetime
        delta = expiry_date - timezone.now()
    else:  # date
        delta = expiry_date - timezone.localdate()
    return max(delta.days, 0)


def evaluate_rule(rule: ProactiveRule):
    """Evaluate one rule. Returns a dict payload or None if no alert."""
    user = rule.user
    now = timezone.now()

    if rule.event_type == "sync_failure":
        since = now - timedelta(hours=24)
        failed = Sale.objects.filter(
            user=user, updated_to_web="failed", created_at__gte=since
        ).count()
        if failed > 0:
            return {"count": failed}
        return None

    if rule.event_type == "low_stock":
        threshold = rule.threshold.get("stock_threshold", 5)
        product = Product.objects.filter(
            user=user, status=True, stock_quantity__lte=threshold
        ).order_by("stock_quantity").first()
        if product:
            return {
                "name": product.name,
                "stock": product.stock_quantity,
                "threshold": threshold,
            }
        return None

    if rule.event_type == "token_expiry":
        days_ahead = rule.threshold.get("days_ahead", 2)
        horizon = now + timedelta(days=days_ahead)
        integration = Integration.objects.filter(
            user=user, is_enabled=True
        ).filter(
            Q(token_expires_at__isnull=False),
            Q(token_expires_at__lte=horizon),
            Q(token_expires_at__gt=now),
        ).order_by("token_expires_at").first()
        if integration:
            return {
                "platform": integration.platform,
                "days": _days_left(integration.token_expires_at),
                "date": integration.token_expires_at.date().isoformat(),
            }
        return None

    if rule.event_type == "subscription_expiring":
        days_ahead = rule.threshold.get("days_ahead", 2)
        horizon = timezone.localdate() + timedelta(days=days_ahead)
        try:
            balance = UserBalance.objects.get(user=user)
        except UserBalance.DoesNotExist:
            return None
        if balance.renewal_date and timezone.localdate() < balance.renewal_date <= horizon:
            return {
                "days": _days_left(balance.renewal_date),
                "date": balance.renewal_date.isoformat(),
            }
        return None

    return None


def _target_conversation(user, channel):
    if channel in ("whatsapp", "messenger", "instagram", "telegram"):
        conv = Conversation.objects.filter(user=user, platform=channel).exclude(
            customer_id__in=["", "test"]
        ).order_by("-updated_at").first()
        if conv:
            return conv
    return Conversation.objects.filter(user=user).exclude(
        customer_id__in=["", "test"]
    ).order_by("-updated_at").first()


def generate_alert_text(rule: ProactiveRule, payload: dict) -> str:
    """Compose the proactive message (LLM first, template fallback)."""
    try:
        from .providers import call_llm

        text, _ = call_llm(
            messages=[
                {"role": "system", "content": PROACTIVE_SYSTEM_PROMPT},
                {"role": "user", "content": f"Event: {rule.event_type}\nPayload: {payload}"},
            ],
            model="openai/gpt-4o-mini",
        )
        text = (text or "").strip()
        if text:
            return text
    except Exception:
        logger.exception("Proactive LLM failed, using template")
    template = _FALLBACK_TEMPLATES.get(rule.event_type, "")
    return template.format(**payload)


def dispatch_alert(rule: ProactiveRule, payload: dict) -> bool:
    """Send the proactive alert to the user's conversation channel (P2-19)."""
    from .sender import send_reply

    conversation = _target_conversation(rule.user, rule.notify_channel)
    if not conversation:
        logger.warning("No target conversation for user=%s", rule.user_id)
        return False

    text = generate_alert_text(rule, payload)
    try:
        send_reply(conversation, text)
        Message.objects.create(
            conversation=conversation,
            sender="bot",
            text=text,
        )
        return True
    except Exception:
        logger.exception("Proactive dispatch failed user=%s", rule.user_id)
        return False


def check_all() -> int:
    """Run all enabled rules once. Returns number of alerts dispatched."""
    dispatched = 0
    for rule in ProactiveRule.objects.filter(is_enabled=True).select_related("user"):
        try:
            payload = evaluate_rule(rule)
            if payload and dispatch_alert(rule, payload):
                dispatched += 1
        except Exception:
            logger.exception("Proactive check failed rule=%s", rule.pk)
    return dispatched
