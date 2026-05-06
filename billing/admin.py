from django.contrib import admin, messages
from django.shortcuts import redirect, render
from django.urls import path

from billing.deductions import top_up
from billing.signals import _sync_profile_plan
from .models import CreditTransaction, ModelPricing, Plan, UsageSummary, UserBalance


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "monthly_credits", "max_messages_per_month", "price_per_month", "is_active")
    list_filter = ("is_active",)
    ordering = ("price_per_month",)


@admin.register(UserBalance)
class UserBalanceAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "credits_remaining", "credits_total", "messages_used", "renewal_date", "updated_at")
    list_filter = ("plan",)
    search_fields = ("user__username", "user__email")
    ordering = ("-updated_at",)
    actions = ["action_top_up", "action_change_plan"]

    def get_readonly_fields(self, request, obj=None):
        if obj:
            # Editing: lock fields that are auto-managed
            return ("credits_total", "messages_used", "renewal_date", "created_at", "updated_at")
        # Adding: only lock audit timestamps; let admin set plan/credits freely
        return ("messages_used", "created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        if not obj.renewal_date:
            obj.renewal_date = UserBalance.next_renewal_date()
        if not change and obj.plan:
            # On create: seed credits from the plan if not set manually
            if not obj.credits_total:
                obj.credits_total = obj.plan.monthly_credits
            if not obj.credits_remaining:
                obj.credits_remaining = obj.plan.monthly_credits
        super().save_model(request, obj, form, change)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("top-up/", self.admin_site.admin_view(self.top_up_view), name="billing_userbalance_topup"),
            path("change-plan/", self.admin_site.admin_view(self.change_plan_view), name="billing_userbalance_changeplan"),
        ]
        return custom + urls

    # ── Actions ──────────────────────────────────────────────────────────────

    @admin.action(description="Add credits (top-up)")
    def action_top_up(self, request, queryset):
        ids = ",".join(str(b.pk) for b in queryset)
        return redirect(f"top-up/?ids={ids}")

    @admin.action(description="Change plan")
    def action_change_plan(self, request, queryset):
        ids = ",".join(str(b.pk) for b in queryset)
        return redirect(f"change-plan/?ids={ids}")

    # ── Intermediate views ────────────────────────────────────────────────────

    def top_up_view(self, request):
        ids = request.POST.get("ids") or request.GET.get("ids", "")
        balances = UserBalance.objects.select_related("user", "plan").filter(
            pk__in=[i for i in ids.split(",") if i]
        )

        if request.method == "POST" and "amount" in request.POST:
            amount_raw = request.POST.get("amount", "").strip()
            note = request.POST.get("note", "").strip() or "Admin top-up"
            try:
                amount = float(amount_raw)
                if amount <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                self.message_user(request, "Enter a positive credit amount.", level=messages.ERROR)
            else:
                for b in balances:
                    top_up(b.user, amount, note)
                self.message_user(request, f"Added {amount} credits to {balances.count()} account(s).")
                return redirect("../")

        context = {
            **self.admin_site.each_context(request),
            "title": "Top Up Credits",
            "balances": balances,
            "ids": ids,
            "opts": self.model._meta,
        }
        return render(request, "admin/billing/top_up_form.html", context)

    def change_plan_view(self, request):
        ids = request.POST.get("ids") or request.GET.get("ids", "")
        balances = UserBalance.objects.select_related("user", "plan").filter(
            pk__in=[i for i in ids.split(",") if i]
        )
        plans = Plan.objects.filter(is_active=True).order_by("price_per_month")

        if request.method == "POST" and "plan_id" in request.POST:
            plan_id = request.POST.get("plan_id")
            reset_credits = "reset_credits" in request.POST
            try:
                new_plan = Plan.objects.get(pk=plan_id)
            except Plan.DoesNotExist:
                self.message_user(request, "Plan not found.", level=messages.ERROR)
            else:
                for b in balances:
                    b.plan = new_plan
                    if reset_credits:
                        b.credits_remaining = new_plan.monthly_credits
                        b.credits_total = new_plan.monthly_credits
                        b.save(update_fields=["plan", "credits_remaining", "credits_total", "updated_at"])
                    else:
                        b.save(update_fields=["plan", "updated_at"])
                    _sync_profile_plan(b.user, new_plan.name)
                self.message_user(
                    request,
                    f"Changed {balances.count()} account(s) to {new_plan.get_name_display()} plan."
                    + (" Credits reset to plan amount." if reset_credits else ""),
                )
                return redirect("../")

        context = {
            **self.admin_site.each_context(request),
            "title": "Change Plan",
            "balances": balances,
            "plans": plans,
            "ids": ids,
            "opts": self.model._meta,
        }
        return render(request, "admin/billing/change_plan_form.html", context)


@admin.register(ModelPricing)
class ModelPricingAdmin(admin.ModelAdmin):
    list_display = ("model_id", "credits_per_1k_input", "credits_per_1k_output", "is_active")
    list_filter = ("is_active",)
    ordering = ("model_id",)


@admin.register(UsageSummary)
class UsageSummaryAdmin(admin.ModelAdmin):
    list_display = ("user", "date", "total_replies", "total_ai_calls", "total_input_tokens", "total_output_tokens", "total_credits_used")
    list_filter = ("date",)
    search_fields = ("user__username", "user__email")
    ordering = ("-date",)
    date_hierarchy = "date"


@admin.register(CreditTransaction)
class CreditTransactionAdmin(admin.ModelAdmin):
    list_display = ("user", "amount", "balance_after", "transaction_type", "reply_id", "timestamp")
    list_filter = ("transaction_type",)
    search_fields = ("user__username", "reply_id")
    ordering = ("-timestamp",)
    readonly_fields = ("user", "amount", "balance_after", "transaction_type", "reply_id", "note", "timestamp")
