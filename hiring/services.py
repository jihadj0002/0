from datetime import datetime

from django.contrib.auth.models import User
from django.template.loader import render_to_string

from back.models import UserProfile
from crm.models import StaffProfile
from crm.services import normalize_phone

from .models import CandidateApplication, HiringMeeting, MeetingAttendee


def parse_datetime(raw):
    """Parse an HTML datetime-local string ('YYYY-MM-DDTHH:MM') into a tz-aware datetime."""
    from django.utils import timezone

    if not raw:
        return None
    try:
        dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M")
    except (ValueError, TypeError):
        return None
    if not timezone.is_aware(dt):
        dt = timezone.make_aware(dt)
    return dt


def create_application(*, name, email, phone="", position="sales_staff",
                       experience_years=0, skills="", expected_salary="",
                       availability="", city="", cover_letter="", source="website"):
    """Create (or return the existing) application. Returns (application, created)."""
    email = (email or "").strip().lower()
    phone = normalize_phone(phone or "")
    if email:
        existing = CandidateApplication.objects.filter(email=email).exclude(
            status="rejected"
        ).first()
        if existing:
            return existing, False

    application = CandidateApplication.objects.create(
        name=(name or "").strip(),
        email=email or None,
        phone=phone,
        position=position,
        experience_years=int(experience_years or 0),
        skills=(skills or "").strip(),
        expected_salary=(expected_salary or "").strip(),
        availability=(availability or "").strip(),
        city=(city or "").strip(),
        cover_letter=cover_letter,
        source=source,
    )
    return application, True


def hire_candidate(*, candidate, role="staff", username=None, temp_password=None):
    """Create/find the User + CRM StaffProfile for a hired candidate.

    Returns (user, temp_password). Uses a passed-in temp password or guests a random one
    shown to the owner once. Idempotent: reuses an existing user by email if present.
    """
    import re

    base = (username or "").strip() or (candidate.email or "sales").split("@")[0] or "sales"
    base = re.sub(r"[^a-zA-Z0-9_.]", "", base)[:30] or "sales"
    user = None
    if candidate.email:
        user = User.objects.filter(email__iexact=candidate.email).first()
    if user is None:
        if candidate.hired_user_id:
            user = candidate.hired_user
        if user is None:
            username, n = base, 1
            while User.objects.filter(username=username).exists():
                username = f"{base}{n}"
                n += 1
            user = User(username=username, email=candidate.email)
            if temp_password:
                user.set_password(temp_password)
            else:
                import secrets
                import string
                alphabet = string.ascii_letters + string.digits
                temp_password = "".join(secrets.choice(alphabet) for _ in range(10))
                user.set_password(temp_password)
            user.first_name = (candidate.name or "").split()[0] if candidate.name else ""
            user.save()
            UserProfile.objects.get_or_create(user=user)

    if temp_password:
        user.set_password(temp_password)
    user.email = candidate.email or user.email
    user.save(update_fields=["email"] if temp_password is None else ["email", "password"])

    candidate.hired_user = user
    candidate.status = "hired"
    candidate.save(update_fields=["hired_user", "status", "updated_at"])

    StaffProfile.objects.update_or_create(
        user=user,
        defaults={
            "role": role,
            "is_active": True,
            "phone": candidate.phone,
            "title": candidate.get_position_display(),
        },
    )
    return user, temp_password


def notify_candidate(application, subject, message):
    """Best-effort email to the candidate. No-op when console backend / no address."""
    if not application.email:
        return False
    try:
        from django.core.mail import send_mail
        from django.conf import settings
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [application.email],
            fail_silently=True,
        )
        return True
    except Exception:
        return False


def schedule_meeting(*, title, when, platform="zoom", link="", location="",
                     notes="", candidates):
    """One HiringMeeting, bulk-invite candidates, mark them interview_scheduled."""
    meeting = HiringMeeting.objects.create(
        title=title, datetime=when, platform=platform, link=link,
        location=location, notes=notes,
    )
    for candidate in candidates:
        MeetingAttendee.objects.get_or_create(meeting=meeting, candidate=candidate)
        if candidate.status in ("applied", "shortlisted"):
            candidate.status = "interview_scheduled"
            candidate.save(update_fields=["status", "updated_at"])
        notify_candidate(
            candidate,
            f"Interview invitation: {meeting.title}",
            render_to_string("hiring/emails/interview_invite.txt", {
                "candidate": candidate,
                "meeting": meeting,
            }),
        )
    return meeting