import json
import logging
from dataclasses import dataclass, field
from typing import Any

from django.contrib.auth.models import User

from back.models import Conversation, Message, Product

logger = logging.getLogger(__name__)

MAX_PROMPT_LENGTH = 10000


# ---------------------------------------------------------------------------
# Shared dataclasses (P0-7)
# ---------------------------------------------------------------------------

@dataclass
class PlanStep:
    tool: str
    args: dict = field(default_factory=dict)
    depends_on: list[int] | None = None
    fallback: str | None = None
    timeout_ms: int = 10000
    retry_count: int = 1


@dataclass
class AIPlan:
    """Structured plan produced by the Planner (new_ai_orchestrator 'AI as advisor'):
      - goal / conversation_state / required_tools / ask_clarification / confidence
      - steps: the validated tool sequence software will execute.

    The LLM proposes; software validates the steps against per-intent tool
    routing and drops risky/unavailable ones before anything executes.
    """
    goal: str = ""
    conversation_state: str = ""
    required_tools: list[str] = field(default_factory=list)
    ask_clarification: bool = False
    confidence: float = 1.0
    reason: str = ""
    steps: list[PlanStep] = field(default_factory=list)


@dataclass
class Response:
    text: str = ""
    images: list[str] = field(default_factory=list)
    cards: list[dict] = field(default_factory=list)
    transferred: bool = False
    metadata: dict = field(default_factory=dict)


@dataclass
class CustomerProfile:
    name: str = ""
    phone: str = ""
    city: str = ""
    address: str = ""
    is_returning: bool = False
    preferred_tone: str = ""
    language_detected: str = ""


@dataclass
class BusinessSettings:
    store_name: str = ""
    address: str = ""
    whatsapp_number: str = ""
    currency: str = "BDT"
    delivery_charge_inside: float = 0
    delivery_charge_outside: float = 0
    support_open_time: str = "09:00"
    support_close_time: str = "21:00"
    timezone: str = "Asia/Dhaka"
    agent_name: str = ""
    agent_role: str = ""
    agent_tone: str = "friendly"
    agent_style: str = "concise"
    agent_language: str = "bn"
    custom_instructions: str = ""
    chit_chat_enabled: bool = True
    chit_chat_style: str = "moderate"
    cross_sell_enabled: bool = True
    ask_open_ended: bool = True
    greeting_message: str = ""


@dataclass
class ProductSummary:
    pid: str = ""
    name: str = ""
    price: str = ""
    discounted_price: str | None = None
    stock: int = 0
    in_stock: bool = True
    description: str = ""
    sku: str = ""
    external_id: str = ""


@dataclass
class OrderSummary:
    oid: str = ""
    status: str = ""
    total: str = ""
    customer_name: str = ""
    created_at: str = ""


@dataclass
class MemorySummary:
    facts: list[dict] = field(default_factory=list)
    preferences: list[dict] = field(default_factory=list)
    text: str = ""


@dataclass
class Intent:
    name: str = "UNKNOWN"
    confidence: float = 0.0
    sub_intent: str = ""
    entities: dict = field(default_factory=dict)


@dataclass
class ConversationContext:
    user: User | None = None
    conversation: Conversation | None = None
    platform: str = ""
    customer: CustomerProfile = field(default_factory=CustomerProfile)
    settings: BusinessSettings = field(default_factory=BusinessSettings)
    products: list[ProductSummary] = field(default_factory=list)
    orders: list[OrderSummary] = field(default_factory=list)
    memory: MemorySummary = field(default_factory=MemorySummary)
    history: list[dict] = field(default_factory=list)
    summary: str = ""
    intent: Intent | None = None
    plan: list[PlanStep] | None = None
    tool_results: list[Any] | None = None
    incoming_text: str = ""
    model: str | None = None
    stage: str = "browsing"

    def summary(self, max_products=5, max_orders=3):
        parts = [f"Platform: {self.platform}"]
        if self.customer.name:
            parts.append(f"Customer: {self.customer.name}")
        if self.customer.phone:
            parts.append(f"Phone: {self.customer.phone}")
        if self.customer.city:
            parts.append(f"City: {self.customer.city}")

        if self.settings.store_name:
            parts.append(f"Store: {self.settings.store_name}")
        parts.append(f"Currency: {self.settings.currency}")

        if self.products:
            lines = ["Focused Products:"]
            for p in self.products[:max_products]:
                price = p.discounted_price or p.price
                lines.append(f"  - {p.name} ({p.pid}) — {price} {self.settings.currency}")
            parts.append("\n".join(lines))

        if self.orders:
            lines = ["Recent Orders:"]
            for o in self.orders[:max_orders]:
                lines.append(f"  - {o.oid}: {o.status} ({o.total})")
            parts.append("\n".join(lines))

        if self.memory.text:
            parts.append(f"Memory:\n{self.memory.text}")

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# ConversationManager (P0-1)
# ---------------------------------------------------------------------------

