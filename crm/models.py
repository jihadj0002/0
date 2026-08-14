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
