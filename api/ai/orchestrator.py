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
from .validator import ResponseValidator

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 7

# Intent confidence below this never auto-executes a risky flow (CREATE_ORDER,
# cancellations, human handoff). See 'Use confidence' in the task docs.
_LOW_CONFIDENCE = 0.6


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
            try:
                if not conversation.auto_enable_ai():
                    logger.info("Conversation AI disabled — skipping orchestrator conv=%s", conversation.pk)
                    return
            except Exception:
                logger.exception("auto_enable_ai failed conv=%s", conversation.pk)
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

        # Step 1c (F1): STAGE-FIRST routing. When an order workflow is active,
        # the workflow consumes order-ish messages BEFORE intent detection runs —
        # a quantity ("2 pcs"), details ("Basundhara R/A B Block Rd 04
        # 01935467644") or a confirmation ("Ho re bhai") is a flow answer,
        # never a product search (R1/R4/R5). Non-order content (questions,
        # browse requests) returns None and the normal pipeline answers while
        # the flow stays paused.
        from .state import WorkflowEngine, get_stage
        stage = get_stage(conversation)
        context.stage = stage
        if stage in ("order_collecting", "awaiting_confirmation"):
            wf_result = WorkflowEngine.handle_message(conversation, customer_text, context)
            if wf_result:
                response = Response(text=wf_result.get("text", ""))
                self.last_response = response
                self.last_reply_id = reply_id
                self._save_and_send(conversation, response, reply_id, dry_run=self.dry_run)
                logger.info("Orchestrator stage-first workflow consumed conv=%s", conversation.pk)
                return

        # Step 2: Detect intent
        intent_name, intent_conf = self.intent_detector.detect_with_confidence(customer_text, context)
        from .context import Intent as IntentDC
        context.intent = IntentDC(name=intent_name, confidence=intent_conf)
        self._intent_name = intent_name
        self._intent_conf = intent_conf

        # F1: AFFIRM outside a live workflow is chit-chat — never a product
        # search ("Ho re bhai" used to search the catalog). The 2a gate below
        # still upgrades it to CREATE_ORDER right after an order question, so
        # a "Ji" answer to "...অর্ডার করতে চান?" starts the order flow.
        if intent_name == "AFFIRM":
            intent_name = "SMALL_TALK"
            context.intent = IntentDC(name=intent_name, confidence=0.75)
            self._intent_name = intent_name
            self._intent_conf = 0.75
            logger.info("Orchestrator AFFIRM → SMALL_TALK conv=%s", conversation.pk)

        # Step 2a: A confirmation ("ok", "হ্যাঁ") right after an ORDER question
        # ("...নিতে আগ্রহী?") means the customer wants that product → order flow.
        # The last bot question must actually be asking for an order — a bare
        # "ok" after any other question ("আরেকটা দেখতে চান?", "ছবি পাঠাব?") is
        # NOT buying intent.
        if intent_name in ("SMALL_TALK", "GREETING", "UNKNOWN"):
            from .state import WorkflowEngine
            if WorkflowEngine.CONFIRM_RE.search(customer_text):
                last_bot = Message.objects.filter(
                    conversation=conversation, sender="bot"
                ).order_by("-timestamp").first()
                if (
                    last_bot and last_bot.text
                    and last_bot.text.rstrip().endswith("?")
                    and WorkflowEngine.ORDER_QUESTION_RE.search(last_bot.text)
                ):
                    intent_name = "CREATE_ORDER"
                    context.intent = IntentDC(name=intent_name, confidence=1.0)
                    self._intent_name = intent_name
                    logger.info(
                        "Orchestrator confirm-after-order-question → CREATE_ORDER conv=%s",
                        conversation.pk,
                    )

        # Step 2a-lite: customers often volunteer their number mid-chat ("amar
        # number 01712345678"). Save it so any later order flow prefills
        # instead of asking. Presence of a phone is NOT buying intent.
        # Skip URLs / long digit blobs (image URLs, tracking codes) — they are
        # never volunteered phone numbers.
        if not getattr(conversation, "customer_phone", ""):
            digits = _re.sub(r"\D", "", customer_text)
            if len(digits) >= 10 and "http" not in customer_text.lower() and len(_re.sub(r"\D", "", customer_text)) <= 15:
                # Prefer a Bangladeshi mobile shape (01XXXXXXXXX / +880…) when
                # visible; otherwise take the first 15 digits as before.
                m = _re.search(r"(\+?88)?0?1\d{9}\b", customer_text)
                phone = m.group(0).lstrip("+88") if m else digits[:15]
                try:
                    Conversation.objects.filter(pk=conversation.pk).update(customer_phone=phone)
                    conversation.customer_phone = phone
                    logger.info("Captured volunteered phone conv=%s", conversation.pk)
                except Exception:
                    logger.exception("Phone capture failed conv=%s", conversation.pk)

        # F3: structured details volunteered mid-browsing ("Basundhara R/A B
        # Block Rd 04 01935467644") are captured into session.pre_collected
        # so a later order flow seeds from them instead of stale conversation
        # defaults (R4). Runs before the repeated-UNKNOWN → CATALOG gate so
        # both paths keep the extracted data.
        if intent_name == "UNKNOWN":
            try:
                from .state import capture_pre_collected
                capture_pre_collected(conversation, customer_text)
            except Exception:
                logger.exception("pre_collected capture failed conv=%s", conversation.pk)

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
        # Low-confidence gate (docs 'Use confidence'): a weak CREATE_ORDER signal
        # must NOT auto-start the order flow — treat it as a product/search turn
        # so the bot asks a clarifying question instead of creating an order.
        if intent_name == "CREATE_ORDER":
            if not getattr(self, "_intent_conf", 1.0) or self._intent_conf < _LOW_CONFIDENCE:
                logger.info(
                    "Low-confidence CREATE_ORDER (%.2f) → SEARCH_PRODUCT conv=%s",
                    self._intent_conf, conversation.pk,
                )
                intent_name = "SEARCH_PRODUCT"
                context.intent = IntentDC(name="SEARCH_PRODUCT", confidence=self._intent_conf)
                self._intent_name = intent_name
            else:
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

        # F2: PROVIDE_QUANTITY ("2 pack den", "5টা") with an active stage was
        # already consumed by the workflow (step 1c). In browsing stage this
        # starts the order flow immediately when a product resolves — waiting
        # for an explicit "order" word is how the Aug-12 session lost the
        # quantity (R2/R3). With no resolvable product the quantity is safely
        # pre-captured and the flow asks which product.
        if intent_name == "PROVIDE_QUANTITY":
            try:
                from .state import capture_pre_collected
                capture_pre_collected(conversation, customer_text)
            except Exception:
                logger.exception("PROVIDE_QUANTITY pre-capture failed conv=%s", conversation.pk)
            started = WorkflowEngine.start_order_flow(conversation, context)
            if started:
                response = Response(text=started.get("text", ""))
                self.last_response = response
                self.last_reply_id = reply_id
                self._save_and_send(conversation, response, reply_id, dry_run=self.dry_run)
                logger.info(
                    "Orchestrator PROVIDE_QUANTITY → order flow conv=%s",
                    conversation.pk,
                )
                return
            intent_name = "UNKNOWN"
            context.intent = IntentDC(name=intent_name, confidence=0.0)
            self._intent_name = intent_name
            self._intent_conf = 0.0

        # Step 3: Generate plan (structured AIPlan — deterministic tiers are
        # free; the LLM tier is software-validated before anything executes)
        _t_plan = time.time()
        plan = self.planner.plan(intent_name, context, reply_id=reply_id)
        plan = self.planner.validate(plan, intent_name, context)
        _t_plan = (time.time() - _t_plan) * 1000
        context.plan = plan
        self._plan_meta = plan
        steps = plan.steps

        # Step 3a: The planner flagged an ambiguous turn — ask instead of
        # guessing ("Use confidence" / ask_clarification in the docs).
        if plan.ask_clarification and not steps:
            clarification = (
                "আপনি কোনটা বোঝাতে চেয়েছেন? একটু বিস্তারিত বলুন (যেমন প্রোডাক্টের নাম বা অর্ডার আইডি)।"
                if context.settings.agent_language == "bn" or getattr(conversation, "language_detected", "") == "bn"
                else "Could you tell me a bit more? For example the product name or order ID."
            )
            response = Response(text=clarification)
            self.last_response = response
            self.last_reply_id = reply_id
            self._save_and_send(conversation, response, reply_id, dry_run=self.dry_run)
            return

        # Step 4: Execute plan
        _t_exec = time.time()
        tool_results = self.executor.execute(steps, context)
        _t_exec = (time.time() - _t_exec) * 1000
        context.tool_results = tool_results
        self._tool_results = tool_results

        # If a human handoff happened, mark the workflow escalated
        if any(r.tool == "create_ticket" and r.state == "success" for r in tool_results):
            from .state import mark_escalated
            mark_escalated(conversation)

        # Per-turn decision context captured once here; written onto every
        # ToolCallLog row so the dashboard can join tool behavior to the
        # planner/intent/state that produced it (PHASE 2 observability).
        try:
            _conv_state = conversation.session.state
        except Exception:
            _conv_state = ""
        _self_intent_conf = getattr(self, "_intent_conf", None)
        _plan_goal = getattr(plan, "goal", "") or ""
        _plan_conf = getattr(plan, "confidence", None)

        # Log tool executions
        for i, r in enumerate(tool_results):
            try:
                ToolCallLog.objects.create(
                    conversation=conversation,
                    user=user,
                    reply_id=reply_id,
                    iteration=i,
                    tool_name=r.tool,
                    arguments=steps[i].args if i < len(steps) else {},
                    result_summary=self._summarize_result(r),
                    execution_time_ms=r.execution_time_ms,
                    conversation_state=_conv_state,
                    intent_confidence=_self_intent_conf,
                    plan_goal=_plan_goal,
                    plan_confidence=_plan_conf,
                )
            except Exception as exc:
                logger.warning("ToolCallLog write failed: %s", exc)

        # Step 5: Generate response
        _t_resp = time.time()
        response = self.response_generator.generate(
            context, tool_results, reply_id=reply_id, dry_run=self.dry_run
        )
        _t_resp = (time.time() - _t_resp) * 1000

        if not response.text:
            logger.warning("Orchestrator produced no reply reply_id=%s conv=%s", reply_id, conversation.pk)
            return

        # Step 5a: Deterministic final gate (Layer 7) — empty/unsafe/verbose/
        # media-cardinality corrections before anything reaches the customer.
        response, self._validation_issues = ResponseValidator.validate(response, context)

        self.last_response = response
        self.last_reply_id = reply_id
        self._latency_ms = {
            "planning": round(_t_plan, 1),
            "executor": round(_t_exec, 1),
            "response": round(_t_resp, 1),
        }

        # Step 6: Save bot message and send
        self._save_and_send(conversation, response, reply_id, dry_run=self.dry_run)

        # A first-contact greeting is now done — mark it so context/prompts can
        # stop treating every later "hi" as a fresh customer.
        if getattr(self, "_intent_name", "") == "GREETING":
            try:
                if not conversation.greeted:
                    Conversation.objects.filter(pk=conversation.pk).update(greeted=True)
                    conversation.greeted = True
            except Exception:
                pass

        # Step 6b: Background memory extraction (P0-12) — non-blocking
        if not self.dry_run:
            try:
                import threading

                from django.db import close_old_connections

                from .context import update_turn_summary
                from .memory import MemoryManager

                turn_text = customer_text
                turn_intent = getattr(self, "_intent_name", "")
                turn_bot = response.text or ""

                def _extract():
                    close_old_connections()
                    try:
                        MemoryManager.extract_from_conversation(conversation, turn_text)
                    except Exception:
                        logger.exception("Background memory extraction failed conv=%s", conversation.pk)
                    try:
                        update_turn_summary(
                            conversation,
                            customer_text=turn_text,
                            bot_text=turn_bot,
                            intent=turn_intent,
                        )
                    except Exception:
                        logger.exception("Background summary update failed conv=%s", conversation.pk)
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
        from .pipeline import _split_text_messages

        # Build attachment metadata
        attachment = {}
        # Cards and raw images are mutually exclusive: product cards already
        # carry the product photo (carousel), so sending raw images on top
        # duplicates media on the customer's screen ("photo and cards both").
        if response.images and not response.cards:
            attachment["images"] = response.images
        if response.cards:
            attachment["cards"] = response.cards
            attachment["type"] = "product_cards" if len(response.cards) > 1 else "product_card"

        # Text discipline lives in ResponseValidator (Layer 7). Here we only
        # enforce bubble cardinality: ONE bubble when visuals are sent, else
        # capped at 3 for pure-text replies.
        has_visuals = bool(response.images or response.cards)
        text = response.text or ""

        # Build pipeline trace for the message
        trace = {}
        if hasattr(self, '_intent_name'):
            trace["intent"] = self._intent_name
            if getattr(self, "_intent_conf", None) is not None:
                trace["intent_confidence"] = round(getattr(self, "_intent_conf", 0.0), 3)
        if hasattr(self, '_tool_results') and self._tool_results:
            trace["tool_calls"] = [
                {"tool": r.tool, "state": r.state, "time_ms": r.execution_time_ms}
                for r in self._tool_results if r.tool
            ]
        if getattr(self, "_validation_issues", None):
            trace["validation"] = self._validation_issues
        plan_meta = getattr(self, "_plan_meta", None)
        if plan_meta is not None:
            trace["plan"] = {
                "goal": getattr(plan_meta, "goal", ""),
                "state": getattr(plan_meta, "conversation_state", ""),
                "confidence": round(getattr(plan_meta, "confidence", 0.0), 3),
                "ask_clarification": bool(getattr(plan_meta, "ask_clarification", False)),
            }
        latency = getattr(self, "_latency_ms", None)
        if latency:
            trace["latency_ms"] = latency
        try:
            if conversation.session_id:
                trace["conversation_state"] = conversation.session.state
        except Exception:
            pass

        # Save message with pipeline trace in raw_payload
        msg = Message.objects.create(
            conversation=conversation,
            sender="bot",
            text=text,
            attachments=attachment or None,
            raw_payload=trace or None,
        )

        if dry_run:
            # Bot Preview: persist the message for state continuity (workflow
            # history, last-bot-question matching) but never send to the platform.
            return

        # Send via platform — one bubble when visuals are sent, otherwise cap at 3.
        texts = _split_text_messages(text, max_parts=1 if has_visuals else 3)
        delivery = send_reply(
            conversation,
            texts,
            image_urls=response.images if not response.cards else None,
            product_cards=response.cards or None,
        )

        # Record the delivery outcome on the message so "saved but not delivered"
        # failures are visible instead of silent.
        try:
            raw = msg.raw_payload or {}
            if not isinstance(raw, dict):
                raw = {}
            raw["delivery"] = delivery
            msg.raw_payload = raw
            msg.save(update_fields=["raw_payload"])
        except Exception:
            logger.warning("Delivery-status write failed reply_id=%s", reply_id)

    def _split_text(self, text: str) -> list[str]:
        # Kept for backward compatibility — superseded by pipeline._split_text_messages.
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
