"""
Orchestrator (P0-2): Directs the full lifecycle of a message through the agent pipeline.

Replaces pipeline.py as the entry point when the AI_ORCHESTRATOR_ENABLED flag is on.
"""
import json
import logging
import re as _re
import time
import uuid

from django.conf import settings

from back.models import Message, ToolCallLog, UsageLog, Conversation

from .context import ConversationManager, Response
from .executor import Executor
from .intent import IntentDetector
from .planner import Planner
from .response import ResponseGenerator
from .tools import ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 7


class Orchestrator:

    def __init__(self, dry_run=False):
        self.conversation_manager = ConversationManager()
        self.intent_detector = IntentDetector()
        self.planner = Planner()
        self.executor = Executor()
        self.response_generator = ResponseGenerator()
        self.dry_run = dry_run
        self.last_response = None
        self.last_reply_id = None

    def process(self, conversation, incoming_message):
        """
        Entry point for processing an incoming message through the orchestrator.

        Args:
            conversation: Conversation model instance
            incoming_message: SimpleNamespace with .text attribute, or Message-like object
        """
        if not conversation.is_ai_enabled:
            return

        user = conversation.user

        # Integration guard (relaxed in dry-run so the dashboard Bot Preview
        # works even when no live integration is configured)
        from back.models import Integration
        integration = Integration.get_active(user, conversation.platform)
        if not self.dry_run:
            if not integration or not integration.is_enabled:
                logger.info("Integration AI disabled — skipping orchestrator conv=%s", conversation.pk)
                return
            if not integration.access_token:
                logger.info("No access token — skipping orchestrator conv=%s", conversation.pk)
                return

        model = (integration.ai_model or None) if integration else None

        # Pre-flight credit check
        if not self.dry_run:
            try:
                from billing.models import UserBalance
                balance = UserBalance.objects.filter(user=user).first()
                if balance and balance.credits_remaining <= 0:
                    logger.warning("Credits exhausted for user=%s — skipping orchestrator", user.pk)
                    return
            except Exception:
                pass

        reply_id = uuid.uuid4().hex
        customer_text = incoming_message.text or ""

        # Step 1: Build context
        context = self.conversation_manager.build(
            conversation, incoming_text=customer_text, model=model,
        )

        # Step 1b: Persist the customer's language hint. Bengali script → bn
        # (always). Latin-only text only sets "en" when it looks like natural
        # English (function words); short/transliterated latin ("hlw", "kmn
        # achen") leaves the hint empty so the agent's default language is
        # used instead of locking the conversation to English.
        if not getattr(conversation, "language_detected", "") or _re.search(r"[\u0980-\u09FF]", customer_text):
            try:
                hint = ""
                if _re.search(r"[\u0980-\u09FF]", customer_text):
                    hint = "bn"
                else:
                    _EN_HINT_WORDS = (
                        "i|my|me|you|your|the|is|are|was|am|have|has|do|does|did|"
                        "can|could|will|would|this|that|these|those|how|what|where|"
                        "when|why|who|want|need|please|thanks|thank|hello|good|great|"
                        "help|hi|yes|no|ok|okay|sure|and|for|with|from"
                    )
                    # Only lock the conversation to English when the message
                    # contains ≥2 distinct English function words. A single
                    # "hello"/"please"/"ok" is common in transliterated Bengali
                    # ("amer achar er photo pathan please") and must NOT flip a
                    # Bengali store's customer to English permanently.
                    if not getattr(conversation, "language_detected", ""):
                        en_words = set(_re.findall(
                            rf"\b({_EN_HINT_WORDS})\b", customer_text, _re.IGNORECASE
                        ))
                        if len(en_words) >= 2:
                            hint = "en"
                if hint:
                    Conversation.objects.filter(pk=conversation.pk).update(language_detected=hint)
                    conversation.language_detected = hint
            except Exception:
                pass

        # Step 2: Detect intent
        intent_name = self.intent_detector.detect(customer_text, context)
        from .context import Intent as IntentDC
        context.intent = IntentDC(name=intent_name, confidence=1.0)
        self._intent_name = intent_name

        # Step 2a: A confirmation ("ok", "হ্যাঁ") right after a product question
        # ("...নিতে আগ্রহী?") means the customer wants that product → order flow.
        if intent_name in ("SMALL_TALK", "GREETING", "UNKNOWN"):
            from .state import WorkflowEngine
            if WorkflowEngine.CONFIRM_RE.search(customer_text):
                last_bot = Message.objects.filter(
                    conversation=conversation, sender="bot"
                ).order_by("-timestamp").first()
                if last_bot and last_bot.text and last_bot.text.rstrip().endswith("?"):
                    intent_name = "CREATE_ORDER"
                    context.intent = IntentDC(name=intent_name, confidence=1.0)
                    self._intent_name = intent_name
                    logger.info(
                        "Orchestrator confirm-after-question → CREATE_ORDER conv=%s",
                        conversation.pk,
                    )

        # Repeated "didn't understand": after one failed turn, proactively show
        # the catalog (text + cards) instead of looping the same canned apology.
        if intent_name == "UNKNOWN":
            try:
                last_bot = Message.objects.filter(
                    conversation=conversation, sender="bot"
                ).order_by("-timestamp").first()
                prev_intent = (last_bot.raw_payload or {}).get("intent") if last_bot and last_bot.raw_payload else None
                if prev_intent == "UNKNOWN":
                    intent_name = "CATALOG"
                    context.intent = IntentDC(name=intent_name, confidence=1.0)
                    self._intent_name = intent_name
                    logger.info(
                        "Orchestrator repeated-UNKNOWN → CATALOG conv=%s",
                        conversation.pk,
                    )
            except Exception:
                pass

        logger.info(
            "Orchestrator conv=%s intent=%s text=%r",
            conversation.pk, intent_name, customer_text[:80],
        )

        # Step 2b: Workflow engine (P1-9..13) — active workflow consumes the message
        from .state import WorkflowEngine
        wf_result = WorkflowEngine.handle_message(conversation, customer_text, context)
        if wf_result:
            response = Response(text=wf_result.get("text", ""))
            self.last_response = response
            self.last_reply_id = reply_id
            self._save_and_send(conversation, response, reply_id, dry_run=self.dry_run)
            return

        # Step 2c: Start a workflow when the intent requires one. Orders are
        # handled EXCLUSIVELY here — if the flow somehow produces no response,
        # ask for the product instead of falling through to the planner (which
        # must never create orders with guessed args).
        if intent_name == "CREATE_ORDER":
            started = WorkflowEngine.start_order_flow(conversation, context)
            if not started:
                started = {"text": (
                    "কোন প্রোডাক্টটা অর্ডার করতে চান? আমাদের ক্যাটালগ থেকে একটার নাম বলুন।"
                    if getattr(conversation, "language_detected", "") == "bn" else
                    "Which product would you like to order? Please name one from our catalog."
                )}
            response = Response(text=started.get("text", ""))
            self.last_response = response
            self.last_reply_id = reply_id
            self._save_and_send(conversation, response, reply_id, dry_run=self.dry_run)
            return

        # Step 3: Generate plan
        plan = self.planner.plan(intent_name, context, reply_id=reply_id)
        context.plan = plan

        # Step 4: Execute plan
        tool_results = self.executor.execute(plan, context)
        context.tool_results = tool_results
        self._tool_results = tool_results

        # If a human handoff happened, mark the workflow escalated
        if any(r.tool == "create_ticket" and r.state == "success" for r in tool_results):
            from .state import mark_escalated
            mark_escalated(conversation)

        # Log tool executions
        for i, r in enumerate(tool_results):
            try:
                ToolCallLog.objects.create(
                    conversation=conversation,
                    user=user,
                    reply_id=reply_id,
                    iteration=i,
                    tool_name=r.tool,
                    arguments=plan[i].args if i < len(plan) else {},
                    result_summary=self._summarize_result(r),
                    execution_time_ms=r.execution_time_ms,
                )
            except Exception as exc:
                logger.warning("ToolCallLog write failed: %s", exc)

        # Step 5: Generate response
        response = self.response_generator.generate(
            context, tool_results, reply_id=reply_id, dry_run=self.dry_run
        )

        if not response.text:
            logger.warning("Orchestrator produced no reply reply_id=%s conv=%s", reply_id, conversation.pk)
            return

        self.last_response = response
        self.last_reply_id = reply_id

        # Step 6: Save bot message and send
        self._save_and_send(conversation, response, reply_id, dry_run=self.dry_run)

        # Step 6b: Background memory extraction (P0-12) — non-blocking
        if not self.dry_run:
            try:
                import threading

                from django.db import close_old_connections

                from .memory import MemoryManager

                turn_text = customer_text

                def _extract():
                    close_old_connections()
                    try:
                        MemoryManager.extract_from_conversation(conversation, turn_text)
                    except Exception:
                        logger.exception("Background memory extraction failed conv=%s", conversation.pk)
                    finally:
                        close_old_connections()

                threading.Thread(target=_extract, daemon=True).start()
            except Exception as exc:
                logger.warning("Memory extraction trigger failed: %s", exc)

        # Step 7: Deduct credits (skipped in dry-run)
        if not self.dry_run:
            try:
                from billing.deductions import deduct_for_reply
                deduct_for_reply(user, reply_id)
            except Exception:
                logger.exception("Credit deduction failed reply_id=%s", reply_id)

    def _save_and_send(self, conversation, response: Response, reply_id: str, dry_run=False):
        """Persist the bot reply and send it via the platform."""
        from .sender import send_reply

        # Build attachment metadata
        attachment = {}
        if response.images:
            attachment["images"] = response.images
        if response.cards:
            attachment["cards"] = response.cards
            attachment["type"] = "product_cards" if len(response.cards) > 1 else "product_card"

        # Build pipeline trace for the message
        trace = {}
        if hasattr(self, '_intent_name'):
            trace["intent"] = self._intent_name
        if hasattr(self, '_tool_results') and self._tool_results:
            trace["tool_calls"] = [
                {"tool": r.tool, "state": r.state, "time_ms": r.execution_time_ms}
                for r in self._tool_results if r.tool
            ]

        # Save message with pipeline trace in raw_payload
        Message.objects.create(
            conversation=conversation,
            sender="bot",
            text=response.text,
            attachments=attachment or None,
            raw_payload=trace or None,
        )

        if dry_run:
            # Bot Preview: persist the message for state continuity (workflow
            # history, last-bot-question matching) but never send to the platform.
            return

        # Send via platform
        texts = self._split_text(response.text)
        send_reply(
            conversation,
            texts,
            image_urls=response.images or None,
            product_cards=response.cards if len(response.cards) > 1 else None,
        )

    def _split_text(self, text: str) -> list[str]:
        if not text:
            return []
        parts = [p.strip() for p in text.split("\n\n") if p.strip()]
        return parts if len(parts) > 1 else [text]

    def _summarize_result(self, r: ToolResult) -> str:
        """Short human-readable summary of a tool result."""
        if r.state == "error":
            return f"Error: {r.error}"[:500]
        if r.state == "permission_denied":
            return "Permission denied"
        if not r.data:
            return "OK"

        if r.tool == "search_products":
            products = r.data.get("products", [])
            names = [p.get("name", p.get("pid", "?"))[:40] for p in products[:5]]
            extra = f" (+{len(products)-5} more)" if len(products) > 5 else ""
            return f"Found {r.data.get('total', len(products))} products: {', '.join(names)}{extra}"

        if r.tool == "send_images":
            products = r.data.get("products", [])
            if products:
                names = [f"{p.get('name', '?')} ({len(p.get('images', []))} imgs)" for p in products[:3]]
                return f"Sent images for {len(products)} product(s): {', '.join(names)}"
            return "No images available"

        if r.tool == "create_order":
            if r.data.get("order_id"):
                return f"Order {r.data['order_id']} created, total={r.data.get('total', '?')}"
            return f"Error: {r.data.get('error', 'unknown')}"

        if r.tool == "get_product_details":
            if r.data.get("name"):
                return f"Product: {r.data['name']} (price={r.data.get('price', '?')})"
            return f"Error: {r.data.get('error', 'not found')}"

        if r.tool == "get_order_status":
            if r.data.get("status"):
                return f"Order {r.data.get('order_id', '?')}: {r.data['status']}"
            return f"Error: {r.data.get('error', 'not found')}"

        return f"OK ({list(r.data.keys())[:3]})"


# ---------------------------------------------------------------------------
# Fallback: redirect old pipeline.run to orchestrator
# ---------------------------------------------------------------------------

def run_via_orchestrator(conversation, incoming_message):
    """Compatibility wrapper so webhooks.py can call orchestrator."""
    orch = Orchestrator()
    orch.process(conversation, incoming_message)