class ConversationManager:

    @staticmethod
    def build(conversation, incoming_text="", model=None) -> ConversationContext:
        user = conversation.user
        ctx = ConversationContext(
            user=user,
            conversation=conversation,
            platform=conversation.platform,
            incoming_text=incoming_text,
            model=model,
            customer=ConversationManager._load_customer(conversation),
            settings=ConversationManager._load_settings(user),
            products=ConversationManager._load_products(conversation),
            orders=ConversationManager._load_orders(conversation),
            history=ConversationManager._load_history(conversation),
            memory=ConversationManager._load_memory(user, conversation),
            summary=getattr(conversation, "chat_summary", "") or "",
        )
        return ctx

    @staticmethod
    def _load_customer(conversation) -> CustomerProfile:
        return CustomerProfile(
            name=conversation.customer_name or "",
            phone=conversation.customer_phone or "",
            city=conversation.customer_city or "",
            address=conversation.customer_address or "",
            is_returning=conversation.is_returning or False,
            preferred_tone=conversation.preferred_tone or "",
            language_detected=conversation.language_detected or "",
        )

    @staticmethod
    def _load_settings(user) -> BusinessSettings:
        from context.models import AgentIdentity, BehaviorRules, StoreConfig

        settings = BusinessSettings()

        store = StoreConfig.objects.filter(user=user).first()
        if store:
            settings.store_name = store.store_name or ""
            settings.address = store.address or ""
            settings.whatsapp_number = store.whatsapp_number or ""
            settings.currency = store.currency or "BDT"
            settings.delivery_charge_inside = float(store.delivery_charge_inside or 0)
            settings.delivery_charge_outside = float(store.delivery_charge_outside or 0)
            settings.support_open_time = str(store.support_open_time or "09:00")
            settings.support_close_time = str(store.support_close_time or "21:00")
            settings.timezone = store.timezone or "Asia/Dhaka"

        identity = AgentIdentity.objects.filter(user=user).first()
        if identity:
            settings.agent_name = identity.name or ""
            settings.agent_role = identity.role or ""
            settings.agent_tone = identity.tone or "friendly"
            settings.agent_style = identity.style or "concise"
            settings.agent_language = identity.language or "bn"

        rules = BehaviorRules.objects.filter(user=user).first()
        if rules:
            settings.custom_instructions = rules.custom_instructions or ""
            settings.chit_chat_enabled = rules.chit_chat_enabled
            settings.chit_chat_style = rules.chit_chat_style or "moderate"
            settings.cross_sell_enabled = rules.cross_sell_enabled
            settings.ask_open_ended = rules.ask_open_ended
            settings.greeting_message = rules.greeting_message or ""

        return settings

    @staticmethod
    def _load_products(conversation) -> list[ProductSummary]:
        from .tools import parse_focus_products
        focus_list = parse_focus_products(getattr(conversation, "current_product", ""))
        products = []
        for f in focus_list:
            products.append(ProductSummary(
                pid=f.get("pid", ""),
                name=f.get("name", ""),
                price=str(f.get("price", "")),
                discounted_price=str(f.get("discounted_price")) if f.get("discounted_price") else None,
                stock=f.get("stock", 0),
                in_stock=f.get("in_stock", True),
                description=f.get("description", "")[:200],
                sku=f.get("sku", ""),
                external_id=f.get("external_id", ""),
            ))
        return products

    @staticmethod
    def _load_orders(conversation) -> list[OrderSummary]:
        orders = []
        try:
            for sale in conversation.orders.all().order_by("-created_at")[:3]:
                orders.append(OrderSummary(
                    oid=sale.oid,
                    status=sale.status,
                    total=str(sale.amount),
                    customer_name=sale.customer_name or "",
                    created_at=sale.created_at.isoformat() if sale.created_at else "",
                ))
        except Exception:
            pass
        return orders

    @staticmethod
    def _load_history(conversation, limit=15) -> list[dict]:
        msgs = list(
            Message.objects
            .filter(conversation=conversation)
            .order_by("-timestamp")[:limit]
        )
        msgs.reverse()
        history = []
        for m in msgs:
            role = "assistant" if m.sender == "bot" else "user"
            content = m.text or ""
            if not content and m.attachments:
                att_type = m.attachments.get("type", "")
                url = m.attachments.get("url") or m.attachments.get("payload", {}).get("url", "")
                content = f"[{att_type}: {url}]" if url else f"[{att_type}]"
            history.append({"role": role, "content": content})
        return history

    @staticmethod
    def _load_memory(user, conversation) -> MemorySummary:
        """Load long-term memory (facts, preferences) into the context (P0-11 wiring)."""
        memory = MemorySummary()
        try:
            from .memory import MemoryManager

            entries = MemoryManager.recall(user)
            if not entries:
                return memory

            for e in entries[:15]:
                item = {"key": e.key, "value": e.value, "memory_type": e.memory_type,
                        "confidence": e.confidence}
                if e.memory_type == "preference":
                    memory.preferences.append(item)
                else:
                    memory.facts.append(item)

            memory.text = MemoryManager.summarize(user, max_items=8, entries=entries)
        except Exception as exc:
            logger.warning("Memory load failed for user=%s: %s", user.pk, exc)
        return memory


