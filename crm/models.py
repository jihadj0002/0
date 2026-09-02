from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.text import slugify
from django_ckeditor_5.fields import CKEditor5Field
from shortuuid.django_fields import ShortUUIDField

# -----------------------
# Staff
# -----------------------
class StaffProfile(models.Model):
    ROLE_CHOICES = [
        ("owner", "Owner"),
        ("manager", "Sales Manager"),
        ("staff", "Sales Staff"),
        ("support", "Support"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="staff_profile")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="staff")
    phone = models.CharField(max_length=30, blank=True, default="")
    title = models.CharField(max_length=100, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-role", "user__username"]

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.get_role_display()})"


# -----------------------
# Pipeline
# -----------------------
class PipelineStage(models.Model):
    name = models.CharField(max_length=60)
    order = models.PositiveIntegerField(default=0)
    color = models.CharField(max_length=20, default="#2563eb")
    is_won = models.BooleanField(default=False)
    is_lost = models.BooleanField(default=False)
    score_value = models.PositiveSmallIntegerField(
        default=0, help_text="Score contribution when a lead reaches this stage (auto lead scoring)"
    )
    tenant = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.CASCADE, related_name="crm_stages"
    )

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.name


# -----------------------
# Company
# -----------------------
class Company(models.Model):
    uid = ShortUUIDField(length=10, prefix="cmp_", alphabet="abcdefghijklmnopqrstuvwxyz0123456789")
    name = models.CharField(max_length=150)
    industry = models.CharField(max_length=100, blank=True, default="")
    website = models.URLField(blank=True, default="")
    employees = models.CharField(max_length=30, blank=True, default="")
    address = models.TextField(blank=True, default="")
    notes = models.TextField(blank=True, default="")
    owner = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="crm_owned_companies")
    tenant = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.CASCADE, related_name="crm_tenant_companies"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Companies"
        ordering = ["name"]

    def __str__(self):
        return self.name


# -----------------------
# Lead
# -----------------------
class Lead(models.Model):
    SOURCE_CHOICES = [
        ("website", "Website"),
        ("facebook", "Facebook"),
        ("messenger", "Messenger"),
        ("whatsapp", "WhatsApp"),
        ("instagram", "Instagram"),
        ("telegram", "Telegram"),
        ("manual", "Manual"),
        ("referral", "Referral"),
        ("import", "Import"),
    ]

    uid = ShortUUIDField(length=10, prefix="ld_", alphabet="abcdefghijklmnopqrstuvwxyz0123456789")
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=40, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.SET_NULL, related_name="leads")
    website = models.URLField(blank=True, default="")
    industry = models.CharField(max_length=100, blank=True, default="")
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="manual")
    stage = models.ForeignKey(PipelineStage, null=True, blank=True, on_delete=models.SET_NULL, related_name="leads")
    score = models.PositiveIntegerField(default=0)
    score_breakdown = models.JSONField(default=dict, blank=True)
    budget = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    expected_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    assigned_to = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="crm_assigned_leads"
    )
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="crm_created_leads"
    )
    next_followup = models.DateTimeField(null=True, blank=True)
    last_contact = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    tags = models.JSONField(default=list, blank=True)
    lost_reason = models.CharField(max_length=200, blank=True, default="")
    converted = models.BooleanField(default=False)
    conversation = models.ForeignKey(
        "back.Conversation", null=True, blank=True, on_delete=models.SET_NULL, related_name="crm_leads"
    )
    tenant = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.CASCADE, related_name="crm_tenant_leads"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["stage"]),
            models.Index(fields=["assigned_to"]),
            models.Index(fields=["source"]),
            models.Index(fields=["phone"]),
        ]

    def __str__(self):
        return self.name

    def is_won(self):
        return bool(self.stage and self.stage.is_won)

    def is_lost(self):
        return bool(self.stage and self.stage.is_lost)

    @property
    def status_bucket(self):
        if self.is_won():
            return "won"
        if self.is_lost():
            return "lost"
        if self.score >= 70:
            return "hot"
        if self.score >= 40:
            return "warm"
        return "cold"


# -----------------------
# Activity Timeline
# -----------------------
class Activity(models.Model):
    TYPE_CHOICES = [
        ("note", "Note"),
        ("call", "Call"),
        ("demo", "Demo"),
        ("email", "Email"),
        ("meeting", "Meeting"),
        ("assignment", "Assignment"),
        ("status_change", "Status Change"),
        ("proposal", "Proposal"),
        ("won", "Deal Won"),
        ("lost", "Deal Lost"),
        ("created", "Lead Created"),
        ("onboarding", "Onboarding"),
        ("score", "Score Change"),
    ]

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="activities")
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, default="note")
    description = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="crm_activities")
    data = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name_plural = "Activities"
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.get_type_display()} - {self.lead.name}"


