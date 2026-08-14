"""Per-user CRM models.

A CRM layer around each Conversation (a SaaS user's chat customer):
- CustomerProfile  — long-lived customer identity + lifecycle + facts/signals
- SalesOpportunity — conversation-specific sales stage
- OrderDraft       — backend-computed order in flight (pre-Sale)
- CrmEvent         — audit timeline

All rows are scoped by ``user`` (per-tenant).
"""
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


# -----------------------
# CustomerProfile — long-term customer record
# -----------------------
class CustomerProfile(models.Model):
    LIFECYCLE_CHOICES = [
        ("lead", "Lead"),
        ("prospect", "Prospect"),
        ("customer", "Customer"),
        ("repeat_customer", "Repeat Customer"),
        ("vip", "VIP"),
        ("inactive", "Inactive"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="crm_customer_profiles")
    conversation = models.OneToOneField(
        "back.Conversation", on_delete=models.CASCADE, related_name="crm_profile"
    )
    customer_id = models.CharField(max_length=255, blank=True, default="")
    platform = models.CharField(max_length=20, blank=True, default="")

    # identity — synced from Conversation / update_customer tool
    name = models.CharField(max_length=255, blank=True, default="")
    phone = models.CharField(max_length=50, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    city = models.CharField(max_length=100, blank=True, default="")
    address = models.TextField(blank=True, default="")

    lifecycle_stage = models.CharField(
        max_length=20, choices=LIFECYCLE_CHOICES, default="lead"
    )
    lead_score = models.IntegerField(default=0, help_text="0-100, derived from buying signals")
    buying_probability = models.FloatField(default=0.0, help_text="0.0-1.0, derived")

    # facts the customer actually told us (source=customer) or backend confirmed
    facts = models.JSONField(default=dict, blank=True, help_text="{key: {value, source, confidence}}")
    preferences = models.JSONField(default=list, blank=True, help_text="list of {pid, name, count}")
    objections = models.JSONField(default=list, blank=True, help_text="list of {type, text, count}")
    buying_signals = models.JSONField(default=dict, blank=True, help_text="boolean signal flags")

    order_count = models.IntegerField(default=0)
    total_spent = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    first_order_at = models.DateTimeField(null=True, blank=True)
    last_contact_at = models.DateTimeField(default=timezone.now)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Customer Profile"
        verbose_name_plural = "Customer Profiles"
        indexes = [
            models.Index(fields=["user", "lifecycle_stage"]),
            models.Index(fields=["user", "phone"]),
            models.Index(fields=["user", "-last_contact_at"]),
        ]

    def __str__(self):
        return f"{self.name or self.customer_id or self.pk} ({self.get_lifecycle_stage_display()})"


# -----------------------
# SalesOpportunity — conversation-specific sales pipeline
# -----------------------
class SalesOpportunity(models.Model):
    STAGE_CHOICES = [
        ("discovery", "Discovery"),
        ("product_interest", "Product Interest"),
        ("considering", "Considering"),
        ("ready_to_buy", "Ready to Buy"),
        ("negotiating", "Negotiating"),
        ("won", "Won"),
        ("lost", "Lost"),
    ]
    STATUS_CHOICES = [
        ("open", "Open"),
        ("won", "Won"),
        ("lost", "Lost"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="crm_opportunities")
    conversation = models.OneToOneField(
        "back.Conversation", on_delete=models.CASCADE, related_name="crm_opportunity"
    )
    customer_profile = models.ForeignKey(
        CustomerProfile, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="opportunities",
    )

    stage = models.CharField(max_length=20, choices=STAGE_CHOICES, default="discovery")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="open")
    intent = models.CharField(max_length=100, blank=True, default="")
    current_product_pid = models.CharField(max_length=50, blank=True, default="")
    budget_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    budget_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    buying_probability = models.FloatField(default=0.0, help_text="0.0-1.0, derived")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Sales Opportunity"
        verbose_name_plural = "Sales Opportunities"
        indexes = [
            models.Index(fields=["user", "stage", "status"]),
            models.Index(fields=["user", "status", "-updated_at"]),
        ]

    def __str__(self):
        return f"{self.get_stage_display()} — conv #{self.conversation_id}"


# -----------------------
# OrderDraft — in-flight order (backend-computed totals, pre-Sale)
# -----------------------
class OrderDraft(models.Model):
    CONFIRMATION_CHOICES = [
        ("no_order", "No Order"),
        ("draft", "Draft"),
        ("awaiting_confirmation", "Awaiting Confirmation"),
        ("confirmed", "Confirmed"),
        ("cancelled", "Cancelled"),
    ]
    DELIVERY_CHOICES = [
        ("inside_dhaka", "Inside Dhaka"),
        ("outside_dhaka", "Outside Dhaka"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="crm_order_drafts")
    conversation = models.OneToOneField(
        "back.Conversation", on_delete=models.CASCADE, related_name="crm_order_draft"
    )
    opportunity = models.ForeignKey(
        SalesOpportunity, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="order_drafts",
    )

    # snapshot of ordered items (denormalized from Sale.OrderItem)
    items = models.JSONField(default=list, blank=True, help_text="[{pid, name, qty, unit_price, variation_id}]")
    delivery_zone = models.CharField(max_length=20, choices=DELIVERY_CHOICES, default="inside_dhaka")
    item_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    delivery_charge = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    confirmation_status = models.CharField(
        max_length=24, choices=CONFIRMATION_CHOICES, default="no_order"
    )
    converted_order = models.ForeignKey(
        "back.Sale", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="crm_draft",
    )
    missing_fields = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Order Draft"
        verbose_name_plural = "Order Drafts"

    def __str__(self):
        return f"{self.get_confirmation_status_display()} ৳{self.grand_total} — conv #{self.conversation_id}"


# -----------------------
# CrmEvent — audit timeline
# -----------------------
class CrmEvent(models.Model):
    TYPE_CHOICES = [
        ("stage_change", "Stage Change"),
        ("order_draft", "Order Draft"),
        ("order_created", "Order Created"),
        ("fact_recorded", "Fact Recorded"),
        ("ticket_created", "Ticket Created"),
        ("signal", "Signal"),
        ("lifecycle_change", "Lifecycle Change"),
        ("note", "Note"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="crm_events")
    conversation = models.ForeignKey(
        "back.Conversation", on_delete=models.CASCADE, related_name="crm_events"
    )
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, default="note")
    description = models.TextField(blank=True, default="")
    data = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "CRM Event"
        verbose_name_plural = "CRM Events"
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["conversation", "-timestamp"]),
        ]

    def __str__(self):
        return f"{self.get_type_display()} — conv #{self.conversation_id} ({self.timestamp:%Y-%m-%d %H:%M})"
