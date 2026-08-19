"""Deterministic lead scoring for the internal CRM.

The score is a pure function of the lead's current state — stage, call
logs, meetings, follow-ups, tasks, proposals and contact recency — so
re-running it always produces the same result (idempotent, no drift,
no double counting). Internal CRM only: tenant (AI) leads are skipped.
"""
from datetime import timedelta

from django.utils import timezone

SCORE_WEIGHTS = {
    "source_referral": 10,
    "source_default": 5,
    "stage_cap": 40,
    "positive_outcome": 6,
    "call_per": 4,
    "call_cap": 20,
    "meeting_completed": 10,
    "meeting_scheduled": 5,
    "meeting_no_show_penalty": 5,
    "meeting_cap": 25,
    "followup_per": 4,
    "followup_cap": 12,
    "task_per": 2,
    "task_cap": 8,
    "proposal_per": 5,
    "proposal_cap": 10,
    "recency_1d": 10,
    "recency_3d": 6,
    "recency_7d": 3,
    "lost_cap": 20,
    "max": 100,
}

POSITIVE_OUTCOMES = ("interested", "meeting_scheduled", "demo_scheduled", "call_later")

SCORE_LABELS = {
    "source": "Source",
    "stage": "Stage",
    "calls": "Calls",
    "meetings": "Demos",
    "followups": "Follow-ups",
    "tasks": "Tasks",
    "proposals": "Proposals",
    "recency": "Recency",
}


def breakdown_items(parts):
    """[(label, points), ...] for template display — skips zero chunks."""
    if not isinstance(parts, dict):
        return []
    return [
        (SCORE_LABELS.get(key, key), value)
        for key, value in parts.items()
        if key != "total" and value
    ]


def _stage(lead):
    stage = getattr(lead, "stage", None)
    if stage is None and lead.stage_id:
        from .models import PipelineStage
        stage = PipelineStage.objects.filter(pk=lead.stage_id).first()
    return stage


def _recency_points(lead, now=None):
    ref = lead.last_contact or lead.updated_at or timezone.now()
    age = (now or timezone.now()) - ref
    w = SCORE_WEIGHTS
    if age <= timedelta(days=1):
        return w["recency_1d"]
    if age <= timedelta(days=3):
        return w["recency_3d"]
    if age <= timedelta(days=7):
        return w["recency_7d"]
    return 0


def score_breakdown(lead):
    """Map lead state -> {component: points, total}. None for tenant leads."""
    if lead.tenant_id is not None:
        return None
    if lead.is_won():
        return {"stage": SCORE_WEIGHTS["max"], "total": SCORE_WEIGHTS["max"]}

    w = SCORE_WEIGHTS
    parts = {
        "source": w["source_referral"] if lead.source == "referral" else w["source_default"],
        "stage": 0,
        "calls": 0,
        "meetings": 0,
        "followups": 0,
        "tasks": 0,
        "proposals": 0,
        "recency": _recency_points(lead),
    }

    stage = _stage(lead)
    if stage:
        parts["stage"] = min(stage.score_value or 0, w["stage_cap"])

    call_count = lead.calls.count()
    parts["calls"] = max(0, min(call_count * w["call_per"], w["call_cap"]))
    if lead.calls.filter(outcome__in=POSITIVE_OUTCOMES).exists():
        parts["calls"] = min(parts["calls"] + w["positive_outcome"], w["call_cap"])

    meeting_pts = (
        lead.meetings.filter(status="completed").count() * w["meeting_completed"]
        + lead.meetings.filter(status="scheduled").count() * w["meeting_scheduled"]
        - lead.meetings.filter(status="no_show").count() * w["meeting_no_show_penalty"]
    )
    parts["meetings"] = max(0, min(meeting_pts, w["meeting_cap"]))

    parts["followups"] = min(
        lead.followups.filter(done=True).count() * w["followup_per"], w["followup_cap"]
    )
    parts["tasks"] = min(
        lead.tasks.filter(status="done").count() * w["task_per"], w["task_cap"]
    )
    parts["proposals"] = min(
        lead.activities.filter(type__in=("proposal", "onboarding")).count() * w["proposal_per"],
        w["proposal_cap"],
    )

    total = sum(parts.values())
    parts["stage"] = min(parts["stage"], w["stage_cap"])
    total = min(total, w["max"])
    if lead.is_lost():
        total = min(total, w["lost_cap"])
    parts["total"] = total
    return parts


def recompute_score(lead, user=None):
    """Recompute and persist lead.score + score_breakdown; log a "score"
    Activity only when the value actually changes."""
    parts = score_breakdown(lead)
    if parts is None or lead.score == parts["total"] and lead.score_breakdown == parts:
        return lead.score

    previous = lead.score
    lead.score = parts["total"]
    lead.score_breakdown = parts
    lead.save(update_fields=["score", "score_breakdown", "updated_at"])
    detail = ", ".join(
        f"{name} {value}"
        for name, value in parts.items()
        if name != "total" and value
    )
    from .services import log_activity
    log_activity(
        lead, "score",
        f"Score updated: {previous} → {parts['total']} ({detail})", user,
        previous=previous, score=parts["total"],
    )
    return parts["total"]