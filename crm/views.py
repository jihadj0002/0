from django.db.models import Count, Sum, Q
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from datetime import timedelta
from django.contrib.auth import logout as auth_logout
from django.http import JsonResponse, HttpResponse
from django.templatetags.static import static
from django.views.decorators.http import require_POST, require_GET
from django.core.paginator import Paginator
from django.contrib import messages
import json
import re

from .permissions import staff_required, crm_role_required
from .scoring import recompute_score, breakdown_items
from .services import (
    lead_queryset_for, get_role, can_manage, create_lead, update_lead,
    add_note, convert_lead, complete_followup, log_activity, notify,
)
from .models import (
    Lead, Activity, Followup, Meeting, Task, CallLog, Company, Customer,
    PipelineStage, SalesScript, FAQ, Notification, StaffProfile,
    LearningTopic, LearningArticle,
)


def _stage_map():
    return {s.pk: s for s in PipelineStage.objects.filter(tenant__isnull=True)}


# ============================================================
# DASHBOARD
# ============================================================
@staff_required
def dashboard(request):
    user = request.user
    now = timezone.now()
    leads = lead_queryset_for(user)

    total_leads = leads.count()
    hot_leads = leads.filter(score__gte=70).exclude(stage__is_won=True).exclude(stage__is_lost=True).count()
    won = leads.filter(stage__is_won=True).count()
    lost = leads.filter(stage__is_lost=True).count()
    open_leads = total_leads - won - lost

    todays_followups = Followup.objects.filter(due__date=now.date(), done=False).select_related("lead").order_by("due")[:10]
    overdue_followups = Followup.objects.filter(due__lt=now, done=False).select_related("lead").order_by("due")[:10]
    upcoming_meetings = Meeting.objects.filter(datetime__gte=now, status="scheduled").select_related("lead").order_by("datetime")[:8]
    recent_activities = Activity.objects.filter(lead__in=leads).select_related("lead", "created_by").order_by("-timestamp")[:12]

    pipeline_counts = [
        {"stage": s, "count": leads.filter(stage=s).count()}
        for s in PipelineStage.objects.filter(tenant__isnull=True).order_by("order")
    ]
    unread_notifications = Notification.objects.filter(user=user, read=False).count()

    context = {
        "total_leads": total_leads, "open_leads": open_leads, "hot_leads": hot_leads,
        "won": won, "lost": lost, "todays_followups": todays_followups,
        "overdue_followups": overdue_followups, "upcoming_meetings": upcoming_meetings,
        "recent_activities": recent_activities, "pipeline_counts": pipeline_counts,
        "unread_notifications": unread_notifications, "role": get_role(user),
    }
    return render(request, "crm/dashboard.html", context)


def logout(request):
    auth_logout(request)
    return redirect("/")


# ============================================================
# PWA (installable CRM app)
# ============================================================
_SW_SOURCE = """/* Matrix CRM service worker — network-first, no aggressive caching. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

self.addEventListener('fetch', (e) => {
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
"""