# -----------------------
# Call Logs
# -----------------------
class CallLog(models.Model):
    OUTCOME_CHOICES = [
        ("no_answer", "No Answer"),
        ("busy", "Busy"),
        ("interested", "Interested"),
        ("not_interested", "Not Interested"),
        ("wrong_number", "Wrong Number"),
        ("call_later", "Call Later"),
        ("meeting_scheduled", "Meeting Scheduled"),
        ("demo_scheduled", "Demo Scheduled"),
    ]

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="calls")
    staff = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="crm_calls")
    duration = models.PositiveIntegerField(default=0, help_text="Seconds")
    outcome = models.CharField(max_length=30, choices=OUTCOME_CHOICES, default="no_answer")
    summary = models.TextField(blank=True, default="")
    next_followup = models.DateTimeField(null=True, blank=True)
    recording = models.URLField(blank=True, default="")
    tags = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.lead.name} - {self.get_outcome_display()}"


# -----------------------
# Meetings / Demos
# -----------------------
class Meeting(models.Model):
    PLATFORM_CHOICES = [
        ("zoom", "Zoom"),
        ("google_meet", "Google Meet"),
        ("offline", "Offline / In-person"),
    ]
    STATUS_CHOICES = [
        ("scheduled", "Scheduled"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
        ("no_show", "No Show"),
    ]

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="meetings")
    staff = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="crm_meetings")
    datetime = models.DateTimeField()
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES, default="zoom")
    link = models.URLField(blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="scheduled")
    notes = models.TextField(blank=True, default="")
    demo_sent = models.BooleanField(default=False)
    joined = models.BooleanField(default=False)
    completed = models.BooleanField(default=False)
    questions_answered = models.BooleanField(default=False)
    next_action = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-datetime"]

    def __str__(self):
        return f"{self.lead.name} - {self.datetime:%Y-%m-%d %H:%M}"


# -----------------------
# Tasks
# -----------------------
class Task(models.Model):
    PRIORITY_CHOICES = [
        ("low", "Low"),
        ("medium", "Medium"),
        ("high", "High"),
    ]
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("doing", "In Progress"),
        ("done", "Done"),
        ("cancelled", "Cancelled"),
    ]

    title = models.CharField(max_length=200)
    lead = models.ForeignKey(Lead, null=True, blank=True, on_delete=models.CASCADE, related_name="tasks")
    assigned_to = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="crm_tasks")
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default="medium")
    deadline = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="crm_created_tasks")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["status", "-deadline"]

    def __str__(self):
        return self.title


# -----------------------
# Followups
# -----------------------
class Followup(models.Model):
    KIND_CHOICES = [
        ("call", "Call"),
        ("whatsapp", "WhatsApp"),
        ("email", "Email"),
        ("visit", "Visit"),
    ]

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="followups")
    due = models.DateTimeField()
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default="call")
    note = models.CharField(max_length=300, blank=True, default="")
    done = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="crm_created_followups")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["due"]

    def __str__(self):
        return f"{self.lead.name} - {self.due:%Y-%m-%d %H:%M}"


# -----------------------
# Sales Scripts
# -----------------------
class SalesScript(models.Model):
    CATEGORY_CHOICES = [
        ("cold_call", "Cold Call"),
        ("followup", "Follow-up"),
        ("objection", "Objection Handling"),
        ("demo", "Demo"),
        ("closing", "Closing"),
        ("renewal", "Renewal"),
        ("upsell", "Upsell"),
        ("lost_customer", "Winning Back Lost Customer"),
        ("texts", "Texts"),
    ]

    title = models.CharField(max_length=200)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default="cold_call")
    content = models.TextField(blank=True, default="")
    position = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    tenant = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.CASCADE, related_name="crm_scripts"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category", "position", "title"]

    def __str__(self):
        return self.title


# -----------------------
# FAQ
# -----------------------
class FAQ(models.Model):
    question = models.CharField(max_length=300)
    answer = models.TextField(blank=True, default="")
    category = models.CharField(max_length=100, blank=True, default="")
    position = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    tenant = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.CASCADE, related_name="crm_faqs"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.question


# -----------------------
# Customer (won leads)
# -----------------------
class Customer(models.Model):
    STATUS_CHOICES = [
        ("active", "Active"),
        ("churned", "Churned"),
    ]

    lead = models.OneToOneField(Lead, on_delete=models.CASCADE, related_name="customer")
    platform_user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="crm_customers"
    )
    package = models.CharField(max_length=100, blank=True, default="")
    monthly_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    renewal = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="active")
    owner = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="crm_owned_customers"
    )
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.lead.name} ({self.package or 'No package'})"


