"""
Image-based lead import: vision LLM extracts structured lead details from an
uploaded image (business card / lead detail sheet), then creates unassigned
leads. Owner-only — guards live in the views.
"""
import json
import logging
import os

from .services import create_lead, log_activity

logger = logging.getLogger(__name__)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
VISION_MODEL = "google/gemini-2.5-flash-lite"
# VISION_MODEL = "openai/gpt-4o-mini"

LEAD_FIELDS = ("name", "phone", "email", "address", "website", "industry", "summary", "tags", "tier", "notes")
MAX_TAGS = 12


def normalize_tags(value, tier=""):
    """Normalize tags (list or comma string) + an optional tier into a clean list."""
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        raw = [t for t in value.split(",") if t.strip()]
    else:
        raw = []
    tags = []
    for t in raw:
        t = str(t).strip().strip("#").lower()[:20]
        if t and t not in tags:
            tags.append(t)
    tier = str(tier or "").strip().strip("#").lower()[:20]
    if tier and tier not in tags:
        tags.append(tier)
    return tags[:MAX_TAGS]


def _vision_client():
    from openai import OpenAI
    return OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
    )


def extract_leads_from_image(data_url: str) -> list:
    """Analyse an image (base64 data URL) via a mini vision model.

    Returns a list of dicts: {name, phone, email, address, website, industry, summary}.
    Entries without a name are dropped. Returns [] on any failure.
    """
    try:
        client = _vision_client()
        resp = client.chat.completions.create(
            model=VISION_MODEL,
            timeout=90,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Return ONLY valid JSON — no markdown, no code fences, no extra text.\n\n"
                            "This image is a lead-information sheet (business card, visiting card, "
                            "shop signboard, or a page with contact details of one or more businesses).\n"
                            "Scan the ENTIRE image carefully — all corners, headers, footers, stamps.\n\n"
                            "Extract one entry per business/person found. For each entry return:\n"
                            "1. \"name\" — business or person name (REQUIRED; omit the entry entirely if no name can be read)\n"
                            "2. \"phone\" — phone/mobile numbers, deduplicated, as plain text\n"
                            "3. \"email\" — email address if visible\n"
                            "4. \"address\" — full address if visible\n"
                            "5. \"website\" — website or Facebook/Instagram handle if visible\n"
                            "6. \"industry\" — what the business does (e.g. garments, restaurant), if derivable\n"
                            "7. \"summary\" — one concise sentence summarising this entry, including any extra visible details (timings, business type, Follower counts, likes)\n"
                            "8. \"tags\" — a JSON array of short lowercase labels clearly visible or derivable, e.g. [\"tier-2\",\"hot\",\"new\",\"wholesale\",\"distributor\",\"budget-conscious\"] — max 6, empty array if none\n"
                            "9. \"tier\" — \"tier-1\", \"tier-2\" or \"tier-3\" when the image marks follower count within 10k for tier 1 within 5k for tier 2 and up to 50k for tier 3, else \"\"\n"
                            "10. \"notes\" — any OTHER visible details not covered above (BIO/VAT/BIN/trade license numbers, business hours, owner name or designation, payment terms, product lines) — free text, else \"\"\n\n"
                            "Use empty string when a field is not found. Transcribe text EXACTLY as written — do not invent details.\n"
                            "Response shape:\n"
                            "{\"leads\":[{\"name\":\"...\",\"phone\":\"...\",\"email\":\"...\","
                            "\"address\":\"...\",\"website\":\"...\",\"industry\":\"...\",\"summary\":\"...\","
                            "\"tags\":[\"tier-2\",\"new\"],\"tier\":\"tier-2\",\"notes\":\"...\"}]}"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url, "detail": "high"},
                    },
                ],
            }],
            max_tokens=2500,
            temperature=0.1,
        )
        text = (resp.choices[0].message.content or "").strip()
        text = _strip_fences(text)
        data = json.loads(text)
        raw = data.get("leads", []) if isinstance(data, dict) else []
        if isinstance(data, list):
            raw = data
        leads = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            cleaned = {f: str(entry.get(f, "") or "").strip() for f in LEAD_FIELDS}
            cleaned["tags"] = normalize_tags(entry.get("tags"))
            if cleaned.get("name"):
                leads.append(cleaned)
        return leads
    except Exception as exc:
        logger.warning("Lead image extraction failed: %s", exc)
        return []


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def create_lead_from_dict(user, d: dict):
    """Create one unassigned lead from extracted data. Returns (lead, created)."""
    name = str(d.get("name", "")).strip()
    if not name:
        return None, False
    notes_parts = []
    address = str(d.get("address", "") or "").strip()
    extra = str(d.get("notes", "") or "").strip()
    summary = str(d.get("summary", "") or "").strip()
    if address:
        notes_parts.append(f"Address: {address}")
    if extra:
        notes_parts.append(extra)
    if summary:
        notes_parts.append(summary)
    lead, created = create_lead(
        user,
        name=name,
        phone=str(d.get("phone", "") or "").strip(),
        email=str(d.get("email", "") or "").strip(),
        website=str(d.get("website", "") or "").strip(),
        industry=str(d.get("industry", "") or "").strip(),
        notes="\n".join(notes_parts) if notes_parts else "",
        tags=normalize_tags(d.get("tags"), d.get("tier")),
        source="import",
        assigned_to=None,
        log=True,
    )
    if created:
        log_activity(lead, "note", "Imported from image", user)
    return lead, created


def leads_from_payload(payload):
    """Normalise the review payload sent by the frontend into create dicts."""
    if isinstance(payload, dict):
        payload = payload.get("leads", [])
    if not isinstance(payload, list):
        return []
    out = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        clean = {f: str(entry.get(f, "") or "").strip() for f in LEAD_FIELDS}
        clean["tags"] = normalize_tags(entry.get("tags"), entry.get("tier"))
        if clean.get("name"):
            out.append(clean)
    return out


__all__ = ["extract_leads_from_image", "create_lead_from_dict", "leads_from_payload", "VISION_MODEL"]