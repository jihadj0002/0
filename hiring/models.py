from django.db import models
from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone
from shortuuid.django_fields import ShortUUIDField


# -----------------------
# Candidate Application
# -----------------------
class CandidateApplication(models.Model):
    POSITION_CHOICES = [
        ("sales_staff", "Sales Staff"),
        ("sales_executive", "Sales Executive"),
        ("sales_manager", "Sales Manager"),
        ("support", "Support"),
    ]
    STATUS_CHOICES = [
        ("applied", "Applied"),
        ("shortlisted", "Shortlisted"),
        ("interview_scheduled", "Interview Scheduled"),
        ("hired", "Hired"),
        ("rejected", "Rejected"),
    ]
    SOURCE_CHOICES = [
        ("website", "Website"),
        ("referral", "Referral"),
        ("social", "Social Media"),
        ("manual", "Manual Entry"),
    ]

    uid = ShortUUIDField(
        length=10, prefix="can_", alphabet="abcdefghijklmnopqrstuvwxyz0123456789"
    )
    name = models.CharField(max_length=150)
    email = models.EmailField()
    phone = models.CharField(max_length=40, blank=True, default="")
    position = models.CharField(max_length=20, choices=POSITION_CHOICES, default="sales_staff")
    experience_years = models.PositiveIntegerField(default=0)
    skills = models.TextField(blank=True, default="", help_text="Comma-separated list of skills")
    expected_salary = models.CharField(max_length=60, blank=True, default="")
    availability = models.CharField(max_length=120, blank=True, default="")
    city = models.CharField(max_length=100, blank=True, default="")
    photo = models.ImageField(upload_to="hiring/photos/%Y/%m/", null=True, blank=True)
    cv = models.FileField(upload_to="hiring/cv/%Y/%m/", null=True, blank=True)
    cover_letter = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="applied")
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="website")
    notes = models.TextField(blank=True, default="", help_text="Internal notes")
    hired_user = models.OneToOneField(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="hired_candidate", help_text="CRM account created on hire",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["email"]),
            models.Index(fields=["phone"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_position_display()})"

    @property
    def skills_list(self):
        return [s.strip() for s in self.skills.split(",") if s.strip()]

    @property
    def status_pill(self):
        return {
            "applied": "gray",
            "shortlisted": "warm",
            "interview_scheduled": "blue",
            "hired": "won",
            "rejected": "lost",
        }.get(self.status, "gray")


# -----------------------
# Group Hiring Meeting
# -----------------------
class HiringMeeting(models.Model):
    PLATFORM_CHOICES = [
        ("zoom", "Zoom"),
        ("google_meet", "Google Meet"),
        ("offline", "Offline / In-person"),
    ]
    STATUS_CHOICES = [
        ("scheduled", "Scheduled"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    title = models.CharField(max_length=200)
    datetime = models.DateTimeField()
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES, default="zoom")
    link = models.URLField(blank=True, default="")
    location = models.CharField(max_length=200, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="scheduled")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-datetime"]

    def __str__(self):
        return f"{self.title} - {self.datetime:%Y-%m-%d %H:%M}"

    def invited_count(self):
        return self.attendees.count()


# -----------------------
# Meeting Attendee
# -----------------------
class MeetingAttendee(models.Model):
    RSVP_CHOICES = [
        ("invited", "Invited"),
        ("attended", "Attended"),
        ("no_show", "No Show"),
    ]

    meeting = models.ForeignKey(HiringMeeting, on_delete=models.CASCADE, related_name="attendees")
    candidate = models.ForeignKey(CandidateApplication, on_delete=models.CASCADE, related_name="meetings")
    rsvp = models.CharField(max_length=10, choices=RSVP_CHOICES, default="invited")
    invited_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = [("meeting", "candidate")]

    def __str__(self):
        return f"{self.meeting.title} - {self.candidate.name}"