import json
import re

from django import template
from django.utils.html import escape

register = template.Library()

_BD_RE = re.compile(r"^0(1[3-9]\d{8})$")
_BD_LOCAL_RE = re.compile(r"^(1[3-9]\d{8})$")
_BD_INTERNATIONAL_RE = re.compile(r"^(?:880)?(1[3-9]\d{8})$")


@register.filter
def json_attr(value):
    """Encode a value as a JSON string safe to embed in an HTML attribute.

    JSON-escapes quotes/backslashes/newlines and HTML-escapes the result so
    the attribute survives HTML parsing (entities are decoded back to the raw
    JSON string in the DOM); JS recovers the original text via JSON.parse.
    Unlike |escapejs, real newlines survive (escapejs leaves \\n literal,
    which the HTML parser collapses to spaces in attribute values).
    """
    return escape(json.dumps(value or ""))


@register.filter
def bd_phone(value):
    """Normalize a Bangladeshi mobile number to +8801XXXXXXXXX form.

    Accepts 01XXXXXXXXX, 1XXXXXXXXX, 8801XXXXXXXXX or +8801XXXXXXXXX;
    returns the value unchanged if it can't be recognized (landline,
    foreign number, empty).
    """
    if not value:
        return ""
    digits = re.sub(r"[\s\-\(\)\.\+]", "", str(value))
    m = _BD_RE.match(digits)
    if m:
        return "+880" + m.group(1)
    m = _BD_LOCAL_RE.match(digits)
    if m:
        return "+880" + m.group(1)
    m = _BD_INTERNATIONAL_RE.match(digits)
    if m:
        return "+880" + m.group(1)
    return value


@register.filter
def wa_digits(value):
    """Digits-only number without '+' for wa.me links."""
    return re.sub(r"\D", "", str(bd_phone(value)))


@register.filter
def duration_display(value):
    """Human-friendly duration. Model stores SECONDS: 300 -> '5m', 45 -> '45s'."""
    try:
        secs = int(value or 0)
    except (TypeError, ValueError):
        secs = 0
    if secs <= 0:
        return "—"
    if secs < 60:
        return f"{secs}s"
    m, s = divmod(secs, 60)
    return f"{m}m {s}s" if s else f"{m}m"
