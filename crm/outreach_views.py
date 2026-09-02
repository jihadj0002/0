"""
Email Outreach — views for campaign management, templates, accounts, analytics.

All views require owner/manager role.
"""
from django.db.models import Count, Sum, Q
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.core.paginator import Paginator
from django.views.decorators.http import require_POST, require_GET
from django.template.loader import render_to_string
from django.contrib import messages
from django.urls import reverse
from django_rq import get_queue
import json

from .permissions import crm_role_required
from .services import (
    get_role, can_manage, lead_queryset_for, internal_queryset,
    build_email_context, render_email, token_replace,
)
from .models import (
    Lead, PipelineStage, StaffProfile,
    EmailTemplate, EmailBatch, EmailLog,
    EmailAccount, Campaign, CampaignLead,
)


_OUTREACH_PER_PAGE = 25


def _role(user):
    return get_role(user)


def _outreach_nav(request):
    return {"role": _role(request.user)}


# ============================================================
# OVERVIEW
# ============================================================
@crm_role_required("owner", "manager")
def outreach_overview(request):
    user = request.user
    campaigns = internal_queryset(Campaign.objects.all())
    total_sent = EmailLog.objects.filter(status="sent").count()
    total_logs = EmailLog.objects.count()
    open_rate = 0
    reply_rate = 0
    if total_logs:
        opens = EmailLog.objects.filter(status="opened").count()
        replies = EmailLog.objects.filter(status="replied").count()
        open_rate = round(opens / total_logs * 100, 1) if total_logs else 0
        reply_rate = round(replies / total_logs * 100, 1) if total_logs else 0

    active_campaigns = campaigns.filter(status__in=("scheduled", "sending")).order_by("-created_at")[:5]
    campaign_count = campaigns.count()

    context = {
        "total_sent": total_sent,
        "open_rate": open_rate,
        "reply_rate": reply_rate,
        "campaign_count": campaign_count,
        "active_campaigns": active_campaigns,
        **_outreach_nav(request),
    }
    return render(request, "crm/outreach/overview.html", context)


