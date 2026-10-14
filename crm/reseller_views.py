from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Sum, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from back.models import Product, Conversation, Sale, Integration
from billing.models import CreditTransaction, UsageSummary, UserBalance
from crm.models import ResellerClient, ResellerBalance, StaffProfile
from crm.permissions import is_reseller, reseller_required


def reseller_pool(user):
    try:
        return user.reseller_balance
    except ResellerBalance.DoesNotExist:
        return None


def _get_client(request, user_id):
    return get_object_or_404(User, pk=user_id, reseller_of__reseller=request.user)


def _clients_qs(request):
    return User.objects.filter(reseller_of__reseller=request.user)


_CREDIT_TYPES = ["top_up_pool_in", "top_up_pool_out", "deduction", "top_up", "adjustment"]


@login_required
@reseller_required
def dashboard(request):
    clients = _clients_qs(request)
    rb = reseller_pool(request.user)
    pool = rb.pool_credits if rb else 0
    now = timezone.now().date()

    total_allocated = UserBalance.objects.filter(user__in=clients).aggregate(
        total=Sum("credits_remaining")
    )["total"] or 0
    total_consumed = UsageSummary.objects.filter(user__in=clients).aggregate(
        total=Sum("total_credits_used")
    )["total"] or 0
    total_received = CreditTransaction.objects.filter(
        user=request.user, transaction_type="top_up_pool_in"
    ).aggregate(total=Sum("amount"))["total"] or 0

    recent_activity = CreditTransaction.objects.filter(
        user__in=list(clients) + [request.user]
    ).order_by("-timestamp")[:5]

    recent_customers = []
    for u in clients.order_by("-date_joined")[:5]:
        bal = getattr(u, "balance", None)
        recent_customers.append({
            "user": u,
            "credits": bal.credits_remaining if bal else 0,
        })

    # Customers needing renewal (renewal_date has passed)
    overdue = 0
    for u in clients.iterator():
        bal = getattr(u, "balance", None)
        rc = getattr(u, "reseller_of", None)
        if rc and bal and bal.renewal_date <= now:
            overdue += 1

    return render(request, "crm/reseller/dashboard.html", {
        "total_customers": clients.count(),
        "pool_credits": pool,
        "total_received": total_received,
        "total_allocated": total_allocated,
        "total_consumed": total_consumed,
        "recent_activity": recent_activity,
        "recent_customers": recent_customers,
        "low_balance": pool > 0 and total_received > 0 and (pool / total_received) < Decimal("0.2"),
        "overdue_count": overdue,
    })


