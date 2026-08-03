from django.contrib import admin

from .models import (
    StaffProfile, PipelineStage, Company, Lead, Activity, CallLog, Meeting,
    Task, Followup, SalesScript, FAQ, Customer, Notification, CrmSetting,
    LearningTopic, LearningArticle,
)


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "role", "is_active"]
    list_filter = ["role", "is_active"]
    search_fields = ["user__username", "user__email", "user__first_name"]


@admin.register(PipelineStage)
class PipelineStageAdmin(admin.ModelAdmin):
    list_display = ["name", "order", "color", "is_won", "is_lost"]
    list_editable = ["order", "color", "is_won", "is_lost"]


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ["name", "industry", "website"]
    search_fields = ["name", "industry"]


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ["name", "phone", "email", "stage", "source", "assigned_to", "score", "converted"]
    list_filter = ["stage", "source", "converted", "created_at"]
    search_fields = ["name", "phone", "email"]


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ["lead", "type", "created_by", "timestamp"]
    list_filter = ["type", "timestamp"]
    search_fields = ["lead__name", "description"]


@admin.register(CallLog)
class CallLogAdmin(admin.ModelAdmin):
    list_display = ["lead", "staff", "outcome", "duration", "created_at"]
    list_filter = ["outcome"]


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ["lead", "datetime", "platform", "status", "staff"]
    list_filter = ["status", "platform"]


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ["title", "lead", "assigned_to", "priority", "status", "deadline"]
    list_filter = ["status", "priority"]


@admin.register(Followup)
class FollowupAdmin(admin.ModelAdmin):
    list_display = ["lead", "due", "kind", "done"]
    list_filter = ["kind", "done"]


@admin.register(SalesScript)
class SalesScriptAdmin(admin.ModelAdmin):
    list_display = ["title", "category", "active", "position"]
    list_filter = ["category", "active"]


@admin.register(FAQ)
class FAQAdmin(admin.ModelAdmin):
    list_display = ["question", "category", "active"]
    list_filter = ["category", "active"]
    search_fields = ["question", "answer"]


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ["lead", "platform_user", "package", "status", "renewal", "owner"]
    list_filter = ["status"]
    search_fields = ["lead__name"]


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["user", "message", "read", "created_at"]
    list_filter = ["read"]


@admin.register(CrmSetting)
class CrmSettingAdmin(admin.ModelAdmin):
    list_display = ["key", "value"]


@admin.register(LearningTopic)
class LearningTopicAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "order", "description"]
    list_editable = ["order"]
    prepopulated_fields = {"slug": ["name"]}
    search_fields = ["name"]


@admin.register(LearningArticle)
class LearningArticleAdmin(admin.ModelAdmin):
    list_display = ["title", "topic", "order", "active", "updated_at"]
    list_filter = ["topic", "active"]
    list_editable = ["order", "active"]
    prepopulated_fields = {"slug": ["title"]}
    search_fields = ["title", "summary"]
    autocomplete_fields = ["topic"]