# ============================================================
# CAMPAIGNS
# ============================================================
@crm_role_required("owner", "manager")
def outreach_campaigns(request):
    user = request.user
    qs = internal_queryset(Campaign.objects.select_related("template", "created_by"))

    status = request.GET.get("status")
    search = request.GET.get("q", "").strip()
    if status:
        qs = qs.filter(status=status)
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(description__icontains=search))

    paginator = Paginator(qs.order_by("-created_at"), _OUTREACH_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    context = {
        "page_obj": page_obj,
        "status_filter": status,
        "search_query": search,
        **_outreach_nav(request),
    }
    return render(request, "crm/outreach/campaign_list.html", context)


@crm_role_required("owner", "manager")
def outreach_campaign_detail(request, uid):
    campaign = get_object_or_404(Campaign, uid=uid)

    # Auto-recover campaigns stuck in "sending" (e.g. thread died on restart)
    if campaign.status == "sending" and campaign.leads.filter(status="pending").exists():
        from .rq import send_campaign_emails
        import threading
        reloaded = False
        if campaign.started_at and (timezone.now() - campaign.started_at).total_seconds() > 60 and campaign.sent_count == 0 and campaign.failed_count == 0:
            def _run():
                from django.db import close_old_connections
                try:
                    send_campaign_emails(campaign.id)
                except Exception:
                    pass
                finally:
                    close_old_connections()
            t = threading.Thread(target=_run, daemon=True)
            t.start()
            reloaded = True

    logs = EmailLog.objects.filter(lead__campaign_leads__campaign=campaign).select_related("lead").order_by("-created_at")[:50]

    context = {
        "campaign": campaign,
        "recent_logs": logs,
        **_outreach_nav(request),
    }
    return render(request, "crm/outreach/campaign_detail.html", context)


@crm_role_required("owner", "manager")
def outreach_campaign_poll(request, uid):
    """JSON endpoint for live campaign stats (used by detail page polling)."""
    campaign = get_object_or_404(Campaign, uid=uid)
    return JsonResponse({
        "status": campaign.status,
        "total_recipients": campaign.total_recipients,
        "sent_count": campaign.sent_count,
        "failed_count": campaign.failed_count,
        "bounced_count": campaign.bounced_count,
        "opened_count": campaign.opened_count,
        "replied_count": campaign.replied_count,
    })


@crm_role_required("owner", "manager")
def outreach_campaign_recipients(request, uid):
    campaign = get_object_or_404(Campaign, uid=uid)
    qs = campaign.leads.select_related("lead__stage", "lead__company")

    status_f = request.GET.get("status")
    search = request.GET.get("q", "").strip()
    if status_f:
        qs = qs.filter(status=status_f)
    if search:
        qs = qs.filter(lead__name__icontains=search)

    paginator = Paginator(qs.order_by("-created_at"), _OUTREACH_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    context = {
        "campaign": campaign,
        "page_obj": page_obj,
        "status_filter": status_f,
        **_outreach_nav(request),
    }
    return render(request, "crm/outreach/campaign_recipients.html", context)


@crm_role_required("owner", "manager")
def outreach_campaign_settings(request, uid):
    campaign = get_object_or_404(Campaign, uid=uid)
    if request.method == "POST":
        campaign.name = request.POST.get("name", campaign.name)
        campaign.description = request.POST.get("description", campaign.description)
        campaign.daily_limit = int(request.POST.get("daily_limit", campaign.daily_limit))
        campaign.min_interval = int(request.POST.get("min_interval", campaign.min_interval))
        campaign.max_interval = int(request.POST.get("max_interval", campaign.max_interval))
        campaign.save(update_fields=["name", "description", "daily_limit", "min_interval", "max_interval", "updated_at"])
        messages.success(request, "Campaign settings updated.")
        return redirect("crm:outreach_campaign_settings", uid=campaign.uid)

    context = {"campaign": campaign, **_outreach_nav(request)}
    return render(request, "crm/outreach/campaign_settings.html", context)


@crm_role_required("owner", "manager")
def outreach_campaign_send(request, uid):
    """Send campaign in a background thread (no RQ worker dependency)."""
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=400)
    campaign = get_object_or_404(Campaign, uid=uid)

    if campaign.status not in ("draft", "scheduled", "paused", "completed", "failed"):
        return JsonResponse({"ok": False, "error": f"Campaign status is '{campaign.status}', cannot send"}, status=400)

    # Materialize CampaignLead rows if missing (e.g. legacy campaigns)
    existing = campaign.leads.count()
    if existing == 0 and campaign.audience_data:
        lead_ids = campaign.audience_data
        if isinstance(lead_ids, list) and lead_ids:
            leads = Lead.objects.filter(id__in=lead_ids)
            cl_batch = [
                CampaignLead(campaign=campaign, lead=lead, email_snapshot=lead.email or "", status="pending")
                for lead in leads
            ]
            CampaignLead.objects.bulk_create(cl_batch)
            campaign.total_recipients = len(cl_batch)
            campaign.save(update_fields=["total_recipients", "updated_at"])

    campaign.status = "sending"
    campaign.started_at = None
    campaign.completed_at = None
    campaign.sent_count = 0
    campaign.failed_count = 0
    campaign.save(update_fields=["status", "started_at", "completed_at", "sent_count", "failed_count", "updated_at"])

    # Reset all CampaignLead statuses to pending for re-send
    campaign.leads.exclude(status="pending").update(status="pending")

    from .rq import send_campaign_emails
    import threading

    # Try RQ first (works if a worker is running)
    rq_queued = False
    try:
        from django_rq import get_queue
        queue = get_queue("email")
        job = queue.enqueue(send_campaign_emails, campaign.id)
        campaign.rq_job_id = job.id
        campaign.save(update_fields=["rq_job_id", "updated_at"])
        rq_queued = True
    except Exception:
        pass

    # Always launch a background thread as fallback/safety net
    def _run():
        from django.db import close_old_connections
        try:
            send_campaign_emails(campaign.id)
        except Exception:
            pass
        finally:
            close_old_connections()

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    return JsonResponse({"ok": True, "status": "queued", "rq": rq_queued})


@crm_role_required("owner", "manager")
def outreach_campaign_pause(request, uid):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=400)
    campaign = get_object_or_404(Campaign, uid=uid, status="sending")
    campaign.status = "paused"
    campaign.save(update_fields=["status", "updated_at"])
    return JsonResponse({"ok": True, "status": "paused"})


