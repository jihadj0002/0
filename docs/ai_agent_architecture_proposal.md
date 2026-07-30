# AI Agent Architecture Proposal — TheMatrixAi

**Date:** 2026-07-30
**Status:** Proposed (pending implementation)
**Based on:** `docs/detals_ai_chatbot.md` (requirements), `docs/orchestrator_update.md` (architecture guidance), codebase analysis

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current Architecture Limitations](#2-current-architecture-limitations)
3. [Proposed Architecture Overview](#3-proposed-architecture-overview)
4. [Component Design](#4-component-design)
   - 4.1 ConversationManager
   - 4.2 Orchestrator
   - 4.3 IntentDetector
   - 4.4 Planner
   - 4.5 Executor
   - 4.6 ResponseGenerator
   - 4.7 ChannelFormatter
   - 4.8 ToolRegistry
5. [Memory System](#5-memory-system)
6. [State Machine & Workflows](#6-state-machine--workflows)
7. [Specialist Agents](#7-specialist-agents)
8. [Security & Permissions](#8-security--permissions)
9. [Tool Registry](#9-tool-registry)
10. [Data Models](#10-data-models)
11. [File Structure](#11-file-structure)
12. [Implementation Phases](#12-implementation-phases)
13. [Migration Strategy](#13-migration-strategy)

---

## 1. Executive Summary

TheMatrixAi's current AI pipeline is a single-LLM, monolithic tool loop — one system prompt, 8 tools, linear execution with max 7 iterations. This works for basic product search and order creation but cannot scale to the full e-commerce SaaS agent described in `detals_ai_chatbot.md`.

The proposed architecture separates **decision-making from execution**: lightweight Python logic handles conversation state and tool execution, while LLMs are reserved for planning and natural-language generation. This is cheaper, faster, more reliable, and easier to debug.

**Key metrics after implementation:**

| Metric | Current | Target |
|---|---|---|
| LLM calls per reply | 1-7 (avg 3.2) | 2 max (plan + respond) |
| Tool execution | LLM-driven (unreliable) | Deterministic Python (reliable) |
| Hallucination risk | High (LLM writes prices/names) | Zero (tools provide verified data) |
| Context window waste | Full prompt every iteration | Dynamic, relevant-only data |
| Multi-step workflows | Ad-hoc in system prompt | State-machine defined |
| Memory | Lost after 20 messages | Persistent long-term memory |

---

## 2. Current Architecture Limitations

### 2.1 Single LLM Does Everything

```python
# Current flow in api/ai/pipeline.py:
for iteration in range(MAX_TOOL_ITERATIONS):
    llm_msg, usage = call_llm(messages, tools=TOOL_DEFINITIONS)
    # LLM decides: should I call a tool? which one? or reply?
    # LLM generates text AND makes decisions in the same call
```

**Problem:** The LLM both decides and speaks in one turn. It frequently:
- Answers a product question without calling `search_products` (hallucinates price)
- Claims to send images without calling `send_images`
- Wastes iterations on redundant thinking instead of executing tools

### 2.2 No Separation of Concerns

The `context.py` builds one giant system prompt (up to 10k tokens) containing:
- Agent identity
- Store config
- Behavior rules
- Custom instructions
- Product catalog (first 20)
- Focused products
- Customer state
- Workflow rules

This is sent verbatim on every iteration. Most of it is irrelevant to the current task.

### 2.3 No Memory Beyond Conversation

- Focused products (5 items on `Conversation.current_product`) are the only cross-turn state
- No user preference memory ("always uses WooCommerce")
- No business context memory ("enterprise customer, priority support")
- Session state is implicit (buried in conversation history, not explicit)

### 2.4 No Security Model

```python
# Current execute_tool:
def execute_tool(fn_name, fn_args, user, conversation):
    # ... executes immediately, no permission check
```

Any authenticated user can call any tool. No role-based access control.

### 2.5 Proactive Intelligence = Zero

The system only reacts to incoming webhook messages. No scheduled checks for:
- Sync failures
- Expiring tokens
- Low inventory
- Expiring subscriptions

---

## 3. Proposed Architecture Overview

```
Webhook (WhatsApp/Messenger/Instagram/Telegram)
    │
    ▼
┌──────────────────────────────────────────────┐
│           ConversationManager                │  ← Pure Python, no LLM
│  Loads: conversation, customer, products,    │
│  orders, settings, memory, AI personality    │
│  Returns: ConversationContext                │
└─────────────────────┬────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────┐
│              Orchestrator                    │
│  1. IntentDetector → classify message        │
│  2. Router → pick specialist agent           │
│  3. Planner → produce PlanSteps              │
│  4. Executor → run tools (pure Python)       │
│  5. ResponseGenerator → LLM writes reply     │
│  6. ChannelFormatter → platform-specific     │
└─────────────────────┬────────────────────────┘
                      │
                      ▼
           ┌─────────────────────┐
           │   Specialist Agent  │  ← or direct tools
           │   (if routed)       │     for simple intents
           └─────────┬───────────┘
                     │
                     ▼
            ┌────────────────┐
            │   Executor     │  ← Pure Python runs each tool
            │  (Tool 1..N)   │     sequentially
            └────────┬───────┘
                     │
                     ▼
            ┌────────────────────┐
            │ ResponseGenerator  │  ← LLM generates final text
            │ (structured JSON   │     from verified tool results
            │  → natural reply)  │
            └────────┬───────────┘
                     │
                     ▼
            ┌────────────────────┐
            │ ChannelFormatter   │  ← WhatsApp/Messenger/Telegram format
            │ + Sender           │     sends reply
            └────────────────────┘
```

### Flow Detail

```
Step 1: ConversationManager (Python)
    conversation = Conversation.objects.get(...)
    context = ConversationContext(
        customer=CustomerProfile(...),
        products=get_focused_products(conversation),
        settings=get_business_settings(user),
        memory=MemoryManager.recall(user),
        state=get_conversation_state(conversation),
        history=get_recent_messages(conversation, limit=15),
    )

Step 2: Orchestrator — IntentDetector
    intent = detect_intent(message, context)
    # Returns: SEARCH_PRODUCT | CREATE_ORDER | CHECK_ORDER | FAQ | ...

Step 3: Orchestrator — Router
    if intent == "UPGRADE_PLAN" and context.state == "billing":
        agent = BillingAgent(context)
    elif intent == "SEARCH_PRODUCT":
        agent = SalesAgent(context)
    else:
        agent = DirectToolAgent(context)  # simple path, no specialist

Step 4: Planner (LLM or rule-based)
    plan = planner.plan(intent, context, agent.tools)
    # Returns: [PlanStep(tool="search_products", args={...}),
    #            PlanStep(tool="check_inventory", args={...})]

Step 5: Executor (Python — no LLM)
    results = []
    for step in plan:
        result = ToolRegistry.execute(step.tool, step.args, context)
        results.append(result)
        if result.state == "error":
            break

Step 6: ResponseGenerator (LLM)
    prompt = f"""Context: {context.summary}
    Tool Results: {json.dumps(results)}
    Generate a natural reply using ONLY the tool results above.
    Do not invent prices, stock, or availability."""
    reply = call_llm(prompt)  # No tools available — generation only

Step 7: ChannelFormatter + Sender (Python)
    formatted = channel_format(reply, images, cards, platform)
    send_via_platform(conversation, formatted)

Step 8: Side-effects (async via event bus)
    MemoryManager.store_fact(extracted_facts)
    AuditLog.log(trace)
    UsageSummary.increment()
```

---

## 4. Component Design

### 4.1 ConversationManager

**File:** `api/ai/context.py` (rewritten)

**Purpose:** Gather all relevant data into a structured `ConversationContext` dataclass. No LLM involved.

```python
@dataclass
class ConversationContext:
    user: User
    conversation: Conversation
    platform: str
    customer: CustomerProfile  # name, phone, city, preferences
    state: ConversationState   # browsing | cart | checkout | ...
    settings: BusinessSettings  # store config, behavior rules, agent identity
    products: list[ProductSummary]  # focused products + recently viewed
    orders: list[OrderSummary]  # recent orders for this customer
    memory: MemorySummary       # condensed from MemoryEntry
    history: list[Message]      # last 15 messages
    intent: Intent | None       # set later by IntentDetector
    plan: list[PlanStep] | None # set later by Planner
    tool_results: list[ToolResult] | None  # set later by Executor
```

**Key behaviors:**
- `.summary()` → returns a compact string representation for LLM context window
- `.build_for_agent(agent_name)` → returns a context subset relevant to a specific specialist agent
- Lazy-loading: products/orders/memory are loaded only when the agent type needs them

### 4.2 Orchestrator

**File:** `api/ai/orchestrator.py` (new)

**Purpose:** Replace `pipeline.py`. Manages the full lifecycle of a message.

```python
class Orchestrator:
    def process(self, message, conversation) -> OrchestratorResult:
        # 1. Build context
        context = ConversationManager.build(conversation, message)

        # 2. Detect intent
        context.intent = IntentDetector.detect(message.text, context)

        # 3. Route to specialist or direct
        agent = self.route(context)

        # 4. Plan tool calls (if needed)
        if agent.needs_planning(context):
            context.plan = Planner.plan(context, agent.tools)

        # 5. Execute plan
        context.tool_results = Executor.execute(context.plan, context)

        # 6. Generate response
        reply_text, images, cards = ResponseGenerator.generate(context)

        # 7. Format and send
        ChannelFormatter.send(conversation, reply_text, images, cards)

        # 8. Side-effects (async)
        self.emit_events(context)

        return OrchestratorResult(conversation, reply_text, images, cards)
```

### 4.3 IntentDetector

**File:** `api/ai/intent.py` (new)

**Purpose:** Map incoming message text to a structured intent. Fast path before invoking any LLM.

**Two-tier approach:**

1. **Rule-based matcher** (fast, zero cost):
   - Regex patterns for common intents
   - Keyword matching: "price" → ASK_PRICE, "order" + "status" → CHECK_ORDER, "refund" → RETURN_PRODUCT
   - State-based: if conversation.state == "cart" and message contains "yes" → CONFIRM_ORDER

2. **Small model fallback** (when rules are uncertain):
   - Single LLM call with a compact prompt, max 50 tokens
   - Returns one of the defined intent labels

**Intent taxonomy:**

```
SEARCH_PRODUCT     — "Do you have black hoodies?"
ASK_PRICE          — "How much is this?"
ASK_STOCK          — "Is it in stock?"
COMPARE_PRODUCTS   — "Which is better?"
CREATE_ORDER       — "I want to order"
CHECK_ORDER        — "Where is my order?"
CANCEL_ORDER       — "Cancel my order"
RETURN_PRODUCT     — "I want to return"
ASK_DELIVERY       — "How long does shipping take?"
ASK_PAYMENT        — "What payment methods?"
ASK_FAQ            — Store policies, hours, etc.
UPGRADE_PLAN       — "Upgrade me to Pro"
BILLING_QUERY      — "How much do I pay?"
STORE_SYNC         — "My Shopify stopped syncing"
ANALYTICS_QUERY    — "What were my sales yesterday?"
CONTENT_REQUEST    — "Write a product description"
GREETING           — "Hi", "Hello"
SMALL_TALK         — "How are you?"
HUMAN_SUPPORT      — "Talk to a person"
ESCALATION         — Angry, frustrated
```

### 4.4 Planner

**File:** `api/ai/planner.py` (new)

**Purpose:** Given an intent + context, produce an ordered sequence of tool calls. Returns `list[PlanStep]`.

**Three modes:**

| Mode | When | How | Cost |
|---|---|---|---|
| **Direct** | Simple intent (ASK_PRICE) | Single tool mapped from intent → PlanStep | 0 LLM calls |
| **Template** | Known workflow (CREATE_ORDER) | Load JSON workflow template → PlanSteps | 0 LLM calls |
| **LLM** | Complex/ambiguous | Single LLM call with context + available tools → returns tool sequence | 1 call |

**PlanStep structure:**
```python
@dataclass
class PlanStep:
    tool: str                    # tool name from registry
    args: dict                   # parameters for the tool
    depends_on: list[int] | None # indices of steps that must complete first
    fallback: str | None         # alternative tool if this fails
    timeout_ms: int              # per-tool timeout
    retry_count: int             # max retries on failure
```

**Example plans:**

```
Intent: CREATE_ORDER
Plan:
  1. search_products({"query": "black hoodie"})
  2. check_inventory({"pid": "sku_abc123"})
  3. calculate_price({"pid": "sku_abc123", "quantity": 2, "delivery_zone": "inside_dhaka"})
  4. update_customer({"name": "...", "phone": "...", "city": "..."})
  5. create_order({"customer_name": "...", "items": [...], ...})

Intent: CHECK_ORDER
Plan:
  1. get_order_status({"order_id": "ord_xyz789"})
  2. track_shipment({"order_id": "ord_xyz789"})  # if status == "delivering"

Intent: STORE_SYNC
Plan:
  1. get_connected_stores({})
  2. check_sync_status({"store_id": "src_..."})
  3. IF result.status == "error": refresh_token({"store_id": "src_..."})
  4. retry_sync({"store_id": "src_..."})
  5. verify_sync({"store_id": "src_..."})
```

### 4.5 Executor

**File:** `api/ai/executor.py` (new)

**Purpose:** Run tools deterministically. Pure Python — no LLM calls. One tool at a time, collecting results.

```python
class Executor:
    def execute(self, plan: list[PlanStep], context: ConversationContext) -> list[ToolResult]:
        results = []
        for step in plan:
            # Check permissions
            if not PermissionChecker.check(context.user, step.tool):
                results.append(ToolResult.error("permission_denied"))
                break

            # Execute with timeout + retry
            result = ToolRegistry.run(step.tool, step.args, context,
                                      timeout=step.timeout_ms,
                                      retries=step.retry_count)
            results.append(result)

            # Handle errors
            if result.state == "error":
                if step.fallback:
                    fallback_result = ToolRegistry.run(step.fallback, step.args, context)
                    results.append(fallback_result)
                else:
                    break

            # Pass result data to next step if needed (e.g., pid from search→inventory)
            if result.data:
                self._propagate(step, next_steps, result.data)

        return results
```

**ToolResult structure:**
```python
@dataclass
class ToolResult:
    state: Literal["success", "error", "empty", "permission_denied"]
    tool: str
    data: dict | None           # structured result data
    error: str | None           # error message if state == "error"
    execution_time_ms: int
    cached: bool                # was result served from cache?
```

### 4.6 ResponseGenerator

**File:** `api/ai/response.py` (new)

**Purpose:** After all tools finish, generate the final natural-language response. This is the only LLM call that produces customer-facing text. It receives structured tool results and must not invent data.

```python
class ResponseGenerator:
    def generate(self, context: ConversationContext) -> Response:
        prompt = self._build_prompt(context)
        # Tools are NOT passed — the LLM cannot call tools here
        llm_response = call_llm(
            messages=[{"role": "system", "content": prompt}],
            tools=None,  # No tools available — generation only
            temperature=0.6,
            max_tokens=512,
        )
        return Response(
            text=llm_response.content,
            images=self._extract_images(context.tool_results),
            cards=self._extract_cards(context.tool_results),
        )
```

**Prompt template:**
```
You are a {tone} {role} for {store_name}.

The customer asked:
{message}

Here are the VERIFIED results from the tools you requested. Only use this data.
Do NOT invent any product names, prices, stock, or availability.

Tool Results:
{json.dumps(tool_results, indent=2)}

Customer context:
- Name: {customer_name}
- Current state: {state}
- Recent memory: {memory_summary}

Write a natural, {style} reply in {language}.
- Max 3 sentences unless the customer asked for detailed information.
- Mention prices and stock only if they were in the tool results.
- If no products were found, say it's unavailable — do NOT suggest alternatives
  that weren't in the search results.
- Suggest one clear next action (order, ask more, etc.).
```

### 4.7 ChannelFormatter

**File:** `api/ai/sender.py` (rewritten)

**Purpose:** Take the generic `Response` (text + image URLs + product cards) and format it for each platform. Extracted from the current monolithic `sender.py`.

```python
class ChannelFormatter:
    @staticmethod
    def format(response: Response, platform: str) -> PlatformPayload:
        if platform == "whatsapp":
            return WhatsAppFormatter.format(response)
        elif platform == "messenger":
            return MessengerFormatter.format(response)
        elif platform == "instagram":
            return InstagramFormatter.format(response)
        elif platform == "telegram":
            return TelegramFormatter.format(response)

class WhatsAppFormatter:
    @staticmethod
    def format(response):
        parts = []
        for card in (response.cards or [])[:5]:
            parts.append({"type": "image", "image": {"link": card.images[0], "caption": card.caption}})
        for img in (response.images or [])[:5]:
            parts.append({"type": "image", "image": {"link": img}})
        for text in split_text(response.text):
            parts.append({"type": "text", "text": {"body": text, "preview_url": False}})
        return parts
```

### 4.8 ToolRegistry

**File:** `api/ai/tools.py` (rewritten)

**Purpose:** Central registry where all tools register themselves. Each tool is a class inheriting from `BaseTool`.

```python
class BaseTool:
    name: str
    description: str
    parameters: dict  # JSON Schema
    permission: str   # "public" | "customer" | "staff" | "manager" | "owner"
    timeout_ms: int = 10000
    retry_count: int = 1
    cost_estimate: int = 0  # estimated credit cost

    def execute(self, args: dict, context: ConversationContext) -> ToolResult:
        raise NotImplementedError

class SearchProductTool(BaseTool):
    name = "search_products"
    description = "Search products by SKU, name, or keyword"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "category": {"type": "string"},
            "min_price": {"type": "number"},
            "max_price": {"type": "number"},
            "limit": {"type": "integer", "default": 10},
        },
        "required": ["query"],
    }
    permission = "public"

    def execute(self, args, context):
        products = Product.objects.filter(...)
        return ToolResult.success(data={"products": [...]})
```

Tools are discovered via Django app configs and registered on startup:

```python
# api/ai/tools.py
class ToolRegistry:
    _tools: dict[str, type[BaseTool]] = {}

    @classmethod
    def register(cls, tool_cls):
        instance = tool_cls()
        cls._tools[instance.name] = instance

    @classmethod
    def get(cls, name) -> BaseTool | None:
        return cls._tools.get(name)

    @classmethod
    def execute(cls, name, args, context, timeout=10000, retries=1):
        tool = cls.get(name)
        if not tool:
            return ToolResult.error(f"Unknown tool: {name}")
        return tool.execute(args, context)
```

Each Django app registers its tools:

```python
# orders/tools.py
class CreateOrderTool(BaseTool):
    name = "create_order"
    ...

ToolRegistry.register(CreateOrderTool)
```

---

## 5. Memory System

### 5.1 Three-Tier Architecture

```
┌──────────────────────────────────────────────┐
│           Short-Term Memory                  │
│  Last 15 messages in conversation history    │
│  Stored on: ConversationContext.history       │
│  Persisted: Message model                     │
├──────────────────────────────────────────────┤
│           Session Memory                      │
│  Current workflow step, pending actions,      │
│  collected data, verification status          │
│  Stored on: SessionContext model              │
│  Persisted: db (per-conversation)             │
├──────────────────────────────────────────────┤
│           Long-Term Memory                    │
│  User preferences, facts, behavior patterns   │
│  Stored on: MemoryEntry model                 │
│  Persisted: db (cross-conversation)           │
└──────────────────────────────────────────────┘
```

### 5.2 MemoryEntry Model

```python
class MemoryEntry(models.Model):
    MEMORY_TYPES = [
        ("preference", "User Preference"),
        ("fact", "Extracted Fact"),
        ("behavior", "Behavior Pattern"),
        ("context", "Business Context"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memories")
    conversation = models.ForeignKey(Conversation, on_delete=models.SET_NULL,
                                      null=True, blank=True)
    memory_type = models.CharField(max_length=20, choices=MEMORY_TYPES)
    key = models.CharField(max_length=100, db_index=True)
    value = models.JSONField()
    confidence = models.FloatField(default=1.0)
    source = models.CharField(max_length=50, blank=True)  # e.g., "extraction", "manual"
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "memory_type", "is_active"]),
            models.Index(fields=["user", "key"]),
        ]
```

### 5.3 MemoryManager

```python
class MemoryManager:
    @staticmethod
    def store_fact(user, key, value, memory_type="fact",
                   confidence=1.0, source="extraction", ttl=None):
        """Store or update a memory entry."""
        entry, created = MemoryEntry.objects.update_or_create(
            user=user, key=key, memory_type=memory_type, is_active=True,
            defaults={
                "value": value,
                "confidence": confidence,
                "source": source,
                "expires_at": timezone.now() + ttl if ttl else None,
            },
        )
        return entry

    @staticmethod
    def recall(user, memory_type=None, key=None, min_confidence=0.3):
        """Retrieve active memories matching criteria."""
        qs = MemoryEntry.objects.filter(user=user, is_active=True,
                                         confidence__gte=min_confidence)
        if memory_type:
            qs = qs.filter(memory_type=memory_type)
        if key:
            qs = qs.filter(key=key)
        return list(qs.order_by("-confidence", "-updated_at"))

    @staticmethod
    def summarize(user, max_items=10):
        """Produce a condensed text summary of all active memories for the context window."""
        entries = MemoryManager.recall(user)
        lines = []
        for e in entries[:max_items]:
            lines.append(f"- {e.key}: {json.dumps(e.value, ensure_ascii=False)} "
                         f"(confidence: {e.confidence:.1f})")
        return "\n".join(lines)

    @staticmethod
    def forget(user, key=None, memory_type=None):
        """Deactivate memories (soft delete)."""
        qs = MemoryEntry.objects.filter(user=user, is_active=True)
        if key:
            qs = qs.filter(key=key)
        if memory_type:
            qs = qs.filter(memory_type=memory_type)
        qs.update(is_active=False)

    @staticmethod
    def extract_from_conversation(conversation, messages):
        """Background task: extract facts from a conversation turn."""
        facts = []
        text = messages[-1].text if messages else ""
        if not text:
            return

        # Extract platform preference
        platforms = ["shopify", "woocommerce", "bigcommerce", "amazon", "etsy"]
        for p in platforms:
            if p in text.lower():
                facts.append({"key": "preferred_platform", "value": p, "confidence": 0.7})

        # Extract location
        city_match = re.search(r"(?:in|from|at)\s+(\w+)", text)
        if city_match:
            facts.append({"key": "location", "value": city_match.group(1), "confidence": 0.5})

        # Extract language preference
        if re.search(r"(bangla|bangali|bn|বাংলা)", text):
            facts.append({"key": "preferred_language", "value": "bn", "confidence": 0.8})

        # Store extracted facts
        for fact in facts:
            MemoryManager.store_fact(conversation.user, fact["key"],
                                     fact["value"], confidence=fact["confidence"])
```

### 5.4 SessionContext Model

```python
class SessionContext(models.Model):
    WORKFLOW_STATES = [
        ("idle", "Idle — no active workflow"),
        ("browsing", "Customer is browsing products"),
        ("product_selected", "A specific product was selected"),
        ("awaiting_details", "AI is collecting order details"),
        ("awaiting_confirmation", "AI is waiting for order confirmation"),
        ("checkout", "Checkout in progress"),
        ("payment", "Payment flow active"),
        ("completed", "Order/Workflow completed"),
        ("escalated", "Handed off to human"),
    ]

    conversation = models.OneToOneField(Conversation, on_delete=models.CASCADE,
                                         related_name="session")
    state = models.CharField(max_length=30, choices=WORKFLOW_STATES, default="idle")
    current_workflow = models.CharField(max_length=100, blank=True)
    workflow_step = models.IntegerField(default=0)
    collected_data = models.JSONField(default=dict, blank=True)
    pending_confirmation = models.JSONField(null=True, blank=True)
    verified = models.BooleanField(default=False)
    verification_method = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

---

## 6. State Machine & Workflows

### 6.1 State Machine

The conversation state machine ensures coherent multi-turn interactions:

```
                    ┌──────────┐
                    │  IDLE    │
                    └────┬─────┘
                         │
                    ┌────▼─────┐
             ┌──────│ BROWSING │◄──────┐
             │      └────┬─────┘       │
             │           │ select      │
             │      ┌────▼──────────┐  │
             │      │ PRODUCT_SEL   │──┘ (ask for another)
             │      └────┬──────────┘
             │           │ order
             │      ┌────▼──────────┐
             │      │ AWAITING_DTLS │──► update_customer
             │      └────┬──────────┘
             │           │ confirm
             │      ┌────▼──────────┐
             │      │ AWAITING_CONF │
             │      └────┬──────────┘
             │           │ confirmed
             │      ┌────▼──────┐
             │      │ CHECKOUT  │
             │      └────┬──────┘
             │           │ paid
             │      ┌────▼─────────┐
             │      │  COMPLETED   │
             │      └──────┬───────┘
             │             │
             └─────────────┘ (start over)
```

**State transitions are enforced:**
```python
VALID_TRANSITIONS = {
    "idle": ["browsing"],
    "browsing": ["product_selected", "idle"],
    "product_selected": ["awaiting_details", "browsing"],
    "awaiting_details": ["awaiting_confirmation", "product_selected"],
    "awaiting_confirmation": ["checkout", "awaiting_details"],
    "checkout": ["completed", "awaiting_details"],
    "completed": ["browsing", "idle"],
    "escalated": ["completed", "browsing"],
}
```

### 6.2 Workflow Templates

Workflows are JSON-defined and stored in `api/ai/workflows/`:

```json
{
  "workflow": "upgrade_plan",
  "description": "Upgrade customer to a higher plan tier",
  "steps": [
    {
      "id": "verify_owner",
      "tool": "verify_permission",
      "args": {"required_role": "owner"},
      "on_error": "escalate"
    },
    {
      "id": "show_pricing",
      "tool": "get_current_plan",
      "args": {},
      "next": "await_confirmation"
    },
    {
      "id": "await_confirmation",
      "requires_input": true,
      "next_yes": "execute_upgrade",
      "next_no": "cancel"
    },
    {
      "id": "execute_upgrade",
      "tool": "upgrade_plan",
      "args": {"plan": "$context.target_plan"},
      "on_error": "rollback"
    },
    {
      "id": "send_receipt",
      "tool": "send_email",
      "args": {"template": "upgrade_receipt"},
      "async": true
    }
  ]
}
```

**Workflow engine behavior:**
- Loads workflow template by name
- Creates `SessionContext` with `current_workflow`, `workflow_step=0`
- On each message, advances to the next step
- Steps requiring user input pause execution until the next message
- Stores collected data in `SessionContext.collected_data`
- Supports rollback steps on failure
- Supports async steps (side-effects that don't block the reply)

### 6.3 Workflow Examples

**Order upgrade workflow:**
```
Message: "Upgrade me to Pro"
→ verify_owner (check role)
→ get_current_plan (show pricing)
→ ask: "Upgrading to Pro costs $24.99/mo. Confirm?"
→ "Yes" → update_stripe → update_subscription → enable_features
→ Reply: "Done! You're now on Pro."
```

**Sync repair workflow:**
```
Message: "My Shopify stopped syncing"
→ get_connected_stores (find Shopify)
→ check_sync_status (detect expired token)
→ refresh_token (OAuth re-auth)
→ retry_sync
→ verify_sync
→ Reply: "Fixed! 43 products synced successfully."
```

---

## 7. Specialist Agents

### 7.1 BaseAgent

```python
class BaseAgent:
    name: str
    domain_prompt: str          # Agent-specific system prompt fragment
    tools: list[type[BaseTool]]  # Tools this agent can use
    permission_level: str        # Minimum role required
    context_builder: Callable    # Builds context subset for this agent

    def plan(self, intent: Intent, context: ConversationContext) -> list[PlanStep]:
        """Default: use Planner. Override for simple rule-based agents."""
        return Planner.plan(intent, context, self.tools)

    def can_handle(self, intent: Intent) -> bool:
        """Whether this agent should handle the given intent."""
        return intent in self._handled_intents
```

### 7.2 Agent Definitions

| Agent | Handles Intents | Key Tools | Permission |
|---|---|---|---|
| **SalesAgent** | SEARCH_PRODUCT, ASK_PRICE, ASK_STOCK, COMPARE_PRODUCTS, CREATE_ORDER | search_products, get_product_details, send_images, compare_products, check_inventory, create_order | public |
| **SupportAgent** | ASK_FAQ, ASK_DELIVERY, ASK_PAYMENT, RETURN_PRODUCT, HUMAN_SUPPORT, ESCALATION | search_knowledge_base, create_ticket, find_previous_tickets, get_order_status, track_shipment | public |
| **BillingAgent** | UPGRADE_PLAN, BILLING_QUERY, CANCEL_SUBSCRIPTION | get_current_plan, get_invoice_history, upgrade_plan, downgrade_plan, cancel_subscription, get_payment_link | owner |
| **StoreAgent** | STORE_SYNC | get_connected_stores, check_sync_status, reconnect_store, refresh_token, sync_now | manager |
| **AnalyticsAgent** | ANALYTICS_QUERY | get_sales_summary, get_top_products, get_abandoned_carts, compare_periods, get_inventory_alerts | manager |
| **ContentAgent** | CONTENT_REQUEST | generate_product_description, generate_seo_title, generate_meta_description, generate_faq, translate_content | staff |

### 7.3 Routing Logic

```python
class Router:
    RULES = [
        (Intent.UPGRADE_PLAN, BillingAgent, 1.0),
        (Intent.STORE_SYNC, StoreAgent, 1.0),
        (Intent.ANALYTICS_QUERY, AnalyticsAgent, 1.0),
        (Intent.HUMAN_SUPPORT, SupportAgent, 1.0),
        (Intent.CREATE_ORDER, SalesAgent, 0.9),
        (Intent.SEARCH_PRODUCT, SalesAgent, 0.9),
        (Intent.RETURN_PRODUCT, SupportAgent, 0.8),
        (Intent.BILLING_QUERY, BillingAgent, 0.8),
        # Fallback: use intent-agnostic direct tool path
    ]

    @staticmethod
    def route(intent: Intent, context: ConversationContext) -> BaseAgent:
        for rule_intent, agent_cls, confidence in Router.RULES:
            if intent == rule_intent:
                return agent_cls()
        return DirectToolAgent()  # Fallback: no specialist, just tools + response
```

---

## 8. Security & Permissions

### 8.1 Role-Based Access Control

```python
class ToolPermission(models.Model):
    role = models.CharField(max_length=30, choices=[
        ("public", "Anyone"),
        ("customer", "Authenticated Customer"),
        ("staff", "Store Staff"),
        ("manager", "Store Manager"),
        ("owner", "Account Owner"),
        ("support", "Support Agent"),
    ])
    tool_name = models.CharField(max_length=100)
    can_execute = models.BooleanField(default=False)

    class Meta:
        unique_together = [("role", "tool_name")]
```

### 8.2 Permission Hierarchy

```
owner → manager → staff → customer → public

If a role has permission, all roles above it also have permission.
```

### 8.3 Permission Check

```python
ROLE_HIERARCHY = ["public", "customer", "staff", "manager", "owner", "support"]

class PermissionChecker:
    @staticmethod
    def can_execute(user: User, tool_name: str) -> bool:
        user_role = PermissionChecker.get_user_role(user)
        tool = ToolRegistry.get(tool_name)
        if not tool:
            return False

        required_role = tool.permission
        user_idx = ROLE_HIERARCHY.index(user_role)
        required_idx = ROLE_HIERARCHY.index(required_role)

        return user_idx >= required_idx
```

### 8.4 AuditLog

```python
class AuditLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    conversation = models.ForeignKey(Conversation, on_delete=models.SET_NULL, null=True)
    reply_id = models.CharField(max_length=64, db_index=True)
    tool_name = models.CharField(max_length=100)
    arguments = models.JSONField()
    result_summary = models.TextField()
    execution_time_ms = models.IntegerField()
    permission_check = models.CharField(max_length=20)  # "granted" | "denied"
    actor_role = models.CharField(max_length=30)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "timestamp"]),
            models.Index(fields=["tool_name", "timestamp"]),
        ]
```

---

## 9. Tool Registry (Complete)

| # | Tool Name | Agent | Permission | Description |
|---|---|---|---|---|
| 1 | `search_products` | Sales | public | Search by name, SKU, keyword, category, price range |
| 2 | `get_product_details` | Sales | public | Full product info by PID |
| 3 | `send_images` | Sales | public | Send product images (single or carousel) |
| 4 | `compare_products` | Sales | public | Side-by-side comparison of up to 4 products |
| 5 | `recommend_products` | Sales | customer | ML-based recommendations from purchase history |
| 6 | `check_inventory` | Sales | public | Stock level by SKU/location |
| 7 | `create_order` | Sales | customer | Create pending order (collects name/phone/address) |
| 8 | `get_order_status` | Support | customer | Order status by OID |
| 9 | `track_shipment` | Support | customer | Real-time tracking info |
| 10 | `cancel_order` | Support | customer | Cancel a pending order |
| 11 | `find_previous_tickets` | Support | customer | Search customer's ticket history |
| 12 | `search_knowledge_base` | Support | public | FAQ, policies, return/shipping/payment info |
| 13 | `create_ticket` | Support | customer | Escalate to human |
| 14 | `update_customer` | All | public | Save/update customer details |
| 15 | `get_current_plan` | Billing | owner | Current subscription plan |
| 16 | `get_invoice_history` | Billing | owner | Past invoices |
| 17 | `upgrade_plan` | Billing | owner | Change to higher tier |
| 18 | `downgrade_plan` | Billing | owner | Change to lower tier |
| 19 | `cancel_subscription` | Billing | owner | Cancel recurring billing |
| 20 | `change_payment_method` | Billing | owner | Update payment info |
| 21 | `get_payment_link` | Billing | customer | Generate payment URL for pending order |
| 22 | `get_coupons` | Sales | public | Available discounts/promotions |
| 23 | `get_connected_stores` | Store | manager | List connected platforms |
| 24 | `check_sync_status` | Store | manager | Integration health check |
| 25 | `reconnect_store` | Store | manager | OAuth re-auth flow |
| 26 | `refresh_token` | Store | manager | Refresh expired API token |
| 27 | `sync_now` | Store | manager | Trigger manual sync |
| 28 | `get_sales_summary` | Analytics | manager | Revenue, conversion, AOV by period |
| 29 | `get_top_products` | Analytics | manager | Best-selling products |
| 30 | `get_abandoned_carts` | Analytics | manager | Abandoned cart data |
| 31 | `compare_periods` | Analytics | manager | WoW/MoM/YoY comparison |
| 32 | `get_inventory_alerts` | Analytics | manager | Low-stock notifications |
| 33 | `generate_product_description` | Content | staff | AI-generated product copy |
| 34 | `generate_seo_title` | Content | staff | SEO-optimized title |
| 35 | `generate_meta_description` | Content | staff | Meta description for listings |
| 36 | `generate_faq` | Content | staff | Auto-generate product FAQ |
| 37 | `translate_content` | Content | staff | Multi-language translation |
| 38 | `book_appointment` | Support | customer | Calendar booking |
| 39 | `send_email` | Billing | owner | Send invoice/receipt/docs |
| 40 | `notify_team` | Store | manager | Slack/Teams/Discord alert |
| 41 | `think` | All | internal | Private reasoning step |

---

## 10. Data Models (New & Changed)

### New Models

| Model | App | Purpose |
|---|---|---|
| `MemoryEntry` | context | Long-term user memory |
| `SessionContext` | context | Current session state and workflow |
| `ToolPermission` | api | Role-based tool access |
| `AuditLog` | api | Full tool execution audit trail |
| `ProactiveRule` | context | User-defined proactive monitoring rules |

### Changed Models

| Model | Change |
|---|---|
| `Conversation` | Replace `detected_intent` CharField with FK to Intent model |
| `Conversation` | Remove `current_product` (migrate to SessionContext) |
| `Conversation` | Remove `current_package` (migrate to SessionContext) |
| `Conversation` | Remove `missing_order_fields` (migrate to SessionContext) |
| `ToolCallLog` | Add `input_tokens`, `output_tokens`, `plan_step_index`, `permission_check_result`, `retry_count` |
| `UserBalance` | Add `plan_change_pending` flag for upgrade workflows |

---

## 11. File Structure

```
api/
├── ai/
│   ├── __init__.py
│   ├── orchestrator.py      ← NEW: replaces pipeline.py as entry point
│   ├── pipeline.py           ← KEPT as fallback, simplified to call orchestrator
│   ├── context.py            ← REWRITTEN: ConversationManager + ConversationContext
│   ├── intent.py             ← NEW: IntentDetector
│   ├── planner.py            ← NEW: Planner (tool sequence generation)
│   ├── executor.py           ← NEW: Executor (deterministic tool runner)
│   ├── response.py           ← NEW: ResponseGenerator (LLM writes final reply)
│   ├── memory.py             ← NEW: MemoryManager (long-term + session memory)
│   ├── tools.py              ← REWRITTEN: ToolRegistry + BaseTool + all existing tools
│   ├── policy.py             ← NEW: PermissionChecker
│   ├── router.py             ← NEW: Agent router
│   ├── providers.py          ← UNCHANGED: OpenRouter client
│   ├── sender.py             ← REWRITTEN: ChannelFormatter + platform senders
│   ├── media.py              ← UNCHANGED: image/video handling
│   ├── workflows/            ← NEW: JSON workflow templates
│   │   ├── upgrade_plan.json
│   │   ├── order_create.json
│   │   ├── sync_repair.json
│   │   └── refund.json
│   └── agents/               ← NEW: specialist agents
│       ├── __init__.py
│       ├── base.py           ← BaseAgent class
│       ├── sales.py
│       ├── support.py
│       ├── billing.py
│       ├── store.py
│       ├── analytics.py
│       └── content.py
│
├── webhooks.py               ← UPDATED: call orchestrator instead of pipeline.run
│
├── tools/                    ← NEW: per-app tool modules
│   ├── __init__.py
│   ├── products.py           ← search_products, get_product_details, check_inventory
│   ├── orders.py             ← create_order, get_order_status, cancel_order
│   ├── billing.py            ← plan/invoice/subscription tools
│   ├── store.py              ← sync management tools
│   ├── analytics.py          ← reporting tools
│   ├── content.py            ← generation tools
│   ├── crm.py                ← customer/profile tools
│   └── communication.py      ← email, notification, calendar tools

back/
├── models.py                 ← UPDATED: Conversation state migration, new AuditLog model

context/
├── models.py                 ← UPDATED: MemoryEntry, SessionContext, ProactiveRule
├── signals.py                ← UPDATED: memory extraction on message post_save
```

---

## 12. Implementation Phases

### Phase 1: Foundation (Week 1-2)

**Goal:** Core orchestration pipeline operational. No specialist agents yet.

| Task | Files | Deps |
|---|---|---|
| Define `ConversationContext`, `PlanStep`, `ToolResult` dataclasses | `api/ai/context.py` | None |
| Implement `ConversationManager` (loads all state) | `api/ai/context.py` | None |
| Implement `ToolRegistry` | `api/ai/tools.py` | None |
| Implement `IntentDetector` (rule-based only) | `api/ai/intent.py` | None |
| Implement `Executor` | `api/ai/executor.py` | ToolRegistry |
| Implement `ResponseGenerator` | `api/ai/response.py` | None |
| Implement `Planner` (direct + template modes) | `api/ai/planner.py` | IntentDetector |
| Implement `Orchestrator` | `api/ai/orchestrator.py` | All above |
| Update `webhooks.py` to call orchestrator | `api/webhooks.py` | Orchestrator |
| Deploy and test with existing 8 tools | — | — |

**Risk:** If orchestrator breaks, fall back to old `pipeline.py` via feature flag.

### Phase 2: Memory (Week 2-3)

| Task | Files | Deps |
|---|---|---|
| Create `MemoryEntry` model + migration | `context/models.py` | Phase 1 |
| Create `SessionContext` model + migration | `context/models.py` | Phase 1 |
| Implement `MemoryManager` | `api/ai/memory.py` | MemoryEntry model |
| Implement memory extraction (post-save signal) | `context/signals.py` | MemoryManager |
| Update `ConversationManager` to load memory | `api/ai/context.py` | MemoryManager |
| Update context window to include memory summary | `api/ai/response.py` | MemoryManager |

### Phase 3: Security (Week 3)

| Task | Files | Deps |
|---|---|---|
| Create `ToolPermission` model + migration | `api/models.py` | Phase 1 |
| Create `AuditLog` model + migration | `back/models.py` | Phase 1 |
| Implement `PermissionChecker` | `api/ai/policy.py` | ToolPermission |
| Add permission check to `Executor` | `api/ai/executor.py` | PermissionChecker |
| Add `AuditLog` logging to `Executor` | `api/ai/executor.py` | AuditLog |
| Migrate `ToolCallLog` to new schema | `back/models.py` | Phase 1 |

### Phase 4: State Machine + Workflows (Week 3-4)

| Task | Files | Deps |
|---|---|---|
| Add `ConversationState` to Conversation + migration | `back/models.py` | Phase 1 |
| Define state transition rules | `api/ai/state.py` (new) | — |
| Create workflow template format + loader | `api/ai/workflows/` | — |
| Implement workflow engine | `api/ai/planner.py` | SessionContext |
| Wire state machine into `Orchestrator` | `api/ai/orchestrator.py` | State + Workflows |

### Phase 5: Tool Refactoring (Week 4-5)

| Task | Files | Deps |
|---|---|---|
| Extract `BaseTool` class | `api/ai/tools.py` | Phase 1 |
| Refactor `search_products` → `api/tools/products.py` | — | BaseTool |
| Refactor `create_order` → `api/tools/orders.py` | — | BaseTool |
| Refactor `send_images` → `api/tools/products.py` | — | BaseTool |
| Refactor remaining 5 tools | — | BaseTool |
| Write 5 new tools (check_inventory, compare_products, etc.) | — | BaseTool |
| Register all tools via `ToolRegistry` | — | BaseTool |

### Phase 6: Specialist Agents (Week 5-6)

| Task | Files | Deps |
|---|---|---|
| Implement `BaseAgent` class | `api/ai/agents/base.py` | Phase 1 |
| Implement `Router` | `api/ai/router.py` | Phase 1 |
| Implement `SalesAgent` | `api/ai/agents/sales.py` | Phase 5 |
| Implement `SupportAgent` | `api/ai/agents/support.py` | Phase 5 |
| Implement `BillingAgent` | `api/ai/agents/billing.py` | Phase 5 |
| Implement `StoreAgent` | `api/ai/agents/store.py` | Phase 5 |
| Implement `AnalyticsAgent` | `api/ai/agents/analytics.py` | Phase 5 |
| Implement `ContentAgent` | `api/ai/agents/content.py` | Phase 5 |
| Wire agent routing into `Orchestrator` | `api/ai/orchestrator.py` | All agents |

### Phase 7: Proactive Intelligence (Week 6-7)

| Task | Files | Deps |
|---|---|---|
| Create `ProactiveRule` model + migration | `context/models.py` | Phase 1 |
| Implement proactive monitor service | `api/ai/proactive.py` (new) | ToolRegistry |
| Create management command for periodic checks | — | Proactive monitor |
| Implement alert dispatch via `ChannelFormatter` | `api/ai/sender.py` | Phase 1 |
| Write proactive agent prompt | `api/ai/agents/proactive.py` | — |

### Phase 8: Async + Observability (Week 7-8)

| Task | Files | Deps |
|---|---|---|
| Implement event bus | `api/ai/events.py` (new) | — |
| Move side-effects to async events | `api/ai/orchestrator.py` | Event bus |
| Enhance `ToolCallLog` schema + migration | `back/models.py` | Phase 1 |
| Add `Orchestrator` trace logging | `api/ai/orchestrator.py` | — |
| Create Django admin views for new models | `api/admin.py` | Phase 2, 3 |
| Per-agent cost attribution | `billing/deductions.py` | Phase 6 |

---

## 13. Migration Strategy

### 13.1 Feature Flag

```python
# settings.py
AI_ORCHESTRATOR_ENABLED = os.getenv("AI_ORCHESTRATOR_ENABLED", "false") == "true"

# api/webhooks.py
if settings.AI_ORCHESTRATOR_ENABLED:
    orchestrator.process(message, conversation)
else:
    pipeline.run(conversation, message)
```

This allows deploying Phase 1 with zero risk. Toggle the flag on/off.

### 13.2 Data Migration

1. Migrate `Conversation.current_product` JSON → `SessionContext.collected_data`
2. Migrate `Conversation.detected_intent` → `Intent` model reference
3. Backfill `MemoryEntry` from existing conversation history (simple keyword extraction)
4. Backfill `ToolPermission` defaults for all roles (all public tools granted)

### 13.3 Rollback Plan

- Phase 1: Flip flag to `false` → old pipeline takes over. Zero data loss.
- Phase 2+: New models exist alongside old. Old pipeline ignores them. No breaking changes.
- The old `pipeline.py` is kept as a fallback for the entire migration period.

---

## Appendix A: Cost Comparison

| Scenario | Current (avg 3.2 LLM calls) | Proposed (2 LLM calls) | Savings |
|---|---|---|---|
| Simple product search | 2 calls (~400 tokens) | 1 call (~150 tokens) | ~62% |
| Order creation (5 steps) | 5 calls (~3500 tokens) | 2 calls (~600 tokens) | ~83% |
| FAQ answer | 2 calls (~300 tokens) | 1 call (~100 tokens) | ~67% |
| Sync repair (workflow) | 4 calls (~2000 tokens) | 2 calls (~400 tokens) | ~80% |

**Estimated monthly savings for a store with 5000 conversations:** 60-80% reduction in token usage.

## Appendix B: Key Design Principles

1. **LLM decides, Python executes** — Never let the LLM run tools directly. The Planner decides the sequence; the Executor runs them deterministically.
2. **Tool results are facts, not suggestions** — The ResponseGenerator must use only verified tool results. No hallucination of prices, stock, or product names.
3. **State is explicit** — Never infer state from conversation history. Use explicit `ConversationState` + `SessionContext`.
4. **Memory is structured** — Store facts as key-value pairs with confidence scores, not free text. The MemoryManager provides queryable, summarizable memory.
5. **Tools are discoverable** — Every app registers its tools via `ToolRegistry`. No magic imports or central tool list.
6. **Agents are composable** — Each specialist agent is self-contained (prompt + tools + permissions). Add new agents without modifying existing code.
7. **Async by default** — Side-effects (analytics, logging, memory extraction) are non-blocking. The customer gets the reply first.
