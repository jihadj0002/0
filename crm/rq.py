import random
import time
from django_rq import job
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone


@job("email", timeout="12h")
def process_email_batch(batch_id):
    """RQ job: send all pending emails for an EmailBatch with rate limiting."""
    from .models import EmailBatch, EmailLog
    from .services import build_email_context, render_email

    batch = EmailBatch.objects.select_related("template").get(id=batch_id)
    template = batch.template
    logs_qs = batch.logs.filter(status="queued").select_related("lead__stage", "lead__company")
    logs = list(logs_qs)

    if not logs:
        batch.status = "completed"
        batch.completed_at = timezone.now()
        batch.save(update_fields=["status", "completed_at"])
        return {"sent": 0, "failed": 0}

    sent = 0
    failed = 0
    total = len(logs)

    for i, log in enumerate(logs):
        lead = log.lead

        if not lead.email:
            log.status = "failed"
            log.error = "No email address"
            log.sent_at = timezone.now()
            log.save(update_fields=["status", "error", "sent_at"])
            failed += 1
            _sleep(i, total)
            continue

        ctx = build_email_context(lead, {"sender": batch.created_by})
        subject, body_html, body_text = render_email(template, ctx)

        try:
            send_mail(
                subject=subject,
                message=body_text,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "MatrixAI Sales <sales@thematrixai.xyz>"),
                recipient_list=[lead.email],
                html_message=body_html,
                fail_silently=False,
            )
            log.status = "sent"
            log.subject = subject
            log.body_html = body_html
            log.sent_at = timezone.now()
            log.save(update_fields=["status", "subject", "body_html", "sent_at"])
            sent += 1
        except Exception as e:
            log.status = "failed"
            log.error = str(e)
            log.sent_at = timezone.now()
            log.save(update_fields=["status", "error", "sent_at"])
            failed += 1

        _sleep(i, total)

    # Update batch
    batch.sent_count = sent
    batch.failed_count = failed
    batch.status = "completed"
    batch.completed_at = timezone.now()
    batch.save(update_fields=["sent_count", "failed_count", "status", "completed_at"])

    return {"sent": sent, "failed": failed, "total": total, "batch_uid": batch.uid}


@job("email", timeout="12h")
def send_single_email_now(lead_id, template_uid, user_id, account_email=None):
    """RQ job: send a single immediate email (from compose modal)."""
    from django.contrib.auth import get_user_model
    from .models import Lead, EmailTemplate, EmailLog
    from .services import build_email_context, render_email

    User = get_user_model()
    user = User.objects.get(id=user_id)
    lead = Lead.objects.get(id=lead_id)
    template = EmailTemplate.objects.get(uid=template_uid)

    if not lead.email:
        return {"status": "failed", "error": "No email address"}

    ctx = build_email_context(lead, {"sender": user})
    subject, body_html, body_text = render_email(template, ctx)

    from_email = account_email or getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@thematrixai.xyz")

    try:
        send_mail(
            subject=subject,
            message=body_text,
            from_email=from_email,
            recipient_list=[lead.email],
            html_message=body_html,
            fail_silently=False,
        )
        EmailLog.objects.create(
            template=template, lead=lead,
            recipient_email=lead.email, subject=subject,
            body_html=body_html, status="sent", sent_at=timezone.now(),
        )
        return {"status": "sent", "subject": subject, "to": lead.email}
    except Exception as e:
        EmailLog.objects.create(
            template=template, lead=lead,
            recipient_email=lead.email, subject=subject,
            body_html=body_html, status="failed",
            error=str(e), sent_at=timezone.now(),
        )
        return {"status": "failed", "error": str(e)}


@job("email", timeout="12h")
def send_campaign_emails(campaign_id):
    """RQ job: send all pending CampaignLead emails with rate limiting.

    This is the Phase 6 campaign-based sender — will work once the Campaign
    model exists in Phase 2.
    """
    from .models import Campaign, CampaignLead, EmailBatch, EmailLog
    from .services import build_email_context, render_email

    campaign = Campaign.objects.get(id=campaign_id)
    if campaign.status not in ("scheduled", "sending"):
        return {"error": f"Campaign status is {campaign.status}, cannot send"}

    campaign.status = "sending"
    campaign.started_at = timezone.now()
    campaign.save(update_fields=["status", "started_at", "updated_at"])

    template = campaign.template
    campaign_leads = list(campaign.leads.filter(status="pending").select_related("lead__stage", "lead__company"))
    total = len(campaign_leads)

    if total == 0:
        campaign.status = "completed"
        campaign.completed_at = timezone.now()
        campaign.save(update_fields=["status", "completed_at", "updated_at"])
        return {"sent": 0, "failed": 0}

    batch = EmailBatch.objects.create(
        template=template,
        subject_rendered=campaign.name,
        status="sending",
        total_recipients=total,
        started_at=timezone.now(),
        created_by=campaign.created_by,
        tenant=campaign.tenant,
    )

    sent = failed = 0

    for i, campaign_lead in enumerate(campaign_leads):
        lead = campaign_lead.lead
        if not lead.email:
            campaign_lead.status = "failed"
            campaign_lead.error = "No email address"
            campaign_lead.save(update_fields=["status", "error"])
            EmailLog.objects.create(
                batch=batch, template=template, lead=lead,
                recipient_email="", subject="", body_html="",
                status="failed", error="Lead has no email address",
            )
            failed += 1
            _sleep(i, total)
            continue

        ctx = build_email_context(lead, {"sender": campaign.created_by})
        subject, body_html, body_text = render_email(template, ctx)

        try:
            from_email = campaign.sending_account.email if campaign.sending_account else getattr(
                settings, "DEFAULT_FROM_EMAIL", "noreply@thematrixai.xyz"
            )
            send_mail(
                subject=subject, message=body_text,
                from_email=from_email, recipient_list=[lead.email],
                html_message=body_html, fail_silently=False,
            )
            campaign_lead.status = "sent"
            campaign_lead.subject_rendered = subject
            campaign_lead.sent_at = timezone.now()
            campaign_lead.save(update_fields=["status", "subject_rendered", "sent_at"])
            EmailLog.objects.create(
                batch=batch, template=template, lead=lead,
                recipient_email=lead.email, subject=subject,
                body_html=body_html, status="sent", sent_at=timezone.now(),
            )
            sent += 1
        except Exception as e:
            campaign_lead.status = "failed"
            campaign_lead.error = str(e)
            campaign_lead.save(update_fields=["status", "error"])
            EmailLog.objects.create(
                batch=batch, template=template, lead=lead,
                recipient_email=lead.email, subject=subject,
                body_html=body_html, status="failed",
                error=str(e), sent_at=timezone.now(),
            )
            failed += 1

        _sleep(i, total)

    batch.sent_count = sent
    batch.failed_count = failed
    batch.status = "completed"
    batch.completed_at = timezone.now()
    batch.save(update_fields=["sent_count", "failed_count", "status", "completed_at"])

    campaign.sent_count = sent
    campaign.failed_count = failed
    campaign.status = "completed" if failed == 0 else ("completed" if sent > 0 else "failed")
    campaign.completed_at = timezone.now()
    campaign.save(update_fields=["sent_count", "failed_count", "status", "completed_at", "updated_at"])

    return {"sent": sent, "failed": failed, "total": total}


def _sleep(i, total, min_minutes=2, max_minutes=5):
    if i < total - 1:
        time.sleep(random.uniform(min_minutes * 60, max_minutes * 60))