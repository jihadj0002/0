from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import AgentIdentity, StoreConfig, BehaviorRules


@receiver(post_save, sender=User, dispatch_uid="context_create_defaults")
def create_context_defaults(sender, instance, created, **kwargs):
    if not created:
        return
    AgentIdentity.objects.get_or_create(user=instance)
    StoreConfig.objects.get_or_create(user=instance)
    BehaviorRules.objects.get_or_create(user=instance)
