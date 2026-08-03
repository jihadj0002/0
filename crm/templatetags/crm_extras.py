import re

from django import template

register = template.Library()

_BD_RE = re.compile(r"^0(1[3-9]\d{8})$")
_BD_LOCAL_RE = re.compile(r"^(1[3-9]\d{8})$")
_BD_INTERNATIONAL_RE = re.compile(r"^(?:880)?(1[3-9]\d{8})$")


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
