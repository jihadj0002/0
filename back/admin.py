import json

from django.contrib import admin, messages as admin_messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.shortcuts import render
from django.urls import path

from .models import (
    Conversation, Integration, Message, OrderItem, Package, PackageImages,
    PackageItem, Product, ProductImages, ProductSource, Sale, Setting,
    SupportTicket, ToolCallLog, UserProfile, UsageLog,
)


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------

class PackageImagesAdmin(admin.TabularInline):
    model = PackageImages

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
    model = ProductImages

class PackageItemAdmin(admin.ModelAdmin):
    list_display = ("package", "product", "add_price", "remove_price", "is_default", "is_optional")
    search_fields = ("package__name", "product__name")
    list_filter = ("package",)

class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = "Profile"
    fk_name = "user"
    extra = 0


# ---------------------------------------------------------------------------
# Custom UserAdmin
# ---------------------------------------------------------------------------

class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)
    list_display = ("username", "email", "first_name", "last_name", "is_staff", "get_plan", "get_uuid")
    list_select_related = ("profile",)
    search_fields = ("username", "email")

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


# ---------------------------------------------------------------------------
# ProductAdmin
# ---------------------------------------------------------------------------

class ProductAdmin(admin.ModelAdmin):
    inlines = [ProductImagesAdmin]
    list_display = ("name", "user", "product_image", "price", "stock_quantity", "upsell_enabled", "last_synced")
    list_filter = ("upsell_enabled",)
    search_fields = ("name", "user__email")
    ordering = ("-last_synced",)


# ---------------------------------------------------------------------------
# ConversationAdmin — hosts AI Audit & Debug + Tools Inspector custom views
# ---------------------------------------------------------------------------

