from django.contrib import admin
from .models import AgentIdentity, RAGChunk, StoreConfig, BehaviorRules, ProactiveRule


class AgentIdentityAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "role", "tone", "language", "updated_at")
    search_fields = ("name", "user__username", "user__email", "role")
    list_filter = ("tone", "language", "style")
    ordering = ("-updated_at",)


class StoreConfigAdmin(admin.ModelAdmin):
    list_display = ("store_name", "user", "whatsapp_number", "delivery_charge_inside", "delivery_charge_outside", "timezone")
    search_fields = ("store_name", "user__username", "user__email")
    ordering = ("store_name",)


class BehaviorRulesAdmin(admin.ModelAdmin):
    list_display = ("user", "chit_chat_enabled", "chit_chat_style", "cross_sell_enabled", "ask_open_ended", "updated_at")
    list_filter = ("chit_chat_enabled", "chit_chat_style", "cross_sell_enabled")
    search_fields = ("user__username", "user__email")
    ordering = ("-updated_at",)

class RAGChunkAdmin(admin.ModelAdmin):
    list_display = ("user", "source", "created_at", "is_active")
    search_fields = ("source",)
    ordering = ("-created_at",)

class ProactiveRuleAdmin(admin.ModelAdmin):
    list_display = ("user", "event_type", "is_enabled", "notify_channel", "updated_at")
    list_filter = ("event_type", "is_enabled", "notify_channel")
    search_fields = ("user__username", "user__email")

admin.site.register(RAGChunk, RAGChunkAdmin)
admin.site.register(AgentIdentity, AgentIdentityAdmin)
admin.site.register(StoreConfig, StoreConfigAdmin)
admin.site.register(BehaviorRules, BehaviorRulesAdmin)
admin.site.register(ProactiveRule, ProactiveRuleAdmin)
