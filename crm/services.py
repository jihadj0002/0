import re
import time
import threading
import random
from django.db import transaction
from django.utils import timezone
from django.db.models import Q
from django.conf import settings

from .models import (
    Lead, Activity, Customer, CrmSetting, StaffProfile, PipelineStage,
    Notification,
    EmailTemplate, EmailBatch, EmailLog,
)
from .scoring import recompute_score


def normalize_phone(phone):
    """Normalize a Bangladeshi mobile number to +8801XXXXXXXXX form.

    Accepts 01XXXXXXXXX, 1XXXXXXXXX, 8801XXXXXXXXX, +8801XXXXXXXXX or
    space/dash/(dot) separated variants; returns the value unchanged if it
    can't be recognized.
    """
    import re

    if not phone:
        return ""
    digits = re.sub(r"[\s\-\(\)\.\+]", "", str(phone).strip())
    m = re.match(r"^0(1[3-9]\d{8})$", digits)
    if m:
        return "+880" + m.group(1)
    m = re.match(r"^(1[3-9]\d{8})$", digits)
    if m:
        return "+880" + m.group(1)
    m = re.match(r"^(?:880)?(1[3-9]\d{8})$", digits)
    if m:
        return "+880" + m.group(1)
    return phone


def setting(key, default=None):
    try:
        return CrmSetting.objects.get(key=key).value
    except CrmSetting.DoesNotExist:
        return default


def set_setting(key, value):
    CrmSetting.objects.update_or_create(key=key, defaults={"value": value})


def internal_queryset(qs):
    """Scope to internal (MatrixAI) records — the tenancy choke-point."""
    return qs.filter(tenant__isnull=True)


def get_role(user):
    try:
        return user.staff_profile.role if user.staff_profile.is_active else None
    except (StaffProfile.DoesNotExist, AttributeError):
        return None


def is_staff_member(user):
    return bool(user.is_authenticated and get_role(user))


def can_manage(user):
    return get_role(user) in ("owner", "manager")


def lead_queryset_for(user):
    qs = internal_queryset(Lead.objects.all())
    role = get_role(user)
    if role in ("owner", "manager", None):
        return qs
    if role == "support":
        return qs.filter(converted=True)
    return qs.filter(Q(assigned_to=user) | Q(assigned_to__isnull=True))


def notify(user, message, url=""):
    if not user:
        return
    Notification.objects.create(user=user, message=message, url=url)


# -----------------------
# Activity logging
# -----------------------
def log_activity(lead, type_, description="", user=None, **data):
    return Activity.objects.create(
        lead=lead, type=type_, description=description,
        created_by=user, data=data,
    )


# -----------------------
# Lead lifecycle
# -----------------------
def create_lead(user, *, name, phone="", email="", source="manual",
                stage=None, assigned_to=None, company=None, website="",
                industry="", notes="", budget=None, expected_value=None,
                next_followup=None, tags=None, conversation=None,
                tenant=None, score=0, log=True):
    """Create a lead with phone dedupe. Returns (lead, created)."""
    phone = normalize_phone(phone)
    phone_clean = (phone or "").strip()
    existing = internal_queryset(Lead.objects.all())
    if phone_clean:
        dup = existing.filter(phone=phone_clean).exclude(converted=True).first()
        if dup:
            return dup, False
    elif email:
        dup = existing.filter(email=email).exclude(converted=True).first()
        if dup:
            return dup, False
    if not phone_clean and not email:
        dup = existing.filter(name__iexact=name.strip()).exclude(converted=True).first()
        if dup:
            return dup, False

    if stage is None:
        stage = PipelineStage.objects.filter(
            tenant__isnull=True, is_lost=False, is_won=False
        ).order_by("order", "id").first()

    lead = Lead.objects.create(
        name=name, phone=phone_clean, email=email, source=source,
        stage=stage, assigned_to=assigned_to, company=company,
        website=website, industry=industry, notes=notes, budget=budget,
        expected_value=expected_value, next_followup=next_followup,
        tags=tags or [], conversation=conversation, created_by=user,
        tenant=tenant, score=score,
    )
    if log:
        log_activity(lead, "created", f"Lead created (source: {source})", user)
    recompute_score(lead, user)
    return lead, True


