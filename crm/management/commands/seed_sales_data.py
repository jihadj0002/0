import csv
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from crm.models import (
    PipelineStage, Lead, Company, SalesScript,
    Activity, CallLog, Followup, Meeting, Task,
)
from crm.services import create_lead

CSV_PATH = Path(settings.BASE_DIR) / "docs" / "03_SALES" / "Customer_relations_matrix - Sheet1.csv"

# Leads tracked in docs/03_SALES pipeline/contact-log that are not in the CSV.
PIPELINE_LEADS = [
    {
        "name": "Mango 2 Rajshahi", "industry": "Mango/Food",
        "source": "messenger", "stage": "Qualified", "score": 85, "tier": 1,
        "next_action": "TODAY: Give number → schedule demo screenshot",
        "contact_log": [
            ("2026-07-28", "Pitched AI assistant — 'ChatGPT but better version. Customer k product show korbe, kotha bolbe, order o nibe.' → asked 'আচ্ছা কত খরচ হবে?' → told 'Almost 0, 1 taka max per customer' → said 'নম্বর দিন'"),
            ("2026-07-28", "Replied with phone number. → Schedule demo."),
        ],
        "followup": "Send number → schedule demo → close",
    },
    {
        "name": "Natural Vine", "industry": "Health/Wellness",
        "website": "https://naturalvinebd.com", "phone": "01916-404543",
        "source": "manual", "stage": "Qualified", "score": 85, "tier": 1,
        "next_action": "TODAY: Send proper demo + pricing details → follow up to close",
        "contact_log": [
            ("2026-07-30", "Called, introduced MatrixAI. Owner asked pricing + details, gave personal number (01916-404543)."),
        ],
        "followup": "Send demo + pricing → follow up to close",
    },
    {
        "name": "Asli (আসলি)", "industry": "Food/Vuna/Achar",
        "website": "https://asli.com.bd", "source": "messenger",
        "stage": "Contacted", "score": 55, "tier": 1,
        "next_action": "Wait 24h → no reply? Re-engage with BangChar case study",
        "contact_log": [
            ("2026-07-28", "Pitched: 'Customer er message response late ba delay houar karone most of the customer onno page e chole jay. Instant reply crucial for business. Amra ei problem solve kortesi. Apnar kemon sales miss hoi?' → 'বলুন স্যার' → listening"),
        ],
        "followup": "Awaiting reply → offer 10-min demo with BangChar Pickles case study",
    },
    {
        "name": "Achar Ghor", "industry": "Achar/Food",
        "source": "messenger", "stage": "Demo Done", "score": 50, "tier": 1,
        "next_action": "Check if they viewed → follow up",
        "contact_log": [
            ("2026-07-28", "Said 'earlier MatrixAI didn't work properly' → Explained it's improved → Sent fresh dashboard credentials"),
        ],
        "followup": "Wait for them to check demo → follow up",
    },
    {
        "name": "Achar 2 Rajshahi", "industry": "Achar/Food",
        "source": "messenger", "stage": "Demo Done", "score": 40, "tier": 1,
        "next_action": "'দেখেছেন? কোনো প্রশ্ন থাকলে বলবেন'",
        "contact_log": [
            ("2026-07-28", "Showed demo + dashboard, explained pricing → asked for meeting → then went silent"),
        ],
        "followup": "Follow up: দেখেছেন? কোনো প্রশ্ন থাকলে বলবেন",
    },
    {
        "name": "E Minor Guitar Academy", "industry": "Music/Education",
        "phone": "01686-771687", "source": "manual",
        "stage": "Contacted", "score": 70, "tier": 1,
        "next_action": "Call back or DM",
        "contact_log": [
            ("2026-07-30", "Called, introduced MatrixAI (42K followers). No response."),
        ],
        "followup": "Call back or DM",
    },
]

# Tier 1 (10K+ followers) / Tier 2 (5-10K) — from pipeline docs, keyed by CSV name.
TIER1 = {"Velina Elite BD", "Fabulla Glow", "Beauty Basics", "Miyabi", "Glowvena",
         "KM Natural Product", "EGC Limited", "TOK- বাংলা"}
TIER2 = {"Elora Mart BD", "Shining Box", "Glowora Skin Care", "On Glow BD", "Mayon", "Glowri"}

