"""
Planner (P0-4): Given intent + context, produce an ordered sequence of PlanSteps.

Three modes:
- Direct: simple intent → single tool call
- Template: known workflow → predefined multi-step sequence
- LLM: complex/ambiguous → single LLM call produces plan
"""
import json
import logging
from pathlib import Path

from back.models import Message

from .context import ConversationContext, PlanStep
from .tools import ToolRegistry

logger = logging.getLogger(__name__)

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


class Planner:

    @staticmethod
    def plan(intent: str, context: ConversationContext, reply_id: str | None = None) -> list[PlanStep]:
        """Produce a list of PlanSteps for the given intent and context.

        Uses direct → template → LLM fallback.
        """
        if intent in ("GREETING", "SMALL_TALK", "UNKNOWN", "FRUSTRATION"):
            # First-contact greeting: text-only (a welcome line). The catalog
            # carousel is shown only when the customer asks to browse ("ki
            # product ache?") — dumping the whole carousel on "hi" is spammy.
            return []

        # Orders are handled EXCLUSIVELY by the deterministic workflow
        # (orchestrator Step 2c). The planner must never create orders with
        # guessed args — that path produced unrequested orders and stale
        # customer data in real conversations.
        if intent == "CREATE_ORDER":
            return []

        # 1) Direct mode: single tool
        direct_tool = _DIRECT_MAP.get(intent)
        if direct_tool and isinstance(direct_tool, str):
            step = PlanStep(
                tool=direct_tool,
                args=Planner._build_args(direct_tool, context),
            )
            return [step]

        # 2) Template mode: known multi-step workflow
        template = _TEMPLATE_PLANS.get(intent)
        if template:
            if intent == "SEND_IMAGES":
                return Planner._send_images_plan(context)
            return Planner._resolve_template(template, context)

        # 3) LLM mode: use LLM to plan
        return Planner._llm_plan(intent, context, reply_id)

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
    def _llm_plan(intent: str, context: ConversationContext, reply_id: str | None = None) -> list[PlanStep]:
        """Use LLM to generate a plan for complex/ambiguous intents."""
        try:
            from .providers import call_llm

            available_tools = ToolRegistry.get_definitions()
            available_names = [t["function"]["name"] for t in available_tools]

            prompt = (
                f"Intent: {intent}\n"
                f"Customer message: {context.incoming_text}\n"
                f"Customer has {len(context.products)} focused products\n\n"
                f"Available tools: {', '.join(available_names)}\n\n"
                "Return a JSON array of tool sequences. Each item: {\"tool\": \"name\", \"args\": {}}\n"
                "Only use tools from the available list. Return [] if no tools needed.\n"
                "Return ONLY the JSON array, nothing else."
            )

            msg, usage = call_llm(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
                temperature=0.3,
                max_tokens=300,
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
                        call_type="planning",
                    )
                except Exception as exc:
                    logger.warning("UsageLog write failed reply_id=%s: %s", reply_id, exc)

            raw = (msg.content or "").strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
                raw = raw.rsplit("\n```", 1)[0]
            plan_data = json.loads(raw) if raw else []

            if isinstance(plan_data, list):
                return [
                    PlanStep(tool=item["tool"], args=item.get("args", {}))
                    for item in plan_data
                    if isinstance(item, dict) and item.get("tool") in available_names
                ]

        except Exception as exc:
            logger.warning("LLM planning failed for intent=%s: %s", intent, exc)

        return []
