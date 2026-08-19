from django.db import transaction
from django.utils import timezone
from django.db.models import Q

from .models import (
    Lead, Activity, Customer, CrmSetting, StaffProfile, PipelineStage,
    Notification,
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