@login_required
@reseller_required
def add_client(request):
    from billing.models import Plan

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        store_name = request.POST.get("store_name", "").strip()
        plan_id = request.POST.get("plan_id", "")
        if not username or not password:
            messages.error(request, "Username and password are required.")
            return render(request, "crm/reseller/add_user.html")
        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already taken.")
            return render(request, "crm/reseller/add_user.html", {"plans": Plan.objects.filter(is_active=True), "store_name": store_name, "email": email})
        if email and User.objects.filter(email=email).exists():
            messages.error(request, "Email already in use.")
            return render(request, "crm/reseller/add_user.html", {"plans": Plan.objects.filter(is_active=True), "store_name": store_name, "username": username})
        user = User.objects.create_user(username=username, email=email, password=password)
        ResellerClient.objects.create(reseller=request.user, client=user)
        if store_name:
            from context.models import StoreConfig
            try:
                store = StoreConfig.objects.get(user=user)
                store.store_name = store_name
                store.save(update_fields=["store_name"])
            except StoreConfig.DoesNotExist:
                pass

        # Assign plan
        try:
            plan = Plan.objects.get(pk=plan_id, is_active=True) if plan_id else Plan.objects.filter(is_active=True).first()
        except Plan.DoesNotExist:
            plan = Plan.objects.filter(is_active=True).first()

        if plan:
            cost = plan.reseller_price
            rb = reseller_pool(request.user)

            # Check pool can cover plan cost
            if cost > 0 and (not rb or rb.pool_credits < cost):
                user.delete()
                messages.error(request, f"Insufficient pool credits. Plan '{plan.name}' costs {cost} credits, but you have {rb.pool_credits if rb else 0}.")
                return render(request, "crm/reseller/add_user.html", {
                    "plans": Plan.objects.filter(is_active=True),
                    "store_name": store_name,
                    "email": email,
                    "username": username,
                })

            # Deduct reseller_price from pool
            if cost > 0 and rb:
                rb.pool_credits -= cost
                rb.save(update_fields=["pool_credits", "updated_at"])
                CreditTransaction.objects.create(
                    user=request.user, amount=-cost, balance_after=rb.pool_credits,
                    transaction_type="top_up_pool_out",
                    note=f"Plan '{plan.name}' for {username}",
                )

            # Setup customer balance
            balance = UserBalance.objects.get(user=user)
            balance.plan = plan
            balance.credits_remaining = plan.monthly_credits
            balance.credits_total = plan.monthly_credits

            # Optional initial credits from reseller pool
            initial_credits = Decimal(request.POST.get("initial_credits", "0"))
            if initial_credits > 0:
                if rb and rb.pool_credits >= initial_credits:
                    rb.pool_credits -= initial_credits
                    rb.save(update_fields=["pool_credits", "updated_at"])
                    balance.credits_remaining += initial_credits
                    balance.credits_total += initial_credits
                    CreditTransaction.objects.create(
                        user=request.user, amount=-initial_credits, balance_after=rb.pool_credits,
                        transaction_type="top_up_pool_out", note=f"Initial top-up to {username}",
                    )
                    CreditTransaction.objects.create(
                        user=user, amount=initial_credits, balance_after=balance.credits_remaining,
                        transaction_type="top_up_from_manager",
                        note=f"Initial top-up from reseller {request.user.username}",
                    )
                else:
                    user.delete()
                    messages.error(request, f"Insufficient pool credits for initial top-up. Need {initial_credits}, have {rb.pool_credits if rb else 0}.")
                    return redirect("back:add_client")

            balance.save()

        messages.success(request, "User created successfully.")
        return redirect("back:manage_users")
    return render(request, "crm/reseller/add_user.html", {
        "plans": Plan.objects.filter(is_active=True),
    })


@login_required
@reseller_required
def edit_client(request, user_id):
    managed_user = _get_client(request, user_id)
    if request.method == "POST":
        managed_user.email = request.POST.get("email", "").strip()
        managed_user.first_name = request.POST.get("first_name", "").strip()
        managed_user.last_name = request.POST.get("last_name", "").strip()
        managed_user.save(update_fields=["email", "first_name", "last_name"])
        new_password = request.POST.get("password", "").strip()
        if new_password:
            managed_user.set_password(new_password)
            managed_user.save(update_fields=["password"])
        messages.success(request, "User updated.")
        return redirect("back:customer_list")
    return render(request, "crm/reseller/edit_user.html", {"managed_user": managed_user})


@login_required
@reseller_required
def topup_client(request, user_id):
    managed_user = _get_client(request, user_id)
    rb = reseller_pool(request.user)
    if not rb:
        messages.error(request, "Reseller balance not found.")
        return redirect("back:manage_users")
    try:
        user_balance = UserBalance.objects.select_for_update().get(user=managed_user)
    except UserBalance.DoesNotExist:
        messages.error(request, "User balance not found.")
        return redirect("back:manage_users")

    if request.method == "POST":
        amount_str = request.POST.get("amount", "").strip()
        try:
            amount = Decimal(str(amount_str))
            if amount <= 0:
                raise ValueError
        except (ValueError, TypeError):
            messages.error(request, "Enter a valid positive amount.")
            return redirect("back:topup_client", user_id=user_id)
        if rb.pool_credits < amount:
            messages.error(request, "Insufficient pool credits.")
            return redirect("back:topup_client", user_id=user_id)

        with transaction.atomic():
            rb2 = ResellerBalance.objects.select_for_update().get(pk=rb.pk)
            ub = UserBalance.objects.select_for_update().get(pk=user_balance.pk)
            rb2.pool_credits -= amount
            rb2.save(update_fields=["pool_credits", "updated_at"])
            ub.credits_remaining += amount
            ub.credits_total += amount
            ub.save(update_fields=["credits_remaining", "credits_total", "updated_at"])
            CreditTransaction.objects.create(
                user=request.user, amount=-amount, balance_after=rb2.pool_credits,
                transaction_type="top_up_pool_out", note=f"Top-up to {managed_user.username}",
            )
            CreditTransaction.objects.create(
                user=managed_user, amount=amount, balance_after=ub.credits_remaining,
                transaction_type="top_up_from_manager", note=f"From reseller {request.user.username}",
            )
            if ub.credits_remaining > 0:
                Integration.objects.filter(user=managed_user, is_enabled=False).update(is_enabled=True)
        messages.success(request, f"Added {amount} credits.")
        return redirect("back:customer_list")

    return render(request, "crm/reseller/topup.html", {
        "managed_user": managed_user,
        "reseller_balance": rb,
        "user_balance": user_balance,
    })


