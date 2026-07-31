from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run proactive monitoring checks against ProactiveRule rows and dispatch alerts."

    def handle(self, *args, **options):
        from api.ai.proactive import check_all

        dispatched = check_all()
        self.stdout.write(self.style.SUCCESS(f"Proactive monitor: {dispatched} alert(s) dispatched"))