SCRIPTS = [
    ("Call Script — Opening (Cold Call)", "cold_call", """Opening (15 seconds)
> Hello we are from MatrixAI. Can we talk to your representative who is managing sales at this moment?
> Apnar Social Media er response time baranor jonno kichu kotha boltam.
> Apnar theke 45 seconds lage?

If they say yes:
> Apnader Facebook page ki 24/7 uptime thakte pare? Like raat 3-4 tay or anytime order nite pare?

Listen to their problem. Most will say "Na, 24/7 hoy na" or "Moderator ache kintu deri hoy."
> Customer er message er reply joto fast diben toto fast order hobe.
> Amader system: jokhon e apnar customer message dibe, seta completely automatically reply pabe.
> Order o nibe automatically. 24/7 without any delay.
> Sales auto boost."""),

    ("Text Script — FB DM Opening", "cold_call", """Opening
> Hello there
> Anyone available? I want to talk to your admin.
OR
> Apnader customer support nia kichu kotha bolte chai
OR
> Apnar Social Media er response time baranor jonno kichu kotha boltam.

When they ask how it's done / what it is:
Customer er message er response late ba delay houar karone most of the customer onno page e chole jay...!
Instant reply is one of the most crucial section for business...!
And amra business owners der ei problem ta solve korar Jonno help kortesi
Apnar customer response delay er karone Kemon sales miss hoi?"""),

    ("Problem Statement Pitch", "followup", """Customer jokhon message dei beshir vag shomoy timely response na Korte parar karone customer onno jaigai chole jay.
Sales miss hoie jay most of the times.
And we are talking about solution for that specific problem.
Jate customer message dilei exact timely response Korte pare and order collect Korte pare.

When they ask how it's done:
Amara main jei support ta provide Kori seita mainly ekta AI sales assistant.
Ekdom chatgpt but better version.
Apnar inbox e ai active thakbe.
Customer k product show korbe kotha bolbe and order o nibe."""),

    ("Objection — Price (Kharche koto?)", "objection",
     "Basic plan BDT 999/month — shurute kono charge nai, demo dilam free."),

    ("Objection — How it works (Ki kore kaj kore?)", "objection",
     "Facebook page connect korben, product catalog upload korben, AI automatically message handle kore."),

    ("Objection — Product variants", "objection",
     "Setai amader strength — MatrixAI product variant bujhe, price, stock, delivery alles automatically bole."),

    ("Objection — AI not trusted", "objection",
     "Amader already 3+ clients (Monowamart, Islamic Gift BD, etc.) — demo dekhen apni."),

    ("Objection — Call me later", "objection",
     "Sure, kobe convenient? (Set exact time — avoid 'porer shomoy')"),

    ("Closing — Demo Setup", "closing", """> Let me set up a quick demo for you. 10 minutes. Show you exactly how it works.
> Your page customers will never wait again.
> Kal ki por-shokal 11tay free demo show kori?

After demo setup: Apnar site er products details dia amra ekta demo banai apnar site e set up kore dei...
Ami kisudin try kore dekhen apnar business kmn handle korte pare AI...
R sathe apnar products er kichu details, like name, shop address, msg reply formats eigula thkle amk provide koren...!"""),

    ("Same-Day Follow-up Message", "followup", """> MatrixAI theke call korechilam.
> Just a quick note — apnar page e jokhon customer message dibe, amra 24/7 automatically handle korte pari.
> Interested hole janaben. Free demo setup kore dibo — 10 minute lagbe na.
> Call / WhatsApp: [Your Number]"""),

    ("Follow-Up Day 3 (Value Share)", "followup",
     "> আপনি কি জানেন, ৬০% কাস্টমার অন্য পেজে চলে যায় যদি ৫ মিনিটের মধ্যে রিপ্লাই না পায়? MatrixAI instant reply দিয়ে সেই sales loss বন্ধ করে। আপনার ব্যবসার জন্য একটু দেখতে চান?"),

    ("Follow-Up Day 7 (Social Proof)", "followup",
     "> আমরা ইতিমধ্যে BangChar Pickles, Monowamart এর মত ব্যবসাদের সাথে কাজ করছি। তাদের daily customer messages handle করতে সাহায্য করছি। আপনার জন্যও করতে পারি। আগ্রহী হলে জানাবেন — ১০ মিনিটের demo দেখাই।"),

    ("Follow-Up Day 14 (Break-Up)", "followup",
     "> ঠিক আছে, আপনার সময় নিন। যখন interest হবে, জানাবেন। আমরা আছি। 🙂"),

    ("Hot Lead — Demo Follow-up (24h)", "demo", """> দেখেছেন? কোনো প্রশ্ন থাকলে বলবেন। আমরা setup করে দিতে পারি ১০ মিনিটে।

Day 3 after demo if no response:
> একটা special offer আছে — প্রথম মাস ফ্রি। আজই শুরু করতে পারেন।"""),

    ("Stalled — Re-engagement Touches", "followup", """1. 48h after last message: "দেখেছেন? কোনো প্রশ্ন থাকলে বলবেন।"
2. Day 5: Share a relevant tip/insight (value-add, no pitch)
3. Day 10: "আমরা launch offer দিচ্ছি — first 10 customers পাচ্ছেন lifetime discount. Interested?"
4. Day 14: "এখনো সময় আছে। জানাবেন।" → move to Lost"""),

    ("Big Problem Pitch (Pre-template)", "cold_call", """Business er shobtheke boro problem ki janen?
Customer message dey… Kintu reply dite dite deri hoye jay.
Keu raat ৩ টায় message dise… Keu abar ek sathe ৫০-১০০ jon inbox korse…
Ar ei delay er jonnoi miss hoye jay SALE.
Matrix AI apnar business ke rakhbe 24/7 online.
✔ Customer der instant reply
✔ Same time e 100+ customer handle
✔ Automatically order collect
✔ Messenger, WhatsApp, Instagram sob jaygay support
✔ Order directly dashboard e update
Apni sudhu dekhben — Ke ke order korlo, Tarpor delivery kore diben.
Business automation er next level — Matrix AI."""),
]

