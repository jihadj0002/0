from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from billing.models import Plan, UserBalance


class Command(BaseCommand):
    help = "Create a free-plan UserBalance for every user who does not have one yet."

    def handle(self, *args, **options):
        plan = Plan.objects.filter(name="free", is_active=True).first()
        if not plan:
            self.stderr.write(self.style.ERROR("No active 'free' Plan found. Run setup_billing first."))
            return

        missing = User.objects.exclude(balance__isnull=False)
        created_count = 0

        for user in missing:
            _, created = UserBalance.objects.get_or_create(
                user=user,
                defaults={
                    "plan": plan,
                    "credits_remaining": plan.monthly_credits,
                    "credits_total": plan.monthly_credits,
                    "renewal_date": UserBalance.next_renewal_date(),
                },
            )
            if created:
                created_count += 1
                self.stdout.write(f"  Created balance for {user.username}")

        self.stdout.write(self.style.SUCCESS(f"Done — created {created_count} UserBalance record(s)."))
