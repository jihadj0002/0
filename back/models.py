from django.db import models

# Create your models here.

from django.db import models
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import User
import uuid


# -----------------------
# Custom User Model
# -----------------------
# class User(AbstractUser):
#     PLAN_CHOICES = [
#         ("free", "Free"),
#         ("pro", "Pro"),
#         ("enterprise", "Enterprise"),
#     ]

#     uuid = models.CharField(max_length=100, null=True, blank=True)
#     email = models.EmailField(unique=True)
#     plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default="free")
#     created_at = models.DateTimeField(auto_now_add=True, editable=False)
#     updated_at = models.DateTimeField(auto_now=True)

#     # Override related_name to avoid clashes with default auth.User
#     groups = models.ManyToManyField(
#         "auth.Group",
#         related_name="custom_user_set",   # 👈 new related_name
#         blank=True,
#         help_text="The groups this user belongs to.",
#         verbose_name="groups"
#     )
#     user_permissions = models.ManyToManyField(
#         "auth.Permission",
#         related_name="custom_user_set",   # 👈 new related_name
#         blank=True,
#         help_text="Specific permissions for this user.",
#         verbose_name="user permissions"
#     )

#     USERNAME_FIELD = "email"
#     REQUIRED_FIELDS = ["username"]  # username still required for admin

#     def __str__(self):
        # return self.email
    
# -----------------------
# User Profile
# -----------------------
class UserProfile(models.Model):
    PLAN_CHOICES = [
        ("free", "Free"),
        ("pro", "Pro"),
        ("enterprise", "Enterprise"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default="free")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} ({self.plan})"


# -----------------------
# Products
# -----------------------
class Product(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="products")
    
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    discounted_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    stock_quantity = models.IntegerField(default=0)
    upsell_enabled = models.BooleanField(default=False)
    last_synced = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.user.email})"


# -----------------------
# Conversations
# -----------------------
class Conversation(models.Model):
    PLATFORM_CHOICES = [
        ("messenger", "Messenger"),
        ("instagram", "Instagram"),
        ("whatsapp", "WhatsApp"),
        ("telegram", "Telegram"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="conversations")
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    customer_id = models.CharField(max_length=255)  # external ID
    message_text = models.TextField()
    response_text = models.TextField(blank=True, null=True)
    is_ai_generated = models.BooleanField(default=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    chat_summary = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.platform} - {self.customer_id} ({self.timestamp})"


# -----------------------
# Sales
# -----------------------
class Sale(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("delivering", "Delivering"),
        ("completed", "Completed"),
        ("refunded", "Refunded"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sales")
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name="sales")
    customer_id = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Sale {self.id} - {self.user.email} ({self.status})"


# -----------------------
# Settings
# -----------------------
class Setting(models.Model):
    PLATFORM_CHOICES = [
        ("messenger", "Messenger"),
        ("instagram", "Instagram"),
        ("whatsapp", "WhatsApp"),
        ("telegram", "Telegram"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="settings")
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    webhook_url = models.URLField(max_length=500, blank=True, null=True)
    access_token = models.CharField(max_length=500, blank=True, null=True)
    ai_rules = models.JSONField(default=dict, blank=True)  # flexible key-value
    working_hours = models.JSONField(default=dict, blank=True)  # {"start": "09:00", "end": "18:00"}
    fallback_message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Settings"

    def __str__(self):
        return f"{self.platform} settings for {self.user.email}"