@staff_required
def pwa_manifest(request):
    icon = lambda name: request.build_absolute_uri(static(f"crm/pwa/{name}"))
    manifest = {
        "name": "Matrix CRM",
        "short_name": "CRM",
        "description": "MatrixAI sales CRM — leads, pipeline and follow-ups.",
        "start_url": "/crm/",
        "scope": "/crm/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#f6f9fd",
        "theme_color": "#2563eb",
        "icons": [
            {"src": icon("icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": icon("icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
            {"src": icon("apple-touch-icon-180.png"), "sizes": "180x180", "type": "image/png"},
        ],
    }
    return JsonResponse(manifest, content_type="application/manifest+json")


@staff_required
def pwa_sw(request):
    return HttpResponse(_SW_SOURCE, content_type="application/javascript")


# ============================================================
# LEADS
# ============================================================
@staff_required
def leads(request):
    user = request.user
    qs = lead_queryset_for(user).select_related("stage", "assigned_to", "company")

    q = request.GET.get("q", "").strip()
    stage = request.GET.get("stage", "")
    source = request.GET.get("source", "")
    assigned = request.GET.get("assigned", "")
    bucket = request.GET.get("bucket", "")
    sort = request.GET.get("sort", "-updated_at")

    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=q) | Q(email__icontains=q) | Q(company__name__icontains=q))
    if stage:
        qs = qs.filter(stage_id=stage)
    if source:
        qs = qs.filter(source=source)
    if assigned == "me":
        qs = qs.filter(assigned_to=user)
    elif assigned == "unassigned":
        qs = qs.filter(assigned_to__isnull=True)
    elif assigned == "all" and can_manage(user):
        pass
    elif assigned.isdigit() and can_manage(user):
        qs = qs.filter(assigned_to_id=assigned)
    if bucket:
        won_stages = PipelineStage.objects.filter(is_won=True).values_list("id", flat=True)
        lost_stages = PipelineStage.objects.filter(is_lost=True).values_list("id", flat=True)
        if bucket == "hot":
            qs = qs.filter(score__gte=70).exclude(stage_id__in=list(won_stages) + list(lost_stages))
        elif bucket == "warm":
            qs = qs.filter(score__gte=40, score__lt=70).exclude(stage_id__in=list(won_stages) + list(lost_stages))
        elif bucket == "cold":
            qs = qs.filter(score__lt=40).exclude(stage_id__in=list(won_stages) + list(lost_stages))
        elif bucket == "won":
            qs = qs.filter(stage__is_won=True)
        elif bucket == "lost":
            qs = qs.filter(stage__is_lost=True)
        elif bucket == "open":
            qs = qs.exclude(stage__is_won=True).exclude(stage__is_lost=True)

    if sort in {"name", "-name", "score", "-score", "created_at", "-created_at",
                "updated_at", "-updated_at", "next_followup", "-next_followup"}:
        qs = qs.order_by(sort)

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page", 1))

    context = {
        "page_obj": page,
        "leads": page.object_list,
        "stages": PipelineStage.objects.filter(tenant__isnull=True).order_by("order"),
        "staff_users": StaffProfile.objects.filter(is_active=True).select_related("user"),
        "lead_sources": Lead.SOURCE_CHOICES,
        "total_count": paginator.count,
        "filters": {"q": q, "stage": stage, "source": source, "assigned": assigned, "bucket": bucket, "sort": sort},
        "qs_params": "&".join(f"{k}={v}" for k, v in
                              [("q", q), ("stage", stage), ("source", source),
                               ("assigned", assigned), ("bucket", bucket)] if v),
        "role": get_role(user),
    }
    return render(request, "crm/leads.html", context)


@staff_required
def lead_new(request):
    user = request.user
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if not name:
            messages.error(request, "Name is required.")
        else:
            stage = PipelineStage.objects.filter(pk=request.POST.get("stage")).first() if request.POST.get("stage") else None
            assigned = User_or_None(request.POST.get("assigned_to"))
            if assigned and assigned.pk != user.pk and not can_manage(user):
                assigned = user
            company = Company.objects.filter(pk=request.POST.get("company")).first() if request.POST.get("company") else None
            lead, created = create_lead(
                user, name=name, phone=request.POST.get("phone", ""),
                email=request.POST.get("email", ""), source=request.POST.get("source", "manual"),
                stage=stage, assigned_to=assigned, company=company,
                website=request.POST.get("website", ""), industry=request.POST.get("industry", ""),
                notes=request.POST.get("notes", ""), budget=request.POST.get("budget") or None,
                expected_value=request.POST.get("expected_value") or None,
                next_followup=request.POST.get("next_followup") or None,
                tags=[t.strip() for t in request.POST.get("tags", "").split(",") if t.strip()],
            )
            if created:
                messages.success(request, f"Lead '{lead.name}' created.")
            else:
                messages.warning(request, f"Duplicate lead found — '{lead.name}' already exists.")
            return redirect("crm:lead_detail", pk=lead.pk)
    context = {
        "stages": PipelineStage.objects.filter(tenant__isnull=True).order_by("order"),
        "companies": Company.objects.filter(tenant__isnull=True).order_by("name"),
        "staff_users": StaffProfile.objects.filter(is_active=True).select_related("user"),
        "lead_sources": Lead.SOURCE_CHOICES,
        "role": get_role(user),
    }
    return render(request, "crm/lead_form.html", context)


def User_or_None(pk):
    from django.contrib.auth.models import User
    if not pk:
        return None
    try:
        return User.objects.get(pk=pk)
    except User.DoesNotExist:
        return None


@staff_required
def lead_detail(request, pk):
    user = request.user
    lead = get_object_or_404(lead_queryset_for(user).select_related("stage", "assigned_to", "company"), pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update":
            fields = {}
            if can_manage(user) or lead.assigned_to_id in (user.id, None):
                if "stage" in request.POST and request.POST.get("stage"):
                    fields["stage"] = PipelineStage.objects.filter(pk=request.POST.get("stage")).first()
                if "assigned_to" in request.POST:
                    new_assignee = User_or_None(request.POST.get("assigned_to"))
                    if can_manage(user) or new_assignee in (None, user):
                        fields["assigned_to"] = new_assignee
                if "next_followup" in request.POST:
                    fields["next_followup"] = request.POST.get("next_followup") or None
                if "budget" in request.POST:
                    fields["budget"] = request.POST.get("budget") or None
                if "expected_value" in request.POST:
                    fields["expected_value"] = request.POST.get("expected_value") or None
                if "source" in request.POST:
                    fields["source"] = request.POST.get("source")
                if "notes" in request.POST:
                    fields["notes"] = request.POST.get("notes", "")
            if fields:
                update_lead(user, lead, **fields)
            messages.success(request, "Lead updated.")
            return redirect("crm:lead_detail", pk=lead.pk)
        elif action == "note":
            add_note(user, lead, request.POST.get("note", ""))
            messages.success(request, "Note added.")
            return redirect("crm:lead_detail", pk=lead.pk)
        elif action == "convert":
            if not lead.converted:
                won_stage = PipelineStage.objects.filter(is_won=True, tenant__isnull=True).order_by("order").first()
                if won_stage and lead.stage_id != won_stage.pk:
                    lead.stage = won_stage
                    lead.save(update_fields=["stage", "updated_at"])
                    log_activity(lead, "status_change", f"Stage changed to {won_stage.name}", user)
            customer = convert_lead(
                user, lead, package=request.POST.get("package", ""),
                monthly_value=request.POST.get("monthly_value") or None,
                renewal=request.POST.get("renewal") or None,
                owner=User_or_None(request.POST.get("owner") or request.user.pk),
            )
            messages.success(request, f"Deal won! Customer record created.")
            return redirect("crm:customer_detail", pk=customer.pk)

    activities = lead.activities.select_related("created_by")[:50]
    context = {
        "lead": lead,
        "activities": activities,
        "stages": PipelineStage.objects.filter(tenant__isnull=True).order_by("order"),
        "staff_users": StaffProfile.objects.filter(is_active=True).select_related("user"),
        "lead_sources": Lead.SOURCE_CHOICES,
        "role": get_role(user),
        "can_edit": can_manage(user) or lead.assigned_to_id == user.id,
        "score_parts": breakdown_items(lead.score_breakdown),
    }
    return render(request, "crm/lead_detail.html", context)


@staff_required
def lead_edit(request, pk):
    user = request.user
    lead = get_object_or_404(lead_queryset_for(user), pk=pk)
    if not (can_manage(user) or lead.assigned_to_id == user.id):
        messages.error(request, "You can only edit your own leads.")
        return redirect("crm:lead_detail", pk=lead.pk)
    if request.method == "POST":
        fields = {
            "name": request.POST.get("name", lead.name),
            "phone": request.POST.get("phone", ""),
            "email": request.POST.get("email", ""),
            "website": request.POST.get("website", ""),
            "industry": request.POST.get("industry", ""),
            "notes": request.POST.get("notes", ""),
            "source": request.POST.get("source", lead.source),
            "budget": request.POST.get("budget") or None,
            "expected_value": request.POST.get("expected_value") or None,
            "company": Company.objects.filter(pk=request.POST.get("company")).first() if request.POST.get("company") else None,
        }
        update_lead(user, lead, **fields)
        messages.success(request, "Lead updated.")
        return redirect("crm:lead_detail", pk=lead.pk)
    context = {
        "lead": lead,
        "companies": Company.objects.filter(tenant__isnull=True).order_by("name"),
        "lead_sources": Lead.SOURCE_CHOICES,
        "role": get_role(user),
    }
    return render(request, "crm/lead_form.html", context)


@staff_required
@require_POST
def lead_delete(request, pk):
    user = request.user
    lead = get_object_or_404(lead_queryset_for(user), pk=pk)
    if not can_manage(user):
        messages.error(request, "Only managers can delete leads.")
        return redirect("crm:lead_detail", pk=lead.pk)
    lead.delete()
    messages.success(request, "Lead deleted.")
    return redirect("crm:leads")


# ============================================================
# COMPANIES
# ============================================================
@staff_required
def companies(request):
    q = request.GET.get("q", "").strip()
    qs = Company.objects.filter(tenant__isnull=True)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(industry__icontains=q))
    paginator = Paginator(qs.order_by("name"), 25)
    page = paginator.get_page(request.GET.get("page", 1))
    context = {
        "page_obj": page,
        "companies": page.object_list,
        "q": q,
        "role": get_role(request.user),
    }
    return render(request, "crm/companies.html", context)


@staff_required
def company_detail(request, pk):
    company = get_object_or_404(Company.objects.filter(tenant__isnull=True), pk=pk)
    leads = company.leads.all()
    if request.method == "POST":
        company.name = request.POST.get("name", company.name)
        company.industry = request.POST.get("industry", "")
        company.website = request.POST.get("website", "")
        company.employees = request.POST.get("employees", "")
        company.address = request.POST.get("address", "")
        company.notes = request.POST.get("notes", "")
        company.save()
        messages.success(request, "Company updated.")
        return redirect("crm:company_detail", pk=company.pk)
    context = {"company": company, "leads": leads, "role": get_role(request.user)}
    return render(request, "crm/company_detail.html", context)


@staff_required
@require_POST
def company_new(request):
    name = request.POST.get("name", "").strip()
    if not name:
        messages.error(request, "Company name is required.")
    else:
        company = Company.objects.create(
            name=name, industry=request.POST.get("industry", ""),
            website=request.POST.get("website", ""),
            employees=request.POST.get("employees", ""),
            address=request.POST.get("address", ""),
            owner=request.user,
        )
        messages.success(request, f"Company '{company.name}' created.")
        return redirect("crm:company_detail", pk=company.pk)
    return redirect("crm:companies")


# ============================================================
# AJAX
# ============================================================
@staff_required
@require_GET
def ajax_search(request):
    q = request.GET.get("q", "").strip()
    results = []
    if q:
        leads_qs = lead_queryset_for(request.user).filter(
            Q(name__icontains=q) | Q(phone__icontains=q) | Q(email__icontains=q)
        )[:6]
        for l in leads_qs:
            results.append({"type": "Lead", "name": l.name, "sub": l.phone or l.email or l.source, "url": f"/crm/leads/{l.pk}/"})
        for c in Company.objects.filter(tenant__isnull=True, name__icontains=q)[:4]:
            results.append({"type": "Company", "name": c.name, "sub": c.industry or "", "url": f"/crm/companies/{c.pk}/"})
        for f in FAQ.objects.filter(tenant__isnull=True, active=True).filter(Q(question__icontains=q) | Q(answer__icontains=q))[:4]:
            results.append({"type": "FAQ", "name": f.question, "sub": f.category, "url": "/crm/faq/"})
    return JsonResponse({"results": results})


@staff_required
def ajax_notifications(request):
    user = request.user
    notes = Notification.objects.filter(user=user)[:20]
    return JsonResponse({"notifications": [
        {"message": n.message, "url": n.url or "#", "read": n.read,
         "time": n.created_at.strftime("%b %d, %H:%M")}
        for n in notes
    ]})


@staff_required
@require_POST
def ajax_notifications_mark_read(request):
    Notification.objects.filter(user=request.user, read=False).update(read=True)
    return JsonResponse({"ok": True})


@staff_required
@require_POST
def ajax_followup_done(request, pk):
    f = get_object_or_404(Followup, pk=pk)
    complete_followup(request.user, f, f.lead)
    return JsonResponse({"ok": True})


@staff_required
@require_POST
def ajax_kanban_move(request, pk):
    lead = get_object_or_404(lead_queryset_for(request.user), pk=pk)
    stage = get_object_or_404(PipelineStage, pk=request.POST.get("stage"), tenant__isnull=True)
    update_lead(request.user, lead, stage=stage)
    return JsonResponse({"ok": True, "won": lead.is_won(), "lost": lead.is_lost()})


@staff_required
@require_POST
def ajax_quick_update(request, pk):
    lead = get_object_or_404(lead_queryset_for(request.user), pk=pk)
    field = request.POST.get("field", "")
    value = request.POST.get("value")
    allowed = {"budget", "expected_value", "next_followup", "stage", "assigned_to", "notes"}
    if not (can_manage(request.user) or lead.assigned_to_id == request.user.id or lead.assigned_to_id is None):
        return JsonResponse({"ok": False, "error": "Not allowed"}, status=403)

    new_notes = request.POST.get("notes")
    if new_notes is not None:
        update_lead(request.user, lead, notes=new_notes.strip())

    new_stage = request.POST.get("stage")
    if new_stage:
        stage = get_object_or_404(PipelineStage, pk=new_stage, tenant__isnull=True)
        update_lead(request.user, lead, stage=stage)
        return JsonResponse({
            "ok": True, "stage_name": stage.name,
            "won": lead.is_won(), "lost": lead.is_lost(),
        })

    if not field:
        return JsonResponse({"ok": True})
    if field in allowed:
        if field == "stage":
            stage = get_object_or_404(PipelineStage, pk=value, tenant__isnull=True)
            update_lead(request.user, lead, stage=stage)
            return JsonResponse({
                "ok": True, "stage_name": stage.name,
                "won": lead.is_won(), "lost": lead.is_lost(),
            })
        if field == "assigned_to":
            if value == "me":
                if lead.assigned_to_id not in (None, request.user.id) and not can_manage(request.user):
                    return JsonResponse({"ok": False, "error": "Lead is already assigned"}, status=403)
                assignee = request.user
            else:
                if not can_manage(request.user):
                    return JsonResponse({"ok": False, "error": "Not allowed"}, status=403)
                assignee = User_or_None(value)
            update_lead(request.user, lead, assigned_to=assignee)
            name = None
            if assignee:
                name = assignee.get_full_name() or assignee.username
            return JsonResponse({"ok": True, "assignee": name})
        if field == "notes":
            update_lead(request.user, lead, notes=(value or "").strip())
        else:
            update_lead(request.user, lead, **{field: value or None})
        return JsonResponse({"ok": True})
    return JsonResponse({"ok": False, "error": "Not allowed"}, status=403)


@staff_required
@require_POST
def ajax_quick_create_lead(request):
    name = request.POST.get("name", "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required"}, status=400)
    lead, created = create_lead(
        request.user, name=name, phone=request.POST.get("phone", ""),
        email=request.POST.get("email", ""), source=request.POST.get("source", "manual"),
    )
    return JsonResponse({"ok": True, "created": created, "url": f"/crm/leads/{lead.pk}/"})


