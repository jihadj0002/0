"""Seed the CRM Learn hub from docs/sales-enablement markdown files.

Idempotent: re-running updates existing articles by slug and creates new ones.

Usage:
    python manage.py seed_learn
"""
import re
from pathlib import Path

from django.core.management.base import BaseCommand

from crm.models import FAQ, LearningArticle, LearningTopic

DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"

_FAQ_QUESTION = re.compile(r"^###\s+(\d+)\.\s+(.+)$")
_FAQ_SECTION = re.compile(r"^##\s+(.+)$")

SOURCES = [
    # (topic, topic_order, topic_slug, article_order, slug, rel_path)
    ("Training Modules", 1, "training-modules", 0, "training-overview", "sales-enablement/training/TRAINING-INDEX.md"),
    ("Training Modules", 1, "training-modules", 1, "module-01-product", "sales-enablement/training/module-01-product.md"),
    ("Training Modules", 1, "training-modules", 2, "module-02-prospecting", "sales-enablement/training/module-02-prospecting.md"),
    ("Training Modules", 1, "training-modules", 3, "module-03-first-contact", "sales-enablement/training/module-03-first-contact.md"),
    ("Training Modules", 1, "training-modules", 4, "module-04-objection-handling", "sales-enablement/training/module-04-objection-handling.md"),
    ("Training Modules", 1, "training-modules", 5, "module-05-demo-and-close", "sales-enablement/training/module-05-demo-and-close.md"),
    ("Training Modules", 1, "training-modules", 6, "module-06-crm-discipline", "sales-enablement/training/module-06-crm-discipline.md"),
    ("Training Modules", 1, "training-modules", 7, "module-07-roleplay-and-quiz", "sales-enablement/training/module-07-roleplay-and-quiz.md"),
    ("Training Modules", 1, "training-modules", 8, "module-08-leads-and-qualification", "sales-enablement/training/module-08-leads-and-qualification.md"),
    ("Training Modules", 1, "training-modules", 9, "module-09-sales-psychology", "sales-enablement/training/module-09-sales-psychology.md"),
    ("Training Modules", 1, "training-modules", 10, "module-10-crm-operations", "sales-enablement/training/module-10-crm-operations.md"),
    ("Playbook & Plans", 2, "playbook-plans", 1, "sales-team-playbook", "sales-enablement/SALES-TEAM-PLAYBOOK.md"),
    ("Playbook & Plans", 2, "playbook-plans", 2, "video-demo-plan", "sales-enablement/video-demo-plan.md"),
    ("Master Scripts", 3, "master-scripts", 1, "call-script", "call_faq/call-script.md"),
    ("Master Scripts", 3, "master-scripts", 2, "text-script", "call_faq/text-script.md"),
    ("Master Scripts", 3, "master-scripts", 3, "follow-up-cadence", "03_SALES/crm/CADENCE.md"),
    ("Master Scripts", 3, "master-scripts", 4, "pipeline-rules", "03_SALES/crm/PIPELINE.md"),
    ("Master Scripts", 3, "master-scripts", 5, "contact-log", "03_SALES/crm/CONTACT-LOG.md"),
    ("Product & FAQ", 4, "product-faq", 1, "product-faq", "call_faq/faq.md"),
    ("Product & FAQ", 4, "product-faq", 2, "product-features", "00_CONTEXT/features_bengali.md"),
    ("Product & FAQ", 4, "product-faq", 3, "product-pricing", "00_CONTEXT/pricing.md"),
]

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_ORDERED = re.compile(r"^\s*\d+[.)]\s+")


def _code_repl(match):
    stem = Path(match.group(1)).stem
    if stem in _slug_map:
        return f'<a href="/crm/learn/{_slug_map[stem]}/"><code>{match.group(1)}</code></a>'
    return f"<code>{match.group(1)}</code>"


def inline(text):
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = _INLINE_CODE.sub(_code_repl, text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)
    text = _LINK.sub(_link_repl, text)
    return text


def _link_repl(match):
    label, url = match.group(1), match.group(2)
    stem = Path(url).stem
    if stem in _slug_map:
        return f'<a href="/crm/learn/{_slug_map[stem]}/">{label}</a>'
    return f"<strong>{label}</strong>"  # repo-relative doc link -> plain text


_slug_map = {slug: slug for _, _, _, _, slug, _ in SOURCES}


def _is_table_sep(row):
    return all(re.fullmatch(r":?-{2,}:?", cell.strip()) for cell in row.strip().strip("|").split("|"))


def _render_table(rows):
    clean = [row.strip().strip("|").split("|") for row in rows if not _is_table_sep(row)]
    if not clean:
        return ""
    header = "".join(f"<th>{inline(c.strip())}</th>" for c in clean[0])
    body = "".join(
        "<tr>" + "".join(f"<td>{inline(c.strip())}</td>" for c in r) + "</tr>"
        for r in clean[1:]
    )
    return f"<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"


