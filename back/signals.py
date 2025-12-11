# signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import UserProfile
from .models import Message

@receiver(post_save, sender=User)
def create_or_update_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)
    instance.profile.save()


@receiver(post_save, sender=Message)
def update_message_counters(sender, instance, created, **kwargs):
    if not created:
        return

    conv = instance.conversation

    # Customer message
    if instance.sender == "customer":
        conv.customer_sent_count += 1
        conv.bot_received_count += 1

    # Bot message
    elif instance.sender == "bot":
        conv.bot_sent_count += 1

    # Agent message
    elif instance.sender == "agent":
        conv.agent_sent_count += 1
        conv.bot_received_count += 1  # bot "receives" agent msg too (optional)

    conv.save()