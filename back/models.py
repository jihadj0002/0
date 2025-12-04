from django.db import models

# Create your models here.

from django.db import models
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import User
import uuid

from django.utils.html import mark_safe
from shortuuid.django_fields import ShortUUIDField

from django.utils import timezone
from datetime import timedelta

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
    image = models.ImageField(upload_to="user_directory_path", default="product.jpg")
    description = models.TextField(blank=True, null=True)

    price = models.DecimalField(max_digits=10, decimal_places=2)
    discounted_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    
    stock_quantity = models.IntegerField(default=0)
    status = models.BooleanField(default=True)
    last_synced = models.DateTimeField(auto_now=True)
    upsell_enabled = models.BooleanField(default=False)

    pid = ShortUUIDField(
        length=6,
        max_length=10,
        prefix="sku_",
        alphabet="abcdefg1234"
    )

    class Meta:
        verbose_name_plural = "Products"

    def product_image(self):
        return mark_safe(f'<img src="{self.image.url}" width="50" height="50" />')

    def __str__(self):
        return f"{self.name} ({self.user.email})"
    
    def get_percentage(self):
        new_price = (self.price / self.discounted_price) * 100
        return new_price
    

class ProductImages(models.Model):
    images = models.ImageField(upload_to="product-images", default="product.jpg")
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Product Images"


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
    message_text = models.TextField(blank=True, null=True)
    response_text = models.TextField(blank=True, null=True)
    is_ai_generated = models.BooleanField(default=True)
    is_ai_enabled = models.BooleanField(default=True)
    ai_disabled_at = models.DateTimeField(null=True, blank=True)   # Time when AI was turned off
    ai_enable_delay = models.IntegerField(default=300)             # Time in seconds before re-enabling AI

    timestamp = models.DateTimeField(auto_now_add=True)
    chat_summary = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.platform} - {self.customer_id} ({self.timestamp})"

    # MAIN LOGIC
    def disable_ai(self):
        """Called when API says `is_ai_enabled = false`."""
        self.is_ai_enabled = False
        self.ai_disabled_at = timezone.now()
        self.save()

    def auto_enable_ai(self):
        """Automatically turns AI back on if the timeout has passed."""
        if not self.is_ai_enabled and self.ai_disabled_at:
            if timezone.now() >= self.ai_disabled_at + timedelta(seconds=self.ai_enable_delay):
                self.is_ai_enabled = True
                self.ai_disabled_at = None
                self.save()
        return self.is_ai_enabled    
    
class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    
    SENDER_CHOICES = [
        ("customer", "Customer"),
        ("bot", "Bot"),
        ("agent", "Agent"),
    ]

    sender = models.CharField(max_length=20, choices=SENDER_CHOICES)
    text = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.sender}: {self.text[:30]}"

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
    # uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sales")
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name="sales")
    customer_name = models.CharField(max_length=150, blank=True)
    customer_address = models.CharField(max_length=150, blank=True)
    customer_phone = models.CharField(max_length=15, blank=True)
    customer_id = models.CharField(max_length=255)

    oid = ShortUUIDField(
        length=6,
        max_length=10,
        prefix="ord_",
        alphabet="abcdefg1234"
    )

    amount = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        creating = self._state.adding  # only decrease stock on first save
        super().save(*args, **kwargs)
        if creating and self.product:
            if self.product.stock_quantity > 0:
                self.product.stock_quantity -= 1
                self.product.save()
            else:
                # optional: raise an error if out of stock
                raise ValueError("Cannot create sale: product out of stock")

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