@crm_role_required("owner")
@require_POST
def ajax_analyze_lead_image(request):
    """Owner-only: vision LLM reads an uploaded image and returns structured leads."""
    from .ai_import import extract_leads_from_image

    data_url = request.POST.get("image", "").strip()
    if not data_url:
        return JsonResponse({"ok": False, "error": "No image received"}, status=400)
    if len(data_url) > 5 * 1024 * 1024:
        return JsonResponse({"ok": False, "error": "Image too large (max 5MB)"}, status=400)
    if not data_url.startswith("data:image/"):
        return JsonResponse({"ok": False, "error": "Unsupported image format"}, status=400)

    leads = extract_leads_from_image(data_url)
    if not leads:
        return JsonResponse({
            "ok": False,
            "error": "Couldn't read any lead details from this image. Try a clearer photo of a business card or lead sheet.",
        })
    return JsonResponse({"ok": True, "leads": leads})


@crm_role_required("owner")
@require_POST
def ajax_create_imported_leads(request):
    """Owner-only: store reviewed leads extracted from an image as unassigned leads."""
    from .ai_import import create_lead_from_dict, leads_from_payload

    try:
        payload = json.loads(request.POST.get("leads", "[]"))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Invalid lead data"}, status=400)

    entries = leads_from_payload(payload)
    if not entries:
        return JsonResponse({"ok": False, "error": "No valid leads to create"}, status=400)

    created_urls, created_count, duplicate_count = [], 0, 0
    for entry in entries:
        lead, created = create_lead_from_dict(request.user, entry)
        if created:
            created_count += 1
            created_urls.append(f"/crm/leads/{lead.pk}/")
        else:
            duplicate_count += 1
    return JsonResponse({
        "ok": True,
        "created": created_count,
        "duplicates": duplicate_count,
        "urls": created_urls,
    })