# -----------------------
# Notifications
# -----------------------
class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="crm_notifications")
    message = models.CharField(max_length=300)
    url = models.CharField(max_length=300, blank=True, default="")
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.message


# -----------------------
# CRM Settings (key/value flags)
# -----------------------
class CrmSetting(models.Model):
    key = models.CharField(max_length=100, unique=True)
    value = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return self.key


# -----------------------
# Cold Email Templates & Batching
# -----------------------
class EmailTemplate(models.Model):
    CATEGORY_CHOICES = [
        ("cold", "Cold Outreach"),
        ("followup", "Follow-up"),
        ("nurture", "Nurture"),
        ("proposal", "Proposal"),
        ("announcement", "Announcement"),
    ]

    uid = ShortUUIDField(length=12, prefix="etp_", alphabet="abcdefghijklmnopqrstuvwxyz0123456789")
    name = models.CharField(max_length=120)
    subject = models.CharField(max_length=200, help_text="Supports tokens: {{lead.name}}, {{lead.email}}, etc.")
    body_html = CKEditor5Field(config_name="blog", help_text="Rich text editor. Tokens will be replaced at send time.")
    body_text = models.TextField(blank=True, default="", help_text="Plain-text fallback (auto-generated from HTML if empty)")
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default="cold")
    is_active = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False, help_text="System templates cannot be deleted")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="crm_email_templates_created")
    tenant = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.CASCADE, related_name="crm_tenant_email_templates",
        help_text="Null = internal MatrixAI template (available to all). Set for per-tenant templates."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category", "name"]
        indexes = [
            models.Index(fields=["tenant", "is_active"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_category_display()})"


class EmailBatch(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("queued", "Queued"),
        ("sending", "Sending"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
    ]

    uid = ShortUUIDField(length=12, prefix="emb_", alphabet="abcdefghijklmnopqrstuvwxyz0123456789")
    template = models.ForeignKey(EmailTemplate, on_delete=models.PROTECT, related_name="batches")
    subject_rendered = models.CharField(max_length=200, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    total_recipients = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    scheduled_at = models.DateTimeField(null=True, blank=True, help_text="Null = send immediately")
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="crm_email_batches_created")
    tenant = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.CASCADE, related_name="crm_tenant_email_batches"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "scheduled_at"]),
        ]

    def __str__(self):
        return f"Batch {self.uid} — {self.template.name} ({self.status})"


class EmailLog(models.Model):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("sent", "Sent"),
        ("failed", "Failed"),
        ("bounced", "Bounced"),
        ("opened", "Opened"),
        ("clicked", "Clicked"),
    ]

    uid = ShortUUIDField(length=12, prefix="eml_", alphabet="abcdefghijklmnopqrstuvwxyz0123456789")
    batch = models.ForeignKey(EmailBatch, null=True, blank=True, on_delete=models.SET_NULL, related_name="logs")
    template = models.ForeignKey(EmailTemplate, on_delete=models.PROTECT, related_name="logs")
    lead = models.ForeignKey("Lead", on_delete=models.PROTECT, related_name="email_logs")
    recipient_email = models.EmailField(help_text="Frozen at send time")
    subject = models.CharField(max_length=200)
    body_html = models.TextField(help_text="Rendered body for debugging/audit")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    provider_message_id = models.CharField(max_length=100, blank=True, default="")
    error = models.TextField(blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    clicked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["batch", "status"]),
            models.Index(fields=["lead", "status"]),
        ]

    def __str__(self):
        return f"EmailLog {self.uid} — {self.lead.name} ({self.status})"


# -----------------------
# Email Account (SMTP connection)
# -----------------------
class EmailAccount(models.Model):
    PROVIDER_CHOICES = [
        ("smtp", "SMTP"),
        ("google", "Google Workspace"),
        ("microsoft", "Microsoft 365"),
    ]
    REPUTATION_CHOICES = [
        ("good", "Good"),
        ("warm", "Warm"),
        ("poor", "Poor"),
    ]

    uid = ShortUUIDField(length=12, prefix="ema_", alphabet="abcdefghijklmnopqrstuvwxyz0123456789")
    email = models.EmailField()
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default="smtp")
    smtp_host = models.CharField(max_length=200, blank=True, default="")
    smtp_port = models.PositiveIntegerField(default=587)
    smtp_user = models.CharField(max_length=200, blank=True, default="")
    smtp_password = models.CharField(max_length=300, blank=True, default="", help_text="Encrypted in production")
    use_tls = models.BooleanField(default=True)
    daily_limit = models.PositiveIntegerField(default=50)
    sent_today = models.PositiveIntegerField(default=0)
    last_sent_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    reputation = models.CharField(max_length=10, choices=REPUTATION_CHOICES, default="good")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="crm_email_accounts")
    tenant = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE, related_name="crm_tenant_email_accounts")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_active", "email"]

    def __str__(self):
        return f"{self.email} ({self.get_provider_display()})"


