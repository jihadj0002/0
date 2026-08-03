from django.contrib import admin

from .models import CandidateApplication, HiringMeeting, MeetingAttendee


@admin.register(CandidateApplication)
class CandidateApplicationAdmin(admin.ModelAdmin):
    list_display = ["name", "position", "phone", "status", "source", "created_at"]
    list_filter = ["status", "position", "source"]
    search_fields = ["name", "phone", "email", "skills"]


@admin.register(HiringMeeting)
class HiringMeetingAdmin(admin.ModelAdmin):
    list_display = ["title", "datetime", "platform", "status", "invited_count"]
    list_filter = ["status", "platform"]


@admin.register(MeetingAttendee)
class MeetingAttendeeAdmin(admin.ModelAdmin):
    list_display = ["meeting", "candidate", "rsvp", "invited_at"]
    list_filter = ["rsvp"]