import calendar
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models
from django.db.models import F


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

class Plan(models.Model):
    PLAN_CHOICES = [
        ("free", "Free"),
        ("basic", "Basic"),
        ("pro", "Pro"),
        ("enterprise", "Enterprise"),
    ]

    name = models.CharField(max_length=20, choices=PLAN_CHOICES, unique=True)
    monthly_credits = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    max_messages_per_month = models.IntegerField(default=0, help_text="0 = unlimited")
    allowed_models = models.JSONField(default=list, blank=True, help_text='e.g. ["openai/gpt-4o-mini"]')
    price_per_month = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["price_per_month"]
        verbose_name = "Plan"
        verbose_name_plural = "Plans"

    def __str__(self):
        return f"{self.get_name_display()} ({self.monthly_credits} credits/mo)"


# ---------------------------------------------------------------------------
# UserBalance
# ---------------------------------------------------------------------------

class UserBalance(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="balance")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscribers")
    credits_remaining = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    credits_total = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    messages_used = models.IntegerField(default=0)
    renewal_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User Balance"
        verbose_name_plural = "User Balances"

    def __str__(self):
        return f"{self.user.username} | {self.credits_remaining}/{self.credits_total} credits | renews {self.renewal_date}"

    def is_exhausted(self):
        return self.credits_remaining <= Decimal("0")

    def usage_percent(self):
        if not self.credits_total:
            return 0
        used = self.credits_total - self.credits_remaining
        return round(float(used / self.credits_total) * 100, 1)

    @staticmethod
    def next_renewal_date(from_date=None):
        """Return the date one calendar month from `from_date` (or today)."""
        d = from_date or date.today()
        month = d.month + 1
        year = d.year + (1 if month > 12 else 0)
        month = month if month <= 12 else 1
        day = min(d.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)


# ---------------------------------------------------------------------------
# ModelPricing
# ---------------------------------------------------------------------------

class ModelPricing(models.Model):
    model_id = models.CharField(max_length=150, unique=True, help_text='OpenRouter model ID e.g. "openai/gpt-4o-mini"')
    credits_per_1k_input = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    credits_per_1k_output = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["model_id"]
        verbose_name = "Model Pricing"
        verbose_name_plural = "Model Pricing"

    def __str__(self):
        return f"{self.model_id}  in={self.credits_per_1k_input}  out={self.credits_per_1k_output}"

    def cost_for(self, input_tokens, output_tokens):
        return (
            Decimal(input_tokens) / 1000 * self.credits_per_1k_input
            + Decimal(output_tokens) / 1000 * self.credits_per_1k_output
        )


# ---------------------------------------------------------------------------
# UsageSummary (per user per day)
# ---------------------------------------------------------------------------

class UsageSummary(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="usage_summaries")
    date = models.DateField(db_index=True)
    total_replies = models.IntegerField(default=0)
    total_ai_calls = models.IntegerField(default=0)
    total_input_tokens = models.IntegerField(default=0)
    total_output_tokens = models.IntegerField(default=0)
    total_credits_used = models.DecimalField(max_digits=12, decimal_places=4, default=0)

    class Meta:
        unique_together = [("user", "date")]
        ordering = ["-date"]
        verbose_name = "Usage Summary"
        verbose_name_plural = "Usage Summaries"

    def __str__(self):
        return f"{self.user.username} | {self.date} | {self.total_replies} replies | {self.total_credits_used} credits"


# ---------------------------------------------------------------------------
# CreditTransaction (audit trail)
# ---------------------------------------------------------------------------

class CreditTransaction(models.Model):
    TYPE_CHOICES = [
        ("deduction", "Deduction"),
        ("renewal", "Renewal"),
        ("top_up", "Top-up"),
        ("adjustment", "Manual Adjustment"),
        ("plan_change", "Plan Change"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="credit_transactions")
    amount = models.DecimalField(max_digits=12, decimal_places=4, help_text="Negative = debit, positive = credit")
    balance_after = models.DecimalField(max_digits=12, decimal_places=4)
    transaction_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    reply_id = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    note = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Credit Transaction"
        verbose_name_plural = "Credit Transactions"

    def __str__(self):
        sign = "+" if self.amount >= 0 else ""
        return f"{self.user.username} | {sign}{self.amount} → {self.balance_after} | {self.transaction_type} | {self.timestamp:%Y-%m-%d %H:%M}"