@login_required
@reseller_required
def client_overview(request, user_id):
    managed_user = _get_client(request, user_id)
    total_sales = Sale.objects.filter(user=managed_user, status="completed").aggregate(total=Sum("amount"))["total"] or 0
    total_conversations = Conversation.objects.filter(user=managed_user).count()
    active_products = Product.objects.filter(user=managed_user, stock_quantity__gt=0).count()
    orders_count = Sale.objects.filter(user=managed_user).count()
    try:
        balance = UserBalance.objects.select_related("plan").get(user=managed_user)
    except UserBalance.DoesNotExist:
        balance = None
    recent_orders = Sale.objects.filter(user=managed_user).order_by("-created_at")[:5]
    recent_conversations = Conversation.objects.filter(user=managed_user).order_by("-timestamp")[:5]
    return render(request, "crm/reseller/overview.html", {
        "managed_user": managed_user,
        "total_sales": total_sales,
        "total_conversations": total_conversations,
        "active_products": active_products,
        "orders_count": orders_count,
        "balance": balance,
        "recent_orders": recent_orders,
        "recent_conversations": recent_conversations,
    })


@login_required
@reseller_required
def client_products(request, user_id):
    managed_user = _get_client(request, user_id)
    all_products = Product.objects.filter(user=managed_user).order_by("-last_synced")
    if request.method == "POST":
        Product.objects.create(
            user=managed_user,
            name=request.POST.get("name"),
            price=request.POST.get("price"),
            stock_quantity=request.POST.get("stock_quantity", 0),
            description=request.POST.get("description", ""),
        )
        messages.success(request, "Product added.")
        return redirect("back:client_products", user_id=user_id)
    return render(request, "crm/reseller/products.html", {
        "managed_user": managed_user,
        "all_products": all_products,
    })


@login_required
@reseller_required
def client_orders(request, user_id):
    managed_user = _get_client(request, user_id)
    all_orders = Sale.objects.filter(user=managed_user).order_by("-created_at")
    return render(request, "crm/reseller/orders.html", {
        "managed_user": managed_user,
        "all_orders": all_orders,
    })


@login_required
@reseller_required
def client_chats(request, user_id):
    managed_user = _get_client(request, user_id)
    all_convo = Conversation.objects.filter(user=managed_user).order_by("-timestamp")
    convo_id = request.GET.get("cid")
    selected_convo = None
    messages_list = None
    if convo_id:
        selected_convo = get_object_or_404(Conversation, id=convo_id, user=managed_user)
        messages_list = selected_convo.messages.all().order_by("timestamp")
    return render(request, "crm/reseller/chats.html", {
        "managed_user": managed_user,
        "all_convo": all_convo,
        "selected_convo": selected_convo,
        "messages_list": messages_list,
    })


