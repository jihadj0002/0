from django.db import models
from django.contrib.auth.models import User


RAG_SOURCE_CHOICES = [
    ("sample_qa", "Sample Q&A"),
    ("knowledge_base", "Knowledge Base"),
]


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
    custom_instructions = models.TextField(blank=True, null=True)
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


# -----------------------
# RAG Chunks (vector search)
# -----------------------
class RAGChunk(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="rag_chunks")
    content = models.TextField(help_text="The chunk text (e.g. a single Q&A pair)")
    embedding = models.JSONField(null=True, blank=True, help_text="Vector embedding as a list of floats")
    source = models.CharField(max_length=50, choices=RAG_SOURCE_CHOICES, default="sample_qa")
    chunk_index = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "RAG Chunk"
        verbose_name_plural = "RAG Chunks"
        indexes = [
            models.Index(fields=["user", "source", "is_active"]),
        ]

    def __str__(self):
        return f"{self.get_source_display()} #{self.chunk_index} — {self.user.username}"


# -----------------------
# MemoryEntry (P0-10) — Long-term user memory
# -----------------------
class MemoryEntry(models.Model):
    MEMORY_TYPES = [
        ("preference", "User Preference"),
        ("fact", "Extracted Fact"),
        ("behavior", "Behavior Pattern"),
        ("context", "Business Context"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memories")
    conversation = models.ForeignKey("back.Conversation", on_delete=models.SET_NULL,
                                      null=True, blank=True)
    memory_type = models.CharField(max_length=20, choices=MEMORY_TYPES, default="fact")
    key = models.CharField(max_length=100, db_index=True)
    value = models.JSONField(default=dict, blank=True)
    confidence = models.FloatField(default=1.0)
    source = models.CharField(max_length=50, blank=True, default="")
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Memory Entry"
        verbose_name_plural = "Memory Entries"
        indexes = [
            models.Index(fields=["user", "memory_type", "is_active"]),
            models.Index(fields=["user", "key"]),
        ]

    def __str__(self):
        return f"{self.memory_type}:{self.key} — {self.user.username}"


# -----------------------
# SessionContext (P0-9) — Per-conversation workflow state
# -----------------------
class SessionContext(models.Model):
    WORKFLOW_STATES = [
        ("idle", "Idle — no active workflow"),
        ("browsing", "Customer is browsing products"),
        ("product_selected", "A specific product was selected"),
        ("awaiting_product_selection", "AI asks which product to order"),
        ("awaiting_variation", "AI asks which size/variant"),
        ("awaiting_details", "AI is collecting order details"),
        ("awaiting_confirmation", "AI is waiting for order confirmation"),
        ("checkout", "Checkout in progress"),
        ("payment", "Payment flow active"),
        ("completed", "Order/Workflow completed"),
        ("escalated", "Handed off to human"),
    ]

    conversation = models.OneToOneField("back.Conversation", on_delete=models.CASCADE,
                                         related_name="session")
    state = models.CharField(max_length=30, choices=WORKFLOW_STATES, default="idle")
    current_workflow = models.CharField(max_length=100, blank=True, default="")
    workflow_step = models.IntegerField(default=0)
    collected_data = models.JSONField(default=dict, blank=True)
    pending_confirmation = models.JSONField(null=True, blank=True)
    verified = models.BooleanField(default=False)
    verification_method = models.CharField(max_length=50, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Session Context"
        verbose_name_plural = "Session Contexts"

    def __str__(self):
        return f"{self.state} — conv #{self.conversation_id}"


# -----------------------
# ProactiveRule (P2) — User-defined monitoring rules
# -----------------------
class ProactiveRule(models.Model):
    EVENT_TYPES = [
        ("sync_failure", "Sync Failure"),
        ("low_stock", "Low Stock"),
        ("token_expiry", "Token Expiry"),
        ("subscription_expiring", "Subscription Expiring"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="proactive_rules")
    event_type = models.CharField(max_length=50, choices=EVENT_TYPES)
    is_enabled = models.BooleanField(default=True)
    notify_channel = models.CharField(max_length=20, choices=[
        ("whatsapp", "WhatsApp"),
        ("messenger", "Messenger"),
        ("email", "Email"),
    ], default="whatsapp")
    threshold = models.JSONField(default=dict, blank=True, help_text="Rule-specific thresholds")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Proactive Rule"
        verbose_name_plural = "Proactive Rules"

    def __str__(self):
        return f"{self.get_event_type_display()} — {self.user.username}"