@staff_required
def ajax_lead_popup(request, pk):
    lead = get_object_or_404(
        lead_queryset_for(request.user).select_related("stage", "assigned_to", "company"),
        pk=pk,
    )
    recent_activities = lead.activities.select_related("created_by")[:4]
    can_edit = (
        can_manage(request.user)
        or lead.assigned_to_id == request.user.id
        or lead.assigned_to_id is None
    )
    stages = PipelineStage.objects.filter(tenant__isnull=True).order_by("order")
    return render(request, "crm/_lead_popup.html", {
        "lead": lead, "recent_activities": recent_activities,
        "stages": stages, "can_edit": can_edit,
        "score_parts": breakdown_items(lead.score_breakdown),
    })


@staff_required
@require_POST
def ajax_call_log(request):
    lead = get_object_or_404(lead_queryset_for(request.user), pk=request.POST.get("lead"))
    log = CallLog.objects.create(
        lead=lead, staff=request.user, duration=int(request.POST.get("duration", 0) or 0),
        outcome=request.POST.get("outcome", "no_answer"),
        summary=request.POST.get("summary", ""),
        next_followup=request.POST.get("next_followup") or None,
        recording=request.POST.get("recording", ""),
        tags=[t.strip() for t in request.POST.get("tags", "").split(",") if t.strip()],
    )
    lead.last_contact = timezone.now()
    lead.save(update_fields=["last_contact", "updated_at"])
    log_activity(lead, "call", f"Call logged ({log.get_outcome_display()})" + (f" — {log.summary[:120]}" if log.summary else ""), request.user,
                 duration=log.duration, outcome=log.outcome)
    if log.next_followup:
        Followup.objects.create(lead=lead, due=log.next_followup, kind="call", note="After call", created_by=request.user)
    recompute_score(lead, request.user)
    return JsonResponse({"ok": True})


