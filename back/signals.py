# signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import UserProfile, Sale, OrderItem
from .models import Message
import requests




EXTERNAL_UPDATE_URL = "https://erp.monowamart.com/api/v1/1/ai/order"

@receiver(post_save, sender=Sale)
def external_order_post_request(sender, instance, created, **kwargs):

    if not created:
        return

    if instance.source != "external":
        return

    if instance.user.username != "monowamart":
        return

    try:
        print("Preparing payload for external order update...")

        payload = {
            "address": {
                "name": instance.customer_name,
                "mobile": instance.customer_phone,
                "address": instance.customer_address,
                "city": getattr(instance, "customer_city", ""),
                "state": getattr(instance, "customer_state", ""),
            },

            "items": [
                {
                    "product_id": item.external_product_id,
                    "variation_id": getattr(item, "external_variation_id", None),
                    "quantity": item.quantity,
                }
                for item in instance.items.all()
            ],

            "delivered_to": getattr(instance, "delivered_to", ""),
            "pickup_location_id": 1,
            "shipping_note": "",
            "source": "ai",
            "payment_method": "cod",
            "rp_redeemed": 0,
        },
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        print("Payload:", payload)

        response = requests.post(
            EXTERNAL_UPDATE_URL,
            json=payload,
            headers=headers,
            timeout=10
        )

        response.raise_for_status()

        if response.status_code in [200, 201]:
            instance.updated_to_web = "updated"
            instance.save(update_fields=["updated_to_web"])

    except requests.RequestException as e:
        print("External order sync failed:", e)




        response = requests.post(EXTERNAL_UPDATE_URL, json=payload, timeout=10)
        response.raise_for_status()  # Raise exception for HTTP errors

        if response.status_code == 200:
            # Mark the sale as completed or update status as needed
            instance.updated_to_web = "updated"
            instance.save(update_fields=["updated_to_web"])

    except requests.RequestException as e:
        # You can log this for debugging
        print(f"Failed to send external order update: {e}")






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