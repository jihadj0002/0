"""Orchestrator replay test — dry_run passes through user jihad's pipeline.

Usage: python3 manage.py shell < scripts/replay_orchestrator.py
Reads a transcript from scripts/replay_cases.py (or inline below).
"""
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "theMatrixAi.settings")
import django

django.setup()

from django.contrib.auth import get_user_model
from back.models import Conversation, Integration, Message
from api.ai.orchestrator import Orchestrator

# Force the LOCAL catalog in this sandbox (the active Monowamart ERP source is
# unreachable here). Keeps user config untouched; only affects this run.
try:
    import api.products.factory as pf
    pf.get_active_source = lambda user: None
except Exception:
    pass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

User = get_user_model()
user = User.objects.get(username="jihad")
integration = Integration.get_active(user, "messenger") or Integration.objects.filter(user=user).first()

conv, _ = Conversation.objects.get_or_create(
    user=user, platform=integration.platform, customer_id="replay-session-1"
)
# Fresh slate
conv.messages.all().delete()
conv.current_product = None
conv.customer_name = ""
conv.customer_phone = ""
conv.customer_city = ""
conv.customer_address = ""
conv.save(update_fields=["current_product", "customer_name", "customer_phone", "customer_city", "customer_address"])
from context.models import SessionContext
SessionContext.objects.filter(conversation=conv).delete()

CASES = [
    "hello",
    "ki product ache?",
    "amer achar er dam koto?",
    "tetuler achar ta dekhan",
    "amar jolpai ta koi?",
    "tetuler achar 2 pcs order korbo",
    "ami ashik, phone 01712345678, address dhaka",
    "ok, final kor",
    "order status ki?",
    "thanks bhai",
]


def run_case(text):
    msg = Message.objects.create(conversation=conv, sender="customer", text=text)
    o = Orchestrator(dry_run=True)
    try:
        o.process(conv, msg)
        bot = conv.messages.filter(sender="bot").order_by("-timestamp").first()
        trace = (bot.raw_payload or {}) if bot else {}
        print("=" * 70)
        print(f"IN : {text}")
        print(f"INTENT: {trace.get('intent')} (conf={trace.get('intent_confidence')})")
        print(f"VAL  : {trace.get('validation')}")
        print(f"TOOLS: {[(t['tool'], t['state']) for t in trace.get('tool_calls', [])]}")
        print(f"OUT : {bot.text[:300] if bot else 'NO REPLY'}")
        if bot and bot.attachments:
            print(f"MEDIA: images={len(bot.attachments.get('images', []))} "
                  f"cards={len(bot.attachments.get('cards', []))} type={bot.attachments.get('type')}")
    except Exception as exc:
        print("=" * 70)
        print(f"IN : {text}")
        print(f"ERROR: {type(exc).__name__}: {exc}")


for c in CASES:
    run_case(c)

print("\nSessionContext state:", end=" ")
from context.models import SessionContext
sc = SessionContext.objects.filter(conversation=conv).first()
print(sc.state if sc else "none")
