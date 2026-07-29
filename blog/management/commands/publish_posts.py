import re, markdown
from datetime import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.html import strip_tags
from django.contrib.auth.models import User
from blog.models import BlogPost, Category, Tag


POSTS = [
    {
        "file": "01-handle-100-customer-messages-daily.md",
        "title": "How to Handle 100+ Customer Messages Daily on Facebook (Without Hiring Staff)",
        "slug": "handle-100-customer-messages-daily-facebook",
        "category": "Social Selling Tips",
        "tags": ["facebook-selling", "social-media-automation", "customer-service", "messenger-bot"],
        "excerpt": "Every late reply is a lost sale. Here's how top social sellers in Bangladesh handle 100+ daily Facebook messages without hiring staff — using AI automation.",
        "meta_title": "Handle 100+ Facebook Messages Daily Without Hiring Staff",
        "meta_description": "Learn how social sellers in Bangladesh automate 100+ daily Facebook messages with AI. Save 90+ hours/month and never lose a sale to a late reply.",
        "is_featured": True,
        "published_at": "2026-07-28 10:00:00",
    },
    {
        "file": "02-what-is-f-commerce-bangladesh.md",
        "title": "What is F-Commerce in Bangladesh? The Complete Guide to Facebook Commerce",
        "slug": "what-is-f-commerce-bangladesh",
        "category": "Bangladesh E-Commerce",
        "tags": ["f-commerce", "bangladesh-ecommerce", "social-commerce", "facebook-selling"],
        "excerpt": "F-Commerce is the backbone of online selling in Bangladesh. This complete guide explains how Facebook commerce works, the challenges sellers face, and how AI is changing the game.",
        "meta_title": "What is F-Commerce in Bangladesh? Complete Guide 2026",
        "meta_description": "F-Commerce in Bangladesh explained: how sellers use Facebook Messenger to sell, the 3 biggest challenges, and why smart sellers are moving to AI automation.",
        "is_featured": False,
        "published_at": "2026-07-29 10:00:00",
    },
    {
        "file": "03-ai-chatbot-vs-manual-social-selling.md",
        "title": "AI Chatbot vs Manual: What Actually Saves More for Social Sellers in Bangladesh?",
        "slug": "ai-chatbot-vs-manual-social-selling-bangladesh",
        "category": "Automation & AI",
        "tags": ["ai-chatbot", "social-media-automation", "messenger-bot", "chatbot-bangladesh"],
        "excerpt": "Manual replying isn't free. It costs time, sales, and growth. Here's a side-by-side comparison with real numbers — AI chatbot vs manual vs rule-based bots.",
        "meta_title": "AI Chatbot vs Manual Social Selling: Cost Comparison Bangladesh",
        "meta_description": "Real cost comparison: manual replying vs AI chatbot for Facebook sellers in Bangladesh. See how much time and money you're actually losing by not automating.",
        "is_featured": False,
        "published_at": "2026-07-29 10:00:00",
    },
]


CATEGORIES = [
    {"name": "Social Selling Tips", "slug": "social-selling-tips", "description": "Tips and strategies for social selling on Facebook, Instagram, and Messenger."},
    {"name": "Bangladesh E-Commerce", "slug": "bangladesh-ecommerce", "description": "Guides about the e-commerce landscape in Bangladesh, including F-Commerce."},
    {"name": "Automation & AI", "slug": "automation-ai", "description": "How AI and automation help businesses scale customer conversations."},
]

TAGS = [
    {"name": "Facebook Selling", "slug": "facebook-selling"},
    {"name": "Social Media Automation", "slug": "social-media-automation"},
    {"name": "Customer Service", "slug": "customer-service"},
    {"name": "F-Commerce", "slug": "f-commerce"},
    {"name": "Bangladesh E-Commerce", "slug": "bangladesh-ecommerce"},
    {"name": "AI Chatbot", "slug": "ai-chatbot"},
    {"name": "Messenger Bot", "slug": "messenger-bot"},
    {"name": "Social Commerce", "slug": "social-commerce"},
    {"name": "Chatbot Bangladesh", "slug": "chatbot-bangladesh"},
]


def md_to_html(md_text):
    html = markdown.markdown(md_text, extensions=["tables", "fenced_code", "attr_list"])
    return html


def clean_md_body(raw):
    lines = raw.split("\n")
    start = 0
    for i, line in enumerate(lines):
        if line.strip() == "---" and i > 0:
            start = i + 1
            break
    body = "\n".join(lines[start:])
    body = re.sub(r"\n\*Keywords:.*$", "", body.rstrip())
    body = re.sub(r"👉.*$", "", body, flags=re.MULTILINE)
    body = body.strip()
    return body


class Command(BaseCommand):
    help = "Publish blog posts from markdown source files"

    def handle(self, *args, **options):
        author = User.objects.filter(is_superuser=True).first()
        if not author:
            self.stderr.write("No superuser found. Create one first.")
            return

        cats = {}
        for c in CATEGORIES:
            obj, _ = Category.objects.get_or_create(
                slug=c["slug"], defaults={"name": c["name"], "description": c["description"]}
            )
            cats[obj.name] = obj
            self.stdout.write(f"  Category: {obj.name}")

        tag_objs = {}
        for t in TAGS:
            obj, _ = Tag.objects.get_or_create(slug=t["slug"], defaults={"name": t["name"]})
            tag_objs[t["slug"]] = obj
        self.stdout.write(f"  Tags: {len(TAGS)} created/found")

        base = "/home/jihad/code/matrix/0/docs/blog_posts"
        created = 0
        for post in POSTS:
            if BlogPost.objects.filter(slug=post["slug"]).exists():
                self.stdout.write(f"  SKIP (exists): {post['slug']}")
                continue

            path = f"{base}/{post['file']}"
            raw = open(path).read()
            body = clean_md_body(raw)
            content = md_to_html(body)
            content = re.sub(r"Start Free Trial\]\(https://thematrixai\.xyz\)", "Start Free Trial", content)
            content = re.sub(r"Try MatrixAI Free\]\(https://thematrixai\.xyz\)", "Try MatrixAI Free", content)

            pub = timezone.make_aware(datetime.strptime(post["published_at"], "%Y-%m-%d %H:%M:%S"))

            bp = BlogPost(
                title=post["title"],
                slug=post["slug"],
                excerpt=post["excerpt"],
                content=content,
                author=author,
                category=cats[post["category"]],
                status="published",
                published_at=pub,
                is_featured=post["is_featured"],
                meta_title=post["meta_title"],
                meta_description=post["meta_description"],
            )
            bp.save()

            for tag_slug in post["tags"]:
                bp.tags.add(tag_objs[tag_slug])

            created += 1
            self.stdout.write(f"  PUBLISHED: {bp.title}")

        self.stdout.write(self.style.SUCCESS(f"\nDone. {created} posts published."))
