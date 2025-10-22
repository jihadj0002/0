from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import UserProfile, Product, Conversation, Sale, Setting

# -----------------------
# Custom User Admin
# -----------------------
# Define an inline admin descriptor for UserProfile model
# which acts a bit like a "subform" of the User admin page
class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = "Profile"
    fk_name = "user"
    extra = 0


# Extend the built-in UserAdmin to include profile info
class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)

    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "is_staff",
        "get_plan",
        "get_uuid",
    )
    list_select_related = ("profile",)
    search_fields = ("username", "email")

    # Custom column methods to show profile info
    def get_plan(self, instance):
        return instance.profile.plan if hasattr(instance, "profile") else "-"
    get_plan.short_description = "Plan"

    def get_uuid(self, instance):
        return instance.profile.uuid if hasattr(instance, "profile") else "-"
    get_uuid.short_description = "UUID"

    def get_inline_instances(self, request, obj=None):
        if not obj:
            return []
        return super().get_inline_instances(request, obj)


# -----------------------
# Product Admin
# -----------------------
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "price", "stock_quantity", "upsell_enabled", "last_synced")
    list_filter = ("upsell_enabled",)
    search_fields = ("name", "user__email")
    ordering = ("-last_synced",)

# -----------------------
# Conversation Admin
# -----------------------
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("platform", "customer_id", "user", "timestamp", "is_ai_generated")
    list_filter = ("platform", "is_ai_generated")
    search_fields = ("customer_id", "user__email", "message_text")
    ordering = ("-timestamp",)

# -----------------------
# Sale Admin
# -----------------------
class SaleAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "product", "customer_id", "amount", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("customer_id", "user__email", "product__name")
    ordering = ("-created_at",)

# -----------------------
# Setting Admin
# -----------------------
class SettingAdmin(admin.ModelAdmin):
    list_display = ("platform", "user", "webhook_url", "created_at", "updated_at")
    list_filter = ("platform",)
    search_fields = ("user__email",)
    ordering = ("-created_at",)

# -----------------------
# Register all models
# -----------------------
admin.site.unregister(User)
admin.site.register(User, UserAdmin)
admin.site.register(Product, ProductAdmin)
admin.site.register(Conversation, ConversationAdmin)
admin.site.register(Sale, SaleAdmin)
admin.site.register(Setting, SettingAdmin)