@transaction.atomic
def update_lead(user, lead, changed_by=None, **fields):
    before = {
        "stage": lead.stage_id,
        "assigned_to": lead.assigned_to_id,
        "score": lead.score,
        "next_followup": lead.next_followup,
    }
    for key, value in fields.items():
        if key == "phone":
            value = normalize_phone(value)
        if hasattr(lead, key):
            setattr(lead, key, value)
    lead.save(update_fields=list(fields.keys()) + ["updated_at"])
    after = {
        "stage": lead.stage_id,
        "assigned_to": lead.assigned_to_id,
        "score": lead.score,
        "next_followup": lead.next_followup,
    }
    if before["assigned_to"] != after["assigned_to"]:
        assignee = lead.assigned_to
        log_activity(lead, "assignment",
                     f"Assigned to {assignee.get_full_name() or assignee.username}" if assignee else "Unassigned",
                     user)
        if assignee:
            notify(assignee, f"New lead assigned: {lead.name}", f"/crm/leads/{lead.pk}/")
    if before["stage"] != after["stage"] and lead.stage:
        log_activity(lead, "status_change",
                     f"Stage changed to {lead.stage.name}", user)
        if lead.stage.is_won and not lead.converted:
            convert_lead(user, lead)
        elif lead.stage.is_lost:
            log_activity(lead, "lost", "Deal marked as lost", user)
    recompute_score(lead, user)
    return lead


def add_note(user, lead, text):
    if not text.strip():
        return None
    return log_activity(lead, "note", text, user)


def convert_lead(user, lead, *, platform_user=None, package="", monthly_value=None,
                 renewal=None, owner=None):
    """Closed-won: create Customer record + platform account onboarding."""
    if Customer.objects.filter(lead=lead).exists():
        return Customer.objects.get(lead=lead)
    if not platform_user and lead.email:
        from django.contrib.auth.models import User
        from back.models import UserProfile
        base_username = (lead.email.split("@")[0] or "customer")[:30]
        username, n = base_username, 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}{n}"
            n += 1
        platform_user = User.objects.create_user(
            username=username, email=lead.email, password=None, first_name=lead.name,
        )
        UserProfile.objects.get_or_create(user=platform_user)
    customer = Customer.objects.create(
        lead=lead, platform_user=platform_user, package=package,
        monthly_value=monthly_value, renewal=renewal or timezone.now().date(),
        owner=owner or user,
    )
    lead.converted = True
    lead.save(update_fields=["converted", "updated_at"])
    log_activity(lead, "won", f"Deal won — customer created ({package or 'No package'})", user,
                 customer_id=customer.pk)
    if owner:
        notify(owner, f"Customer onboarded: {lead.name}", f"/crm/customers/{customer.pk}/")
    recompute_score(lead, user)
    return customer


def complete_followup(user, followup, lead):
    followup.done = True
    followup.save(update_fields=["done"])
    lead.last_contact = timezone.now()
    lead.save(update_fields=["last_contact", "updated_at"])
    log_activity(lead, "call" if followup.kind == "call" else "note",
                 f"Follow-up completed ({followup.get_kind_display()}): {followup.note or 'Done'}", user)
    recompute_score(lead, user)


# -----------------------
# Cold Email Utilities
# -----------------------

TOKEN_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


def token_replace(text, context):
    """Replace {{token}} placeholders in text with values from context dict."""
    if not text:
        return text
    def repl(match):
        key = match.group(1)
        # Support nested access: lead.name -> context['lead']['name']
        parts = key.split(".")
        value = context
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part, "")
            else:
                value = getattr(value, part, "")
        return str(value) if value is not None else ""
    return TOKEN_PATTERN.sub(repl, text)


def build_email_context(lead, custom=None):
    """Build the token replacement context for a lead."""
    from django.conf import settings
    sender = custom.get("sender") if custom and custom.get("sender") else None
    
    ctx = {
        "lead": {
            "name": lead.name,
            "phone": lead.phone,
            "email": lead.email,
            "company": {
                "name": lead.company.name if lead.company else "",
            },
            "stage": {
                "name": lead.stage.name if lead.stage else "",
            },
            "score": str(lead.score),
            "tags": lead.tags or [],
        },
        "sender": {
            "name": sender.get_full_name() if sender else "MatrixAI Sales",
            "email": sender.email if sender else "sales@thematrixai.xyz",
        },
        "company": {
            "name": "TheMatrixAi",
        },
    }
    if custom:
        ctx.update(custom)
    return ctx