# ---------------------------------------------------------------------------
# Backward-compatible functions (used by old pipeline)
# ---------------------------------------------------------------------------

# Rolling conversation summary cap (chars). Kept tight so the summary adds
# context cheaply without bloating the system prompt.
_SUMMARY_MAX_CHARS = 900
_SUMMARY_TURNS_KEPT = 10


def update_turn_summary(conversation, *, customer_text="", bot_text="", intent=""):
    """Append one compact turn to the conversation's rolling summary.

    Deterministic + free (no LLM call per turn). The summary gives the planner
    and the response generator durable memory of what happened before the
    visible message window, at a bounded token cost.
    """
    try:
        if conversation is None:
            return
        line = f"customer: {customer_text}"[:180]
        if bot_text:
            line += f" || bot: {bot_text[:140]}"
        if intent:
            line += f" || intent: {intent}"

        old = conversation.chat_summary or ""
        # Drop the oldest kept turns when over budget: keep only the most
        # recent _SUMMARY_TURNS_KEPT lines inside the char cap.
        if old:
            turns = [t for t in old.split("\n|") if t.strip()]
            joined = "\n|".join(turns)
            while len(joined) > _SUMMARY_MAX_CHARS and len(turns) > 2:
                turns = turns[-(_SUMMARY_TURNS_KEPT - 1):]
                joined = "\n|".join(turns)
            old = joined + "\n|" if joined else ""

        new = old + line
        if len(new) > _SUMMARY_MAX_CHARS:
            new = new[-_SUMMARY_MAX_CHARS:]
        Conversation.objects.filter(pk=conversation.pk).update(chat_summary=new)
        conversation.chat_summary = new
    except Exception:
        pass

def build_system_prompt(user, conversation, image_analysis=None):
    ctx = ConversationManager.build(conversation)
    return _build_system_prompt_from_ctx(ctx, image_analysis)