class ConversationAdmin(admin.ModelAdmin):
    list_display = ("platform", "customer_id", "user", "timestamp", "is_ai_generated")
    list_filter = ("platform", "is_ai_generated")
    search_fields = ("customer_id", "user__email", "message_text")
    ordering = ("-timestamp",)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "ai-debug/",
                self.admin_site.admin_view(self.ai_debug_view),
                name="back_ai_debug",
            ),
            path(
                "tools/",
                self.admin_site.admin_view(self.tools_inspector_view),
                name="back_tools_inspector",
            ),
        ]
        return custom + urls

    # ── Tools Inspector ────────────────────────────────────────────────────

    def tools_inspector_view(self, request):
        from api.ai.tools import TOOL_DEFINITIONS

        ICONS = {
            "search_products": "🔍",
            "get_product_details": "📦",
            "send_images": "🖼",
            "create_order": "🛒",
            "get_order_status": "📋",
            "update_customer": "👤",
            "create_ticket": "🎫",
        }

        # Pre-process into a display-friendly list so the template never calls
        # `.items` on a dict — Django dict lookup finds properties["items"] before
        # calling the .items() method, which breaks the create_order tool.
        tools_display = []
        for tool in TOOL_DEFINITIONS:
            fn = tool["function"]
            props = fn.get("parameters", {}).get("properties", {})
            required = fn.get("parameters", {}).get("required", [])
            params = [
                {
                    "name": pname,
                    "type": pdata.get("type", "any"),
                    "description": pdata.get("description", ""),
                    "required": pname in required,
                    "default": pdata.get("default"),
                    "enum": pdata.get("enum"),
                }
                for pname, pdata in props.items()
            ]
            tools_display.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "icon": ICONS.get(fn["name"], "⚙️"),
                "params": params,
            })

        context = {
            **self.admin_site.each_context(request),
            "title": "Tools Inspector",
            "opts": Conversation._meta,
            "tools_display": tools_display,
            "tool_json": json.dumps(TOOL_DEFINITIONS, indent=2),
        }
        return render(request, "admin/back/tools_inspector.html", context)

    # ── AI Debug & Audit ──────────────────────────────────────────────────

    def ai_debug_view(self, request):
        from api.ai.context import build_system_prompt, get_conversation_history
        from api.ai.providers import call_llm
        from api.ai.tools import TOOL_DEFINITIONS, execute_tool
        from billing.models import CreditTransaction, ModelPricing, UsageSummary, UserBalance
        from collections import defaultdict

        users = User.objects.filter(is_active=True).order_by("username")

        selected_user = None
        selected_conv = None
        conversations = []
        account_info = None
        system_prompt_text = None
        prompt_token_estimate = 0
        conv_history = []
        pipeline_result = None
        message_text = ""
        active_tab = request.GET.get("tab", "prompt")

        user_id = request.POST.get("user_id") or request.GET.get("user_id")
        conv_id = request.POST.get("conv_id") or request.GET.get("conv_id")
        message_text = request.POST.get("message", "")
        tool_filter = request.POST.get("tool_name") or request.GET.get("tool_name") or ""
        reply_filter = request.POST.get("reply_id") or request.GET.get("reply_id") or ""
        error_only = (request.POST.get("error_only") or request.GET.get("error_only")) == "1"

        # ── Load selected user ────────────────────────────────────────────
        if user_id:
            try:
                selected_user = User.objects.get(pk=user_id)
                conversations = list(
                    Conversation.objects.filter(user=selected_user).order_by("-updated_at")[:60]
                )

                # Account info bundle
                try:
                    balance = UserBalance.objects.select_related("plan").get(user=selected_user)
                except UserBalance.DoesNotExist:
                    balance = None

                integrations = list(selected_user.integrations.all().order_by("platform"))
                usage_days = list(UsageSummary.objects.filter(user=selected_user).order_by("-date")[:7])
                recent_txns = list(
                    CreditTransaction.objects.filter(user=selected_user).order_by("-timestamp")[:15]
                )
                recent_logs = list(
                    UsageLog.objects.filter(user=selected_user).order_by("-timestamp")[:20]
                )

                account_info = {
                    "balance": balance,
                    "integrations": integrations,
                    "usage_days": usage_days,
                    "recent_txns": recent_txns,
                    "recent_logs": recent_logs,
                }
            except User.DoesNotExist:
                pass

        # ── Load selected conversation ────────────────────────────────────
        tool_call_logs = []
        pipeline_runs = []
        if conv_id and selected_user:
            try:
                selected_conv = Conversation.objects.get(pk=conv_id, user=selected_user)
                conv_history = list(
                    Message.objects.filter(conversation=selected_conv).order_by("timestamp")
                )
                tool_qs = ToolCallLog.objects.filter(conversation=selected_conv)
                if tool_filter:
                    tool_qs = tool_qs.filter(tool_name=tool_filter)
                if reply_filter:
                    tool_qs = tool_qs.filter(reply_id__icontains=reply_filter)
                if error_only:
                    tool_qs = tool_qs.filter(result_summary__icontains="error")

                tool_call_logs = list(
                    tool_qs.order_by("reply_id", "iteration", "timestamp")[:300]
                )

                reply_ids = sorted({t.reply_id for t in tool_call_logs if t.reply_id})
                usage_by_reply = defaultdict(lambda: {"input": 0, "output": 0, "models": set()})
                if reply_ids:
                    for ul in UsageLog.objects.filter(user=selected_user, reply_id__in=reply_ids):
                        usage_by_reply[ul.reply_id]["input"] += ul.input_tokens or 0
                        usage_by_reply[ul.reply_id]["output"] += ul.output_tokens or 0
                        if ul.model:
                            usage_by_reply[ul.reply_id]["models"].add(ul.model)

                credit_by_reply = {}
                if reply_ids:
                    for tx in CreditTransaction.objects.filter(
                        user=selected_user,
                        reply_id__in=reply_ids,
                        transaction_type="deduction",
                    ):
                        credit_by_reply[tx.reply_id] = tx.amount

                runs_map = defaultdict(list)
                for t in tool_call_logs:
                    runs_map[t.reply_id].append({
                        "tool_name": t.tool_name,
                        "iteration": t.iteration,
                        "timestamp": t.timestamp,
                        "execution_time_ms": t.execution_time_ms,
                        "arguments": t.arguments,
                        "result_summary": t.result_summary,
                        "is_error": "error" in (t.result_summary or "").lower(),
                    })

                pipeline_runs = []
                for rid, items in runs_map.items():
                    usage = usage_by_reply.get(rid, {"input": 0, "output": 0, "models": set()})
                    pipeline_runs.append({
                        "reply_id": rid,
                        "tool_calls": items,
                        "usage_input": usage["input"],
                        "usage_output": usage["output"],
                        "models": sorted(usage["models"]),
                        "credit_cost": credit_by_reply.get(rid),
                    })
            except Conversation.DoesNotExist:
                pass

        # ── Render system prompt ──────────────────────────────────────────
        if selected_user:
            conv_for_prompt = selected_conv
            if not conv_for_prompt and conversations:
                conv_for_prompt = conversations[0]
            if conv_for_prompt:
                try:
                    system_prompt_text = build_system_prompt(selected_user, conv_for_prompt)
                    prompt_token_estimate = len(system_prompt_text) // 4
                except Exception as exc:
                    system_prompt_text = f"[Error building prompt: {exc}]"

        # ── Pipeline test ─────────────────────────────────────────────────
        if "run_pipeline" in request.POST and selected_conv and message_text.strip():
            active_tab = "test"
            integration = Integration.get_active(selected_user, selected_conv.platform)
            model = (integration.ai_model or None) if integration else None

            sp = build_system_prompt(selected_user, selected_conv)
            history = get_conversation_history(selected_conv, limit=20)
            if not history or history[-1].get("content") != message_text or history[-1].get("role") != "user":
                history.append({"role": "user", "content": message_text})
            messages_list = [{"role": "system", "content": sp}] + history

            # Snapshot of messages sent (for display)
            messages_sent_display = json.dumps(
                [{"role": m["role"], "content": str(m.get("content", ""))[:400]} for m in messages_list],
                indent=2,
                ensure_ascii=False,
            )

            iterations_trace = []
            final_text = None
            pending_images = []
            total_input = 0
            total_output = 0
            error = None
            used_model = model or "default"

            try:
                for iteration in range(5):
                    iter_data = {
                        "n": iteration + 1,
                        "tool_calls": [],
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "model_used": used_model,
                        "is_final": False,
                        "response": None,
                    }

                    llm_msg, usage = call_llm(messages=messages_list, tools=TOOL_DEFINITIONS, model=model)
                    iter_data["input_tokens"] = usage.get("input_tokens", 0)
                    iter_data["output_tokens"] = usage.get("output_tokens", 0)
                    iter_data["model_used"] = usage.get("model", model or "unknown")
                    used_model = iter_data["model_used"]
                    total_input += iter_data["input_tokens"]
                    total_output += iter_data["output_tokens"]

                    if not llm_msg.tool_calls:
                        final_text = llm_msg.content or ""
                        iter_data["is_final"] = True
                        iter_data["response"] = final_text
                        iterations_trace.append(iter_data)
                        break

                    messages_list.append(llm_msg)

                    for tc in llm_msg.tool_calls:
                        fn_name = tc.function.name
                        try:
                            fn_args = json.loads(tc.function.arguments or "{}")
                        except (json.JSONDecodeError, TypeError):
                            fn_args = {}

                        tc_result = execute_tool(fn_name, fn_args, selected_user, selected_conv)
                        is_error = isinstance(tc_result, dict) and bool(tc_result.get("error"))

                        if fn_name == "send_images" and isinstance(tc_result, dict):
                            pending_images.extend(tc_result.get("images", []))

                        iter_data["tool_calls"].append({
                            "name": fn_name,
                            "args_json": json.dumps(fn_args, indent=2, ensure_ascii=False),
                            "result_json": json.dumps(tc_result, indent=2, ensure_ascii=False, default=str),
                            "is_error": is_error,
                        })

                        messages_list.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(tc_result),
                        })

                        if fn_name == "create_ticket":
                            final_text = "Creating support ticket and transferring to human agent."
                            iter_data["is_final"] = True
                            iter_data["response"] = final_text
                            break

                    iterations_trace.append(iter_data)
                    if final_text:
                        break

            except Exception as exc:
                error = str(exc)

            # Credit cost estimate
            credit_cost = None
            try:
                pricing = ModelPricing.objects.filter(model_id__icontains=used_model.split("/")[-1]).first()
                if not pricing:
                    pricing = ModelPricing.objects.filter(is_active=True).first()
                if pricing:
                    credit_cost = float(pricing.cost_for(total_input, total_output))
            except Exception:
                pass

            seen = set()
            unique_images = [img for img in pending_images if img not in seen and not seen.add(img)][:5]

            pipeline_result = {
                "model": used_model,
                "iterations_trace": iterations_trace,
                "total_input": total_input,
                "total_output": total_output,
                "credit_cost": credit_cost,
                "final_text": final_text or "",
                "images": unique_images,
                "error": error,
                "messages_sent_display": messages_sent_display,
                "system_prompt": sp,
            }

        context = {
            "tool_call_logs": tool_call_logs,
            **self.admin_site.each_context(request),
            "title": "AI Audit & Debug",
            "opts": Conversation._meta,
            "users": users,
            "selected_user": selected_user,
            "conversations": conversations,
            "selected_conv": selected_conv,
            "account_info": account_info,
            "system_prompt_text": system_prompt_text,
            "prompt_token_estimate": prompt_token_estimate,
            "conv_history": conv_history,
            "pipeline_result": pipeline_result,
            "message_text": message_text,
            "active_tab": active_tab,
            "tool_count": 8,
            "pipeline_runs": pipeline_runs,
            "tool_filter": tool_filter,
            "reply_filter": reply_filter,
            "error_only": error_only,
        }
        return render(request, "admin/back/ai_debug.html", context)