@login_required
@reseller_required
def client_settings(request, user_id):
    managed_user = _get_client(request, user_id)
    from context.models import AgentIdentity, StoreConfig, BehaviorRules
    from billing.models import ModelPricing
    identity, _ = AgentIdentity.objects.get_or_create(user=managed_user)
    store, _ = StoreConfig.objects.get_or_create(user=managed_user)
    rules, _ = BehaviorRules.objects.get_or_create(user=managed_user)

    if request.method == "POST":
        section = request.POST.get("section", "")
        if section == "store":
            store.store_name = request.POST.get("store_name", "")
            store.address = request.POST.get("address", "")
            store.whatsapp_number = request.POST.get("whatsapp_number", "")
            store.delivery_charge_inside = request.POST.get("delivery_charge_inside", 0)
            store.delivery_charge_outside = request.POST.get("delivery_charge_outside", 0)
            store.support_open_time = request.POST.get("support_open_time") or "09:00"
            store.support_close_time = request.POST.get("support_close_time") or "21:00"
            store.timezone = request.POST.get("timezone", "UTC")
            store.currency = request.POST.get("currency", "BDT")
            store.save()
        elif section == "agent":
            identity.name = request.POST.get("agent_name", "")
            identity.role = request.POST.get("role", "")
            identity.tone = request.POST.get("tone", "")
            identity.style = request.POST.get("communication_style", "")
            identity.language = request.POST.get("language", "en")
            if "image" in request.FILES:
                identity.image = request.FILES["image"]
            identity.save()
        elif section == "behavior":
            rules.greeting_message = request.POST.get("greeting_message", "")
            rules.custom_instructions = request.POST.get("custom_instructions", "")
            rules.chit_chat_enabled = request.POST.get("chit_chat_enabled") == "on"
            rules.cross_sell_enabled = request.POST.get("cross_sell_enabled") == "on"
            rules.ask_open_ended = request.POST.get("ask_open_ended") == "on"
            rules.chit_chat_style = request.POST.get("chit_chat_style", "moderate")
            rules.sample_questions_answers = request.POST.get("sample_questions_answers", "")
            rules.save()
        elif section == "knowledge":
            rules.knowledge_base = request.POST.get("knowledge_base", "")
            rules.save()
        elif section == "ai":
            ai_enabled = request.POST.get("ai_enabled") == "on"
            training_mode = request.POST.get("training_mode") == "on"
            Integration.objects.filter(user=managed_user).update(is_enabled=ai_enabled)
            managed_user.profile.is_training = training_mode
            managed_user.profile.save(update_fields=["is_training"])
            messages.success(request, f"AI auto-reply {'ON' if ai_enabled else 'OFF'}, training mode {'ON' if training_mode else 'OFF'}.")
            return redirect(f"{request.path}?tab=ai")
        messages.success(request, f"{section.capitalize()} settings updated.")
        return redirect(f"{request.path}?tab={section}")

    timezones = [
        "Asia/Dhaka", "Asia/Kolkata", "Asia/Karachi", "Asia/Dubai",
        "Asia/Singapore", "Asia/Bangkok", "Asia/Jakarta",
        "UTC", "Europe/London", "Europe/Paris", "Europe/Berlin",
        "America/New_York", "America/Chicago", "America/Los_Angeles",
    ]
    available_models = list(ModelPricing.objects.filter(is_active=True).values_list("model_id", flat=True))
    integrations = list(Integration.objects.filter(user=managed_user))
    ai_active = any(i.is_enabled for i in integrations)
    is_training = managed_user.profile.is_training
    rc = getattr(managed_user, "reseller_of", None)
    auto_renew = rc.auto_renew if rc else True
    return render(request, "crm/reseller/settings.html", {
        "managed_user": managed_user,
        "identity": identity,
        "store": store,
        "rules": rules,
        "integrations": integrations,
        "available_models": available_models,
        "active_tab": request.GET.get("tab", "store"),
        "timezones": timezones,
        "ai_active": ai_active,
        "is_training": is_training,
        "auto_renew": auto_renew,
    })


@login_required
@reseller_required
def client_billing(request, user_id):
    managed_user = _get_client(request, user_id)
    try:
        balance = UserBalance.objects.select_related("plan").get(user=managed_user)
    except UserBalance.DoesNotExist:
        balance = None
    today = timezone.now().date()
    recent_txns = list(CreditTransaction.objects.filter(user=managed_user)[:20])
    month_totals = UsageSummary.objects.filter(
        user=managed_user, date__gte=today.replace(day=1)
    ).aggregate(
        replies=Sum("total_replies"),
        ai_calls=Sum("total_ai_calls"),
        credits=Sum("total_credits_used"),
    )
    return render(request, "crm/reseller/billing.html", {
        "managed_user": managed_user,
        "balance": balance,
        "recent_txns": recent_txns,
        "month_totals": month_totals,
    })


@login_required
@reseller_required
def reseller_stats(request):
    clients = User.objects.filter(reseller_of__reseller=request.user)
    total_clients = clients.count()
    total_orders = Sale.objects.filter(user__in=clients).count()
    total_sales = Sale.objects.filter(user__in=clients, status="completed").aggregate(total=Sum("amount"))["total"] or 0
    total_conversations = Conversation.objects.filter(user__in=clients).count()
    total_credits = UserBalance.objects.filter(user__in=clients).aggregate(total=Sum("credits_remaining"))["total"] or 0
    return render(request, "crm/reseller/stats.html", {
        "total_clients": total_clients,
        "total_orders": total_orders,
        "total_sales": total_sales,
        "total_conversations": total_conversations,
        "total_credits": total_credits,
    })


