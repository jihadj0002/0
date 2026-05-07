from django.db import models
from django.contrib.auth.models import User


TONE_CHOICES = [
    ("formal", "Formal"),
    ("friendly", "Friendly"),
    ("professional", "Professional"),
    ("casual", "Casual"),
    ("humorous", "Humorous"),
]

LANGUAGE_CHOICES = [
    ("en", "English"),
    ("bn", "Bengali"),
    ("ar", "Arabic"),
    ("hi", "Hindi"),
    ("fr", "French"),
    ("es", "Spanish"),
    ("tr", "Turkish"),
    ("id", "Indonesian"),
]

STYLE_CHOICES = [
    ("concise", "Concise"),
    ("detailed", "Detailed"),
    ("conversational", "Conversational"),
    ("formal", "Formal"),
]

CHIT_CHAT_STYLE_CHOICES = [
    ("off", "Off"),
    ("minimal", "Minimal"),
    ("moderate", "Moderate"),
    ("full", "Full"),
]


# -----------------------
# Agent Identity
# -----------------------
class AgentIdentity(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="agent_identity")
    name = models.CharField(max_length=100, default="Assistant")
    role = models.CharField(max_length=150, blank=True, null=True)
    tone = models.CharField(max_length=20, choices=TONE_CHOICES, default="friendly")
    style = models.CharField(max_length=20, choices=STYLE_CHOICES, default="conversational")
    language = models.CharField(max_length=10, choices=LANGUAGE_CHOICES, default="en")
    image = models.ImageField(upload_to="agent_images", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Agent Identity"
        verbose_name_plural = "Agent Identities"

    def __str__(self):
        return f"{self.name} — {self.user.username}"


# -----------------------
# Store Config
# -----------------------
class StoreConfig(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="store_config")
    store_name = models.CharField(max_length=255, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    whatsapp_number = models.CharField(max_length=20, blank=True, null=True)
    delivery_charge_inside = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    delivery_charge_outside = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    support_open_time = models.TimeField(default="09:00")
    support_close_time = models.TimeField(default="21:00")
    timezone = models.CharField(max_length=50, default="Asia/Dhaka")
    currency = models.CharField(max_length=10, default="BDT")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Store Config"
        verbose_name_plural = "Store Configs"

    def __str__(self):
        return f"{self.store_name or 'Unnamed Store'} — {self.user.username}"


# -----------------------
# Behavior Rules
# -----------------------
class BehaviorRules(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="behavior_rules")
    greeting_message = models.TextField(blank=True, null=True)
    out_of_hours_message = models.TextField(blank=True, null=True)
    chit_chat_enabled = models.BooleanField(default=True)
    chit_chat_style = models.CharField(max_length=20, choices=CHIT_CHAT_STYLE_CHOICES, default="moderate")
    cross_sell_enabled = models.BooleanField(default=True)
    ask_open_ended = models.BooleanField(default=True)
    knowledge_base = models.TextField(
        blank=True,
        help_text="Facts the AI should always know. One per line. e.g. 'Return policy: 7 days, no questions asked.'"
    )
    sample_questions_answers = models.TextField(
        blank=True,
        help_text="Sample Q&A pairs for AI training (format: Q: question\\nA: answer)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Behavior Rules"
        verbose_name_plural = "Behavior Rules"

    def __str__(self):
        return f"Behavior rules — {self.user.username}"