def _render_list(items, ordered):
    tag = "ol" if ordered else "ul"
    return f"<{tag}>" + "".join(f"<li>{inline(item)}</li>" for item in items) + f"</{tag}>"


def md_to_html(md_text):
    lines = md_text.splitlines()
    out = []
    para, ul, ol, table, quote = [], [], [], [], []
    first_h1 = True

    def flush():
        if para:
            out.append("<p>" + "<br>".join(inline(p) for p in para) + "</p>")
        if ul:
            out.append(_render_list(ul, ordered=False))
        if ol:
            out.append(_render_list(ol, ordered=True))
        if table:
            out.append(_render_table(table))
        if quote:
            out.append("<blockquote>" + "<br>".join(inline(q) for q in quote) + "</blockquote>")
        para.clear(); ul.clear(); ol.clear(); table.clear(); quote.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if stripped.startswith("|") and "|" in stripped:
            if not table:
                flush()
            table.append(stripped)
            continue
        if stripped.startswith("> "):
            flush(); quote.append(stripped[2:]); continue
        if stripped.startswith("### "):
            flush(); out.append(f"<h3>{inline(stripped[4:])}</h3>"); continue
        if stripped.startswith("## "):
            flush(); out.append(f"<h2>{inline(stripped[3:])}</h2>"); continue
        if stripped.startswith("# "):
            flush()
            if first_h1:
                first_h1 = False  # title line — dropped, used as article title
            else:
                out.append(f"<h1>{inline(stripped[2:])}</h1>")
            continue
        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", stripped):
            flush(); out.append("<hr>"); continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            flush(); ul.append(stripped[2:]); continue
        m = _ORDERED.match(stripped)
        if m:
            flush(); ol.append(stripped[m.end():]); continue
        para.append(stripped)
    flush()
    return "\n".join(out)


def _plain(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip()


def _summary(html):
    m = re.search(r"<p>(.*?)</p>", html, re.S)
    plain = _plain(m.group(1)) if m else ""
    return plain[:240]


def _title(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem.replace("-", " ").title()


def _seed_faq(path, stdout, style):
    """Seed the CRM FAQ page from docs/call_faq/faq.md (numbered ### questions).

    Returns (created, updated).
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    created = updated = 0
    category, question, answer_parts = "", None, []

    def flush():
        nonlocal created, updated, question, answer_parts
        if question is not None:
            answer = "\n\n".join(" ".join(p.split()) for p in answer_parts).strip()
            faq, was_created = FAQ.objects.get_or_create(
                question=question, tenant__isnull=True,
                defaults={"answer": answer, "category": category, "active": True},
            )
            if not was_created:
                faq.answer, faq.category, faq.active = answer, category, True
                faq.save(update_fields=["answer", "category", "active"])
            created += int(was_created)
            updated += int(not was_created)
        question, answer_parts = None, []

    for line in lines:
        stripped = line.strip()
        m = _FAQ_SECTION.match(stripped)
        if m:
            flush()
            category = m.group(1).strip()
            continue
        m = _FAQ_QUESTION.match(stripped)
        if m:
            flush()
            question = m.group(2).strip()
            continue
        if question is None or not stripped:
            continue
        if stripped.startswith("|") or stripped.startswith("---"):
            continue
        if stripped.startswith("#"):
            continue
        answer_parts.append(stripped)
    flush()
    stdout.write(style.SUCCESS(f"seed_learn: FAQ — {created} created, {updated} updated "
                               f"({FAQ.objects.filter(active=True).count()} active FAQs)."))


class Command(BaseCommand):
    help = "Seed/update the CRM Learn hub from docs/sales-enablement markdown files."

    def handle(self, *args, **options):
        created = updated = 0
        for topic_name, topic_order, topic_slug, article_order, slug, rel in SOURCES:
            path = DOCS_DIR / rel
            if not path.exists():
                self.stderr.write(self.style.WARNING(f"Missing source: {rel} — skipping"))
                continue
            topic, _ = LearningTopic.objects.update_or_create(
                slug=topic_slug,
                defaults={"name": topic_name, "order": topic_order},
            )
            md_text = path.read_text(encoding="utf-8")
            html = md_to_html(md_text)
            title = _title(path)
            _, was_created = LearningArticle.objects.update_or_create(
                slug=slug,
                defaults={
                    "topic": topic, "title": title, "order": article_order,
                    "summary": _summary(html), "content": html, "active": True,
                },
            )
            created += int(was_created)
            updated += int(not was_created)

        self.stdout.write(
            self.style.SUCCESS(f"seed_learn: {created} created, {updated} updated "
                               f"({LearningArticle.objects.filter(active=True).count()} active articles).")
        )
        _seed_faq(DOCS_DIR / "call_faq/faq.md", self.stdout, self.style)
