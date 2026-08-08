"""
ResponseValidator (new_ai_orchestrator_check Layer 7): deterministic final gate
between the ResponseGenerator and the platform send.

Catches the classic failure modes before anything reaches the customer:
  - empty / whitespace-only reply          -> fallback template
  - reply never injected stored URL nonsense -> strip (already in generator)
  - verbose reply shipped alongside images/cards -> collapse to one short message
  - unsafe/profanity in the generated text -> neutral templated reply
  - wrong media cardinality -> capped to platform rules

Validation is intentionally correction-oriented: instead of raising, it returns
a fixed Response plus a list of issue codes (persisted for observability).
"""
import logging
import re

from .context import ConversationContext, Response

logger = logging.getLogger(__name__)

# Profanity / unsafe content that must never reach a customer. Kept tight to
# avoid false positives (bangla + english).
_UNSAFE_RE = re.compile(
    r"\b(chud|chudu|magir|maggi|bala|bhodai|bhos|bhen|m\*th|f\*k|"
    r"মাগির|চুদ|বাল|বখোদা|ঠাকুর)\b",
    re.IGNORECASE,
)

_MAX_TEXT_LEN = 500

# Bengali-script detector used for the wrong-language correction.
_HAS_BN_SCRIPT = re.compile(r"[\u0980-\u09FF]")


class ResponseValidator:

    @staticmethod
    def validate(response: Response, context: ConversationContext) -> tuple[Response, list[str]]:
        """Run the safety/quality checks. Returns (fixed_response, issue_codes)."""
        if response is None:
            response = Response(text="")
        issues: list[str] = []

        text = response.text or ""
        if not text.strip():
            issues.append("empty_response")
            from .response import ResponseGenerator
            fallback = ResponseGenerator._fallback_text(
                context.tool_results or [], context
            )
            if not fallback:
                fallback = "Sorry, I didn't catch that. Could you rephrase?"
            response.text = fallback
            text = fallback

        # Unsafe content — replace rather than forward.
        if _UNSAFE_RE.search(text):
            issues.append("unsafe_content")
            lang = "bn" if (getattr(context, "conversation", None)
                            and getattr(context.conversation, "language_detected", "") == "bn") else "en"
            response.text = (
                "দুঃখিত ভাই, অসুবিধার জন্য দুঃখিত। আমি এভাবে সাহায্য করতে পারছি না — "
                "দাম, অর্ডার বা ডেলিভারি নিয়ে জানতে চান বলুন।"
                if lang == "bn" else
                "Sorry, I can't respond to that. I'm happy to help with prices, "
                "orders, or delivery instead."
            )
            text = response.text

        # Verbose text alongside visuals — collapse deterministically.
        has_visuals = bool(response.images or response.cards)
        if has_visuals:
            from .pipeline import _collapse_verbose_text, _is_verbose
            if _is_verbose(text):
                issues.append("verbose_with_visuals")
                response.text = _collapse_verbose_text(text)

        # Media cardinality — platform/marketing limits.
        if len(response.images or []) > 4:
            issues.append("too_many_images")
            response.images = (response.images or [])[:4]
        if len(response.cards or []) > 4:
            issues.append("too_many_cards")
            response.cards = (response.cards or [])[:4]

        # Oversized text — hard ceiling.
        if len(response.text) > _MAX_TEXT_LEN:
            issues.append("too_long_text")
            response.text = response.text[:_MAX_TEXT_LEN]

        # Repeated-line spam: a single non-empty line echoed 3+ times in a row
        # (e.g. "Jolpaia achar — 450.00" ×7) is a generator failure, not a list.
        lines = [ln.strip() for ln in response.text.split("\n") if ln.strip()]
        if len(lines) >= 3 and len(set(lines)) == 1:
            issues.append("repeated_line")
            response.text = lines[0]

        # Uniform micro-cluster (≤3 distinct short lines, no visuals):
        # a bare repeated-line cluster without any prose is equally spammy.
        if (not has_visuals and len(lines) >= 5
                and len(set(lines)) <= 2
                and all(len(ln) <= 60 for ln in lines)):
            issues.append("spammy_list")
            response.text = (
                "\n".join(lines) if len(lines) <= 3
                else "\n".join(dict.fromkeys(lines))
            )

        # Wrong-language reply: conversation locked to Bengali but the reply is
        # pure Latin (a generator failure) — neutralize with the Bengali fallback
        # rather than ship an English reply to a Bengali customer.
        conv = getattr(context, "conversation", None)
        if (
            conv is not None
            and getattr(conv, "language_detected", "") == "bn"
            and not _HAS_BN_SCRIPT.search(response.text or "")
            and (response.text or "").strip()
        ):
            issues.append("wrong_language")
            response.text = (
                "দুঃখিত, আমি আবার বলছি — আপনি কোন পণ্যটা খুঁজছেন? "
                "দাম বা ছবি জানতে পণ্যটির নাম বলুন।"
            )

        # Broken markdown / stray emphasis asterisks — WhatsApp/Telegram render
        # ** literally; strip them for a clean bubble.
        if re.search(r"\*\*|__|\*[^*\s]", response.text or ""):
            issues.append("broken_markdown")
            response.text = re.sub(r"(\*\*+|__+)", "", response.text or "")
            response.text = re.sub(r"^\s*[-*]\s+", "", response.text)

        # Missing tool output: the reply cites an order ID or claims a
        # confirmed order/payment, but no order tool ran this turn.
        if re.search(r"\bord_[a-z0-9]+\b", response.text or ""):
            from .tools import ToolResult
            ran_order_tool = any(
                isinstance(r, ToolResult) and r.tool in ("create_order", "get_order_status")
                for r in (getattr(context, "tool_results", None) or [])
            )
            if not ran_order_tool:
                issues.append("missing_tool_output")
                response.text = (
                    "দুঃখিত, অর্ডারটা এখন যাচাই করা যাচ্ছে না। একটু পরে আবার জিজ্ঞেস করবেন।"
                    if (conv and getattr(conv, "language_detected", "") == "bn")
                    else "Sorry, I can't verify that order right now. Please ask again shortly."
                )

        # Sold-out claims: the reply says out of stock but the catalog search
        # this turn returned in-stock matches for the same product — the claim
        # is contradicted by real data, so soften it.
        if re.search(r"(স্টকে নেই|আছে না|নাই|out of stock|unavailable)", response.text or "", re.IGNORECASE):
            from .tools import ToolResult
            in_stock_names = []
            for r in (getattr(context, "tool_results", None) or []):
                if isinstance(r, ToolResult) and r.tool == "search_products" and r.state == "success":
                    for p in (r.data or {}).get("products", []) or []:
                        if p.get("in_stock"):
                            in_stock_names.append(str(p.get("name", "")).lower())
            if in_stock_names:
                mentioned = any(
                    nm and nm in (response.text or "").lower() for nm in in_stock_names
                )
                if mentioned:
                    issues.append("sold_out_contradiction")
                    response.text = (
                        "আমাদের কাছে এটা পাওয়া যাচ্ছে। দাম ও ছবি জানাতে চাইলে বলুন!"
                        if (conv and getattr(conv, "language_detected", "") == "bn")
                        else "We do have that in stock. Want me to share the price and photos?"
                    )

        return response, issues