def _build_system_prompt_from_ctx(ctx, image_analysis=None):
    from api.products.factory import get_active_source, is_external

    settings = ctx.settings
    source = get_active_source(ctx.user)
    external_catalog = bool(source) and is_external(ctx.user)

    parts = []

    if settings.agent_name:
        parts.append(
            f"## Your Identity\n"
            f"Name: {settings.agent_name}\n"
            f"Role: {settings.agent_role or 'Sales & Support Agent'}\n"
            f"Tone: {settings.agent_tone}  |  Style: {settings.agent_style}  |  Language: {settings.agent_language}\n"
            f"Always respond in the customer's detected language, defaulting to {settings.agent_language}."
        )

    if settings.custom_instructions:
        parts.append(f"## Custom Instructions (user-defined)\n{settings.custom_instructions}")

    tone = settings.agent_tone or "friendly"
    style = settings.agent_style or "concise"
    parts.append(
        "## BEHAVIOR (follow exactly)\n"
        "- Warm, human, concise (1-3 sentences).\n"
        "- No numbered lists. No URLs. No JSON.\n"
        "- Specific product request → show only the best or exact matches.\n"
        "- If multiple options, use send_images(pids=[...]) to send carousel as names and price are already in the carousel.\n"
        "- If you want multiple short messages, separate with a blank line.\n"
        "- Delivery/payment questions: answer directly; don't collect details unless ordering.\n"
        "- The catalog has NO videos. If asked for videos/ভিডিও, say they're unavailable "
        "and offer to send pictures instead. Never claim to send videos.\n"
        "- Send at most ONE image per product and no more than 4 products per turn. "
        "Never resend images you already showed in this conversation unless the customer "
        "explicitly asks for more.\n"
        "- When product cards or images are being sent, reply with ONE short "
        "message: a brief intro sentence and one short follow-up question. Never "
        "repeat names, prices, or details already shown in the cards. No numbered "
        "lists, no markdown (**), no image URLs in text.\n"
    )

    parts.append(
        "## WORKFLOW (product requests)\n"
        "1) think(): plan 2-3 queries.\n"
        "2) search_products with different keywords (Bengali → English).\n"
        "3) Verify names match; if not, search again.\n"
        "4) Respond with genuine matches or say out of stock.\n"
        "5) If several products match, call send_images ONCE with all pids "
        "(pids=[...]). NEVER call send_images twice for the same product.\n"
    )
    if external_catalog:
        parts.append("- For images: search SKU first, then name.\n")
    else:
        parts.append("- For images: search SKU first, then name.\n")
    parts.append(
        "- NEVER state a price or name you didn't just get from a tool.\n"
        "- If search results don't include any product relevant to the "
        "customer's query → say it's out of stock. Do NOT invent product "
        "names, prices, or descriptions.\n"
        "- If product has variations (size/color), show options before ordering.\n"
        "- If product not found → say out of stock (not 'not found').\n"
        "- Use get_order_status for existing orders.\n"
        "- Complaints/policy questions: check search_knowledge_base FIRST (return, "
        "refund, delivery, payment, warranty); answer from it. Use create_ticket only "
        "to escalate genuine complaints, angry customers, or out-of-scope requests.\n"
        "- Use search_knowledge_base for FAQs, policies, delivery info — not for products.\n"
        "- Never paste image URLs in text — use send_images only.\n"
        "- Before create_order collect: customer name, phone, delivery address.\n"
        f"- Keep replies {tone} and {style}."
    )

    if image_analysis:
        parts.append(
            "## Image Analysis (already processed)\n"
            "The customer sent an image. The system analyzed it and pre-searched "
            "the catalog for matching products (results are below in "
            "'Recent Searched Products'). You do NOT need to search again "
            "unless the customer asks for something different."
        )
        if image_analysis.get("sku"):
            parts[-1] += f"\nDetected SKU: {image_analysis['sku']}"
        if image_analysis.get("product_name"):
            parts[-1] += f"\nDetected name: {image_analysis['product_name']}"
        if image_analysis.get("brand"):
            parts[-1] += f"\nDetected brand: {image_analysis['brand']}"

    parts.append(
        "## RESPONSE FLOW\n"
        "- For images: use analyzed SKU/name first, then search.\n"
        "- Never claim to send images without send_images.\n"
        "- send_images(pid=...) for one, send_images(pids=[...]) for many.\n"
        "- Keep reply short: name + price + one follow-up.\n"
        "- Multi-item order: confirm items, then create_order.\n"
    )

    if settings.store_name:
        parts.append(
            f"## Store\n"
            f"Name: {settings.store_name or 'Our Store'}\n"
            f"Address: {settings.address or 'Not set'}\n"
            f"WhatsApp: {settings.whatsapp_number or 'Not set'}\n"
            f"Support hours: {settings.support_open_time} – {settings.support_close_time} ({settings.timezone})\n"
            f"Currency: {settings.currency}\n"
            f"Delivery inside: {settings.delivery_charge_inside} {settings.currency}  |  "
            f"Outside: {settings.delivery_charge_outside} {settings.currency}"
        )

    chit = f"{'on' if settings.chit_chat_enabled else 'off'} ({settings.chit_chat_style})"
    parts.append(
        f"## Behavior\n"
        f"Chit-chat: {chit}  |  Cross-sell: {'yes' if settings.cross_sell_enabled else 'no'}  |  "
        f"Ask open-ended questions: {'yes' if settings.ask_open_ended else 'no'}"
    )
    if settings.greeting_message:
        parts.append(f"Greeting template: {settings.greeting_message}")

    cust = []
    if ctx.customer.name:
        cust.append(f"Name: {ctx.customer.name}")
    if ctx.customer.phone:
        cust.append(f"Phone: {ctx.customer.phone}")
    if ctx.customer.city:
        cust.append(f"City: {ctx.customer.city}")
    if ctx.conversation and ctx.conversation.greeted:
        cust.append("Already greeted: yes")
    if ctx.conversation and ctx.conversation.detected_intent:
        cust.append(f"Intent: {ctx.conversation.detected_intent}")

    # Rolling summary of earlier turns — cheap durable memory for planner and
    # response generation, bounded to ~900 chars (see update_turn_summary).
    if ctx.summary:
        parts.append("## Conversation Summary (previous turns)\n" + ctx.summary)

    currency = settings.currency or "BDT"

    # Active order session — the customer is mid-order (details or confirm
    # step). The LLM MUST see the exact pending state so price/quantity
    # questions ("total koto?") are answered from it, not hallucinated.
    if ctx.conversation is not None:
        try:
            from context.models import SessionContext
            from api.ai.state import ORDER_FIELDS
            from back.models import Product
            sess = SessionContext.objects.filter(conversation=ctx.conversation).first()
            if sess and sess.current_workflow == "create_order" and sess.state != "idle":
                cd = sess.collected_data or {}
                pending = []
                pid = cd.get("pid")
                if pid:
                    prod = Product.objects.filter(user=ctx.user, pid=pid).first()
                    qty = int(cd.get("quantity") or 1)
                    name = cd.get("product_name") or (prod.name if prod else "")
                    pending.append(f"Product: {name} (qty {qty})")
                    if prod:
                        price = prod.discounted_price or prod.price or 0
                        pending.append(f"Unit price: {price} {currency}")
                if sess.state == "awaiting_confirmation" and sess.pending_confirmation:
                    pc = sess.pending_confirmation
                    if pc.get("customer_name"):
                        pending.append(f"Name: {pc.get('customer_name')}")
                    if pc.get("customer_phone"):
                        pending.append(f"Phone: {pc.get('customer_phone')}")
                    if pc.get("customer_address"):
                        pending.append(f"Address: {pc.get('customer_address')}")
                pending.append(f"Flow step: {sess.state}")
                if pending:
                    parts.append(
                        "## ACTIVE ORDER (pending, do not guess amounts)\n"
                        + "\n".join(pending)
                        + "\nAnswer order-price/quantity questions from this "
                          "section. Do NOT create or repeat the order — the flow "
                          "collects details and asks for confirmation itself."
                    )
        except Exception:
            pass

    from .tools import parse_focus_products
    focus_list = parse_focus_products(ctx.conversation.current_product if ctx.conversation else "")
    if focus_list:
        cust.append(_render_focus_products(focus_list, currency))

    if cust:
        parts.append("## Current Customer\n" + "\n".join(cust))

    if external_catalog:
        parts.append("## END Of Recent searched Products\n")
    else:
        available_products = list(Product.objects.filter(user=ctx.user, status=True)[:20])
        if available_products:
            lines = ["## Available Products (sample — use search_products for the full catalog)"]
            for p in available_products:
                desc = (p.description or "")[:80]
                price_str = f"{p.price} {currency}"
                if p.discounted_price and p.discounted_price < p.price:
                    price_str += f" (discounted: {p.discounted_price} {currency})"
                lines.append(
                    f"- {p.name} (PID: {p.pid}) — {price_str}"
                    + (f" — {desc}" if desc else "")
                )
            parts.append("\n".join(lines))
        else:
            parts.append("## Available Products\nNo products listed — use search_products.")

    system_prompt = "\n\n".join(parts)
    if len(system_prompt) > MAX_PROMPT_LENGTH:
        system_prompt = system_prompt[:MAX_PROMPT_LENGTH] + "\n\n[SYSTEM PROMPT TRUNCATED]"
    return system_prompt


