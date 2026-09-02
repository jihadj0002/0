from django.core.management.base import BaseCommand
from crm.models import EmailTemplate


TEMPLATES = [
    {
        "name": "Cold Introduction — General",
        "category": "cold",
        "subject": "A better way to handle {{lead.company.name}}'s customer conversations",
        "body_html": (
            "<p>Hi {{lead.name}},</p>"
            "<p>I came across {{lead.company.name}} and noticed you're doing great work in {{lead.industry}}.</p>"
            "<p>We built MatrixAI to help businesses like yours automate customer engagement across WhatsApp, Messenger, and Instagram. Would you be open to a quick chat?</p>"
            "<p>Best regards,<br>{{sender.name}}</p>"
        ),
        "body_text": (
            "Hi {{lead.name}},\n\n"
            "I came across {{lead.company.name}} and noticed you're doing great work in {{lead.industry}}.\n\n"
            "We built MatrixAI to help businesses like yours automate customer engagement across WhatsApp, Messenger, and Instagram. Would you be open to a quick chat?\n\n"
            "Best regards,\n{{sender.name}}"
        ),
        "is_system": True,
    },
    {
        "name": "Follow-up — Check-in (Day 3)",
        "category": "followup",
        "subject": "Following up — {{lead.company.name}}",
        "body_html": (
            "<p>Hi {{lead.name}},</p>"
            "<p>I wanted to follow up on my previous message. I'd love to show you how MatrixAI can help {{lead.company.name}} save time on customer support.</p>"
            "<p>Would 15 minutes this week work?</p>"
            "<p>Best,<br>{{sender.name}}</p>"
        ),
        "body_text": (
            "Hi {{lead.name}},\n\n"
            "I wanted to follow up on my previous message. I'd love to show you how MatrixAI can help {{lead.company.name}} save time on customer support.\n\n"
            "Would 15 minutes this week work?\n\n"
            "Best,\n{{sender.name}}"
        ),
        "is_system": True,
    },
    {
        "name": "Nurture — Value Proposition",
        "category": "nurture",
        "subject": "How {{lead.company.name}} can automate 80% of customer responses",
        "body_html": (
            "<p>Hi {{lead.name}},</p>"
            "<p>Did you know that businesses using AI chatbots save up to 30% on customer support costs?</p>"
            "<p>MatrixAI can help {{lead.company.name}} automatically respond to common customer questions, qualify leads, and book meetings — 24/7.</p>"
            "<p>Want to see it in action?</p>"
            "<p>Cheers,<br>{{sender.name}}</p>"
        ),
        "body_text": (
            "Hi {{lead.name}},\n\n"
            "Did you know that businesses using AI chatbots save up to 30% on customer support costs?\n\n"
            "MatrixAI can help {{lead.company.name}} automatically respond to common customer questions, qualify leads, and book meetings — 24/7.\n\n"
            "Want to see it in action?\n\n"
            "Cheers,\n{{sender.name}}"
        ),
        "is_system": True,
    },
    {
        "name": "Proposal — Partnership",
        "category": "proposal",
        "subject": "Partnership opportunity for {{lead.company.name}}",
        "body_html": (
            "<p>Hi {{lead.name}},</p>"
            "<p>I'd like to propose a partnership between MatrixAI and {{lead.company.name}}. We believe our AI platform would be a great fit for your customer engagement needs.</p>"
            "<p>Key benefits include:</p>"
            "<ul>"
            "<li>24/7 automated responses</li>"
            "<li>Multi-platform support (WhatsApp, Messenger, Instagram)</li>"
            "<li>Seamless CRM integration</li>"
            "</ul>"
            "<p>Let's schedule a call to discuss this further.</p>"
            "<p>Best regards,<br>{{sender.name}}</p>"
        ),
        "body_text": (
            "Hi {{lead.name}},\n\n"
            "I'd like to propose a partnership between MatrixAI and {{lead.company.name}}. We believe our AI platform would be a great fit for your customer engagement needs.\n\n"
            "Key benefits include:\n"
            "- 24/7 automated responses\n"
            "- Multi-platform support (WhatsApp, Messenger, Instagram)\n"
            "- Seamless CRM integration\n\n"
            "Let's schedule a call to discuss this further.\n\n"
            "Best regards,\n{{sender.name}}"
        ),
        "is_system": True,
    },
    {
        "name": "Announcement — New Feature",
        "category": "announcement",
        "subject": "New: AI-powered cold emailing in MatrixAI",
        "body_html": (
            "<p>Hi {{lead.name}},</p>"
            "<p>We're excited to announce our new cold emailing feature! Now you can run full email campaigns directly from MatrixAI CRM.</p>"
            "<p>Features include:</p>"
            "<ul>"
            "<li>Personalized email templates with dynamic variables</li>"
            "<li>Smart lead segmentation</li>"
            "<li>Automated follow-ups</li>"
            "<li>Detailed campaign analytics</li>"
            "</ul>"
            "<p>Log in to try it now!</p>"
            "<p>The MatrixAI Team</p>"
        ),
        "body_text": (
            "Hi {{lead.name}},\n\n"
            "We're excited to announce our new cold emailing feature! Now you can run full email campaigns directly from MatrixAI CRM.\n\n"
            "Features include:\n"
            "- Personalized email templates with dynamic variables\n"
            "- Smart lead segmentation\n"
            "- Automated follow-ups\n"
            "- Detailed campaign analytics\n\n"
            "Log in to try it now!\n\n"
            "The MatrixAI Team"
        ),
        "is_system": True,
    },
]


class Command(BaseCommand):
    help = "Seed sample email templates for the cold mailing feature"

    def handle(self, *args, **options):
        count = 0
        for tpl in TEMPLATES:
            obj, created = EmailTemplate.objects.update_or_create(
                tenant=None,
                name=tpl["name"],
                defaults={
                    "category": tpl["category"],
                    "subject": tpl["subject"],
                    "body_html": tpl["body_html"],
                    "body_text": tpl["body_text"],
                    "is_system": tpl["is_system"],
                    "is_active": True,
                    "created_by": None,
                },
            )
            count += 1

        self.stdout.write(self.style.SUCCESS(f"Seeded {count} email templates"))