# ---------------------------------------------------------------------------
# Other admins
# ---------------------------------------------------------------------------

class MessageAdmin(admin.ModelAdmin):
    list_display = ("conversation", "sender", "text", "timestamp")
    list_filter = ("sender",)
    search_fields = ("sender", "conversation", "text")
    ordering = ("-timestamp",)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("product_name", "price", "quantity")


class SaleAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "get_products", "customer_id", "amount", "status", "created_at")
    inlines = [OrderItemInline]
    list_filter = ("status", "source")
    ordering = ("-created_at",)
    search_fields = ("customer_id", "user__email", "items__product_name", "items__external_product_id")

    def get_products(self, obj):
        return ", ".join(item.product_name for item in obj.items.all())
    get_products.short_description = "Products"


class IntegrationAdmin(admin.ModelAdmin):
    list_display = ("platform", "user", "webhook_url", "access_token", "created_at", "is_enabled", "connection_method", "page_name", "is_connected", "token_expires_at")
    list_filter = ("platform",)
    search_fields = ("user",)
    ordering = ("-created_at",)
    readonly_fields = ("token_expires_at",)


class SettingAdmin(admin.ModelAdmin):
    list_display = ("platform", "user", "webhook_url", "created_at", "updated_at")
    list_filter = ("platform",)
    search_fields = ("user__email",)
    ordering = ("-created_at",)


