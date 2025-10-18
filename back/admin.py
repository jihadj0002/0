from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, Product, Conversation, Sale, Setting

# -----------------------
# Custom User Admin
# -----------------------
class UserAdmin(BaseUserAdmin):
    model = User
    list_display = ("email", "username", "plan", "is_staff", "is_active", "created_at")
    list_filter = ("plan", "is_staff", "is_active")
    search_fields = ("email", "username")
    ordering = ("email",)
    fieldsets = (
        (None, {"fields": ("email", "username", "password")}),
        ("Permissions", {"fields": ("is_staff", "is_active", "is_superuser", "groups", "user_permissions")}),
        ("Plan Info", {"fields": ("plan",)}),
        ("Important Dates", {"fields": ("last_login", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "username", "password1", "password2", "is_staff", "is_active", "plan")}
        ),
    )

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
admin.site.register(User, UserAdmin)
admin.site.register(Product, ProductAdmin)
admin.site.register(Conversation, ConversationAdmin)
admin.site.register(Sale, SaleAdmin)
admin.site.register(Setting, SettingAdmin)
