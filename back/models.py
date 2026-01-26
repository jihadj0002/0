from django.db import models

# Create your models here.

from django.db import models
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import User
import uuid

from django.utils.html import mark_safe
from shortuuid.django_fields import ShortUUIDField

from django.utils import timezone
from django.core.exceptions import ValidationError
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
    PRODUCT_CHOICES = [
        ("normal_product", "Normal Product"),
        ("digital", "Digital Product"),
        ("package", "Package"),
        ("reservation", "Reservation"),
        ("service", "Service"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default="free")
    product_type = models.CharField(max_length=20, choices=PRODUCT_CHOICES, default="normal_product")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} ({self.plan})"

# -----------------------
# User Profile Integrations
# -----------------------
class Integration(models.Model):
    PLATFORM_CHOICES = [
        ("messenger", "Messenger"),
        ("whatsapp", "WhatsApp"),
        ("instagram", "Instagram"),
        ("telegram", "Telegram"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="integrations")
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)

    webhook_url = models.URLField(blank=True, null=True)
    access_token = models.TextField(blank=True, null=True)
    integration_id = models.TextField(blank=True, null=True)

    is_enabled = models.BooleanField(default=False)
    is_connected = models.BooleanField(default=False)

    last_verified_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

# -----------------------
# Products
# -----------------------
class Product(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="products")
    
    name = models.CharField(max_length=255)
    image = models.ImageField(upload_to="products", default="product.jpg")
    description = models.TextField(blank=True, null=True)

    price = models.DecimalField(max_digits=10, decimal_places=2)
    discounted_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    
    stock_quantity = models.IntegerField(default=0)
    status = models.BooleanField(default=True)
    last_synced = models.DateTimeField(auto_now=True)
    upsell_enabled = models.BooleanField(default=False)

    pid = ShortUUIDField(
        length=6,
        max_length=15,
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



    # Package Model Based On products

class Package(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="packages")
    pacid = ShortUUIDField(length=6,max_length=15,prefix="pac_",alphabet="abcdefg1234")

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    discounted_price = models.DecimalField(max_digits=10, decimal_places=2)
    stock_quantity = models.IntegerField(default=100)
    upsell_enabled = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    image = models.ImageField(upload_to="package", default="package.jpg")

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
    
    def get_percentage(self):
        new_price = (self.price / self.discounted_price) * 100
        return new_price
    
    
    def product_image(self):
        return mark_safe(f'<img src="{self.image.url}" width="50" height="50" />')

    


class PackageImages(models.Model):
    images = models.ImageField(upload_to="package-images", default="package.jpg")
    package = models.ForeignKey(Package, on_delete=models.SET_NULL, null=True)
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Package Images"

class PackageItem(models.Model):
    package = models.ForeignKey(Package, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE)

    # Price difference
    add_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    remove_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    is_default = models.BooleanField(default=True)  # included initially
    is_optional = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.product.name} in {self.package.name}"



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
    
    GENDER_CHOICES = [
        ("male", "Male"),
        ("female", "Female"),
        ("unknown", "Unknown"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="conversations")
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    customer_id = models.CharField(max_length=255)  # external ID
    profile_image = models.ImageField(upload_to="customer_profiles", default="customer.jpg")
    
    customer_name = models.CharField(max_length=255, blank=True, null=True)
    customer_gender = models.CharField(max_length=20, choices=GENDER_CHOICES, default="unknown", null=True, blank=True)
    refer_customer_with = models.CharField(max_length=20, default="Sir")

    message_text = models.TextField(blank=True, null=True)
    response_text = models.TextField(blank=True, null=True)

    is_ai_generated = models.BooleanField(default=True)
    is_ai_enabled = models.BooleanField(default=True)
    ai_disabled_at = models.DateTimeField(null=True, blank=True)   # Time when AI was turned off
    ai_enable_delay = models.IntegerField(default=300)             # Time in seconds before re-enabling AI

    timestamp = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    chat_summary = models.TextField(blank=True, null=True)

    # -----------------------------------------------------
    # EXTENDED VARIABLES (all new fields added here)
    # -----------------------------------------------------

    customer_phone = models.CharField(max_length=50, blank=True, null=True)
    customer_city = models.CharField(max_length=100, blank=True, null=True)

    is_returning = models.BooleanField(default=False)
    preferred_tone = models.CharField(max_length=50, blank=True, null=True)

    last_viewed_product = models.CharField(max_length=255, blank=True, null=True)
    detected_intent = models.CharField(max_length=100, blank=True, null=True)
    current_product = models.CharField(max_length=1500, blank=True, null=True)
    current_package = models.CharField(max_length=1500, blank=True, null=True)
    extra_data = models.JSONField(blank=True, null=True)

    language_detected = models.CharField(max_length=20, blank=True, null=True)

    order_status = models.CharField(max_length=100, blank=True, null=True)
    order_total = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)

    missing_order_fields = models.JSONField(blank=True, null=True)

    greeted = models.BooleanField(default=False)
    image_allowed = models.BooleanField(default=True)
    last_message_type = models.CharField(max_length=50, blank=True, null=True)

    user_category = models.CharField(max_length=100, blank=True, null=True)
    related_products = models.JSONField(blank=True, null=True)

    interest_score = models.FloatField(blank=True, null=True)
    estimated_budget = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)


    # NEW COUNTERS
    bot_sent_count = models.IntegerField(default=0)
    bot_received_count = models.IntegerField(default=0)
    customer_sent_count = models.IntegerField(default=0)
    agent_sent_count = models.IntegerField(default=0)


    # -----------------------------------------------------
    # END EXTENDED VARIABLES
    # -----------------------------------------------------

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
    

    
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "platform", "customer_id"],
                name="unique_user_platform_customer"
            )
        ]
    # def check_integration_for_messenger(self):
    #     """Check if Messenger integration is disabled, and disable AI if so."""
    #     # Check if there is an integration with 'messenger' platform and `is_enabled = False`
    #     integration = Integration.objects.filter(user=self.user, platform="messenger", is_enabled=False).first()
    #     if integration:
    #         self.is_ai_enabled = False
    #         self.ai_disabled_at = timezone.now()
    #         self.save()
    #         return True  # AI was disabled due to the Messenger integration being disabled
    #     return False
    
    # def save(self, *args, **kwargs):
    #     # Check if Messenger integration is disabled for the user
    #     self.check_integration_for_messenger()
    #     super(Conversation, self).save(*args, **kwargs)
    

