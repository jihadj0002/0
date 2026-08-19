from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta

from crm.models import (
    StaffProfile, PipelineStage, Lead, Company, SalesScript, FAQ,
    Activity, CallLog, Followup, Meeting, Task,
)
from crm.services import create_lead


DEFAULT_STAGES = [
    ("New Leads", 0, "#2563eb", False, False, 10),
    ("Contacted", 10, "#7c3aed", False, False, 20),
    ("Qualified", 20, "#0891b2", False, False, 35),
    ("Demo Scheduled", 30, "#d97706", False, False, 50),
    ("Demo Done", 40, "#ca8a04", False, False, 55),
    ("Negotiation", 50, "#ea580c", False, False, 70),
    ("Proposal Sent", 60, "#4f46e5", False, False, 75),
    ("Waiting", 70, "#64748b", False, False, 40),
    ("Won", 80, "#16a34a", False, True, 100),
    ("Lost", 90, "#dc2626", True, False, 10),
]

DEMO_SCRIPTS = [
    ("Opening - Cold Call", "cold_call",
     "1. Ask for the customer by name and introduce yourself\n"
     "2. Ask permission: 'Do you have a couple of minutes to talk?'\n"
     "3. State the one-liner: We help small businesses automate WhatsApp/Messenger replies with AI\n"
     "4. Ask one qualifying question about their current process"),
    ("Follow-up after demo", "followup",
     "1. Recap what they liked in the demo\n2. Ask: 'What's holding you back?'\n3. Offer a trial"),
    ("Price objection", "objection",
     "1. Acknowledge: 'That's fair.'\n2. Reframe around value: monthly credits vs. hours saved\n3. Offer a smaller plan first"),
]


class Command(BaseCommand):
    help = "Seed CRM pipeline stages, sample staff, scripts, FAQ and demo leads."

    def handle(self, *args, **options):
        self.stdout.write("Seeding CRM pipeline stages...")
        for name, order, color, is_lost, is_won, score_value in DEFAULT_STAGES:
            PipelineStage.objects.update_or_create(
                tenant=None, name=name,
                defaults={"order": order, "color": color, "is_lost": is_lost,
                          "is_won": is_won, "score_value": score_value},
            )
        self.stdout.write("Seeding owner staff profile...")
        try:
            owner = User.objects.get(username="jihad")
            StaffProfile.objects.get_or_create(user=owner, defaults={"role": "owner", "title": "Founder"})
        except User.DoesNotExist:
            self.stdout.write(self.style.WARNING("User 'jihad' not found — skipping owner profile."))

        self.stdout.write("Seeding sales scripts...")
        for title, cat, content in DEMO_SCRIPTS:
            SalesScript.objects.get_or_create(tenant=None, title=title, defaults={"category": cat, "content": content})

        self.stdout.write("Seeding FAQ...")
        FAQ.objects.get_or_create(
            tenant=None, question="How does MatrixAI pricing work?",
            defaults={"answer": "Plans start free; paid plans include monthly credits for AI messages, renewing each month.", "category": "Pricing"},
        )
        FAQ.objects.get_or_create(
            tenant=None, question="Which platforms are supported?",
            defaults={"answer": "WhatsApp, Messenger, Instagram and Telegram.", "category": "Product"},
        )

        if Lead.objects.filter(tenant=None).exists():
            self.stdout.write(self.style.WARNING("Leads already exist — skipping sample leads."))
            return

        self.stdout.write("Seeding sample companies and leads...")
        comp = Company.objects.create(name="Monowa Mart", industry="E-commerce", website="https://monowamart.com")
        stages = {s.name: s for s in PipelineStage.objects.filter(tenant=None)}
        staff_users = []
        for uname, role in [("manager1", "manager"), ("staff1", "staff"), ("staff2", "staff")]:
            u, created = User.objects.get_or_create(username=uname, defaults={"first_name": uname.title()})
            StaffProfile.objects.get_or_create(user=u, defaults={"role": role})
            staff_users.append(u)
        staff = staff_users[1] if staff_users else None

        samples = [
            {"name": "Rahim Uddin", "phone": "+8801711000001", "email": "rahim@example.com", "source": "website",
             "stage": "New Leads", "assigned": staff, "expected": 2990},
            {"name": "Karim Ahmed", "phone": "+8801711000002", "email": "karim@example.com", "source": "referral",
             "stage": "Qualified", "assigned": staff, "expected": 5990},
            {"name": "Sultana Begum", "phone": "+8801711000003", "source": "facebook", "stage": "Demo Scheduled",
             "assigned": staff, "expected": 9990},
            {"name": "Nasir Khan", "phone": "+8801711000004", "email": "nasir@company.com", "source": "manual",
             "stage": "Negotiation", "company": comp, "assigned": staff, "expected": 19900},
            {"name": "Farida Yasmin", "phone": "+8801711000005", "source": "whatsapp", "stage": "Won",
             "assigned": staff, "expected": 5990},
        ]
        for s in samples:
            lead, created = create_lead(
                None, name=s["name"], phone=s["phone"], email=s.get("email", ""),
                source=s["source"], stage=stages[s["stage"]], assigned_to=s.get("assigned"),
                company=s.get("company"), expected_value=s.get("expected"),
            )
            if created:
                Activity.objects.create(
                    lead=lead, type="note",
                    description=f"Sample lead from seed. Interested in AI automation for {s.get('stage','')}.",
                    data={"seed": True},
                )
                Followup.objects.create(
                    lead=lead, due=timezone.now() + timedelta(days=1), kind="call", note="Initial follow-up",
                )

        lead = Lead.objects.filter(tenant=None, name="Sultana Begum").first()
        if lead:
            Meeting.objects.create(lead=lead, staff=staff, datetime=timezone.now() + timedelta(days=2), platform="zoom")
            Task.objects.create(title="Prepare demo slides", lead=lead, assigned_to=staff,
                                deadline=timezone.now() + timedelta(days=1))
            CallLog.objects.create(lead=lead, staff=staff, duration=180, outcome="interested",
                                   summary="Wants WhatsApp automation; budget around 10k.")
            Activity.objects.create(lead=lead, type="call", description="Intro call — positive, wants demo",
                                    data={"duration": 180})

        from crm.scoring import recompute_score
        for lead in Lead.objects.filter(tenant=None).iterator():
            recompute_score(lead)

        self.stdout.write(self.style.SUCCESS("CRM seed complete."))