@crm_role_required("owner", "manager")
def outreach_campaign_archive(request, uid):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=400)
    campaign = get_object_or_404(Campaign, uid=uid)
    if campaign.status in ("draft", "completed", "failed", "paused"):
        campaign.status = "archived"
        campaign.save(update_fields=["status", "updated_at"])
        return JsonResponse({"ok": True, "status": "archived"})
    return JsonResponse({"ok": False, "error": "Cannot archive a sending campaign"}, status=400)


# ============================================================
# CREATE CAMPAIGN WIZARD (session-based draft)
# ============================================================
@crm_role_required("owner", "manager")
def outreach_campaign_create(request):
    user = request.user
    draft_key = f"campaign_draft_{user.id}"
    draft = request.session.get(draft_key, {})

    if request.method == "POST":
        step = request.POST.get("step", "0")
        action = request.POST.get("action", "continue")

        # Save current step data
        if step == "0":
            draft.update({
                "name": request.POST.get("campaign_name", ""),
                "description": request.POST.get("description", ""),
                "campaign_type": request.POST.get("campaign_type", "one_time"),
            })
        elif step == "1":
            selected_leads = request.POST.getlist("selected_leads")
            draft.update({
                "audience_source": request.POST.get("audience_source", "segment"),
                "stage": request.POST.get("stage", ""),
                "bucket": request.POST.get("bucket", ""),
                "source": request.POST.get("source", ""),
                "search": request.POST.get("search", ""),
                "selected_leads": selected_leads,
            })
        elif step == "2":
            draft["template_uid"] = request.POST.get("template_uid", "")
        elif step == "3":
            fields = []
            if request.POST.get("var_first_name") == "1": fields.append("first_name")
            if request.POST.get("var_company") == "1": fields.append("company")
            if request.POST.get("var_industry") == "1": fields.append("industry")
            if request.POST.get("var_website") == "1": fields.append("website")
            draft.update({
                "ai_personalization": request.POST.get("ai_personalization") == "1",
                "ai_instruction": request.POST.get("ai_instruction", ""),
                "ai_tone": request.POST.get("ai_tone", "friendly"),
                "personalization_fields": fields,
            })
        elif step == "4":
            schedule_date = request.POST.get("schedule_date", "")
            schedule_time = request.POST.get("schedule_time", "")
            scheduled_at = ""
            if schedule_date and schedule_time:
                scheduled_at = f"{schedule_date} {schedule_time}"
            draft.update({
                "sending_account": request.POST.get("sending_account", ""),
                "reply_to": request.POST.get("reply_to", ""),
                "daily_limit": int(request.POST.get("daily_limit", 50)),
                "min_interval": int(request.POST.get("min_interval", 2)),
                "max_interval": int(request.POST.get("max_interval", 5)),
                "schedule_type": request.POST.get("schedule_type", "now"),
                "scheduled_at": scheduled_at,
                "options": json.dumps({
                    "stop_on_reply": request.POST.get("stop_on_reply") == "1",
                    "skip_bounced": request.POST.get("skip_bounced") == "1",
                    "skip_contacted": request.POST.get("skip_contacted") == "1",
                }),
            })

        request.session[draft_key] = draft
        request.session.modified = True

        if action == "filter_leads":
            qs = internal_queryset(Lead.objects.select_related("company"))
            stage = request.POST.get("stage", "").strip()
            if stage:
                qs = qs.filter(stage_id=stage)
            source = request.POST.get("source", "").strip()
            if source:
                qs = qs.filter(source=source)
            search = request.POST.get("search", "").strip()
            if search:
                qs = qs.filter(Q(name__icontains=search) | Q(email__icontains=search))
            bucket = request.POST.get("bucket", "").strip()
            if bucket:
                if bucket == "hot":
                    qs = qs.filter(score__gte=80)
                elif bucket == "warm":
                    qs = qs.filter(score__gte=40, score__lt=80)
                elif bucket == "cold":
                    qs = qs.filter(score__lt=40)
            leads_data = []
            for l in qs[:200]:
                leads_data.append({
                    "id": l.id,
                    "name": l.name,
                    "email": l.email,
                    "company": l.company.name if l.company else "",
                })
            return JsonResponse({"leads": leads_data, "total": qs.count()})

        if action == "save_audience":
            # Save selected leads without advancing step
            lead_ids = request.POST.get("lead_ids", "[]")
            try:
                draft["selected_leads"] = json.loads(lead_ids)
            except json.JSONDecodeError:
                draft["selected_leads"] = []
            request.session[draft_key] = draft
            request.session.modified = True
            return JsonResponse({"ok": True})

        if action == "save_draft":
            campaign = _create_campaign_from_draft(draft, user)
            del request.session[draft_key]
            request.session.modified = True
            messages.success(request, f"Campaign '{campaign.name}' saved as draft.")
            return JsonResponse({"ok": True, "redirect": reverse("crm:outreach_campaign_detail", kwargs={"uid": campaign.uid})})

        if action == "finish":
            campaign = _create_campaign_from_draft(draft, user)
            del request.session[draft_key]
            request.session.modified = True
            if campaign.scheduled_at:
                messages.success(request, f"Campaign '{campaign.name}' scheduled.")
            else:
                messages.success(request, f"Campaign '{campaign.name}' created. Send when ready.")
            return redirect("crm:outreach_campaign_detail", uid=campaign.uid)

        if action == "submit":
            campaign = _create_campaign_from_draft(draft, user)
            del request.session[draft_key]
            request.session.modified = True
            campaign.status = "sending"
            campaign.save(update_fields=["status", "updated_at"])
            from .rq import send_campaign_emails
            import threading, django_rq
            rq_ok = False
            try:
                queue = django_rq.get_queue("email")
                job = queue.enqueue(send_campaign_emails, campaign.id)
                campaign.rq_job_id = job.id
                campaign.save(update_fields=["rq_job_id", "updated_at"])
                rq_ok = True
            except Exception:
                pass
            def _run():
                from django.db import close_old_connections
                try:
                    send_campaign_emails(campaign.id)
                except Exception:
                    pass
                finally:
                    close_old_connections()
            t = threading.Thread(target=_run, daemon=True)
            t.start()
            messages.success(request, f"Campaign '{campaign.name}' queued for sending.")
            return JsonResponse({"ok": True, "redirect": reverse("crm:outreach_campaign_detail", kwargs={"uid": campaign.uid})})

        # Continue to next step
        next_step = int(step) + 1
        return JsonResponse({"ok": True, "next_step": next_step})

    context = {
        "draft": draft,
        "leads": internal_queryset(Lead.objects.select_related("company", "stage"))[:200],
        "templates": EmailTemplate.objects.filter(tenant__isnull=True, is_active=True),
        "accounts": EmailAccount.objects.filter(tenant__isnull=True, is_active=True),
        "stages": PipelineStage.objects.filter(tenant__isnull=True).order_by("order"),
        **_outreach_nav(request),
    }
    return render(request, "crm/outreach/campaign_create.html", context)


