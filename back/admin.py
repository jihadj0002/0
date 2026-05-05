import json

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.shortcuts import render
from django.urls import path

from .models import PackageImages, UserProfile, Product, Conversation, Message, Sale, Setting, ProductImages, Integration, OrderItem, Package, PackageItem
# -----------------------
# Custom User Admin
# -----------------------
# Define an inline admin descriptor for UserProfile model
# which acts a bit like a "subform" of the User admin page.


class PackageImagesAdmin(admin.TabularInline):
    model= PackageImages

class PackageAdmin(admin.ModelAdmin):
    inlines = [PackageImagesAdmin]
    list_display = ("name", "price", "is_active", "created_at")
    search_fields = ("name",)
    list_filter = ("is_active",)


class PackageInline(admin.TabularInline):
    model = Package
    extra = 0

class PackageItemInline(admin.TabularInline):
    model = PackageItem
    extra = 0

    
class ProductImagesAdmin(admin.TabularInline):
    model= ProductImages

class PackageItemAdmin(admin.ModelAdmin):
    list_display = (
        "package",
        "product",
        "add_price",
        "remove_price",
        "is_default",
        "is_optional",
    )
    search_fields = ("package__name", "product__name")
    list_filter = ("package",)


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
    inlines = [ProductImagesAdmin]
    list_display = ("name", "user","product_image", "price", "stock_quantity", "upsell_enabled", "last_synced")
    list_filter = ("upsell_enabled",)
    search_fields = ("name", "user__email")
    ordering = ("-last_synced",)

# -----------------------
# Conversation Admin  (also hosts the pipeline-test custom view)
# -----------------------
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("platform", "customer_id", "user", "timestamp", "is_ai_generated")
    list_filter = ("platform", "is_ai_generated")
    search_fields = ("customer_id", "user__email", "message_text")
    ordering = ("-timestamp",)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "pipeline-test/",
                self.admin_site.admin_view(self.pipeline_test_view),
                name="back_pipeline_test",
            ),
        ]
        return custom + urls

    def pipeline_test_view(self, request):
        from api.ai.context import build_system_prompt, get_conversation_history
        from api.ai.providers import call_llm
        from api.ai.tools import TOOL_DEFINITIONS, execute_tool

        users = User.objects.filter(is_active=True).order_by("username")

        selected_user = None
        conversations = []
        selected_conv = None
        result = None
        message_text = ""

        user_id = request.POST.get("user_id") or request.GET.get("user_id")
        conv_id = request.POST.get("conv_id")
        message_text = request.POST.get("message", "")
        run = "run" in request.POST

        if user_id:
            try:
                selected_user = User.objects.get(pk=user_id)
                conversations = list(
                    Conversation.objects.filter(user=selected_user).order_by("-updated_at")[:50]
                )
            except User.DoesNotExist:
                pass

        if conv_id and run and message_text.strip():
            try:
                selected_conv = Conversation.objects.select_related("user").get(pk=conv_id)
            except Conversation.DoesNotExist:
                selected_conv = None

            if selected_conv:
                user = selected_conv.user
                integration = user.integrations.filter(platform=selected_conv.platform).first()
                model = (integration.ai_model or None) if integration else None

                system_prompt = build_system_prompt(user, selected_conv)
                history = get_conversation_history(selected_conv, limit=20)
                if not history or history[-1].get("content") != message_text or history[-1].get("role") != "user":
                    history.append({"role": "user", "content": message_text})

                messages_list = [{"role": "system", "content": system_prompt}] + history

                tool_calls_log = []
                final_text = None
                pending_images = []
                total_input = 0
                total_output = 0
                iterations = 0
                error = None

                try:
                    for iteration in range(5):
                        iterations += 1
                        llm_msg, usage = call_llm(
                            messages=messages_list,
                            tools=TOOL_DEFINITIONS,
                            model=model,
                        )
                        total_input += usage.get("input_tokens", 0)
                        total_output += usage.get("output_tokens", 0)

                        if not llm_msg.tool_calls:
                            final_text = llm_msg.content or ""
                            break

                        messages_list.append(llm_msg)

                        for tc in llm_msg.tool_calls:
                            fn_name = tc.function.name
                            try:
                                fn_args = json.loads(tc.function.arguments or "{}")
                            except (json.JSONDecodeError, TypeError):
                                fn_args = {}

                            tc_result = execute_tool(fn_name, fn_args, user, selected_conv)

                            if fn_name == "send_images" and isinstance(tc_result, dict):
                                pending_images.extend(tc_result.get("images", []))

                            tool_calls_log.append({
                                "name": fn_name,
                                "args": json.dumps(fn_args, ensure_ascii=False)[:200],
                                "result": json.dumps(tc_result, ensure_ascii=False, default=str)[:300],
                            })

                            messages_list.append({
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": json.dumps(tc_result),
                            })

                            if fn_name == "transfer_chat":
                                final_text = "I'm connecting you with a human agent now."
                                break

                        if final_text:
                            break

                except Exception as exc:
                    error = str(exc)

                seen = set()
                unique_images = [img for img in pending_images if img not in seen and not seen.add(img)][:5]

                result = {
                    "user": user.username,
                    "platform": selected_conv.platform,
                    "customer": selected_conv.customer_name or selected_conv.customer_id,
                    "model": model or "default (gpt-4o-mini)",
                    "iterations": iterations,
                    "input_tokens": total_input,
                    "output_tokens": total_output,
                    "tool_calls": tool_calls_log,
                    "response": final_text or "",
                    "images": unique_images,
                    "error": error,
                }

        context = {
            **self.admin_site.each_context(request),
            "title": "AI Pipeline Test",
            "users": users,
            "selected_user": selected_user,
            "conversations": conversations,
            "selected_conv": selected_conv,
            "message": message_text,
            "result": result,
            "opts": Conversation._meta,
        }
        return render(request, "admin/back/pipeline_test.html", context)


class MessageAdmin(admin.ModelAdmin):
    list_display = ("conversation", "sender", "text", "timestamp")
    list_filter = ("sender",)
    search_fields = ("sender", "conversation", "text")
    ordering = ("-timestamp",)

# -----------------------
# Sale Admin
# -----------------------

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("product_name", "price", "quantity")


class SaleAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "get_products",
        "customer_id",
        "amount",
        "status",
        "created_at",
    )
    inlines = [OrderItemInline]
    list_filter = ("status", "source")
    ordering = ("-created_at",)

    search_fields = (
        "customer_id",
        "user__email",
        "items__product_name",
        "items__external_product_id",
    )

    def get_products(self, obj):
        return ", ".join(
            item.product_name for item in obj.items.all()
        )

    get_products.short_description = "Products"


# -----------------------
# IntegrationAdmin Admin
# -----------------------
class IntegrationAdmin(admin.ModelAdmin):
    list_display = ("platform", "user", "webhook_url", "access_token", "created_at", "is_enabled")
    list_filter = ("platform",)
    search_fields = ("user",)
    ordering = ("-created_at",)

# -----------------------
# SettingAdmin Admin
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
admin.site.register(Integration, IntegrationAdmin)
admin.site.register(Message, MessageAdmin)
admin.site.register(Package, PackageAdmin)
admin.site.register(PackageItem, PackageItemAdmin)
# admin.site.register(ProductImages, ProductImagesAdmin)
