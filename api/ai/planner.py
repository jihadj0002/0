"""
Planner (P0-4 / Phase 1.1): Given intent + context, produce a structured AIPlan
{goal, conversation_state, required_tools, ask_clarification, confidence, steps}.

Three modes:
- Direct: simple intent → single tool call (deterministic, free)
- Template: known workflow → predefined multi-step sequence (deterministic)
- LLM: complex/ambiguous → structured JSON plan, then software validation

The LLM proposes; software validates (PlanValidator) — see 'AI as advisor':
reject tools outside the intent's allowed set, never create orders here, and
surface low confidence / ask_clarification to the orchestrator.
"""
import json
import logging
from pathlib import Path

from back.models import Message

from .context import AIPlan, ConversationContext, PlanStep
from .tools import ToolRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool routing: the deterministic allow-list per intent ("Tool Routing" in the
# doc). The LLM is NEVER handed tools outside this set for a given intent —
# software, not the model, decides what is allowed.
# ---------------------------------------------------------------------------

_INTENT_ALLOWED_TOOLS: dict[str, set[str]] = {
    "ASK_PRICE": {"search_products", "get_product_details"},
    "ASK_STOCK": {"search_products", "check_inventory"},
    "SEARCH_PRODUCT": {"search_products", "get_product_details", "send_images"},
    "ASK_DETAILS": {"search_products", "get_product_details", "send_images"},
    "RECOMMEND": {"search_products", "get_product_details", "send_images"},
    "COMPARE_PRODUCTS": {"search_products", "get_product_details"},
    "CATALOG": {"search_products", "send_images"},
    "SEND_IMAGES": {"search_products", "send_images"},
    "CHECK_ORDER": {"get_order_status", "track_shipment"},
    "CANCEL_ORDER": {"get_order_status", "create_ticket"},
    "RETURN_PRODUCT": {"search_knowledge_base", "create_ticket"},
    "ASK_DELIVERY": {"search_knowledge_base", "track_shipment"},
    "ASK_PAYMENT": {"search_knowledge_base", "get_payment_link"},
    "ASK_FAQ": {"search_knowledge_base"},
    "HUMAN_SUPPORT": {"create_ticket"},
    "ANALYTICS_QUERY": {"get_sales_summary"},
    "NEGOTIATE": {"search_products", "get_product_details"},
    "UPGRADE_PLAN": {"search_knowledge_base"},
    "BILLING_QUERY": {"search_knowledge_base"},
    "STORE_SYNC": {"search_knowledge_base"},
    "FRUSTRATION": {"search_knowledge_base", "create_ticket"},
    "AFFIRM": set(),
    "PROVIDE_QUANTITY": set(),
    "STORE_INFO": {"search_knowledge_base"},
}

# Tools that can NEVER appear in a software-executed plan step — reserved for
# the deterministic workflow engine (order flow) or side-effects the planner
# must not trigger with guessed arguments.
_FORBIDDEN_PLAN_TOOLS = {"create_order", "update_customer", "create_ticket"}

# Low-confidence structured plans below this threshold ask a clarifying
# question instead of executing (mirrors _LOW_CONFIDENCE in the orchestrator).
PLAN_CONFIDENCE_THRESHOLD = 0.6
MAX_PLAN_STEPS = 4

# Two-tier planning (docs 'Two AI planners'). Cheap model decides most turns;
# the expensive model is used only for complex conversations or when the cheap
# plan is low confidence / asks for clarification.
FAST_PLANNER_MODEL = "openai/gpt-4o-mini"
SMART_PLANNER_MODEL = None  # None → context.model (integration ai_model)

# Intents where planning is genuinely hard — skip the cheap tier entirely.
_SMART_ONLY_INTENTS = {
    "NEGOTIATE", "COMPARE_PRODUCTS", "FRUSTRATION", "UNKNOWN",
    "RETURN_PRODUCT", "RECOMMEND",
}

# Conversation states that signal an in-flight, complex exchange.
_COMPLEX_STATES = {"comparing", "negotiating", "product_selection", "checkout", "payment"}


def _is_complex_context(context: ConversationContext) -> bool:
    """True when the current conversation state indicates a complex exchange
    that deserves the smart planner (checkout, payment, comparing…)."""
    if getattr(getattr(context, "intent", None), "name", "") in _SMART_ONLY_INTENTS:
        return True
    state = ""
    try:
        if getattr(context, "conversation", None) is not None:
            from context.models import SessionContext
            sc = SessionContext.objects.filter(conversation=context.conversation).first()
            state = (sc.state or "") if sc else ""
    except Exception:
        state = ""
    return state in _COMPLEX_STATES


# ---------------------------------------------------------------------------
# Direct mappings (simple intents → single tool)
# ---------------------------------------------------------------------------

_DIRECT_MAP: dict[str, str | list[PlanStep]] = {
    "ASK_PRICE": "search_products",
    "ASK_STOCK": "search_products",
    "SEARCH_PRODUCT": "search_products",
    "ASK_DETAILS": "search_products",
    "RECOMMEND": "search_products",
    "CHECK_ORDER": "get_order_status",
    "CANCEL_ORDER": "get_order_status",
    "RETURN_PRODUCT": "search_knowledge_base",
    "ASK_DELIVERY": "search_knowledge_base",
    "ASK_PAYMENT": "search_knowledge_base",
    "ASK_FAQ": "search_knowledge_base",
    "HUMAN_SUPPORT": "create_ticket",
    "ANALYTICS_QUERY": "get_sales_summary",
    "STORE_INFO": "search_knowledge_base",
}

# ---------------------------------------------------------------------------
# Template-based plans (known workflows)
# ---------------------------------------------------------------------------

_TEMPLATE_PLANS: dict[str, list[dict]] = {
    "COMPARE_PRODUCTS": [
        {"tool": "search_products", "args": {"query": "__incoming_text__"}},
        {"tool": "get_product_details", "args": {"pid": "__focus_pid__"}},
    ],
    "CATALOG": [
        {"tool": "search_products", "args": {"query": "", "limit": 10}},
        {"tool": "send_images", "args": {}},
    ],
    "SEND_IMAGES": [
        {"tool": "search_products", "args": {"query": "__incoming_text__", "limit": 5}},
        {"tool": "send_images", "args": {}},
    ],
    "NEGOTIATE": [
        {"tool": "search_products", "args": {"query": "__focus_name__", "limit": 5}},
    ],
    "UPGRADE_PLAN": [
        {"tool": "search_knowledge_base", "args": {"query": "pricing plans"}},
    ],
    "BILLING_QUERY": [
        {"tool": "search_knowledge_base", "args": {"query": "pricing billing"}},
    ],
    "STORE_SYNC": [
        {"tool": "search_knowledge_base", "args": {"query": "sync troubleshooting"}},
    ],
}

# goal / state labels for the deterministic tiers (free metadata).
_INTENT_GOALS: dict[str, tuple[str, str]] = {
    "ASK_PRICE": ("share_product_price", "browsing"),
    "ASK_STOCK": ("share_product_stock", "browsing"),
    "SEARCH_PRODUCT": ("find_product", "browsing"),
    "ASK_DETAILS": ("share_product_details", "browsing"),
    "RECOMMEND": ("recommend_product", "product_selection"),
    "COMPARE_PRODUCTS": ("compare_products", "comparing"),
    "CATALOG": ("show_catalog", "browsing"),
    "SEND_IMAGES": ("send_product_images", "browsing"),
    "CHECK_ORDER": ("check_order_status", "order_lookup"),
    "CANCEL_ORDER": ("cancel_order", "order_lookup"),
    "RETURN_PRODUCT": ("help_with_return", "support"),
    "ASK_DELIVERY": ("answer_delivery_question", "support"),
    "ASK_PAYMENT": ("answer_payment_question", "support"),
    "ASK_FAQ": ("answer_faq", "support"),
    "HUMAN_SUPPORT": ("handoff_to_human", "support"),
    "ANALYTICS_QUERY": ("share_sales_summary", "analytics"),
    "NEGOTIATE": ("handle_price_negotiation", "negotiating"),
    "UPGRADE_PLAN": ("answer_plan_question", "support"),
    "BILLING_QUERY": ("answer_billing_question", "support"),
    "STORE_SYNC": ("answer_sync_question", "support"),
    "AFFIRM": ("acknowledge", "browsing"),
    "PROVIDE_QUANTITY": ("capture_quantity", "browsing"),
    "STORE_INFO": ("answer_store_info", "support"),
}


class Planner:

    @staticmethod
    def plan(intent: str, context: ConversationContext, reply_id: str | None = None) -> AIPlan:
        """Produce a structured AIPlan for the given intent and context.

        Deterministic tiers (direct → template) first — free and consistent.
        The LLM tier is used only for intents with no canned plan, and its
        output is software-validated before it reaches the executor.
        """
        if intent in ("GREETING", "SMALL_TALK", "UNKNOWN", "FRUSTRATION", "AFFIRM", "PROVIDE_QUANTITY"):
            # First-contact greeting: text-only (a welcome line). The catalog
            # carousel is shown only when the customer asks to browse ("ki
            # product ache?") — dumping the whole carousel on "hi" is spammy.
            return AIPlan(
                goal="respond_conversationally",
                conversation_state="idle",
                confidence=1.0,
                steps=[],
            )

        # Orders are handled EXCLUSIVELY by the deterministic workflow
        # (orchestrator Step 2c). The planner must never create orders with
        # guessed args — that path produced unrequested orders and stale
        # customer data in real conversations.
        if intent == "CREATE_ORDER":
            return AIPlan(
                goal="start_order_flow",
                conversation_state="ordering",
                confidence=1.0,
                steps=[],
            )

        # 1) Direct mode: single tool
        direct_tool = _DIRECT_MAP.get(intent)
        if direct_tool and isinstance(direct_tool, str):
            step = PlanStep(
                tool=direct_tool,
                args=Planner._build_args(direct_tool, context),
            )
            return Planner._wrap(intent, [step])

        # 2) Template mode: known multi-step workflow
        template = _TEMPLATE_PLANS.get(intent)
        if template:
            if intent == "SEND_IMAGES":
                steps = Planner._send_images_plan(context)
            else:
                steps = Planner._resolve_template(template, context)
            return Planner._wrap(intent, steps)

        # 3) LLM mode: structured plan, software-validated
        return Planner._llm_plan(intent, context, reply_id)

    @staticmethod
    def _wrap(intent: str, steps: list[PlanStep], confidence: float = 1.0,
              reason: str = "") -> AIPlan:
        goal, state = _INTENT_GOALS.get(intent, ("respond", "idle"))
        tools = [s.tool for s in steps if s.tool]
        return AIPlan(
            goal=goal,
            conversation_state=state,
            required_tools=tools,
            ask_clarification=False,
            confidence=confidence,
            reason=reason,
            steps=steps,
        )

    # ------------------------------------------------------------------
    # Software validation ("AI proposes, software disposes")
    # ------------------------------------------------------------------

    @staticmethod
    def validate(plan: AIPlan, intent: str, context: ConversationContext) -> AIPlan:
        """Deterministic gate over any plan (LLM or template) before execution.

        - Drops steps whose tool is not in the intent's allowed set.
        - Forbids create_order / update_customer / create_ticket as planner
          steps (they run only via the deterministic workflow / tool paths).
        - Low confidence + any steps → ask_clarification instead of executing.
        - Caps iteration count so a runaway LLM plan can't loop the pipeline.
        """
        from .orchestrator import MAX_TOOL_ITERATIONS

        allowed = _INTENT_ALLOWED_TOOLS.get(intent, set()) | _INTENT_ALLOWED_TOOLS.get("SEARCH_PRODUCT", set())
        validated: list[PlanStep] = []
        for step in plan.steps:
            tool = step.tool
            if not tool:
                continue
            if tool in _FORBIDDEN_PLAN_TOOLS:
                logger.warning("PlanValidator rejected forbidden tool=%s intent=%s", tool, intent)
                continue
            if allowed and tool not in allowed:
                logger.warning("PlanValidator rejected tool=%s (not allowed for intent=%s)", tool, intent)
                continue
            validated.append(step)
            if len(validated) >= MAX_TOOL_ITERATIONS:
                break

        if not validated:
            return AIPlan(
                goal=plan.goal,
                conversation_state=plan.conversation_state,
                ask_clarification=bool(plan.ask_clarification),
                confidence=plan.confidence,
                reason=plan.reason,
                steps=[],
            )

        # Low-confidence structured plans never execute blindly.
        if plan.confidence < PLAN_CONFIDENCE_THRESHOLD:
            logger.info(
                "PlanValidator: low confidence %.2f for intent=%s → ask clarification",
                plan.confidence, intent,
            )
            return AIPlan(
                goal=plan.goal,
                conversation_state=plan.conversation_state,
                ask_clarification=True,
                confidence=plan.confidence,
                reason=plan.reason,
                steps=[],
            )

        return AIPlan(
            goal=plan.goal,
            conversation_state=plan.conversation_state,
            required_tools=[s.tool for s in validated],
            ask_clarification=plan.ask_clarification,
            confidence=plan.confidence,
            reason=plan.reason,
            steps=validated,
        )

    @staticmethod
    def _send_images_plan(context: ConversationContext) -> list[PlanStep]:
        """SEND_IMAGES: when the message refers to a product already focused in
        the conversation, send THAT product's images directly. Never re-search
        the raw text first — "pic dekhi" would fan out to junk words ("pic"
        matches "Earwax Picker") and overwrite the focus. Search-then-send only
        when nothing is focused or the message names an unrelated product.
        """
        from .tools import _focus_match_for_query

        match = None
        try:
            match = _focus_match_for_query(context.incoming_text or "", context.conversation)
        except Exception:
            match = None

        if match:
            return [PlanStep(tool="send_images", args={"pids": [match["pid"]]})]

        return [
            PlanStep(tool="search_products", args={"query": context.incoming_text or "", "limit": 5}),
            PlanStep(tool="send_images", args={}),
        ]

    @staticmethod
    def _build_args(tool_name: str, context: ConversationContext) -> dict:
        """Build appropriate args for a tool based on intent and context."""
        if tool_name == "search_products":
            return {"query": context.incoming_text or "", "limit": 10}
        if tool_name == "get_order_status":
            import re as _re
            oid_match = _re.search(r"(ord_[a-z0-9]+)", context.incoming_text or "")
            if oid_match:
                return {"order_id": oid_match.group(1)}
            # No order id in the message ("order id koto?", "ager order ki holo?")
            # → answer with the conversation's most recent order instead of
            # letting the LLM hallucinate a stale id from history.
            conv = context.conversation
            if conv:
                try:
                    from back.models import Sale
                    latest = Sale.objects.filter(conversation=conv).order_by("-created_at").first()
                    if latest:
                        return {"order_id": latest.oid}
                except Exception as exc:
                    logger.warning("Latest-order lookup failed: %s", exc)
            return {"order_id": ""}
        if tool_name == "send_images":
            products = list(getattr(context, "products", []) or [])
            pids = []
            if products:
                try:
                    from .state import resolve_product_reference
                    # context.products are ProductSummary dataclasses — normalize
                    # to plain dicts so resolve_product_reference can match names.
                    dicts = []
                    for p in products:
                        d = dict(getattr(p, "__dict__", p)) if hasattr(p, "__dict__") else (p if isinstance(p, dict) else {})
                        if isinstance(d, dict) and d.get("pid"):
                            dicts.append(d)
                    sel = resolve_product_reference(context.incoming_text or "", dicts) if dicts else None
                    pids = [sel["pid"]] if sel else []
                except Exception:
                    pids = []
                if not pids:
                    pids = [p.pid if hasattr(p, "pid") else p.get("pid", "") for p in products]
                    pids = [p for p in pids if p][:5]
            if not pids:
                try:
                    from .state import WorkflowEngine
                    found = WorkflowEngine._quick_catalog_search(context.conversation.user, context.incoming_text or "") or []
                    if found:
                        try:
                            from .state import resolve_product_reference
                            sel = resolve_product_reference(context.incoming_text or "", found)
                            if sel:
                                found = [sel]
                        except Exception:
                            pass
                    pids = [f.get("pid", "") for f in found[:5] if f.get("pid")]
                except Exception:
                    pids = []
            return {"pids": pids}
        if tool_name == "search_knowledge_base":
            return {"query": context.incoming_text or "", "limit": 3}
        if tool_name == "create_ticket":
            return {
                "subject": "Customer requested human support",
                "description": context.incoming_text or "",
                "priority": "medium",
            }
        if tool_name == "update_customer":
            return {}
        return {}

    @staticmethod
    def _resolve_template(template: list[dict], context: ConversationContext) -> list[PlanStep]:
        """Resolve a template into concrete PlanSteps, replacing placeholders."""
        steps = []
        for t in template:
            args = dict(t.get("args", {}))
            for key, val in args.items():
                if val == "__incoming_text__":
                    args[key] = context.incoming_text
                elif val == "__focus_pid__":
                    pid = context.products[0].pid if context.products else ""
                    args[key] = pid
                elif val == "__focus_name__":
                    name = context.products[0].name if context.products else ""
                    args[key] = name
            # Pre-fill create_order with customer info from conversation context
            if t["tool"] == "create_order":
                if not args.get("customer_name") and context.customer.name:
                    args["customer_name"] = context.customer.name
                if not args.get("customer_phone") and context.customer.phone:
                    args["customer_phone"] = context.customer.phone
                if not args.get("customer_city") and context.customer.city:
                    args["customer_city"] = context.customer.city
                if not args.get("customer_address") and context.customer.address:
                    args["customer_address"] = context.customer.address
            # Catalog browse: send_images without explicit args → whole catalog
            # (all active products) so the customer sees actual cards, not the
            # single stale focus product. External live stores are excluded —
            # their catalog lives on the provider, so tool_send_images fetches
            # it (local DB rows would show the wrong store's products).
            if t["tool"] == "send_images" and not args.get("pid") and not args.get("pids"):
                try:
                    from api.products.factory import get_active_source, is_external
                    source = get_active_source(context.user)
                    external_live = bool(source) and source.mode == "live" and is_external(context.user)
                except Exception:
                    external_live = False
                if not external_live:
                    from back.models import Product
                    catalog_pids = list(
                        Product.objects.filter(user=context.user, status=True)
                        .values_list("pid", flat=True)[:8]
                    )
                    if catalog_pids:
                        args["pids"] = catalog_pids
            steps.append(PlanStep(tool=t["tool"], args=args))
        return steps

    @staticmethod
    def _llm_plan(intent: str, context: ConversationContext, reply_id: str | None = None) -> AIPlan:
        """Two-tier structured planning (docs 'Two AI planners').

        1. Fast tier (cheap model) tries every LLM-required turn first.
        2. Smart tier (the user's configured model) is used only when:
             - the intent is on the hard list, or
             - the conversation is mid-complex-flow (comparing/negotiating/
               product_selection/payment), or
             - the fast plan is low confidence or asks for clarification.
        """
        result = None
        first_pass = None
        try:
            if intent in _SMART_ONLY_INTENTS or _is_complex_context(context):
                result = Planner._plan_call(intent, context, reply_id, smart=True, first_pass=False)
            else:
                result = Planner._plan_call(intent, context, reply_id, smart=False, first_pass=True)
                first_pass = result
                if result.confidence < PLAN_CONFIDENCE_THRESHOLD or result.ask_clarification:
                    result = Planner._plan_call(
                        intent, context, reply_id, smart=True, first_pass=False, draft=first_pass,
                    )
        except Exception:
            logger.exception("Two-tier planning failed intent=%s", intent)
            result = first_pass or AIPlan(goal="respond", confidence=0.0, ask_clarification=True, steps=[])
        return result

    @staticmethod
    def _smart_model(context: ConversationContext) -> str:
        if SMART_PLANNER_MODEL:
            return SMART_PLANNER_MODEL
        dm = getattr(context, "model", None) or ""
        return dm or FAST_PLANNER_MODEL

    @staticmethod
    def _plan_call(intent, context: ConversationContext, reply_id, *, smart: bool, first_pass: bool, draft=None) -> AIPlan:
        try:
            from .providers import call_llm

            allowed = _INTENT_ALLOWED_TOOLS.get(intent, set())
            draft_hint = ""
            if draft is not None:
                tools = ", ".join(s.tool for s in draft.steps) or "none"
                draft_hint = (
                    f'The first attempt was low-confidence (conf={draft.confidence}). '
                    f'Its steps were: {tools}. Re-analyze and produce a better plan.\n'
                )

            prompt = (
                f"Intent: {intent}\n"
                f"Customer message: {context.incoming_text}\n"
                f"Customer has {len(context.products)} focused products\n\n"
                + draft_hint +
                f"Allowed tools (USE ONLY THESE): {', '.join(sorted(allowed))}\n\n"
                "Return a single JSON object:\n"
                "{\n"
                '  "goal": "short verb phrase of what to achieve",\n'
                '  "conversation_state": "browsing|product_selection|comparing|support|order_lookup",\n'
                '  "required_tools": ["tool names from the allowed list"],\n'
                '  "ask_clarification": false,\n'
                '  "confidence": 0.0-1.0,\n'
                '  "steps": [{"tool": "name", "args": {}}]\n'
                "}\n"
                "Each step.tool MUST be from the allowed list. ask_clarification=true "
                "when the message is ambiguous. Return ONLY the JSON, nothing else."
            )

            msg, usage = call_llm(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
                model=Planner._smart_model(context) if smart else FAST_PLANNER_MODEL,
                temperature=0.2,
                max_tokens=400,
            )

            if reply_id:
                from back.models import UsageLog
                try:
                    UsageLog.objects.create(
                        user=context.user,
                        reply_id=reply_id,
                        model=usage.get("model", ""),
                        input_tokens=usage.get("input_tokens", 0),
                        output_tokens=usage.get("output_tokens", 0),
                        call_type=("planning_smart" if smart else "planning_fast"),
                    )
                except Exception as exc:
                    logger.warning("UsageLog write failed reply_id=%s: %s", reply_id, exc)

            raw = (msg.content or "").strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
                raw = raw.rsplit("\n```", 1)[0]
            plan_data = json.loads(raw) if raw else {}
            if not isinstance(plan_data, dict):
                plan_data = {}

            steps = []
            steps_raw = plan_data.get("steps") or []
            for item in steps_raw:
                if not isinstance(item, dict):
                    continue
                tool = item.get("tool")
                if tool in _INTENT_ALLOWED_TOOLS.get(intent, set()) and tool:
                    args = item.get("args")
                    steps.append(PlanStep(tool=tool, args=args if isinstance(args, dict) else {}))

            return AIPlan(
                goal=str(plan_data.get("goal") or ""),
                conversation_state=str(plan_data.get("conversation_state") or ""),
                required_tools=list(plan_data.get("required_tools") or [])[:8],
                ask_clarification=bool(plan_data.get("ask_clarification")),
                confidence=Planner._safe_confidence(plan_data.get("confidence")),
                reason=str(plan_data.get("reason") or ""),
                steps=steps[:MAX_PLAN_STEPS],
            )

        except Exception as exc:
            logger.warning("LLM planning failed for intent=%s: %s", intent, exc)

            return AIPlan(
                goal="respond",
                confidence=0.0,
                ask_clarification=True,
                steps=[],
            )

    @staticmethod
    def _safe_confidence(value) -> float:
        try:
            v = float(value)
            return max(0.0, min(1.0, v))
        except (TypeError, ValueError):
            return 0.0