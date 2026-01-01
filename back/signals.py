# signals.py
from datetime import timezone
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import UserProfile, Sale, OrderItem
from .models import Message, Integration, Conversation
import requests
import json




WEBHOOK_URL = "https://n8n.srv915514.hstgr.cloud/webhook/b3270b85-fae4-4d1b-ba47-a3ff66d28527"
EXTERNAL_UPDATE_URL = "https://erp.monowamart.com/api/v1/1/ai/order"



@receiver(post_save, sender=OrderItem)
def external_order_item_post_request(sender, instance, created, **kwargs):
    # Only process when the OrderItem is newly created
    if not created:
        return

    # Get the Sale instance related to this OrderItem
    sale = instance.order

    # Check if the Sale is external and if the username is "monowamart"
    if sale.source != "external" or sale.user.username != "monowamart":
        return

    try:
        print("Preparing payload for external order update...")

        # Create the payload similar to your previous Sale signal
        payload = {
            "order_id": sale.oid,
            "address": {
                "name": sale.customer_name,
                "mobile": sale.customer_phone,
                "address": sale.customer_address,
                "city": getattr(sale, "customer_city", ""),
                "state": getattr(sale, "customer_state", ""),
            },
            "items": [
                {
                    "product_id": instance.external_product_id,
                    "variation_id": instance.external_variation_id,
                    "quantity": instance.quantity,
                }
            ],
            "delivered_to": getattr(sale, "delivered_to", ""),
            "pickup_location_id": 1,
            "shipping_note": "",
            "source": "ai",  # assuming "ai" is the source you're using for external orders
            "payment_method": "cod",
            "rp_redeemed": 0,
        }

        # Define headers for the request
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        print("Payload:", payload)

        # Send the request to the external webhook
        try:
            response = requests.post(
                WEBHOOK_URL,
                json=payload,      # ✅ webhook body
                headers=headers,
                timeout=5,
            )
            print("Webhook delivered successfully.")
            print("Webhook response status:", response.status_code)
            print("Response content:", response.text)
        except requests.RequestException as e:
            print("Webhook delivery failed:", e)

    except Exception as e:
        print("Error during processing OrderItem post-save:", e)


        response = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        print("External order update response status:", response.status_code)
        response.raise_for_status()  # Raise exception for HTTP errors

        if response.status_code == 200:
            # Mark the sale as completed or update status as needed
            instance.updated_to_web = "updated"
            instance.save(update_fields=["updated_to_web"])

    except requests.RequestException as e:
        # You can log this for debugging
        print(f"Failed to send external order update: {e}")



@receiver(post_save, sender=Integration)
def sync_ai_status_to_conversations(sender, instance, **kwargs):
    Conversation.objects.filter(
        user=instance.user,
        platform=instance.platform
    ).update(
        is_ai_enabled=instance.is_enabled,
        # ai_disabled_at=None if instance.is_enabled else timezone.now()
    )


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