class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")

    # 🔹 Platform message id (mid / wamid / etc.)
    mid = models.CharField(max_length=255, unique=True, blank=True, null=True, db_index=True)
    # Send image video anything as attachments (JSON)
    attachments = models.JSONField(blank=True, null=True)
    # Replied To Message
    replied_to = models.ForeignKey("self", null=True, blank=True, to_field="mid", on_delete=models.SET_NULL, related_name="replies")


    SENDER_CHOICES = [
        ("customer", "Customer"),
        ("bot", "Bot"),
        ("agent", "Agent"),
    ]

    sender = models.CharField(max_length=20, choices=SENDER_CHOICES)
    text = models.TextField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)


    def __str__(self):
        if self.text:
            return f"{self.sender}: {self.text[:30]}"

        if self.attachments:
            return f"{self.sender}: [attachment]"

        return f"{self.sender}: [empty message]"

# -----------------------
# Sales
# -----------------------
class Sale(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("pending", "Pending"),
        ("delivering", "Delivering"),
        ("completed", "Completed"),
        ("refunded", "Refunded"),
    ]
    
    
    # uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    conversation = models.ForeignKey(Conversation,on_delete=models.SET_NULL,null=True,blank=True,related_name="orders")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sales")
    SOURCE_CHOICES = [
            ("internal", "Internal"),
            ("external", "External"),
        ]
    
    DELIVERED_CHOICES = [
            ("inside_dhaka", "Inside Dhaka"),
            ("outside_dhaka", "Outside Dhaka"),
        ]

    UPDATE_CHOICES = [
            ("failed", "Failed"),
            ("updated", "Updated"),
        ]

    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="internal")

    # If Sale is a package sale
    package = models.ForeignKey("Package",on_delete=models.PROTECT,null=True,blank=True,related_name="package")
    
    delivered_to = models.CharField(max_length=20, choices=DELIVERED_CHOICES, default="inside_dhaka")
    
    updated_to_web = models.CharField(max_length=20, choices=UPDATE_CHOICES, default="failed")
    # updated_to_web = models.BooleanField(default=False)
    
    external_order_id = models.CharField(max_length=255, blank=True, null=True)

    # product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name="sales")
    
    customer_name = models.CharField(max_length=150, blank=True)
    customer_address = models.CharField(max_length=150, blank=True)
    customer_city = models.CharField(max_length=150, blank=True)
    customer_state = models.CharField(max_length=150, blank=True)
    customer_phone = models.CharField(max_length=15, blank=True)
    customer_id = models.CharField(max_length=255)

    oid = ShortUUIDField(
        length=6,
        max_length=15,
        prefix="ord_",
        alphabet="abcdefg1234"
    )

    amount = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self):
        return f"Sale {self.id} - {self.user.email} ({self.status})"
    
class OrderItem(models.Model):


    ACTION_CHOICES = [
        ("base", "Base Included"),
        ("added", "Added"),
        ("removed", "Removed"),
    ]

    action = models.CharField(max_length=20, choices=ACTION_CHOICES, default="base")    
    price_delta = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    
    
    order = models.ForeignKey(Sale, related_name="items", on_delete=models.CASCADE)

    # Editable product data
    product_name = models.CharField(max_length=255, default="Unknown Product", null=True, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    quantity = models.PositiveIntegerField(default=1)

    product = models.ForeignKey(Product, on_delete=models.PROTECT , null=True, blank=True)  # required FK

    # Optional references
    internal_product = models.ForeignKey(Product,on_delete=models.SET_NULL,null=True,blank=True,related_name="order_items")

    external_product_id = models.CharField(max_length=255, blank=True, null=True)
    external_variation_id = models.CharField(max_length=255, blank=True, null=True)

    raw_product_data = models.JSONField(blank=True, null=True)


    def save(self, *args, **kwargs):
        if self.pk and self.order.status in ["completed", "refunded"]:
            raise ValidationError(
                "Completed or refunded orders cannot be modified."
            )
        super().save(*args, **kwargs)



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







