from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta


# Create your models here.
class ChatTransferProtocol(models.Model):
    PLATFORM_CHOICES = [
        ("messenger", "Messenger"),
        ("instagram", "Instagram"),
        ("whatsapp", "WhatsApp"),
        ("telegram", "Telegram"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="chat_transfer")
    
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES, default="messenger")
    access_token = models.CharField(max_length=500, blank=True, null=True)

    is_ai_enabled = models.BooleanField(default=True)
    ai_disabled_at = models.DateTimeField(null=True, blank=True)   # Time when AI was turned off
    ai_enable_delay = models.IntegerField(default=300)             # Time in seconds before re-enabling AI

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Chat Transfer Protocol"

    def __str__(self):
        return f"{self.platform} – AI Enabled: {self.is_ai_enabled}"

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