from django.core.management.base import BaseCommand

from crm.models import Lead, PipelineStage
from crm.scoring import recompute_score

DEFAULT_STAGE_VALUES = {
    "new": 10, "new lead": 10, "new leads": 10,
    "contacted": 20,
    "qualified": 35,
    "demo": 55, "demo scheduled": 50, "demo done": 55,
    "negotiation": 70,
    "proposal": 75, "proposal sent": 75,
    "won": 100, "closed won": 100,
    "lost": 10, "closed lost": 10,
}


class Command(BaseCommand):
    help = "Set PipelineStage.score_value defaults (only where still 0) and recompute every internal lead score."

    def handle(self, *args, **options):
        self.stdout.write("Setting stage score values...")
        updated_stages = 0
        for stage in PipelineStage.objects.filter(tenant__isnull=True, score_value=0):
            value = DEFAULT_STAGE_VALUES.get(stage.name.strip().lower())
            if value is None:
                continue
            stage.score_value = value
            stage.save(update_fields=["score_value"])
            updated_stages += 1
        self.stdout.write(self.style.SUCCESS(f"  {updated_stages} stages updated"))

        self.stdout.write("Recomputing lead scores...")
        recomputed = 0
        for lead in Lead.objects.filter(tenant__isnull=True).select_related("stage").iterator():
            old = lead.score
            recompute_score(lead)
            if lead.score != old:
                recomputed += 1
        self.stdout.write(self.style.SUCCESS(
            f"  done: {Lead.objects.filter(tenant__isnull=True).count()} leads, "
            f"{recomputed} score changes"
        ))