def render_email(template, context):
    """Render subject and body (html + text) for a template with context."""
    subject = token_replace(template.subject, context)
    body_html = token_replace(template.body_html, context)
    body_text = token_replace(template.body_text or "", context)
    # Auto-generate plain text from HTML if body_text is empty
    if not body_text.strip() and body_html:
        # Simple HTML to text conversion
        import re
        text = re.sub(r"<[^>]+>", "", body_html)
        text = re.sub(r"\s+", " ", text).strip()
        body_text = text
    return subject, body_html, body_text


def _send_email_single(lead, template, batch, user, sent_lock, failed_lock):
    """Send a single email and update batch counters (thread-safe)."""
    from django.core.mail import send_mail
    from django.conf import settings
    from .models import EmailLog
    
    if not lead.email:
        with failed_lock:
            EmailLog.objects.create(
                batch=batch,
                template=template,
                lead=lead,
                recipient_email="",
                subject="",
                body_html="",
                status="failed",
                error="Lead has no email address",
            )
        return "failed"
    
    context = build_email_context(lead, {"sender": user})
    subject, body_html, body_text = render_email(template, context)
    
    try:
        send_mail(
            subject=subject,
            message=body_text,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "MatrixAI Sales <sales@thematrixai.xyz>"),
            recipient_list=[lead.email],
            html_message=body_html,
            fail_silently=False,
        )
        with sent_lock:
            EmailLog.objects.create(
                batch=batch,
                template=template,
                lead=lead,
                recipient_email=lead.email,
                subject=subject,
                body_html=body_html,
                status="sent",
                sent_at=timezone.now(),
            )
        return "sent"
    except Exception as e:
        with failed_lock:
            EmailLog.objects.create(
                batch=batch,
                template=template,
                lead=lead,
                recipient_email=lead.email,
                subject=subject,
                body_html=body_html,
                status="failed",
                error=str(e),
                sent_at=timezone.now(),
            )
        return "failed"


def _rate_limited_send(user, template, lead_ids, interval_min=2, interval_max=5):
    """Send emails one by one with random intervals in a background thread."""
    from django.core.mail import send_mail
    from django.conf import settings
    from .models import Lead, EmailBatch, EmailLog
    
    # Get leads (internal only)
    leads_qs = internal_queryset(Lead.objects.select_related("stage", "company")).filter(id__in=lead_ids)
    leads = list(leads_qs)
    
    if not leads:
        raise ValueError("No valid leads found")
    
    # Create batch
    batch = EmailBatch.objects.create(
        template=template,
        subject_rendered="",
        status="sending",
        total_recipients=len(lead_ids),
        scheduled_at=None,
        started_at=timezone.now(),
        created_by=user,
        tenant=None,
    )
    
    # Threading locks for safe counter updates
    sent_lock = threading.Lock()
    failed_lock = threading.Lock()
    
    sent = 0
    failed = 0
    
    for i, lead in enumerate(leads):
        if not lead.email:
            with failed_lock:
                EmailLog.objects.create(
                    batch=batch,
                    template=template,
                    lead=lead,
                    recipient_email="",
                    subject="",
                    body_html="",
                    status="failed",
                    error="Lead has no email address",
                )
            failed += 1
            # Still apply interval even for failed leads
            if i < len(leads) - 1:
                interval = random.uniform(interval_min * 60, interval_max * 60)
                time.sleep(interval)
            continue
        
        result = _send_email_single(lead, template, batch, user, sent_lock, failed_lock)
        
        if result == "sent":
            with sent_lock:
                sent += 1
        else:
            with failed_lock:
                failed += 1
        
        # Apply random interval between emails (not after the last one)
        if i < len(leads) - 1:
            interval = random.uniform(interval_min * 60, interval_max * 60)
            time.sleep(interval)
    
    # Update batch status
    batch.sent_count = sent
    batch.failed_count = failed
    batch.status = "completed" if failed == 0 else ("completed" if sent > 0 else "failed")
    batch.completed_at = timezone.now()
    batch.save(update_fields=["sent_count", "failed_count", "status", "completed_at"])
    
    return batch