def _render_focus_products(focus_list, currency):
    seen = set()
    deduped = []
    for f in focus_list:
        pid = f.get("pid")
        if pid and pid not in seen:
            seen.add(pid)
            deduped.append(f)
    focus_list = deduped
    if not focus_list:
        return ""

    lines = ["## Recent Searched Products (recent — what this conversation is about, newest first)"]

    primary = focus_list[0]
    p_pid = primary.get("pid", "")
    p_name = primary.get("name") or ""
    header = f"- {p_name} (PID: {p_pid})" if p_name else f"- PID: {p_pid}"
    if primary.get("sku"):
        header += f"  SKU: {primary['sku']}"
    lines.append(header)
    if primary.get("price") is not None:
        price_line = f"   Price: {primary['price']} {currency}"
        if primary.get("discounted_price"):
            price_line += f"  Discounted: {primary['discounted_price']} {currency}"
        lines.append(price_line)
    if primary.get("stock") is not None:
        lines.append(f"   Stock: {primary['stock']} ({'in stock' if primary.get('in_stock', True) else 'out of stock'})")
    if primary.get("description"):
        lines.append(f"   Description: {primary['description']}")
    for v in primary.get("variations") or []:
        stock_note = "" if v.get("in_stock", True) else " — out of stock"
        lines.append(
            f"   • {v.get('name')} — {v.get('price')} {currency} "
            f"(variation_id={v.get('variation_id')}){stock_note}"
        )

    for f in focus_list[1:]:
        name = f.get("name") or ""
        label = f"{name} (PID: {f.get('pid')})" if name else f"PID: {f.get('pid')}"
        price = f"{f['price']} {currency}" if f.get("price") is not None else ""
        stock_note = "" if f.get("in_stock", True) else " — out of stock"
        lines.append(f"- {label}" + (f" — {price}" if price else "") + stock_note)

    lines.append(
        "All data above is COMPLETE — you already have name, price, stock, "
        "description, and variations. Call send_images(pid=...) to send photos "
        "and describe the product from this data. Do NOT call get_product_details "
        "for any product listed above — the data is already here."
    )
    return "\n".join(lines)


def get_conversation_history(conversation, limit=20):
    msgs = list(
        Message.objects
        .filter(conversation=conversation)
        .order_by("-timestamp")[:limit]
    )
    msgs.reverse()
    history = []
    for m in msgs:
        role = "assistant" if m.sender == "bot" else "user"
        content = m.text or ""
        if not content and m.attachments:
            att_type = m.attachments.get("type", "")
            url = m.attachments.get("url") or m.attachments.get("payload", {}).get("url", "")
            content = f"[{att_type}: {url}]" if url else f"[{att_type}]"
        history.append({"role": role, "content": content})
    return history