def _create_campaign_from_draft(draft, user):
    """Create (or update) a Campaign from the session draft dict."""
    template = None
    if draft.get("template_uid"):
        try:
            template = EmailTemplate.objects.get(uid=draft["template_uid"])
        except EmailTemplate.DoesNotExist:
            pass

    account = None
    if draft.get("sending_account"):
        try:
            account = EmailAccount.objects.get(uid=draft["sending_account"])
        except EmailAccount.DoesNotExist:
            pass

    scheduled_at = None
    if draft.get("schedule_type") == "schedule" and draft.get("scheduled_at"):
        from django.utils.dateparse import parse_datetime
        scheduled_at = parse_datetime(draft["scheduled_at"])

    options = {}
    if draft.get("options"):
        try:
            options = json.loads(draft["options"]) if isinstance(draft["options"], str) else draft["options"]
        except (json.JSONDecodeError, TypeError):
            options = {}

    lead_ids = draft.get("selected_leads", [])
    if isinstance(lead_ids, str):
        try:
            lead_ids = json.loads(lead_ids)
        except (json.JSONDecodeError, TypeError):
            lead_ids = []

    campaign = Campaign.objects.create(
        name=draft.get("name", "Untitled Campaign"),
        description=draft.get("description", ""),
        campaign_type=draft.get("campaign_type", "one_time"),
        status="draft",
        template=template,
        sending_account=account,
        reply_to=draft.get("reply_to", ""),
        daily_limit=int(draft.get("daily_limit", 50)),
        min_interval=int(draft.get("min_interval", 2)),
        max_interval=int(draft.get("max_interval", 5)),
        scheduled_at=scheduled_at,
        ai_personalization=draft.get("ai_personalization", False),
        ai_instruction=draft.get("ai_instruction", ""),
        ai_tone=draft.get("ai_tone", "friendly"),
        personalization_fields=draft.get("personalization_fields", []),
        audience_type=draft.get("audience_source", "manual"),
        audience_data=lead_ids,
        options=options,
        created_by=user,
        tenant=None,
    )

    if lead_ids:
        leads = Lead.objects.filter(id__in=lead_ids)
        campaign_leads = [
            CampaignLead(
                campaign=campaign,
                lead=lead,
                email_snapshot=lead.email or "",
                status="pending",
            )
            for lead in leads
        ]
        CampaignLead.objects.bulk_create(campaign_leads)
        campaign.total_recipients = len(campaign_leads)
        campaign.save(update_fields=["total_recipients", "updated_at"])

    return campaign