def _rate_limited_send_loop(user, template, lead_ids, interval_min, interval_max, batch, lock):
    """Background thread: send emails one by one with random 2-5 min intervals, updating shared batch."""
    from .models import Lead
    
    leads_qs = internal_queryset(Lead.objects.select_related("stage", "company")).filter(id__in=lead_ids)
    leads = list(leads_qs)
    
    sent_count = [0]
    failed_count = [0]
    
    for i, lead in enumerate(leads):
        # Send single email and update logs
        if not lead.email:
            with lock:
                failed_count[0] += 1
                EmailLog.objects.create(
                    batch=batch,
                    template=template,
                    lead=lead,
                    recipient_email="",
                    subject="",
                    body_html="",
                    status="failed",
                    error="Lead has no email address",
                    sent_at=timezone.now(),
                )
        else:
            context = build_email_context(lead, {"sender": user})
            subject, body_html, body_text = render_email(template, context)
            
            try:
                from django.core.mail import send_mail
                from django.conf import settings
                
                send_mail(
                    subject=subject,
                    message=body_text,
                    from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "MatrixAI Sales <sales@thematrixai.xyz>"),
                    recipient_list=[lead.email],
                    html_message=body_html,
                    fail_silently=False,
                )
                with lock:
                    sent_count[0] += 1
                    EmailLog.objects.create(
                        batch=batch,
                        template=template,
                        lead=lead,
                        recipient_email=lead.email,
                        subject=subject,
                        body_html=body_html,
                        status="sent",
                        sent_at=timezone.now(),
                    )
            except Exception as e:
                with lock:
                    failed_count[0] += 1
                    EmailLog.objects.create(
                        batch=batch,
                        template=template,
                        lead=lead,
                        recipient_email=lead.email,
                        subject=subject,
                        body_html=body_html,
                        status="failed",
                        error=str(e),
                        sent_at=timezone.now(),
                    )
        
        # Random interval between 2-5 minutes (not after the last lead)
        if i < len(leads) - 1:
            interval = random.uniform(interval_min * 60, interval_max * 60)
            time.sleep(interval)
    
    # Update batch final status (thread-safe)
    with lock:
        batch.sent_count = sent_count[0]
        batch.failed_count = failed_count[0]
        batch.status = "completed" if failed_count[0] == 0 else "failed"
        batch.completed_at = timezone.now()
        batch.save(update_fields=["sent_count", "failed_count", "status", "completed_at"])


def send_bulk_emails(user, template, lead_ids, scheduled_at=None, interval_min=2, interval_max=5):
    """
    Send emails to a list of leads using a template.
    
    For immediate send: enqueues an RQ job and returns immediately.
    For scheduled send: uses RQ scheduler.
    
    Returns:
        EmailBatch instance (status/counters updated by RQ worker)
    """
    from .models import EmailBatch, EmailLog, Lead
    from django_rq import get_queue
    from .rq import process_email_batch
    
    # Get leads (internal only)
    leads_qs = internal_queryset(Lead.objects.select_related("stage", "company")).filter(id__in=lead_ids)
    leads = list(leads_qs)
    
    if not leads:
        raise ValueError("No valid leads found")
    
    # Create batch
    batch = EmailBatch.objects.create(
        template=template,
        subject_rendered="",
        status="queued",
        total_recipients=len(lead_ids),
        scheduled_at=scheduled_at if scheduled_at and scheduled_at > timezone.now() else None,
        started_at=timezone.now() if not scheduled_at else None,
        created_by=user,
        tenant=None,
    )
    
    # Create queued EmailLog rows (so the UI can track progress)
    logs = []
    for lead in leads:
        ctx = build_email_context(lead, {"sender": user})
        subject, body_html, body_text = render_email(template, ctx)
        logs.append(EmailLog(
            batch=batch, template=template, lead=lead,
            recipient_email=lead.email, subject=subject,
            body_html=body_html, status="queued",
        ))
    EmailLog.objects.bulk_create(logs)
    
    # Enqueue to RQ
    queue = get_queue("email")
    if scheduled_at and scheduled_at > timezone.now():
        queue.enqueue_at(scheduled_at, process_email_batch, batch.id)
    else:
        queue.enqueue(process_email_batch, batch.id)
    
    return batch