@login_required
@reseller_required
def customer_list(request):
    users = _clients_qs(request).select_related("profile", "balance__plan").order_by("-date_joined")
    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "all")
    now = timezone.now().date()
    if q:
        users = users.filter(Q(username__icontains=q) | Q(email__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q))

    user_data = []
    for u in users:
        bal = getattr(u, "balance", None)
        credits = bal.credits_remaining if bal else 0
        rc = getattr(u, "reseller_of", None)
        # Determine status
        if not u.is_active:
            status = "suspended"
        elif rc and bal and bal.renewal_date <= now:
            status = "renewal_needed"
        elif credits <= 0:
            status = "low"
        else:
            status = "active"
        if status_filter != "all" and status != status_filter:
            continue
        allocated = bal.credits_total if bal else 0
        consumed = UsageSummary.objects.filter(user=u).aggregate(t=Sum("total_credits_used"))["t"] or 0
        user_data.append({
            "user": u,
            "credits": credits,
            "allocated": allocated,
            "consumed": consumed,
            "status": status,
            "auto_renew": rc.auto_renew if rc else True,
            "created": u.date_joined,
        })

    rb = reseller_pool(request.user)
    return render(request, "crm/reseller/customers.html", {
        "user_data": user_data,
        "pool_credits": rb.pool_credits if rb else 0,
        "query": q,
        "active_filter": status_filter,
    })


@login_required
@reseller_required
def wallet_overview(request):
    rb = reseller_pool(request.user)
    pool = rb.pool_credits if rb else 0
    clients = _clients_qs(request)
    total_received = CreditTransaction.objects.filter(
        user=request.user, transaction_type="top_up_pool_in"
    ).aggregate(total=Sum("amount"))["total"] or 0
    total_allocated = UserBalance.objects.filter(user__in=clients).aggregate(
        total=Sum("credits_total")
    )["total"] or 0
    total_consumed = UsageSummary.objects.filter(user__in=clients).aggregate(
        total=Sum("total_credits_used")
    )["total"] or 0

    txns = CreditTransaction.objects.filter(
        Q(user=request.user) | Q(user__in=list(clients))
    ).order_by("-timestamp")[:50]

    return render(request, "crm/reseller/wallet.html", {
        "pool_credits": pool,
        "total_received": total_received,
        "total_allocated": total_allocated,
        "total_consumed": total_consumed,
        "txns": txns,
        "low_balance": pool > 0 and total_received > 0 and (pool / total_received) < Decimal("0.2"),
    })


@login_required
@reseller_required
def transactions(request):
    clients = _clients_qs(request)
    ttype = request.GET.get("type", "")
    txn_qs = CreditTransaction.objects.filter(
        Q(user=request.user) | Q(user__in=list(clients))
    )
    if ttype:
        txn_qs = txn_qs.filter(transaction_type=ttype)
    txns = txn_qs.order_by("-timestamp")[:100]

    return render(request, "crm/reseller/transactions.html", {
        "txns": txns,
        "type_filter": ttype,
    })


@login_required
@reseller_required
def reseller_settings_view(request):
    if request.method == "POST":
        section = request.POST.get("section", "")
        if section == "profile":
            sp = request.user.staff_profile
            sp.phone = request.POST.get("phone", "")
            sp.title = request.POST.get("business_name", "")
            sp.save(update_fields=["phone", "title"])
            request.user.email = request.POST.get("email", "")
            request.user.first_name = request.POST.get("first_name", "")
            request.user.last_name = request.POST.get("last_name", "")
            request.user.save(update_fields=["email", "first_name", "last_name"])
            messages.success(request, "Profile updated.")
        elif section == "password":
            pwd = request.POST.get("new_password", "").strip()
            if pwd:
                request.user.set_password(pwd)
                request.user.save(update_fields=["password"])
                messages.success(request, "Password updated. Please log in again.")
                return redirect("front:login")
            messages.error(request, "Password cannot be empty.")
        return redirect("back:reseller_settings")

    sp = request.user.staff_profile
    return render(request, "crm/reseller/settings_profile.html", {
        "staff_profile": sp,
    })


