from django.db.models.signals import post_save
from django.dispatch import receiver

from .services import setting, create_lead, internal_queryset
from .models import Lead


@receiver(post_save, sender="back.Conversation", dispatch_uid="crm_social_lead_creation")
def social_lead_creation(sender, instance, created, **kwargs):
    """Auto-create a lead from a new conversation (gated by CrmSetting).

    Active only when the 'social_lead_creation' setting is enabled — used
    once MatrixAI connects its own social pages, or for per-tenant rollout.
    """
    if not setting("social_lead_creation", False):
        return
    if not created:
        return
    try:
        existing = internal_queryset(Lead.objects.filter(conversation=instance)).exists()
        if existing:
            return
        create_lead(
            None,
            name=instance.customer_name or instance.customer_id,
            phone=instance.customer_phone or "",
            source=instance.platform,
            conversation=instance,
            log=False,
        )
    except Exception:
        return