# ============================================================
# TEMPLATES
# ============================================================
@crm_role_required("owner", "manager")
def outreach_templates(request):
    templates = internal_queryset(EmailTemplate.objects.all())
    category = request.GET.get("category")
    search = request.GET.get("q", "").strip()
    if category and category != "all":
        templates = templates.filter(category=category)
    if search:
        templates = templates.filter(Q(name__icontains=search) | Q(subject__icontains=search))
    context = {
        "templates": templates,
        "category_filter": category,
        **_outreach_nav(request),
    }
    return render(request, "crm/outreach/template_list.html", context)


@crm_role_required("owner", "manager")
def outreach_template_new(request):
    if request.method == "POST":
        template = EmailTemplate.objects.create(
            name=request.POST["name"],
            subject=request.POST["subject"],
            body_html=request.POST.get("body_html", ""),
            body_text=request.POST.get("body_text", ""),
            category=request.POST.get("category", "cold"),
            is_active=True,
            created_by=request.user,
            tenant=None,
        )
        messages.success(request, f"Template '{template.name}' created.")
        return redirect("crm:outreach_templates")

    context = {**_outreach_nav(request)}
    return render(request, "crm/outreach/template_editor.html", context)


@crm_role_required("owner", "manager")
def outreach_template_edit(request, uid):
    template = get_object_or_404(EmailTemplate, uid=uid)
    if request.method == "POST":
        template.name = request.POST.get("name", template.name)
        template.subject = request.POST.get("subject", template.subject)
        template.body_html = request.POST.get("body_html", template.body_html)
        template.body_text = request.POST.get("body_text", template.body_text)
        template.category = request.POST.get("category", template.category)
        template.save(update_fields=["name", "subject", "body_html", "body_text", "category", "updated_at"])
        messages.success(request, "Template updated.")
        return redirect("crm:outreach_templates")

    context = {"template": template, **_outreach_nav(request)}
    return render(request, "crm/outreach/template_editor.html", context)


@crm_role_required("owner", "manager")
@require_POST
def outreach_template_delete(request, uid):
    template = get_object_or_404(EmailTemplate, uid=uid)
    if template.is_system:
        return JsonResponse({"ok": False, "error": "System templates cannot be deleted"}, status=400)
    template.delete()
    messages.success(request, "Template deleted.")
    return redirect("crm:outreach_templates")


