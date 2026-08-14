"""CRM snapshot — the live-state block injected into the system prompt.

Renders `## CUSTOMER CRM` and `## SALES CONTEXT` from CustomerProfile /
SalesOpportunity / OrderDraft / SessionContext. Distinguishes hard facts
(customer-sourced, high confidence) from AI inferences, and computes a
"next best action" priority list so the prompt stays small.
"""
import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

_FACT_CONFIDENCE_MIN = 0.8


def _currency_for(user):
    from context.models import StoreConfig
    store = StoreConfig.objects.filter(user=user).first()
    return store.currency if store else "BDT"


def _fmt_money(value):
    try:
        return f"{Decimal(str(value)):.2f}"
    except Exception:
        return str(value or "0")


def _greeted(conversation):
    """Whether the AI has already sent at least one message to this customer."""
    try:
        from back.models import Message
        return Message.objects.filter(conversation=conversation, sender="bot").exists()
    except Exception:
        return False


def _draft_missing(conversation, draft):
    from context.crm.drafts import draft_missing_fields
    return draft_missing_fields(conversation, draft)


def build_crm_snapshot(conversation):
    """Return a rendered CRM snapshot block (or "" when nothing to show)."""
    try:
        from context.crm_models import CustomerProfile, OrderDraft, SalesOpportunity

        user = conversation.user
        currency = _currency_for(user)
        profile = CustomerProfile.objects.filter(conversation=conversation).first()
        opp = SalesOpportunity.objects.filter(conversation=conversation).first()
        draft = OrderDraft.objects.filter(conversation=conversation).first()

        lines = ["## CUSTOMER CRM"]
        has_any = False

        if profile:
            has_any = True
            lines.append(
                f"Lifecycle: {profile.get_lifecycle_stage_display()}  |  "
                f"Lead score: {profile.lead_score}/100  |  "
                f"Buying probability: {round(profile.buying_probability * 100)}%"
            )
            lines.append(
                f"Greeted: {'yes' if _greeted(conversation) else 'no'}  |  "
                f"Name: {conversation.customer_name or 'unknown'}"
            )
            orders = profile.order_count
            if orders:
                lines.append(f"Orders placed: {orders}  |  Total spent: {currency} {_fmt_money(profile.total_spent)}")

            # Facts the customer actually told us — only high-confidence ones.
            facts = profile.facts or {}
            confirmed = [
                (k, v) for k, v in facts.items()
                if v.get("source") in ("customer", "system")
                and float(v.get("confidence", 0)) >= _FACT_CONFIDENCE_MIN
            ]
            if confirmed:
                lines.append("Known facts:")
                for key, val in confirmed[:8]:
                    lines.append(f"- {key}: {val.get('value')}")

            preferences = profile.preferences or []
            if preferences:
                tops = sorted(preferences, key=lambda p: -int(p.get("count", 0)))[:4]
                lines.append("Known preferences: " + ", ".join(
                    f"{p.get('name') or p.get('pid')} (x{p.get('count', 1)})" for p in tops
                ))

            objections = profile.objections or []
            if objections:
                lines.append("Known objections: " + ", ".join(o.get("text", o.get("type")) for o in objections[:4]))

        if opp and opp.status == "open":
            has_any = True
            lines.append("")
            lines.append("## SALES CONTEXT")
            lines.append(f"Stage: {opp.get_stage_display()}  |  Intent: {opp.intent or 'unknown'}")
            if opp.current_product_pid:
                lines.append(f"Current product interest: PID {opp.current_product_pid}")
            if opp.budget_min is not None or opp.budget_max is not None:
                budget = ""
                if opp.budget_min is not None:
                    budget += f"min {currency} {opp.budget_min}"
                if opp.budget_max is not None:
                    budget += (f" / max {currency} {opp.budget_max}" if budget else f"max {currency} {opp.budget_max}")
                lines.append(f"Budget: {budget}")

            # Missing order information (backend truth) + next best action.
            missing = []
            if not (conversation.customer_name or "").strip():
                missing.append("customer name")
            if not (conversation.customer_phone or "").strip():
                missing.append("phone")
            if not (conversation.customer_address or "").strip():
                missing.append("delivery address")
            elif not (conversation.customer_city or "").strip():
                missing.append("city/area")
            if draft and draft.confirmation_status == "awaiting_confirmation":
                missing.append("final order confirmation (show the total, ask for a clear yes)")

            if missing:
                lines.append("Missing order information: " + ", ".join(missing[:4]))

            next_actions = ["Answer the customer's current question first"]
            if missing:
                next_actions.append("Collect only the missing information above — never re-ask what is already known")
            if draft and draft.confirmation_status == "draft" and draft.missing_fields:
                still_missing = _draft_missing(conversation, draft)
                next_actions.append(
                    "A bare 'yes'/'ok' from the customer creates NOTHING yet — "
                    "required info is still missing: " + ", ".join(still_missing[:3])
                )
            if draft and draft.confirmation_status == "awaiting_confirmation":
                next_actions.append(f"Present the exact order total ({currency} {_fmt_money(draft.grand_total)}) and ask for a clear yes before create_order")
            if opp.current_product_pid:
                next_actions.append("Keep focus on the current product — do not push unrelated products")
            lines.append("Next best action: " + "; ".join(next_actions[:4]))
            lines.append("Avoid: re-searching the catalog unless the customer asks; re-asking for known information; sending more images unless requested; inventing prices/stock/delivery charges — use tools.")

        if draft and draft.confirmation_status in ("awaiting_confirmation", "confirmed"):
            has_any = True
            lines.append("")
            lines.append("## ACTIVE ORDER DRAFT")
            lines.append(
                f"Status: {draft.get_confirmation_status_display()}  |  "
                f"Item total: {currency} {_fmt_money(draft.item_total)}  |  "
                f"Delivery: {currency} {_fmt_money(draft.delivery_charge)}  |  "
                f"Grand total: {currency} {_fmt_money(draft.grand_total)}"
            )
            lines.append(f"Delivery zone: {draft.get_delivery_zone_display()}")

        if not has_any:
            return ""
        return "\n".join(lines)
    except Exception:
        logger.exception("build_crm_snapshot failed conv=%s", getattr(conversation, "pk", None))
        return ""
