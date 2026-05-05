import calendar
import logging
from datetime import date

from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(post_save, sender=User, dispatch_uid="billing_create_user_balance")
def create_user_balance(sender, instance, created, **kwargs):
    """Auto-create a UserBalance with the free plan when a new user registers."""
    if not created:
        return

    from .models import Plan, UserBalance

    plan = Plan.objects.filter(name="free", is_active=True).first()
    if not plan:
        logger.warning("No active 'free' Plan found — skipping UserBalance creation for user=%s", instance.pk)
        return

    UserBalance.objects.get_or_create(
        user=instance,
        defaults={
            "plan": plan,
            "credits_remaining": plan.monthly_credits,
            "credits_total": plan.monthly_credits,
            "renewal_date": UserBalance.next_renewal_date(),
        },
    )