DUMMY_LEAD_NAMES = ["Rahim Uddin", "Karim Ahmed", "Sultana Begum", "Nasir Khan", "Farida Yasmin"]
DUMMY_SCRIPT_TITLES = ["Opening - Cold Call", "Follow-up after demo", "Price objection"]


def _clean(value):
    if value is None:
        return ""
    value = value.strip()
    if value in ("#ERROR!", "null", "N/A", "Unknown"):
        return ""
    return value


def _clean_phone(value):
    value = _clean(value)
    if not value:
        return ""
    return value.split(",")[0].strip()


def _clean_email(value):
    value = _clean(value)
    if "@" not in value:
        return ""
    return value


def _clean_url(value):
    value = _clean(value)
    if not value:
        return ""
    if "facebook.com" in value or "fb.com" in value:
        return value
    if not value.startswith(("http://", "https://")):
        return "https://" + value
    return value


class Command(BaseCommand):
    help = "Seed real sales leads from docs/03_SALES (CSV + pipeline), real scripts, and remove demo/dummy data."

    def handle(self, *args, **options):
        owner = User.objects.filter(username="jihad").first()
        if not owner:
            self.stdout.write(self.style.ERROR("User 'jihad' not found — aborting."))
            return

        stages = {s.name: s for s in PipelineStage.objects.all()}
        new_stage = stages.get("New Leads")

        self.stdout.write("Removing dummy/demo data...")
        dummy_leads = Lead.objects.filter(name__in=DUMMY_LEAD_NAMES)
        removed_leads = dummy_leads.count()
        for lead in dummy_leads:
            lead.calls.all().delete()
            lead.followups.all().delete()
            lead.meetings.all().delete()
            lead.tasks.all().delete()
            lead.activities.all().delete()
        dummy_leads.delete()
        Company.objects.filter(name="Monowa Mart").delete()
        SalesScript.objects.filter(tenant=None, title__in=DUMMY_SCRIPT_TITLES).delete()
        self.stdout.write(self.style.SUCCESS(f"  removed {removed_leads} dummy leads, Monowa Mart, demo scripts"))

        self.stdout.write("Seeding sales scripts from docs...")
        for title, cat, content in SCRIPTS:
            SalesScript.objects.update_or_create(
                tenant=None, title=title,
                defaults={"category": cat, "content": content, "active": True},
            )
        self.stdout.write(self.style.SUCCESS(f"  {len(SCRIPTS)} scripts ready"))

        companies = {}
        def get_company(name, industry="", website="", address="", notes=""):
            if not name:
                return None
            key = name.strip().lower()
            if key not in companies:
                comp, _ = Company.objects.get_or_create(
                    tenant=None, name=name.strip(),
                    defaults={"industry": industry, "website": website,
                              "address": address, "notes": notes},
                )
                companies[key] = comp
            return companies[key]

        self.stdout.write("Importing CSV leads...")
        created_count = 0
        updated_count = 0
        if not CSV_PATH.exists():
            self.stdout.write(self.style.WARNING(f"  CSV not found: {CSV_PATH}"))
        else:
            with open(CSV_PATH, encoding="utf-8-sig") as fh:
                for row in csv.DictReader(fh):
                    name = _clean(row.get("Name", ""))
                    if not name:
                        continue
                    industry = _clean(row.get("Type", ""))
                    address = _clean(row.get("Address", ""))
                    phone = _clean_phone(row.get("Phone", ""))
                    email = _clean_email(row.get("Email", ""))
                    website = _clean_url(row.get("Site Link", ""))
                    notes = _clean(row.get("Notes", ""))
                    status = _clean(row.get("Status", ""))
                    if status and "Open" in status:
                        notes = f"{notes}\nStatus: {status}".strip()

                    tier = 1 if name in TIER1 else (2 if name in TIER2 else 3)
                    score = {1: 65, 2: 50, 3: 35}.get(tier, 35)
                    tags = [f"tier-{tier}"]

                    comp = get_company(name, industry, website, address, notes)
                    lead, created = create_lead(
                        owner, name=name, phone=phone, email=email,
                        source="facebook", stage=new_stage, assigned_to=owner,
                        company=comp, website=website, industry=industry,
                        notes=notes, tags=tags, score=score,
                    )
                    if created:
                        created_count += 1
                    else:
                        updated_count += 1
            self.stdout.write(self.style.SUCCESS(f"  CSV: {created_count} created, {updated_count} existing"))

        self.stdout.write("Importing pipeline leads...")
        now = timezone.now()
        for pl in PIPELINE_LEADS:
            comp = get_company(pl["name"], pl.get("industry", ""), pl.get("website", ""), "", pl.get("next_action", ""))
            lead, created = create_lead(
                owner, name=pl["name"], phone=pl.get("phone", ""),
                source=pl["source"], stage=stages.get(pl["stage"], new_stage),
                assigned_to=owner, company=comp, website=pl.get("website", ""),
                industry=pl.get("industry", ""), score=pl["score"],
                tags=[f"tier-{pl['tier']}", "active"],
                notes=pl.get("next_action", ""),
            )
            if created:
                from datetime import datetime
                for ts_str, desc in pl["contact_log"]:
                    ts = timezone.make_aware(datetime.strptime(ts_str, "%Y-%m-%d"))
                    Activity.objects.create(
                        lead=lead, type="note", description=desc,
                        created_by=owner, timestamp=ts,
                    )
                if pl["name"] == "Natural Vine":
                    CallLog.objects.create(
                        lead=lead, staff=owner, duration=240, outcome="interested",
                        summary="Owner asked pricing + details, gave personal number (01916-404543).",
                    )
                if pl["name"] == "E Minor Guitar Academy":
                    CallLog.objects.create(
                        lead=lead, staff=owner, duration=120, outcome="no_answer",
                        summary="Called, introduced MatrixAI. No response (42K followers).",
                    )
            Followup.objects.get_or_create(
                lead=lead, note=pl["followup"],
                defaults={"due": now + timedelta(hours=4), "kind": "whatsapp",
                          "created_by": owner},
            )
            self.stdout.write(f"  {'+' if created else '='} {pl['name']} → {pl['stage']}")

        self.stdout.write("Seeding followups for Tier 1/2 CSV leads...")
        tier1_leads = Lead.objects.filter(tenant=None, tags__contains=["tier-1"]).exclude(
            name__in=[p["name"] for p in PIPELINE_LEADS])
        for lead in tier1_leads:
            Followup.objects.get_or_create(
                lead=lead, note="Call today (Tier 1)",
                defaults={"due": now + timedelta(hours=4), "kind": "call", "created_by": owner},
            )
        tier2_leads = Lead.objects.filter(tenant=None, tags__contains=["tier-2"])
        for lead in tier2_leads:
            Followup.objects.get_or_create(
                lead=lead, note="DM on FB (Tier 2)",
                defaults={"due": now + timedelta(days=1), "kind": "whatsapp", "created_by": owner},
            )

        self.stdout.write(self.style.SUCCESS(
            f"Done. Leads: {Lead.objects.filter(tenant=None).count()} | "
            f"Companies: {Company.objects.filter(tenant=None).count()} | "
            f"Followups: {Followup.objects.filter(lead__tenant=None).count()}"
        ))