@login_required
@reseller_required
def renew_client(request, user_id):
    """Manually renew a customer's plan — deduct reseller_price from pool, reset credits."""
    managed_user = _get_client(request, user_id)
    rc = getattr(managed_user, "reseller_of", None)
    if not rc:
        messages.error(request, "Customer is not linked to a reseller.")
        return redirect("back:customer_list")

    rb = reseller_pool(request.user)
    if not rb:
        messages.error(request, "Reseller balance not found.")
        return redirect("back:customer_list")

    try:
        balance = UserBalance.objects.select_for_update().get(user=managed_user)
    except UserBalance.DoesNotExist:
        messages.error(request, "Customer balance not found.")
        return redirect("back:customer_list")

    cost = balance.plan.reseller_price
    if cost > 0 and rb.pool_credits < cost:
        messages.error(request, f"Insufficient pool credits. Need {cost}, have {rb.pool_credits}.")
        return redirect("back:customer_list")

    plan_name = balance.plan.name
    with transaction.atomic():
        if cost > 0:
            rb2 = ResellerBalance.objects.select_for_update().get(pk=rb.pk)
            rb2.pool_credits -= cost
            rb2.save(update_fields=["pool_credits", "updated_at"])
            CreditTransaction.objects.create(
                user=request.user, amount=-cost, balance_after=rb2.pool_credits,
                transaction_type="top_up_pool_out",
                note=f"Manual renewal '{plan_name}' for {managed_user.username}",
            )

        balance.credits_remaining = balance.plan.monthly_credits
        balance.credits_total = balance.plan.monthly_credits
        balance.messages_used = 0
        balance.renewal_date = UserBalance.next_renewal_date(balance.renewal_date)
        balance.save(update_fields=["credits_remaining", "credits_total", "messages_used", "renewal_date", "updated_at"])

        Integration.objects.filter(user=managed_user, is_enabled=False).update(is_enabled=True)

        CreditTransaction.objects.create(
            user=managed_user, amount=balance.plan.monthly_credits,
            balance_after=balance.credits_remaining, transaction_type="renewal",
            note=f"Monthly renewal — plan: {plan_name}",
        )

    messages.success(request, f"'{managed_user.username}' renewed successfully for {plan_name}.")
    return redirect("back:customer_list")


@login_required
@reseller_required
def suspend_client(request, user_id):
    """Suspend a customer — set credits to 0, disable integrations."""
    managed_user = _get_client(request, user_id)
    try:
        balance = UserBalance.objects.select_for_update().get(user=managed_user)
    except UserBalance.DoesNotExist:
        messages.error(request, "Customer balance not found.")
        return redirect("back:customer_list")

    balance.credits_remaining = Decimal("0")
    balance.credits_total = Decimal("0")
    balance.save(update_fields=["credits_remaining", "credits_total", "updated_at"])

    Integration.objects.filter(user=managed_user, is_enabled=True).update(is_enabled=False)

    CreditTransaction.objects.create(
        user=managed_user, amount=0, balance_after=0, transaction_type="adjustment",
        note=f"Account suspended by reseller {request.user.username}",
    )

    messages.success(request, f"'{managed_user.username}' has been suspended.")
    return redirect("back:customer_list")


@login_required
@reseller_required
def reactivate_client(request, user_id):
    """Reactivate a suspended customer — re-enable integrations only (renewal adds credits)."""
    managed_user = _get_client(request, user_id)
    Integration.objects.filter(user=managed_user, is_enabled=False).update(is_enabled=True)
    messages.success(request, f"'{managed_user.username}' has been reactivated. Use 'Renew' to add credits.")
    return redirect("back:customer_list")


@login_required
@reseller_required
def toggle_auto_renew(request, user_id):
    """Toggle auto-renew for a customer."""
    managed_user = _get_client(request, user_id)
    rc = getattr(managed_user, "reseller_of", None)
    if not rc:
        messages.error(request, "Customer is not linked to a reseller.")
        return redirect("back:customer_list")

    rc.auto_renew = not rc.auto_renew
    rc.save(update_fields=["auto_renew", "updated_at"])
    state = "ON" if rc.auto_renew else "OFF"
    messages.success(request, f"Auto-renew for '{managed_user.username}' turned {state}.")
    return redirect("back:customer_list")
