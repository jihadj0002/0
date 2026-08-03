from django.contrib import messages
from django.db.models import Case, Q, When
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from crm.permissions import crm_role_required

from .models import CandidateApplication, HiringMeeting, MeetingAttendee
from .services import (
    create_application,
    hire_candidate,
    notify_candidate,
    parse_datetime,
    schedule_meeting,
)

# ============================================================
# PUBLIC
# ============================================================
def apply(request):
    if request.method == "POST":
        # Honeypot: silently accept, never store
        if request.POST.get("company_website"):
            return render(request, "hiring/thanks.html")

        application, created = create_application(
            name=request.POST.get("name", ""),
            email=request.POST.get("email", ""),
            phone=request.POST.get("phone", ""),
            position=request.POST.get("position", "sales_staff"),
            experience_years=request.POST.get("experience_years", 0),
            skills=request.POST.get("skills", ""),
            expected_salary=request.POST.get("expected_salary", ""),
            availability=request.POST.get("availability", ""),
            city=request.POST.get("city", ""),
            cover_letter=request.POST.get("cover_letter", ""),
            source="website",
        )
        if created and (request.FILES.get("photo") or request.FILES.get("cv")):
            if request.FILES.get("photo"):
                application.photo = request.FILES["photo"]
            if request.FILES.get("cv"):
                application.cv = request.FILES["cv"]
            application.save(update_fields=["photo", "cv"])

        if created:
            notify_candidate(
                application,
                "Application received — TheMatrixAi Sales Team",
                "Hi %s,\n\nWe received your application. Our team will review it and get back to you soon.\n\n— The MatrixAi Team" % application.name,
            )
        return render(request, "hiring/thanks.html", {"application": application})
    return render(request, "hiring/apply.html", {"positions": CandidateApplication.POSITION_CHOICES})


def thanks(request):
    return render(request, "hiring/thanks.html")


# ============================================================
# ADMIN
# ============================================================
@crm_role_required("owner", "manager")
def index(request):
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()

    qs = CandidateApplication.objects.all()
    if status:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(
            Q(name__icontains=q) | Q(phone__icontains=q) | Q(email__icontains=q)
            | Q(skills__icontains=q) | Q(city__icontains=q)
        )

    stats = {
        "total": CandidateApplication.objects.count(),
        "applied": CandidateApplication.objects.filter(status="applied").count(),
        "shortlisted": CandidateApplication.objects.filter(status="shortlisted").count(),
        "interview": CandidateApplication.objects.filter(status="interview_scheduled").count(),
        "hired": CandidateApplication.objects.filter(status="hired").count(),
    }
    upcoming = HiringMeeting.objects.filter(status="scheduled").order_by("datetime")[:5]

    context = {
        "applications": qs.select_related("hired_user").order_by(
            Case(
                When(status="applied", then=0),
                When(status="shortlisted", then=1),
                When(status="interview_scheduled", then=2),
                When(status="hired", then=3),
                When(status="rejected", then=4),
                default=5,
            ),
            "-created_at",
        ),
        "q": q,
        "status": status,
        "status_choices": CandidateApplication.STATUS_CHOICES,
        "stats": stats,
        "upcoming": upcoming,
    }
    return render(request, "hiring/index.html", context)


@crm_role_required("owner", "manager")
def candidate_detail(request, uid):
    application = get_object_or_404(CandidateApplication, uid=uid)
    if request.method == "POST" and request.POST.get("section") == "notes":
        application.notes = request.POST.get("notes", "")
        application.save(update_fields=["notes", "updated_at"])
        messages.success(request, "Notes saved.")
        return redirect("hiring_admin:candidate_detail", uid=application.uid)
    context = {
        "candidate": application,
        "meetings": application.meetings.select_related("meeting"),
        "attendees": MeetingAttendee.objects.filter(candidate=application),
    }
    return render(request, "hiring/candidate_detail.html", context)


@crm_role_required("owner", "manager")
@require_POST
def candidate_action(request, uid):
    application = get_object_or_404(CandidateApplication, uid=uid)
    action = request.POST.get("action", "")

    if action == "shortlist":
        application.status = "shortlisted"
        application.save(update_fields=["status", "updated_at"])
        messages.success(request, f"{application.name} shortlisted.")
        notify_candidate(
            application,
            "You've been shortlisted — MatrixAi Sales",
            f"Hi {application.name},\n\nGreat news — you've been shortlisted for the {application.get_position_display()} role at MatrixAi. We'll be in touch for the interview.\n\n",
        )
    elif action == "reject":
        application.status = "rejected"
        application.save(update_fields=["status", "updated_at"])
        messages.success(request, f"{application.name} rejected.")
        notify_candidate(
            application,
            "Application update",
            f"Hi {application.name},\n\nThank you for applying to MatrixAi. After careful review, we've decided not to move forward at this time.\n\n",
        )
    elif action == "hire":
        role = request.POST.get("role", "staff")
        temp_password = request.POST.get("password", "") or None
        user, password = hire_candidate(
            candidate=application, role=role, temp_password=temp_password,
        )
        messages.success(
            request,
            f"Hired! Account created — username: {user.username}"
            + (f" · temp password: {password}" if password else ""),
        )
        notify_candidate(
            application,
            "You're hired — Welcome to MatrixAi!",
            f"Hi {application.name},\n\nWelcome to the MatrixAi team! Your CRM access will be arranged shortly.\n\n",
        )
    elif action == "delete":
        application.delete()
        messages.success(request, "Application deleted.")
        return redirect("hiring_admin:index")
    else:
        messages.error(request, "Unknown action.")

    return redirect("hiring_admin:candidate_detail", uid=application.uid)


@crm_role_required("owner", "manager")
def meetings(request):
    context = {
        "meetings": HiringMeeting.objects.prefetch_related("attendees__candidate"),
    }
    return render(request, "hiring/meetings.html", context)


@crm_role_required("owner", "manager")
@require_POST
def meeting_status(request, pk):
    meeting = get_object_or_404(HiringMeeting, pk=pk)
    status = request.POST.get("status", "")
    if status in ("scheduled", "completed", "cancelled"):
        meeting.status = status
        meeting.save(update_fields=["status"])
        messages.success(request, f"Meeting marked {meeting.get_status_display()}.")
    return redirect("hiring_admin:meetings")


@crm_role_required("owner", "manager")
def meeting_new(request):
    if request.method == "POST":
        when = parse_datetime(request.POST.get("datetime", ""))
        candidate_ids = [int(x) for x in request.POST.getlist("candidates") if x.isdigit()]
        candidates = list(CandidateApplication.objects.filter(id__in=candidate_ids))
        if not when:
            messages.error(request, "A valid date & time is required.")
            return redirect("hiring_admin:meeting_new")
        meeting = schedule_meeting(
            title=request.POST.get("title", "Sales Team Interview"),
            when=when,
            platform=request.POST.get("platform", "zoom"),
            link=request.POST.get("link", ""),
            location=request.POST.get("location", ""),
            notes=request.POST.get("notes", ""),
            candidates=candidates,
        )
        messages.success(request, f"Meeting created — {meeting.invited_count()} candidate(s) invited.")
        return redirect("hiring_admin:meetings")

    candidate_qs = CandidateApplication.objects.exclude(status__in=["rejected", "hired"])
    context = {
        "candidates": candidate_qs,
        "platforms": HiringMeeting.PLATFORM_CHOICES,
    }
    return render(request, "hiring/meeting_form.html", context)