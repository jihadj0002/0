"""
MemoryManager (P0-11/P0-12): CRUD for MemoryEntry + background extraction.
"""
import json
import logging
import re
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)

# Whitelist for location extraction — "in X" where X is a real place.
_KNOWN_PLACES = [
    "dhaka", "ঢাকা", "chattogram", "chittagong", "চট্টগ্রাম", "sylhet", "সিলেট",
    "khulna", "খুলনা", "rajshahi", "রাজশাহী", "barishal", "বরিশাল", "rangpur",
    "রংপুর", "mymensingh", "ময়মনসিংহ", "cumilla", "কুমিল্লা", "cox's bazar",
    "কক্সবাজার", "narayanganj", "নারায়ণগঞ্জ", "gazipur", "গাজীপুর", "mirpur",
    "মিরপুর", "uttara", "উত্তরা", "gulshan", "গুলশান", "banani", "বনানী",
    "dhanmondi", "ধানমন্ডি", "মোহাম্মদপুর", "mohammadpur", "বাড্ডা", "badda",
    "জামালপুর", "jamalpur", "নোয়াখালী", "noakhali", "bogra", "বগুড়া",
    "dinajpur", "দিনাজপুর", "tangail", "টাঙ্গাইল", "faridpur", "ফরিদপুর",
    "pabna", "পাবনা", "kushtia", "কুষ্টিয়া", "jessore", "যশোর",
    "manikganj", "মানিকগঞ্জ", "savar", "সাভার", "ঢাকার", "চট্টগ্রামের",
]


class MemoryManager:

    @staticmethod
    def store_fact(user, key, value, memory_type="fact", confidence=1.0,
                   source="extraction", ttl=None, conversation=None):
        from context.models import MemoryEntry
        entry, created = MemoryEntry.objects.update_or_create(
            user=user, key=key, memory_type=memory_type, is_active=True,
            defaults={
                "value": value if isinstance(value, dict) else {"value": value},
                "confidence": confidence,
                "source": source,
                "expires_at": timezone.now() + ttl if ttl else None,
                "conversation": conversation,
            },
        )
        return entry

    @staticmethod
    def recall(user, memory_type=None, key=None, min_confidence=0.3):
        from context.models import MemoryEntry
        qs = MemoryEntry.objects.filter(
            user=user, is_active=True, confidence__gte=min_confidence,
        )
        if memory_type:
            qs = qs.filter(memory_type=memory_type)
        if key:
            qs = qs.filter(key=key)
        return list(qs.order_by("-confidence", "-updated_at"))

    @staticmethod
    def summarize(user, max_items=10):
        entries = MemoryManager.recall(user)
        if not entries:
            return ""
        lines = []
        for e in entries[:max_items]:
            lines.append(
                f"- {e.key}: {json.dumps(e.value, ensure_ascii=False)} "
                f"(confidence: {e.confidence:.1f})"
            )
        return "\n".join(lines)

    @staticmethod
    def forget(user, key=None, memory_type=None):
        from context.models import MemoryEntry
        qs = MemoryEntry.objects.filter(user=user, is_active=True)
        if key:
            qs = qs.filter(key=key)
        if memory_type:
            qs = qs.filter(memory_type=memory_type)
        qs.update(is_active=False)

    @staticmethod
    def extract_from_conversation(conversation, messages_text=""):
        """Background task: extract facts from a conversation turn.

        messages_text should be the CUSTOMER's message only (bot replies
        introduce English/platform tokens that pollute extraction).
        """
        if not messages_text:
            return

        facts = []
        text_lower = messages_text.lower()

        # Extract platform preference
        platforms = ["shopify", "woocommerce", "bigcommerce", "amazon", "etsy"]
        for p in platforms:
            if p in text_lower:
                facts.append({"key": "preferred_platform", "value": p, "confidence": 0.7})

        # Extract language preference (word-boundary — "en" alone matches
        # inside words like "between", "open" etc.)
        if re.search(r"\b(bangla|bangali|bengali|বাংলা)\b", text_lower):
            facts.append({"key": "preferred_language", "value": "bn", "confidence": 0.85})
        elif re.search(r"\b(english|ইংরেজি)\b", text_lower):
            facts.append({"key": "preferred_language", "value": "en", "confidence": 0.7})

        # Extract budget mention
        budget_match = re.search(
            r"(?:budget|বাজেট|spend|খরচ).{0,20}?(\d+[\d,.]*)",
            text_lower,
        )
        if budget_match:
            try:
                amount = float(budget_match.group(1).replace(",", ""))
                facts.append({
                    "key": "budget_mention",
                    "value": {"amount": amount},
                    "confidence": 0.5,
                })
            except ValueError:
                pass

        # Extract location/city — whitelist of known places to avoid junk like
        # "stock", "house", "one" being captured as locations.
        city_pattern = re.compile(
            r"\b(?:in|from|at|থেকে|হতে|এ)\s+(" + "|".join(_KNOWN_PLACES) + r")\b",
            re.IGNORECASE,
        )
        match = city_pattern.search(messages_text)
        if match:
            facts.append({
                "key": "location",
                "value": match.group(1),
                "confidence": 0.7,
            })

        for fact in facts:
            try:
                MemoryManager._store_if_not_downgraded(
                    conversation.user, fact["key"],
                    fact["value"], confidence=fact["confidence"],
                    conversation=conversation,
                )
            except Exception as exc:
                logger.warning("Failed to store fact: %s", exc)

        return facts

    @staticmethod
    def _store_if_not_downgraded(user, key, value, confidence=1.0, conversation=None):
        """Store a fact only if it doesn't downgrade a higher-confidence one."""
        from context.models import MemoryEntry

        existing = MemoryEntry.objects.filter(
            user=user, key=key, is_active=True,
        ).order_by("-confidence").first()
        if existing and existing.confidence > confidence:
            logger.info(
                "Skipping lower-confidence %s (%s < %s) — keeping existing",
                key, confidence, existing.confidence,
            )
            return existing
        return MemoryManager.store_fact(
            user, key, value, confidence=confidence,
            source="extraction", conversation=conversation,
        )
