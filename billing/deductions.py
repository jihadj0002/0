import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

logger = logging.getLogger(__name__)


def deduct_for_reply(user, reply_id):
    """
    Atomic credit deduction for a completed AI reply.

    Steps:
      1. Auto-renew if renewal_date has passed.
      2. Sum all UsageLog rows for this reply_id.
      3. Calculate credit cost using ModelPricing (0 if model not priced).
      4. select_for_update() → deduct → floor at 0.
      5. Write CreditTransaction audit row.
      6. Increment UsageSummary for today.
      7. If credits exhausted → disable all user Integrations.
    """
    from back.models import UsageLog, Integration
    from .models import CreditTransaction, ModelPricing, UsageSummary, UserBalance

    try:
        logs = list(UsageLog.objects.filter(user=user, reply_id=reply_id))
        if not logs:
            return

        # --- Calculate cost ---
        total_cost = Decimal("0")
        total_input = 0
        total_output = 0

        pricing_cache = {}
        for log in logs:
            total_input += log.input_tokens
            total_output += log.output_tokens

            if log.model not in pricing_cache:
                pricing_cache[log.model] = (
                    ModelPricing.objects.filter(model_id=log.model, is_active=True).first()
                )
            pricing = pricing_cache[log.model]
            if pricing:
                total_cost += pricing.cost_for(log.input_tokens, log.output_tokens)

        total_calls = len(logs)

        with transaction.atomic():
            try:
                balance = UserBalance.objects.select_for_update().get(user=user)
            except UserBalance.DoesNotExist:
                logger.warning("No UserBalance for user=%s — skipping deduction", user.pk)
                return

            # Auto-renew if period has passed
            today = timezone.now().date()
            if today >= balance.renewal_date:
                _do_renewal(balance, today)

            new_credits = max(Decimal("0"), balance.credits_remaining - total_cost)
            balance.credits_remaining = new_credits
            balance.messages_used = F("messages_used") + 1
            balance.save(update_fields=["credits_remaining", "messages_used", "updated_at"])

            CreditTransaction.objects.create(
                user=user,
                amount=-total_cost,
                balance_after=new_credits,
                transaction_type="deduction",
                reply_id=reply_id,
            )

            # Daily summary — use F() expressions to avoid race conditions
            summary, _ = UsageSummary.objects.get_or_create(
                user=user,
                date=today,
                defaults={
                    "total_replies": 0,
                    "total_ai_calls": 0,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "total_credits_used": Decimal("0"),
                },
            )
            UsageSummary.objects.filter(pk=summary.pk).update(
                total_replies=F("total_replies") + 1,
                total_ai_calls=F("total_ai_calls") + total_calls,
                total_input_tokens=F("total_input_tokens") + total_input,
                total_output_tokens=F("total_output_tokens") + total_output,
                total_credits_used=F("total_credits_used") + total_cost,
            )

            # Disable AI across all integrations if balance is now zero
            if new_credits <= Decimal("0"):
                disabled = Integration.objects.filter(user=user, is_enabled=True).update(is_enabled=False)
                if disabled:
                    logger.info("Credits exhausted for user=%s — disabled %d integrations", user.pk, disabled)
            elif balance.credits_total > 0:
                pct = new_credits / balance.credits_total
                if pct < Decimal("0.2"):
                    logger.warning(
                        "LOW_BALANCE user=%s remaining=%s total=%s pct=%.0f%%",
                        user.pk, new_credits, balance.credits_total, float(pct) * 100,
                    )

    except Exception:
        logger.exception("deduct_for_reply failed user=%s reply_id=%s", user.pk, reply_id)


def top_up(user, amount, note=""):
    """Add credits to a user's balance (admin top-up or manual adjustment)."""
    from .models import CreditTransaction, UserBalance

    amount = Decimal(str(amount))
    if amount <= 0:
        raise ValueError("top_up amount must be positive")

    with transaction.atomic():
        balance = UserBalance.objects.select_for_update().get(user=user)
        balance.credits_remaining += amount
        balance.credits_total += amount
        balance.save(update_fields=["credits_remaining", "credits_total", "updated_at"])

        CreditTransaction.objects.create(
            user=user,
            amount=amount,
            balance_after=balance.credits_remaining,
            transaction_type="top_up",
            note=note,
        )

    return balance


def _do_renewal(balance, _today=None):
    """
    Reset credits for a new billing period.
    Called inside an existing select_for_update() transaction block.
    """
    from .models import CreditTransaction, UserBalance

    old_credits = balance.credits_remaining
    new_credits = balance.plan.monthly_credits

    balance.credits_remaining = new_credits
    balance.credits_total = new_credits
    balance.messages_used = 0
    balance.renewal_date = UserBalance.next_renewal_date(balance.renewal_date)
    balance.save(update_fields=["credits_remaining", "credits_total", "messages_used", "renewal_date", "updated_at"])

    CreditTransaction.objects.create(
        user=balance.user,
        amount=new_credits - old_credits,
        balance_after=new_credits,
        transaction_type="renewal",
        note=f"Monthly renewal — plan: {balance.plan.name}",
    )
    logger.info("Renewed balance for user=%s new_credits=%s", balance.user_id, new_credits)