class ProductSourceAdmin(admin.ModelAdmin):
    list_display = ("provider", "user", "status", "mode", "is_active", "last_synced")
    list_filter = ("provider", "status", "mode", "is_active")
    search_fields = ("user__email", "name", "store_url")
    ordering = ("-created_at",)
    exclude = ("_consumer_key", "_consumer_secret", "_api_key", "_access_token")


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

admin.site.unregister(User)
admin.site.register(User, UserAdmin)
admin.site.register(Product, ProductAdmin)
admin.site.register(Conversation, ConversationAdmin)
admin.site.register(Sale, SaleAdmin)
admin.site.register(Setting, SettingAdmin)
admin.site.register(Integration, IntegrationAdmin)
admin.site.register(Message, MessageAdmin)
admin.site.register(Package, PackageAdmin)
admin.site.register(ProductSource, ProductSourceAdmin)
admin.site.register(PackageItem, PackageItemAdmin)

@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ["pk", "subject", "status", "priority", "assigned_to", "conversation_link", "created_at"]
    list_filter = ["status", "priority"]
    search_fields = ["subject", "description", "conversation__customer_name", "conversation__customer_id"]
    actions = ["mark_resolved", "mark_open"]

    def conversation_link(self, obj):
        from django.utils.html import format_html
        return format_html('<a href="/admin/back/conversation/{}/change/">View</a>', obj.conversation_id)
    conversation_link.short_description = "Conversation"

    @admin.action(description="Mark selected as resolved")
    def mark_resolved(self, request, queryset):
        updated = queryset.update(status="resolved", resolved_at=timezone.now())
        self.message_user(request, f"{updated} ticket(s) resolved.")

    @admin.action(description="Reopen selected")
    def mark_open(self, request, queryset):
        updated = queryset.update(status="open", resolved_at=None)
        self.message_user(request, f"{updated} ticket(s) reopened.")


@admin.register(ToolCallLog)
class ToolCallLogAdmin(admin.ModelAdmin):
    list_display = ["tool_name", "conversation_link", "reply_id", "iteration", "execution_time_ms", "timestamp"]
    list_filter = ["tool_name", "timestamp", "conversation__user"]
    search_fields = ["tool_name", "reply_id", "result_summary", "conversation__customer_id"]
    readonly_fields = [f.name for f in ToolCallLog._meta.fields]
    list_select_related = ("conversation", "user")
    raw_id_fields = ("conversation", "user")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def conversation_link(self, obj):
        from django.utils.html import format_html
        return format_html(
            '<a href="/admin/back/conversation/{}/change/">{} ({})</a>',
            obj.conversation_id,
            obj.conversation.customer_id[:20] if obj.conversation and obj.conversation.customer_id else "?",
            obj.conversation.platform if obj.conversation else "?",
        )
    conversation_link.short_description = "Conversation"