@crm_role_required("owner", "manager")
def outreach_template_preview(request, uid):
    """AJAX: render template with sample lead data."""
    template = get_object_or_404(EmailTemplate, uid=uid)
    sample_lead = internal_queryset(Lead.objects.select_related("stage", "company")).first()
    if sample_lead:
        ctx = build_email_context(sample_lead, {"sender": request.user})
        subject, body_html, body_text = render_email(template, ctx)
    else:
        subject = token_replace(template.subject, {"lead": {"name": "Rahim", "email": "rahim@email.com"}})
        body_html = token_replace(template.body_html, {"lead": {"name": "Rahim", "email": "rahim@email.com"}})
    return JsonResponse({"subject": subject, "body_html": body_html})


# ============================================================
# EMAIL ACCOUNTS
# ============================================================
@crm_role_required("owner", "manager")
def outreach_accounts(request):
    accounts = internal_queryset(EmailAccount.objects.all())
    context = {"accounts": accounts, **_outreach_nav(request)}
    return render(request, "crm/outreach/email_accounts.html", context)


@crm_role_required("owner", "manager")
def outreach_account_connect(request):
    if request.method == "POST":
        account = EmailAccount.objects.create(
            email=request.POST["email"],
            provider=request.POST.get("provider", "smtp"),
            smtp_host=request.POST.get("smtp_host", ""),
            smtp_port=int(request.POST.get("smtp_port", 587)),
            smtp_user=request.POST.get("smtp_user", ""),
            smtp_password=request.POST.get("smtp_password", ""),
            use_tls=request.POST.get("use_tls") == "on",
            daily_limit=int(request.POST.get("daily_limit", 50)),
            is_active=True,
            created_by=request.user,
            tenant=None,
        )
        messages.success(request, f"Account {account.email} connected.")
        return redirect("crm:outreach_accounts")

    context = {**_outreach_nav(request)}
    return render(request, "crm/outreach/connect_account.html", context)


@crm_role_required("owner", "manager")
def outreach_account_edit(request, uid):
    account = get_object_or_404(EmailAccount, uid=uid)
    if request.method == "POST":
        account.email = request.POST.get("email", account.email)
        account.smtp_host = request.POST.get("smtp_host", account.smtp_host)
        account.smtp_port = int(request.POST.get("smtp_port", account.smtp_port))
        account.smtp_user = request.POST.get("smtp_user", account.smtp_user)
        if request.POST.get("smtp_password"):
            account.smtp_password = request.POST["smtp_password"]
        account.daily_limit = int(request.POST.get("daily_limit", account.daily_limit))
        account.save(update_fields=["email", "smtp_host", "smtp_port", "smtp_user", "smtp_password", "daily_limit", "updated_at"])
        messages.success(request, "Account updated.")
        return redirect("crm:outreach_accounts")

    context = {"account": account, **_outreach_nav(request)}
    return render(request, "crm/outreach/connect_account.html", context)


@crm_role_required("owner", "manager")
@require_POST
def outreach_account_delete(request, uid):
    account = get_object_or_404(EmailAccount, uid=uid)
    account.delete()
    messages.success(request, "Account removed.")
    return redirect("crm:outreach_accounts")


@crm_role_required("owner", "manager")
def outreach_account_test(request, uid):
    """Test SMTP connection for an account."""
    account = get_object_or_404(EmailAccount, uid=uid)
    import smtplib
    try:
        if account.use_tls:
            server = smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=15)
            server.starttls()
        else:
            server = smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=15)
        server.login(account.smtp_user, account.smtp_password)
        server.quit()
        return JsonResponse({"ok": True, "message": "Connection successful"})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


# ============================================================
# ANALYTICS
# ============================================================
@crm_role_required("owner", "manager")
def outreach_analytics(request):
    context = {**_outreach_nav(request)}
    return render(request, "crm/outreach/analytics.html", context)


@crm_role_required("owner", "manager")
def outreach_analytics_data(request):
    """JSON endpoint for chart data."""
    from datetime import timedelta
    days = int(request.GET.get("days", 30))
    since = timezone.now() - timedelta(days=days)
    logs = EmailLog.objects.filter(created_at__gte=since)
    campaigns = internal_queryset(Campaign.objects.filter(created_at__gte=since))
    return JsonResponse({
        "total_sent": logs.filter(status="sent").count(),
        "total_failed": logs.filter(status="failed").count(),
        "total_opened": logs.filter(status="opened").count(),
        "total_bounced": logs.filter(status="bounced").count(),
        "campaign_count": campaigns.count(),
        "campaigns": list(campaigns.values("name", "sent_count", "failed_count", "status")),
    })