# -----------------------
# Campaign (Outreach)
# -----------------------
class Campaign(models.Model):
    CAMPAIGN_TYPE_CHOICES = [
        ("one_time", "One-time Campaign"),
        ("sequence", "Email Sequence"),
    ]
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("scheduled", "Scheduled"),
        ("sending", "Sending"),
        ("paused", "Paused"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("archived", "Archived"),
    ]
    TONE_CHOICES = [
        ("professional", "Professional"),
        ("friendly", "Friendly"),
        ("direct", "Direct"),
        ("casual", "Casual"),
    ]
    AUDIENCE_TYPE_CHOICES = [
        ("segment", "Existing Segment"),
        ("filter", "Filter Leads"),
        ("manual", "Select Manually"),
    ]

    uid = ShortUUIDField(length=12, prefix="cmp_", alphabet="abcdefghijklmnopqrstuvwxyz0123456789")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="", help_text="Internal reference")
    campaign_type = models.CharField(max_length=20, choices=CAMPAIGN_TYPE_CHOICES, default="one_time")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    template = models.ForeignKey(EmailTemplate, null=True, blank=True, on_delete=models.SET_NULL, related_name="campaigns")
    sending_account = models.ForeignKey(EmailAccount, null=True, blank=True, on_delete=models.SET_NULL, related_name="campaigns")
    reply_to = models.EmailField(blank=True, default="")
    daily_limit = models.PositiveIntegerField(default=50)
    min_interval = models.PositiveIntegerField(default=2, help_text="Minimum minutes between sends")
    max_interval = models.PositiveIntegerField(default=5, help_text="Maximum minutes between sends")
    scheduled_at = models.DateTimeField(null=True, blank=True, help_text="Null = send immediately")
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    total_recipients = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    bounced_count = models.PositiveIntegerField(default=0)
    opened_count = models.PositiveIntegerField(default=0)
    replied_count = models.PositiveIntegerField(default=0)
    ai_personalization = models.BooleanField(default=False)
    ai_instruction = models.TextField(blank=True, default="")
    ai_tone = models.CharField(max_length=20, choices=TONE_CHOICES, default="friendly")
    personalization_fields = models.JSONField(default=list, blank=True)
    options = models.JSONField(default=dict, blank=True, help_text="stop_on_reply, skip_bounced, skip_contacted, etc.")
    audience_type = models.CharField(max_length=20, choices=AUDIENCE_TYPE_CHOICES, default="manual")
    audience_data = models.JSONField(default=dict, blank=True, help_text="Filter criteria or selected lead IDs")
    rq_job_id = models.CharField(max_length=200, blank=True, default="")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="crm_campaigns_created")
    tenant = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE, related_name="crm_tenant_campaigns")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "scheduled_at"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"


# -----------------------
# Campaign Lead (per-lead status in campaign)
# -----------------------
class CampaignLead(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("queued", "Queued"),
        ("sent", "Sent"),
        ("failed", "Failed"),
        ("bounced", "Bounced"),
        ("opened", "Opened"),
        ("replied", "Replied"),
    ]

    uid = ShortUUIDField(length=12, prefix="cpl_", alphabet="abcdefghijklmnopqrstuvwxyz0123456789")
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="leads")
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="campaign_leads")
    email_snapshot = models.EmailField(blank=True, default="", help_text="Frozen at send time")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    subject_rendered = models.CharField(max_length=200, blank=True, default="")
    error = models.TextField(blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    replied_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["campaign", "status"]),
            models.Index(fields=["lead", "status"]),
        ]
        verbose_name = "Campaign Lead"
        verbose_name_plural = "Campaign Leads"

def __str__(self):
        return f"{self.lead.name} — {self.get_status_display()}"


# -----------------------
# CRM Settings (key/value flags)
# -----------------------
# Learn (sales training hub)
# -----------------------
class LearningTopic(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    description = models.CharField(max_length=300, blank=True, default="")
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class LearningArticle(models.Model):
    topic = models.ForeignKey(
        LearningTopic, on_delete=models.CASCADE, related_name="articles"
    )
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    summary = models.TextField(blank=True, default="")
    content = CKEditor5Field(config_name="blog")
    order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["topic__order", "order", "title"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.title)
            slug = base_slug
            counter = 1
            while LearningArticle.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)
