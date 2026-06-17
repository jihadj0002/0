import json
import logging
import os
import re

from .providers import call_llm

logger = logging.getLogger(__name__)

DEFAULT_PLANNER_MODEL = os.environ.get("OPENROUTER_PLANNER_MODEL", "google/gemini-2.5-flash-lite")


def _strip_code_fences(text):
    if not text:
        return ""
    return re.sub(r"^```(?:json)?\s*|```$", "", text.strip(), flags=re.IGNORECASE | re.DOTALL)


def _fallback_plan(customer_text, max_retries=3):
    words = [w for w in re.split(r"\s+", customer_text or "") if w]
    short = " ".join(words[:2]) if len(words) >= 2 else (customer_text or "")
    queries = [q for q in [customer_text, short] if q]
    seen = set()
    search_queries = []
    for q in queries:
        key = q.lower().strip()
        if key and key not in seen:
            seen.add(key)
            search_queries.append(q)
    stop_tokens = [w.lower() for w in words if len(w) > 2][:4]
    return {
        "intent": "find_product",
        "search_queries": search_queries or [customer_text or ""],
        "stop_criteria": {"name_contains": stop_tokens},
        "max_retries": max_retries,
        "needs_images": False,
        "match_mode": "specific",
        "result_limit": 1,
    }


def build_planner_prompt(user, conversation, customer_text, retry_reason=None, previous_queries=None):
    from context.models import AgentIdentity, StoreConfig

    identity = AgentIdentity.objects.filter(user=user).first()
    store = StoreConfig.objects.filter(user=user).first()

    identity_block = ""
    if identity:
        identity_block = (
            f"Store agent: {identity.name}. "
            f"Language: {identity.language}. "
            f"Tone: {identity.tone}. "
        )

    store_block = ""
    if store and store.store_name:
        store_block = f"Store name: {store.store_name}. "

    retry_block = ""
    if retry_reason:
        retry_block = f"Retry reason: {retry_reason}. "

    prev_block = ""
    if previous_queries:
        prev_block = f"Previous queries: {', '.join(previous_queries)}. "

    system_prompt = (
        "You are a search planner for a retail chatbot. Output ONLY valid JSON. "
        "The JSON schema is:\n"
        "{\n"
        "  \"intent\": \"find_product\" | \"policy\" | \"order\" | \"smalltalk\",\n"
        "  \"search_queries\": [string, ...],\n"
        "  \"stop_criteria\": {\"name_contains\": [string, ...]},\n"
        "  \"max_retries\": 3,\n"
        "  \"needs_images\": true | false,\n"
        "  \"match_mode\": \"specific\" | \"browsing\",\n"
        "  \"result_limit\": 1 | 2 | 3\n"
        "}\n"
        "Rules:\n"
        "- Always produce 2-3 search_queries when intent=find_product.\n"
        "- Translate Bengali terms to English in the search queries.\n"
        "- Use short keyword queries (1-3 words), include synonyms.\n"
        "- name_contains should include core product words (e.g., dress, frock, skirt).\n"
        "- Use max_retries=3.\n"
        "- Do NOT add unrelated product types (e.g., do not add 'bread' unless the customer asked for bread).\n"
        "- If the request is for a specific product (brand + number/variant), "
        "set match_mode=specific and result_limit=1.\n"
        "- If the customer is browsing or asks 'what do you have', set "
        "match_mode=browsing and result_limit=3.\n"
        "- Output ONLY JSON, no extra text.\n"
    )

    user_prompt = (
        f"{identity_block}{store_block}{retry_block}{prev_block}"
        f"Customer message: {customer_text}"
    )

    return system_prompt, user_prompt


def plan_search(user, conversation, customer_text, retry_reason=None, previous_queries=None):
    system_prompt, user_prompt = build_planner_prompt(
        user, conversation, customer_text, retry_reason=retry_reason, previous_queries=previous_queries
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        llm_msg, usage = call_llm(
            messages=messages,
            tools=None,
            model=DEFAULT_PLANNER_MODEL,
            temperature=0.2,
            max_tokens=400,
        )
    except Exception:
        logger.exception("Planner LLM call failed")
        return _fallback_plan(customer_text), {"model": DEFAULT_PLANNER_MODEL, "input_tokens": 0, "output_tokens": 0}

    raw = _strip_code_fences(llm_msg.content or "")
    try:
        plan = json.loads(raw)
    except Exception:
        logger.warning("Planner returned invalid JSON: %s", raw[:200])
        plan = _fallback_plan(customer_text)

    if not isinstance(plan, dict):
        plan = _fallback_plan(customer_text)

    plan.setdefault("max_retries", 3)
    plan.setdefault("intent", "find_product")
    plan.setdefault("search_queries", [])
    plan.setdefault("stop_criteria", {"name_contains": []})
    plan.setdefault("needs_images", False)
    plan.setdefault("match_mode", "specific")
    plan.setdefault("result_limit", 1)

    # Normalize queries
    queries = []
    seen = set()
    for q in plan.get("search_queries") or []:
        if not q:
            continue
        key = str(q).strip().lower()
        if key and key not in seen:
            seen.add(key)
            queries.append(str(q).strip())
    if not queries:
        queries = _fallback_plan(customer_text)["search_queries"]
    plan["search_queries"] = queries[:3]

    # Normalize stop criteria
    stop = plan.get("stop_criteria") or {}
    name_contains = [str(t).strip().lower() for t in (stop.get("name_contains") or []) if str(t).strip()]
    plan["stop_criteria"] = {"name_contains": name_contains[:5]}

    # Normalize match mode / result limit
    match_mode = str(plan.get("match_mode") or "specific").strip().lower()
    if match_mode not in ("specific", "browsing"):
        match_mode = "specific"
    plan["match_mode"] = match_mode

    try:
        limit = int(plan.get("result_limit", 1))
    except Exception:
        limit = 1
    if limit not in (1, 2, 3):
        limit = 1 if match_mode == "specific" else 3
    plan["result_limit"] = limit

    return plan, usage