# ============================================================
# ACTIVITY
# ============================================================
@crm_role_required("owner", "manager")
def outreach_activity(request):
    user = request.user
    logs = EmailLog.objects.select_related("lead", "template").order_by("-created_at")[:_OUTREACH_PER_PAGE * 2]
    context = {"logs": logs, **_outreach_nav(request)}
    return render(request, "crm/outreach/activity.html", context)


# ============================================================
# BATCH DETAIL & POLL
# ============================================================
@crm_role_required("owner", "manager")
def outreach_batch_detail(request, uid):
    batch = get_object_or_404(EmailBatch, uid=uid)
    logs = batch.logs.select_related("lead").order_by("-created_at")
    status_f = request.GET.get("status")
    if status_f:
        logs = logs.filter(status=status_f)
    paginator = Paginator(logs, 50)
    page_obj = paginator.get_page(request.GET.get("page", 1))
    context = {
        "batch": batch,
        "page_obj": page_obj,
        "status_filter": status_f or "",
        **_outreach_nav(request),
    }
    return render(request, "crm/outreach/batch_detail.html", context)


@crm_role_required("owner", "manager")
def outreach_batch_poll(request, uid):
    batch = get_object_or_404(EmailBatch, uid=uid)
    logs = batch.logs.all()
    return JsonResponse({
        "status": batch.status,
        "total": batch.total_recipients,
        "sent": logs.filter(status="sent").count(),
        "failed": logs.filter(status="failed").count(),
        "queued": logs.filter(status="queued").count(),
        "completed_at": batch.completed_at.isoformat() if batch.completed_at else None,
    })


# ============================================================
# EXPORT
# ============================================================
@crm_role_required("owner", "manager")
@require_POST
def outreach_export(request):
    """Export campaign leads as CSV/HTML."""
    campaign_uid = request.POST.get("campaign_uid")
    campaign = get_object_or_404(Campaign, uid=campaign_uid)
    leads_qs = campaign.leads.select_related("lead").filter(lead__email__gt="")

    fmt = request.POST.get("format", "csv")
    if fmt == "html":
        rows = []
        for cl in leads_qs:
            ctx = build_email_context(cl.lead, {"sender": request.user})
            subject, body_html, body_text = render_email(campaign.template, ctx)
            rows.append({
                "name": cl.lead.name,
                "email": cl.lead.email,
                "subject": subject,
                "html": body_html,
                "text": body_text,
            })
        html = render_to_string("crm/outreach/export.html", {"rows": rows, "campaign": campaign})
        resp = HttpResponse(html, content_type="text/html")
        resp["Content-Disposition"] = f'attachment; filename="campaign_export_{timezone.now().strftime("%Y%m%d")}.html"'
        return resp

    import csv
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="campaign_export_{timezone.now().strftime("%Y%m%d")}.csv"'
    writer = csv.writer(resp)
    writer.writerow(["Name", "Email", "Subject", "HTML Body", "Text Body"])
    for cl in leads_qs:
        ctx = build_email_context(cl.lead, {"sender": request.user})
        subject, body_html, body_text = render_email(campaign.template, ctx)
        writer.writerow([cl.lead.name, cl.lead.email, subject, body_html, body_text])
    return resp


# ============================================================
# LEGACY COLD-MAIL REDIRECTS
# ============================================================
def legacy_cold_mail_redirect(request):
    return redirect("crm:outreach_overview")


def legacy_cold_mail_select_redirect(request):
    return redirect("crm:outreach_campaigns")


def legacy_cold_mail_batch_redirect(request, uid):
    return redirect("crm:outreach_batch_detail", uid=uid)


def legacy_cold_mail_batch_poll_redirect(request, uid):
    return redirect("crm:outreach_batch_poll", uid=uid)