@staff_required
@require_POST
def ajax_task_toggle(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if task.assigned_to_id not in (request.user.id, None) and not can_manage(request.user):
        return JsonResponse({"ok": False, "error": "Not allowed"}, status=403)
    task.status = "done" if task.status != "done" else "pending"
    task.save(update_fields=["status"])
    if task.lead_id:
        recompute_score(task.lead, request.user)
    return JsonResponse({"ok": True, "status": task.status})


@staff_required
@require_POST
def ajax_task_update(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if task.assigned_to_id not in (request.user.id, None) and task.created_by_id != request.user.id and not can_manage(request.user):
        return JsonResponse({"ok": False, "error": "Not allowed"}, status=403)
    status = request.POST.get("status")
    if status:
        if status not in dict(Task.STATUS_CHOICES):
            return JsonResponse({"ok": False, "error": "Invalid status"}, status=400)
        task.status = status
    title = request.POST.get("title", "").strip()
    if title:
        task.title = title
    priority = request.POST.get("priority")
    if priority in dict(Task.PRIORITY_CHOICES):
        task.priority = priority
    deadline = request.POST.get("deadline")
    if deadline is not None:
        task.deadline = parse_datetime(deadline) if deadline.strip() else None
    assigned_to = request.POST.get("assigned_to")
    if assigned_to is not None and can_manage(request.user):
        task.assigned_to = User_or_None(assigned_to)
    task.save()
    if task.lead_id:
        recompute_score(task.lead, request.user)
    return JsonResponse({"ok": True, "status": task.status})


@staff_required
@require_POST
def ajax_meeting_status(request, pk):
    meeting = get_object_or_404(Meeting, pk=pk)
    status = request.POST.get("status")
    if status not in ("scheduled", "completed", "cancelled", "no_show"):
        return JsonResponse({"ok": False, "error": "Invalid status"}, status=400)
    meeting.status = status
    if status == "completed":
        meeting.completed = True
        meeting.joined = True
    meeting.save()
    log_activity(meeting.lead, "demo", f"Meeting marked {status}", request.user, meeting_id=meeting.pk)
    recompute_score(meeting.lead, request.user)
    return JsonResponse({"ok": True})


@staff_required
@require_GET
def ajax_calendar_events(request):
    start = request.GET.get("start")
    end = request.GET.get("end")
    events = []
    for m in Meeting.objects.filter(datetime__range=(start, end)).select_related("lead", "staff"):
        events.append({
            "id": f"m{m.pk}", "title": f"Demo: {m.lead.name}", "start": m.datetime.isoformat(),
            "color": "#2563eb", "url": f"/crm/leads/{m.lead.pk}/",
        })
    for f in Followup.objects.filter(due__range=(start, end), done=False).select_related("lead"):
        events.append({
            "id": f"f{f.pk}", "title": f"Follow-up: {f.lead.name} ({f.get_kind_display()})",
            "start": f.due.isoformat(), "color": "#d97706", "url": f"/crm/leads/{f.lead.pk}/",
        })
    for t in Task.objects.filter(deadline__range=(start, end), status__in=("pending", "doing")).select_related("lead"):
        events.append({
            "id": f"t{t.pk}", "title": f"Task: {t.title}",
            "start": t.deadline.isoformat(), "color": "#16a34a", "url": f"/crm/tasks/",
        })
    return JsonResponse(events, safe=False)


@staff_required
@require_POST
def ajax_convert_customer(request, pk):
    user = request.user
    lead = get_object_or_404(lead_queryset_for(user), pk=pk)
    if not lead.converted:
        won_stage = PipelineStage.objects.filter(is_won=True, tenant__isnull=True).order_by("order").first()
        if won_stage and lead.stage_id != won_stage.pk:
            lead.stage = won_stage
            lead.save(update_fields=["stage", "updated_at"])
            log_activity(lead, "status_change", f"Stage changed to {won_stage.name}", user)
    customer = convert_lead(
        request.user, lead,
        package=request.POST.get("package", ""),
        monthly_value=request.POST.get("monthly_value") or None,
        renewal=request.POST.get("renewal") or None,
        owner=User_or_None(request.POST.get("owner") or request.user.pk),
    )
    return JsonResponse({"ok": True, "url": f"/crm/customers/{customer.pk}/"})


# ============================================================
# PIPELINE (KANBAN)
# ============================================================
@staff_required
def pipeline(request):
    user = request.user
    leads = lead_queryset_for(user).select_related("stage", "assigned_to", "company")
    stages = PipelineStage.objects.filter(tenant__isnull=True).order_by("order")
    columns = []
    for s in stages:
        columns.append({
            "stage": s,
            "leads": [l for l in leads if l.stage_id == s.pk],
            "total": leads.filter(stage=s).count(),
        })
    context = {
        "columns": columns,
        "role": get_role(user),
        "can_edit": True,
    }
    return render(request, "crm/pipeline.html", context)


# ============================================================
# CUSTOMERS
# ============================================================
@staff_required
def customers(request):
    user = request.user
    qs = Customer.objects.select_related("lead", "platform_user", "owner")
    if get_role(user) == "support":
        qs = qs.filter(lead__assigned_to=user) | qs.filter(owner=user)
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if q:
        qs = qs.filter(Q(lead__name__icontains=q) | Q(lead__phone__icontains=q) | Q(lead__email__icontains=q))
    if status:
        qs = qs.filter(status=status)
    paginator = Paginator(qs.distinct(), 25)
    page = paginator.get_page(request.GET.get("page", 1))
    context = {
        "page_obj": page,
        "customers": page.object_list,
        "q": q,
        "status": status,
        "role": get_role(user),
    }
    return render(request, "crm/customers.html", context)


@staff_required
def customer_detail(request, pk):
    customer = get_object_or_404(
        Customer.objects.select_related("lead", "platform_user", "owner"), pk=pk
    )
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update":
            customer.package = request.POST.get("package", customer.package)
            customer.monthly_value = request.POST.get("monthly_value") or None
            customer.renewal = request.POST.get("renewal") or None
            customer.status = request.POST.get("status", customer.status)
            customer.notes = request.POST.get("notes", customer.notes)
            customer.save()
            messages.success(request, "Customer updated.")
            return redirect("crm:customer_detail", pk=customer.pk)
        if action == "note":
            add_note(request.user, customer.lead, request.POST.get("note", ""))
            messages.success(request, "Note added.")
            return redirect("crm:customer_detail", pk=customer.pk)

    lead = customer.lead
    conversations = []
    integrations = []
    sales = []
    usage = None
    if customer.platform_user:
        from back.models import Conversation, Integration, Sale
        from billing.models import UsageSummary
        conversations = Conversation.objects.filter(user=customer.platform_user).order_by("-updated_at")[:20]
        integrations = Integration.objects.filter(user=customer.platform_user)
        sales = Sale.objects.filter(user=customer.platform_user).order_by("-created_at")[:20]
        usage = UsageSummary.objects.filter(user=customer.platform_user).order_by("-date")[:30]

    context = {
        "customer": customer,
        "lead": lead,
        "conversations": conversations,
        "integrations": integrations,
        "sales": sales,
        "usage": usage,
        "role": get_role(request.user),
    }
    return render(request, "crm/customer_detail.html", context)


# ============================================================
# CALLS
# ============================================================
@staff_required
def calls(request):
    qs = CallLog.objects.select_related("lead", "staff")
    outcome = request.GET.get("outcome", "")
    q = request.GET.get("q", "").strip()
    if outcome:
        qs = qs.filter(outcome=outcome)
    if q:
        qs = qs.filter(Q(lead__name__icontains=q) | Q(lead__phone__icontains=q))
    paginator = Paginator(qs.order_by("-created_at"), 25)
    page = paginator.get_page(request.GET.get("page", 1))
    context = {
        "page_obj": page,
        "calls": page.object_list,
        "outcome": outcome,
        "q": q,
        "call_outcomes": CallLog.OUTCOME_CHOICES,
        "leads_json": json.dumps([
            {"id": l.pk, "name": l.name, "phone": l.phone}
            for l in lead_queryset_for(request.user).select_related("stage")[:200]
        ]),
        "role": get_role(request.user),
    }
    return render(request, "crm/calls.html", context)


# ============================================================
# MEETINGS / DEMOS
# ============================================================
@staff_required
def demos(request):
    qs = Meeting.objects.select_related("lead", "staff")
    status = request.GET.get("status", "")
    if status:
        qs = qs.filter(status=status)
    if request.method == "POST":
        try:
            lead_pk = int(request.POST.get("lead") or 0)
        except (TypeError, ValueError):
            lead_pk = 0
        lead = get_object_or_404(lead_queryset_for(request.user), pk=lead_pk)
        raw_dt = request.POST.get("datetime") or ""
        meeting_dt = parse_datetime(raw_dt) if raw_dt.strip() else None
        if meeting_dt is None:
            return JsonResponse({"ok": False, "error": "Enter a valid date & time (YYYY-MM-DD HH:MM)"}, status=400)
        platform = request.POST.get("platform", "zoom")
        if platform not in dict(Meeting.PLATFORM_CHOICES):
            platform = "zoom"
        meeting = Meeting.objects.create(
            lead=lead, staff=request.user,
            datetime=meeting_dt,
            platform=platform,
            link=request.POST.get("link", ""),
            notes=request.POST.get("notes", ""),
        )
        log_activity(lead, "demo", f"Demo scheduled ({meeting.get_platform_display()}) {meeting.datetime:%b %d, %H:%M}",
                     request.user, meeting_id=meeting.pk)
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": True, "url": f"/crm/demos/#meeting-{meeting.pk}"})
        messages.success(request, "Meeting scheduled.")
        return redirect("crm:demos")
    paginator = Paginator(qs.order_by("-datetime"), 25)
    page = paginator.get_page(request.GET.get("page", 1))
    context = {
        "page_obj": page,
        "meetings": page.object_list,
        "status": status,
        "meeting_statuses": Meeting.STATUS_CHOICES,
        "leads_json": json.dumps([
            {"id": l.pk, "name": l.name, "phone": l.phone}
            for l in lead_queryset_for(request.user).select_related("stage")[:200]
        ]),
        "role": get_role(request.user),
    }
    return render(request, "crm/demos.html", context)


# ============================================================
# FOLLOWUPS
# ============================================================
@staff_required
def followups(request):
    user = request.user
    now = timezone.now()
    qs = Followup.objects.filter(done=False).select_related("lead")
    if not can_manage(user):
        qs = qs.filter(Q(lead__assigned_to=user) | Q(lead__assigned_to__isnull=True))

    overdue = qs.filter(due__lt=now - timedelta(minutes=1)).order_by("due")
    today = qs.filter(due__date=now.date()).order_by("due")
    tomorrow = qs.filter(due__date=(now + timedelta(days=1)).date()).order_by("due")
    week = qs.filter(
        due__date__gt=(now + timedelta(days=1)).date(),
        due__date__lte=(now + timedelta(days=7)).date(),
    ).order_by("due")
    later = qs.filter(due__date__gt=(now + timedelta(days=7)).date()).order_by("due")

    context = {
        "groups": [
            ("Overdue", overdue, "stat-red"),
            ("Today", today, "stat-amber"),
            ("Tomorrow", tomorrow, ""),
            ("This Week", week, ""),
            ("Later", later, ""),
        ],
        "role": get_role(user),
    }
    return render(request, "crm/followups.html", context)


# ============================================================
# CALENDAR
# ============================================================
@staff_required
def calendar(request):
    context = {"role": get_role(request.user)}
    return render(request, "crm/calendar.html", context)


# ============================================================
# TASKS
# ============================================================
@staff_required
def tasks(request):
    user = request.user
    qs = Task.objects.select_related("lead", "assigned_to")
    if not can_manage(user):
        qs = qs.filter(Q(assigned_to=user) | Q(assigned_to__isnull=True))
    status = request.GET.get("status", "")
    if status == "all":
        pass
    elif status:
        qs = qs.filter(status=status)
    else:
        qs = qs.exclude(status="done")
    if request.method == "POST":
        if can_manage(user):
            assigned = User_or_None(request.POST.get("assigned_to") or user.pk)
        else:
            assigned = user
        raw_deadline = request.POST.get("deadline") or ""
        deadline = parse_datetime(raw_deadline) if raw_deadline.strip() else None
        task = Task.objects.create(
            title=request.POST.get("title", "").strip(),
            lead=Lead.objects.filter(pk=request.POST.get("lead")).first() if request.POST.get("lead") else None,
            assigned_to=assigned,
            priority=request.POST.get("priority", "medium"),
            deadline=deadline,
            created_by=user,
        )
        if task.lead:
            log_activity(task.lead, "note", f"Task created: {task.title}", user)
        messages.success(request, "Task created.")
        return redirect("crm:tasks")
    paginator = Paginator(qs.order_by("status", "-deadline"), 25)
    page = paginator.get_page(request.GET.get("page", 1))
    context = {
        "page_obj": page,
        "tasks": page.object_list,
        "status": status,
        "leads_json": json.dumps([
            {"id": l.pk, "name": l.name}
            for l in lead_queryset_for(user).select_related("stage")[:200]
        ]),
        "staff_json": json.dumps([
            {"id": sp.user.pk, "name": sp.user.get_full_name() or sp.user.username}
            for sp in StaffProfile.objects.filter(is_active=True).select_related("user")
        ]),
        "role": get_role(user),
    }
    return render(request, "crm/tasks.html", context)


@staff_required
@require_POST
def task_delete(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if task.assigned_to_id not in (request.user.id, None) and not can_manage(request.user):
        messages.error(request, "Not allowed.")
        return redirect("crm:tasks")
    task.delete()
    messages.success(request, "Task deleted.")
    return redirect("crm:tasks")


# ============================================================
# SALES SCRIPTS
# ============================================================
@staff_required
def scripts(request):
    qs = SalesScript.objects.filter(tenant__isnull=True)
    category = request.GET.get("category", "")
    if category:
        qs = qs.filter(category=category)
    is_xhr = request.headers.get("x-requested-with") == "XMLHttpRequest"
    if request.method == "POST":
        if not can_manage(request.user):
            if is_xhr:
                return JsonResponse({"ok": False, "error": "Not allowed"}, status=403)
            return redirect("crm:scripts")
        title = request.POST.get("title", "").strip()
        if not title:
            if is_xhr:
                return JsonResponse({"ok": False, "error": "Title is required"}, status=400)
            return redirect("crm:scripts")
        script = SalesScript.objects.create(
            title=title,
            category=request.POST.get("category", "cold_call"),
            content=request.POST.get("content", ""),
        )
        if is_xhr:
            return JsonResponse({"ok": True, "pk": script.pk})
        messages.success(request, "Script saved.")
        return redirect("crm:scripts")
    context = {
        "scripts": qs.order_by("category", "position", "title"),
        "category": category,
        "categories": SalesScript.CATEGORY_CHOICES,
        "role": get_role(request.user),
    }
    return render(request, "crm/scripts.html", context)


@staff_required
@require_POST
def script_edit(request, pk):
    if not can_manage(request.user):
        return JsonResponse({"ok": False, "error": "Not allowed"}, status=403)
    script = get_object_or_404(SalesScript, pk=pk)
    title = request.POST.get("title", "").strip()
    if not title:
        return JsonResponse({"ok": False, "error": "Title is required"}, status=400)
    category = request.POST.get("category", script.category)
    if category not in dict(SalesScript.CATEGORY_CHOICES):
        return JsonResponse({"ok": False, "error": "Invalid category"}, status=400)
    script.title = title
    script.category = category
    script.content = request.POST.get("content", script.content)
    script.save()
    return JsonResponse({"ok": True, "pk": script.pk})


@staff_required
@require_POST
def script_toggle(request, pk):
    if not can_manage(request.user):
        return JsonResponse({"ok": False, "error": "Not allowed"}, status=403)
    script = get_object_or_404(SalesScript, pk=pk)
    script.active = not script.active
    script.save(update_fields=["active"])
    return JsonResponse({"ok": True, "active": script.active})


# ============================================================
# FAQ
# ============================================================
@staff_required
def faq(request):
    qs = FAQ.objects.filter(tenant__isnull=True, active=True)
    q = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    if q:
        qs = qs.filter(Q(question__icontains=q) | Q(answer__icontains=q))
    if category:
        qs = qs.filter(category=category)
    if request.method == "POST":
        if can_manage(request.user):
            FAQ.objects.create(
                question=request.POST.get("question", "").strip(),
                answer=request.POST.get("answer", ""),
                category=request.POST.get("category", ""),
            )
            messages.success(request, "FAQ saved.")
        return redirect("crm:faq")
    context = {
        "faqs": qs.order_by("position", "id"),
        "q": q,
        "category": category,
        "categories": FAQ.objects.filter(tenant__isnull=True).values_list("category", flat=True).distinct().exclude(category=""),
        "role": get_role(request.user),
    }
    return render(request, "crm/faq.html", context)


# ============================================================
# LEARN (training hub)
# ============================================================
_TABLE_WRAP_RE = re.compile(r"(<table[^>]*>.*?</table>)", re.S)


def _responsive_tables(html):
    """Wrap tables for horizontal scroll and tag every <td> with a data-label
    taken from its header cell so mobile can render stacked card rows."""
    from html import escape

    def handle_table(match):
        table = match.group(0)
        headers = []
        thead = re.search(r"<thead[^>]*>(.*?)</thead>", table, re.S)
        if thead:
            headers = [re.sub(r"<[^>]+>", " ", h) for h in re.findall(r"<th[^>]*>(.*?)</th>", thead.group(1), re.S)]
        if not headers:
            first_tr = re.search(r"<tr[^>]*>(.*?)</tr>", table, re.S)
            if first_tr:
                headers = [re.sub(r"<[^>]+>", " ", h) for h in re.findall(r"<th[^>]*>(.*?)</th>", first_tr.group(1), re.S)]
        if not headers:
            return table
        headers = [" ".join(h.split()) for h in headers]

        def handle_row(row_match):
            row = row_match.group(0)
            cells = list(re.finditer(r"<td([^>]*)>(.*?)</td>", row, re.S))
            if not cells:
                return row
            out = []
            for i, cell in enumerate(cells):
                attrs, inner = cell.group(1), cell.group(2)
                label = escape(headers[i] if i < len(headers) else "", quote=True)
                out.append(f'<td{attrs} data-label="{label}">{inner}</td>')
            return re.sub(r"<td[^>]*>.*?</td>", lambda _: out.pop(0), row, flags=re.S)

        return re.sub(r"<tr[^>]*>.*?</tr>", handle_row, table, flags=re.S)

    wrapped = _TABLE_WRAP_RE.sub(r'<div class="tbl-scroll">\1</div>', html)
    return re.sub(r"<table[^>]*>.*?</table>", handle_table, wrapped, flags=re.S)


@staff_required
def learn(request, slug=None):
    topics = (
        LearningTopic.objects
        .annotate(active_count=Count("articles", filter=Q(articles__active=True)))
        .prefetch_related("articles")
        .order_by("order", "name")
    )
    article = None
    article_content = ""
    if slug:
        article = get_object_or_404(
            LearningArticle.objects.select_related("topic").filter(active=True), slug=slug
        )
        article_content = _responsive_tables(article.content)
    else:
        first = LearningArticle.objects.filter(active=True).order_by("topic__order", "order", "id").first()
        if first:
            return redirect("crm:learn_article", slug=first.slug)
    context = {
        "article": article,
        "article_content": article_content,
        "topics": topics,
        "role": get_role(request.user),
    }
    return render(request, "crm/learn.html", context)


# ============================================================
# TEAM
# ============================================================
@crm_role_required("owner", "manager")
def team(request):
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    won_stage = PipelineStage.objects.filter(is_won=True).first()
    lost_stage = PipelineStage.objects.filter(is_lost=True).first()

    assigned_agg = Lead.objects.filter(assigned_to__isnull=False).values("assigned_to").annotate(
        assigned=Count("id"),
        won=Count("id", filter=Q(stage=won_stage)) if won_stage else Count("id", filter=Q(stage__isnull=True)),
        open_count=Count("id", filter=~Q(stage=won_stage) & ~Q(stage=lost_stage) if won_stage and lost_stage else Q(stage__isnull=True)),
        revenue=Sum("expected_value", filter=Q(stage=won_stage)) if won_stage else Sum("expected_value"),
    )
    won_agg = {a["assigned_to"]: a for a in assigned_agg}
    calls_month = {
        c["staff"]: c["c"]
        for c in CallLog.objects.filter(created_at__gte=month_start).values("staff").annotate(c=Count("id"))
    }
    calls_today = {
        c["staff"]: c["c"]
        for c in CallLog.objects.filter(created_at__date=now.date()).values("staff").annotate(c=Count("id"))
    }
    meetings_month = {
        m["staff"]: m["c"]
        for m in Meeting.objects.filter(datetime__gte=month_start).values("staff").annotate(c=Count("id"))
    }
    won_month = {
        w["assigned_to"]: w["c"]
        for w in Lead.objects.filter(updated_at__gte=month_start, stage=won_stage).values("assigned_to").annotate(c=Count("id"))
    } if won_stage else {}

    members = []
    for sp in StaffProfile.objects.filter(is_active=True).select_related("user"):
        a = won_agg.get(sp.user_id) or {}
        members.append({
            "user_first": sp.user.get_full_name() or sp.user.username,
            "role": sp.role,
            "title": sp.title,
            "assigned": a.get("assigned", 0),
            "open": a.get("open_count", 0),
            "won": a.get("won", 0),
            "calls_month": calls_month.get(sp.user_id, 0),
            "calls_today": calls_today.get(sp.user_id, 0),
            "meetings_month": meetings_month.get(sp.user_id, 0),
            "won_month": won_month.get(sp.user_id, 0),
            "revenue": a.get("revenue", 0) or 0,
        })
    members.sort(key=lambda m: (-m["won"], -m["revenue"]))
    context = {"members": members, "role": get_role(request.user)}
    return render(request, "crm/team.html", context)


# ============================================================
# REPORTS
# ============================================================
@crm_role_required("owner", "manager")
def reports(request):
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    won_qs = Lead.objects.filter(tenant__isnull=True, stage__is_won=True)
    all_qs = Lead.objects.filter(tenant__isnull=True)

    monthly_revenue = []
    for i in range(5, -1, -1):
        start = (now - timedelta(days=30 * i)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (start + timedelta(days=32)).replace(day=1)
        won = Lead.objects.filter(tenant__isnull=True, stage__is_won=True,
                                  updated_at__gte=start, updated_at__lt=end).aggregate(t=Sum("expected_value"))["t"] or 0
        monthly_revenue.append({"label": start.strftime("%b %y"), "value": float(won)})

    total_won = won_qs.count()
    total_leads = all_qs.count()
    conversion = round(total_won / total_leads * 100, 1) if total_leads else 0

    source_counts = list(all_qs.values_list("source").annotate(c=Count("id")).order_by("-c"))
    stage_counts = [
        {"name": s.name, "count": all_qs.filter(stage=s).count(), "color": s.color}
        for s in PipelineStage.objects.filter(tenant__isnull=True).order_by("order")
    ]
    top_performers = [
        {"staff": sp, "won": Lead.objects.filter(assigned_to=sp.user, stage__is_won=True).count(),
         "revenue": Lead.objects.filter(assigned_to=sp.user, stage__is_won=True).aggregate(t=Sum("expected_value"))["t"] or 0}
        for sp in StaffProfile.objects.filter(is_active=True).select_related("user")
        if sp.role in ("staff", "manager", "owner")
    ]
    top_performers.sort(key=lambda x: (-x["won"], -x["revenue"]))

    lost_reasons = [
        {"reason": r or "(no reason)", "count": c}
        for r, c in Lead.objects.filter(tenant__isnull=True, stage__is_lost=True)
        .values_list("lost_reason").annotate(c=Count("id")).order_by("-c")[:8]
    ]

    context = {
        "monthly_revenue": monthly_revenue,
        "monthly_revenue_json": json.dumps(monthly_revenue),
        "sources_json": json.dumps([
            {"label": dict(Lead.SOURCE_CHOICES).get(s, s), "value": c}
            for s, c in source_counts
        ]),
        "total_won": total_won,
        "total_leads": total_leads,
        "conversion": conversion,
        "source_counts": source_counts,
        "stage_counts": stage_counts,
        "top_performers": top_performers,
        "lost_reasons": lost_reasons,
        "role": get_role(request.user),
    }
    return render(request, "crm/reports.html", context)


# ============================================================
# SETTINGS
# ============================================================
@crm_role_required("owner", "manager")
def settings(request):
    if request.method == "POST":
        section = request.POST.get("section", "")
        if section == "stage":
            stage_id = request.POST.get("stage_id")
            if stage_id:
                stage = get_object_or_404(PipelineStage, pk=stage_id, tenant__isnull=True)
                stage.name = request.POST.get("name", stage.name)
                stage.color = request.POST.get("color", stage.color)
                stage.order = int(request.POST.get("order", stage.order))
                stage.is_won = request.POST.get("is_won") == "on"
                stage.is_lost = request.POST.get("is_lost") == "on"
                stage.save()
            else:
                PipelineStage.objects.create(
                    name=request.POST.get("name", "New Stage"),
                    color=request.POST.get("color", "#2563eb"),
                    order=int(request.POST.get("order", 999)),
                    is_won=request.POST.get("is_won") == "on",
                    is_lost=request.POST.get("is_lost") == "on",
                )
            messages.success(request, "Stage saved.")
        elif section == "staff":
            if get_role(request.user) == "owner":
                username = request.POST.get("username", "").strip()
                password = request.POST.get("password", "")
                from django.contrib.auth.models import User
                user, created = User.objects.get_or_create(username=username)
                if password:
                    user.set_password(password)
                user.first_name = request.POST.get("first_name", "")
                user.email = request.POST.get("email", "")
                user.save()
                StaffProfile.objects.update_or_create(
                    user=user,
                    defaults={"role": request.POST.get("role", "staff"),
                              "phone": request.POST.get("phone", ""),
                              "title": request.POST.get("title", "")},
                )
                messages.success(request, "Staff member saved.")
        return redirect("crm:settings")

    context = {
        "stages": PipelineStage.objects.filter(tenant__isnull=True).order_by("order"),
        "staff_members": StaffProfile.objects.filter(is_active=True).select_related("user").order_by("-role"),
        "role": get_role(request.user),
    }
    return render(request, "crm/settings.html", context)
