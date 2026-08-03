from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from hiring.models import CandidateApplication, HiringMeeting
from hiring.services import create_application, schedule_meeting


SAMPLE_CANDIDATES = [
    {"name": "Sakshi Hasan", "email": "sakshi@example.com", "phone": "01711100001",
     "position": "sales_staff", "experience_years": 2,
     "skills": "cold calling, WhatsApp sales, negotiation",
     "expected_salary": "৳20,000", "availability": "Immediate", "city": "Dhaka",
     "cover_letter": "2 years closing SME clients over WhatsApp and phone."},
    {"name": "Tanvir Ahmed", "email": "tanvir@example.com", "phone": "01711100002",
     "position": "sales_executive", "experience_years": 4,
     "skills": "lead closing, demos, objection handling",
     "expected_salary": "৳35,000", "availability": "2 weeks notice", "city": "Dhaka",
     "cover_letter": "B2B SaaS closer, consistently hit 120% of target."},
    {"name": "Nowshin Islam", "email": "nowshin@example.com", "phone": "01711100003",
     "position": "sales_staff", "experience_years": 1,
     "skills": "social media, outreach, follow-up",
     "expected_salary": "৳15,000", "availability": "Immediate", "city": "Chattogram",
     "cover_letter": "Energetic, eager to learn and grow in SaaS sales."},
]


class Command(BaseCommand):
    help = "Seed sample hiring candidates and a group meeting."

    def handle(self, *args, **options):
        self.stdout.write("Seeding sample candidates...")
        for s in SAMPLE_CANDIDATES:
            app, created = create_application(
                name=s["name"], email=s["email"], phone=s["phone"],
                position=s["position"], experience_years=s["experience_years"],
                skills=s["skills"], expected_salary=s["expected_salary"],
                availability=s["availability"], city=s["city"],
                cover_letter=s["cover_letter"], source="manual",
            )
            self.stdout.write(f"  {app.name} {'created' if created else 'already exists'}")

        if HiringMeeting.objects.exists():
            self.stdout.write(self.style.WARNING("Meetings already exist — skipping."))
            return

        when = timezone.now() + timedelta(days=2)
        when = when.replace(hour=11, minute=0, second=0, microsecond=0)
        candidates = list(CandidateApplication.objects.filter(status="applied"))
        if candidates:
            schedule_meeting(
                title="Sales Team Group Interview",
                when=when,
                platform="google_meet",
                link="https://meet.google.com/",
                candidates=candidates,
            )
            self.stdout.write(self.style.SUCCESS(f"Seeded group interview with {len(candidates)} candidate(s)."))
            self.stdout.write(self.style.SUCCESS("Hiring seed complete."))
        else:
            self.stdout.write(self.style.WARNING("No applied candidates — meeting skipped."))