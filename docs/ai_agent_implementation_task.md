# AI Agent Implementation Task List — TheMatrixAi

Phase planning: 2026-07-30 | Total items: 48

---
 
## 🔴 P0 — Foundation (must have for any agent to work)

- [x] **P0-1**: `ConversationManager` — pure Python class that loads conversation state, customer profile, products, orders, business settings, AI personality, and memory into a `ConversationContext` dataclass. No LLM involved.
- [x] **P0-2**: `Orchestrator` — entry point that receives `ConversationContext`, runs intent detection, calls planner, dispatches to executor, then response generator. Replaces current `pipeline.py`.
- [x] **P0-3**: `IntentDetector` — lightweight classifier (rule-based + small model) that maps incoming message to intent (SEARCH_PRODUCT, CREATE_ORDER, CHECK_ORDER, ASK_PRICE, GREETING, FAQ, HUMAN_SUPPORT, etc.). High-confidence intents bypass full planner.
- [x] **P0-4**: `Planner` — takes intent + context, produces a sequence of tool calls. Example: intent=CREATE_ORDER → [search_products, check_inventory, calculate_price, create_cart]. Returned as a list of `PlanStep` objects.
- [x] **P0-5**: `Executor` — runs tools from the plan deterministically (pure Python, no LLM). Each tool returns a `ToolResult` (structured JSON). Results are collected for the response generator.
- [x] **P0-6**: `ResponseGenerator` — after all tools finish, LLM generates the final natural-language reply from structured tool results. Prevents hallucination of product names/prices/stock.
- [x] **P0-7**: `ToolResult` / `PlanStep` / `ConversationContext` dataclasses — shared data types across all components.
- [x] **P0-8**: `ToolRegistry` — centralized registry where each app registers its tools. Tools have: name, description, parameters (JSON schema), permission level, timeout, retry policy, cost estimate.

## 🔴 P0 — Memory System

- [x] **P0-9**: `SessionContext` model — tracks current workflow step, verification status, pending actions, collected data. One per conversation.
- [x] **P0-10**: `MemoryEntry` model — long-term user memory (preferences, facts, behavior patterns). Fields: user, conversation (nullable), memory_type (preference/fact/behavior/context), key, value (JSON), confidence, expires_at, is_active.
- [x] **P0-11**: `MemoryManager` (memory.py) — CRUD for MemoryEntry. Functions: `store_fact()`, `recall()`, `forget()`, `summarize_memory()` (produces condensed summary for context window).
- [x] **P0-12**: Background memory extraction — after each conversation turn, extract facts from exchange and persist as MemoryEntry. Runs async. *(wired — daemon thread in orchestrator.py Step 6b with `close_old_connections()` wrapper)*

## 🟡 P1 — Security & Tool Layer

- [x] **P1-1**: `PermissionChecker` class in `policy.py` — role-based access with hierarchy public→customer→staff→manager→owner→support.
- [x] **P1-2**: Permission check in `Executor` — every tool call verifies the user's role has permission before execution. *(wired into `_execute_with_retry` — denied → `ToolResult.permission_denied`, no retries)*
- [x] **P1-3**: `AuditLog` model — full audit trail for every tool execution. *(back/models.py + migration 0023; written by Executor via `_write_audit`, incl. permission denials)*
- [x] **P1-4**: Refactor `send_images` tool — wrapped as `SendImagesTool(BaseTool)` in tools.py with permission, timeout, retry.
- [x] **P1-5**: Refactor `search_products` tool — wrapped as `SearchProductsTool(BaseTool)` with permission.
- [x] **P1-6**: Refactor `create_order` tool — wrapped as `CreateOrderTool(BaseTool)` with permission.
- [x] **P1-7**: Refactor `get_order_status` / `update_customer` / `create_ticket` / `search_knowledge_base` — all wrapped as BaseTool subclasses.
- [ ] **P1-8**: Create `ChannelFormatter` — converts final response + images + cards into platform-specific format. *(still in sender.py, not extracted)*

## 🟡 P1 — State Machine & Workflows

- [x] **P1-9**: Workflow state — `SessionContext` model wired into the Orchestrator via `api/ai/state.py` (`WorkflowEngine`). *Decision: reuse existing `SessionContext` instead of adding a `ConversationState` field to Conversation.*
- [x] **P1-10**: State transition rules — `STATE_TRANSITIONS` map in `state.py`; `transition()` validates + persists.
- [x] **P1-11**: Workflow templates — JSON-style step lists in `state.py` (`ORDER_FIELDS` collection flow + confirm/cancel regexes, en/bn).
- [x] **P1-12**: Workflow engine — `WorkflowEngine.handle_message()` consumes messages while a workflow is active; `start_order_flow()` launches CREATE_ORDER collection; `mark_escalated()` on ticket success. Orchestrator Steps 2b/2c.
- [x] **P1-13**: Handle disambiguation — `resolve_product_reference()` in state.py resolves product refs from `SessionContext.collected_data` (en/bn).

## 🟡 P1 — Specialist Agents

- [x] **P1-14** through **P1-21**: Specialist agents ✅ — *architecture decision: implemented as specialist prompt fragments in `response.py` (`_SPECIALIST_FRAGMENTS` SALES/SUPPORT/BILLING + `_INTENT_TO_SPECIALIST` map) appended in `_build_prompt()`. Single LLM call, role-specialized — no 7 separate agent classes, no routing cost.*

## 🟢 P2 — Expanded Tool Layer (Nice to Have)

- [x] **P2-1**: `check_inventory` tool — stock level by SKU with alerts at configurable thresholds.
- [ ] **P2-2**: `compare_products` tool — side-by-side comparison of up to 4 products (price, features, stock).
- [ ] **P2-3**: `recommend_products` tool — based on purchase history + browsing behavior + collaborative filtering.
- [x] **P2-4**: `find_previous_tickets` tool — search customer ticket history by keyword/status/date range.
- [ ] **P2-5**: `get_coupons` tool — available discounts/promotions for the customer.
- [x] **P2-6**: `get_payment_link` tool — generate payment link for pending order.
- [x] **P2-7**: `track_shipment` tool — real-time tracking info by order ID.
- [ ] **P2-8**: `book_appointment` tool — calendar booking for demo/onboarding/support.
- [ ] **P2-9**: `send_email` tool — send follow-up, invoice, documentation via email.
- [ ] **P2-10**: `notify_team` tool — Slack/Teams/Discord notification for events.
- [x] **P2-11**: `get_sales_summary` tool — revenue, conversion, AOV, top products by period.
- [ ] **P2-12**: `get_analytics` tool — trend analysis, compare periods, export data.
- [ ] **P2-13**: `generate_content` tool — product descriptions, SEO titles, meta descriptions, ad copy.
- [ ] **P2-14**: `translate_content` tool — multi-language product listing translation.
- [ ] **P2-15**: `check_sync_status` tool — integration health check for each connected store.
- [ ] **P2-16**: `reconnect_platform` tool — OAuth re-auth flow for expired tokens.

## 🟢 P2 — Proactive Intelligence

- [x] **P2-17**: `ProactiveRule` model — user-configurable rules: event_type (sync_failure, low_stock, token_expiry, subscription_expiring), is_enabled, notify_channel. *(context/models.py)*
- [x] **P2-18**: Proactive monitor service — `api/ai/proactive.py` (`evaluate_rule` / `check_all`) + management command `python manage.py run_proactive_monitor`. Runs outside the message flow.
- [x] **P2-19**: Alert dispatch — `dispatch_alert()` pushes proactive message via `sender.py send_reply` to the user's conversation channel and stores a bot `Message`.
- [x] **P2-20**: Proactive agent prompt — `PROACTIVE_SYSTEM_PROMPT` in `api/ai/proactive.py` (short, action-oriented) with per-event template fallbacks.

## 🟢 P2 — Event-Driven & Async

- [ ] **P2-21**: Event bus abstraction — lightweight in-process event bus (can be swapped for Redis/Celery later). Events: `message_received`, `tool_executed`, `memory_stored`, `reply_sent`.
- [ ] **P2-22**: Async side-effects — analytics logging, CRM updates, memory extraction run as side-effects on the event bus (non-blocking for the customer).
- [ ] **P2-23**: `UsageSummary` / `CreditTransaction` / `AuditLog` writes move to async events — no latency impact on reply.

## 🟢 P2 — Observability & Debugging

- [ ] **P2-24**: Orchestrator trace log — every decision (intent, plan, tool results) logged as structured JSON for debugging.
- [ ] **P2-25**: `ToolCallLog` enhancement — add: input_tokens, output_tokens, plan_step_index, permission_check_result, retry_count.
- [ ] **P2-26**: Agent dashboard — Django admin views for MemoryEntry, ProactiveRule, Orchestrator traces.
- [ ] **P2-27**: Per-agent cost tracking — attribute UsageLog entries to specific agents for granular billing.

---

## Implementation Log (2026-07-31)

### Critical fixes (audit findings)

| Fix | File(s) | Detail |
|---|---|---|
| Billing disconnect | `api/ai/response.py`, `api/ai/planner.py`, `api/ai/orchestrator.py` | Orchestrator imported `UsageLog` but never created rows → `deduct_for_reply()` found 0 rows → **no credits were ever deducted**. Now `generate()` and `planner.plan()` write `UsageLog` rows (call_type `response_generation`/`planning`) keyed by `reply_id` (uuid4 hex). Verified: balance 50.0000 → 49.9982 after one turn. |
| Memory never loaded | `api/ai/context.py` | `_load_memory()` existed but was never called → MemorySummary was always empty. Now called in `ConversationManager.build()`. Verified facts/preferences appear in the system prompt. |
| Permissions bypassed | `api/ai/executor.py` | `PermissionChecker.can_execute()` never called. Now checked in `_execute_with_retry()` before each tool; denied → `ToolResult.permission_denied` (no retries) → `response.py` renders a polite refusal. |
| No AuditLog | `back/models.py` + migration `0023_auditlog` | Model created (user, conversation, tool_name, arguments JSON, result_state, result_summary, execution_time_ms, actor_role, ip_address, timestamp); Executor writes via `_write_audit()`; `AuditLogAdmin` registered (readonly). |

### Additional fixes

- **UNKNOWN intent silence**: `_greeting_or_fallback()` returned `""` for unknown intents → the bot stayed silent. Now returns a canned bn/en "I didn't understand" reply.
- **SEARCH_PRODUCT regex**: "do you sell snacks?" matched nothing (no `sell` token). Added `sell|sells|selling` patterns.
- **Conversation missing `customer_address`**: field added via migration `0024_conversation_customer_address` + loaded in `ConversationManager._load_customer()`. Unblocks one-turn orders (name/phone/address all known → order created immediately, no questions) and the `update_customer` tool (previously raised FieldError).

### Real-world test fixes (manual Messenger test replay)

The user's manual test failed: no memory, no catalog listing, wrong stock/discount claims, order flow broke at "ok". Root causes and fixes:

| # | Failure | Root cause | Fix |
|---|---|---|---|
| A | "ki product ache?" → single product | `_build_prompt` had no history + only focus count; focus shortcut returned stale product | `_is_generic_catalog_query()` + `_GENERIC_PHRASES`; focus shortcut guarded by `_query_matches_product` (name-substring only); no-match search returns focused candidates |
| B | "discunt nai jolpai?" → "no discount" | system prompt rendered `price` only, not `discounted_price` | Available Products sample renders `(discounted: …)`; prompt rule: discount exists when `discounted_price` present & lower |
| C | "300 takai diben?" → wrong/no product | no history in prompt; focus hijacked | history (last 12) + full context in `_build_prompt`; `_resolve_from_history()` |
| D | "ok" stored as customer name | no field validation | `_validate_field_value()` (name ≥3 chars, phone ≥10 digits, address ≥5 chars) in `state.py` |
| E | memory junk (`location=stock`, en/bn conflict) | bot-reply text fed to extraction; unbounded patterns | memory extraction uses customer text only; `_KNOWN_PLACES` whitelist; `_store_if_not_downgraded` keeps higher-confidence fact |
| F | "আমের আচার এক কেজি অর্ডার দিব" → "বুঝতে পারিনি" | CREATE_ORDER regex lacked "অর্ডার দিব"; no focus → flow dropped | regex += `অর্ডার দিব|অর্ডার করব|অর্ডার করি|কিনবো|নিবো`; `start_order_flow` quick-searches catalog when focus empty |
| G | awaiting_product_selection stuck loop | no exit when customer doesn't name a listed product | after 2 unresolved replies → fall back to first focus product |
| H | Bengali words never found latin-named products ("জলপাইয়ের আচার") | no transliteration; step-2 `\w` strip destroyed Bengali vowel signs | `_latinize_bn()` (inherent vowel, nukta pairs) + latin + prefix-truncated search variations; `\u0980-\u09FF` kept in stripping |
| I | ambiguous "আচার কিনতে চাই" silently picked Amer | latin token matched all 3 achar products | resolve only when a token uniquely matches one product; otherwise selection prompt |
| J | "জলপাইয়ের আচার কত?" → UNKNOWN | ASK_PRICE lacked bare "কত" | `কত(?!\s*স্টক)` added to ASK_PRICE |
| K | replies in wrong language | language = settings default, ignored conversation hint | `_detect_lang()` (conversation hint first) + FINAL INSTRUCTION at prompt end |
| L | greeting repeated every turn | greeting template always injected | prompt rule: no re-greeting when history non-empty |

### Real-world test fixes round 2 (order integrity + repeat orders)

The user's second Messenger replay: "4 pcs order korbo" ordered **Jolpaia instead of Amer**, "ji" confirmed an order with **no create_order tool call**, "order id koto eitar?" quoted a **stale id**, "na eita to ager order" created an **unrequested order**, and a final turn created an order with **stale Karim Hossain data**. Two solutions per user decision: (1) order intent is 100% workflow-driven — never LLM-improvised; (2) order data & confirmations are integrity-protected. Plus repeat-order UX: "আবার অর্ডার করব" reuses the last order's product + quantity + customer and creates directly.

| # | Failure | Root cause | Fix |
|---|---|---|---|
| M | wrong product on "4 pcs order korbo" | `_TEMPLATE_PLANS["CREATE_ORDER"]` let the LLM craft a create_order plan with guessed args; workflow hardcoded quantity=1 | template deleted; `planner.py` returns `[]` for CREATE_ORDER — orders exist only via `state.py` workflow |
| N | fake "order confirmed" on "ji" | LLM free-formed a confirmation after update_customer | `response.py` prompt rules: never claim an order created/confirmed without a create_order result in THIS turn; never quote an order id outside THIS turn's tool results |
| O | stale order id quoted | `get_order_status` called with no `ord_` → LLM answered from memory/history | planner arg-builder falls back to the conversation's latest Sale oid |
| P | "na eita to ager order" created unrequested order | bare "order" word → CREATE_ORDER | CREATE_ORDER regex: `order(?!\s+(id|number|status|track|kothay|koi|ager|purono|previous|old|sob|সব))` + explicit buying-intent words; CHECK_ORDER extended to `(ager|purono|previous|old|amr|amar|my|last)\s+order`, `order kothay/ki/koto` (0.95) |
| Q | quantity never parsed ("৪ পিস" ignored) | workflow used qty=1 | `_parse_quantity` (bn digits, এক-পাঁচ, pcs/pc/pieces/kg/কেজি/পিস/টা/টি/খানা) — `(?!\w)` instead of `\b` (CPython re `\b` fails for Bengali at string end); selection state preserves quantity |
| R | details answer rejected ("Rafiul alam, Mirpur 10 kajipara, 01793504010") | selection state only accepted product names | ≥10-digit replies parsed via `_parse_details` → fills name+phone+address in one turn (also in `awaiting_details`) |
| S | orders created with stale customer data | conversation carried old Karim Hossain fields; LLM reused history | workflow `collected_data` is the single source; `_execute_create_order` persists collected fields via `tool_update_customer` after success |
| T | "আবার অর্ডার করব" → qty 1 | repeat signal checked after history resolution (which already resolved a product) | repeat checked BEFORE history: `_previous_order_for` (last Sale → product + quantity + customer) reused; explicit "আবার ২ পিস" overrides quantity |
| U | ambiguous "4 pcs achar order korbo" ordered Amer | name-token loop returned the first focus product on any token match ("achar" matches all 3) | plain-token loop now resolves only when a word uniquely matches one product (same rule as latin loop) |
| V | "আচার দেখতে চাই" → CREATE_ORDER | `চাই` regex lacked lookbehind | `(?<!দেখতে )(?<!জানতে )(?<!বলতে )(?<!শুনতে )(?<!পড়তে )(?<!বুঝতে )চাই`; SEARCH_PRODUCT += `দেখতে চাই` |
| W | unknown-product message dropped | `start_order_flow` could return None | resolution step 6 canned "which product?" reply; orchestrator Step 2c returns it instead of falling through to LLM |

### Real-world flow-test round 3 (new customer, full conversation walkthrough)

Full walkthrough on a fresh conversation (greetings → catalog → price/discount/delivery → order with quantity → repeat → images) surfaced and fixed:

| # | Issue | Root cause | Fix |
|---|---|---|---|
| X | "hlw" → UNKNOWN | GREETING regex lacked latin shorthand | added `hlw\|helo\|hllo\|hy\|gm\|gn` |
| Y | "kmn achen?" → SEARCH_PRODUCT | SMALL_TALK regex lacked transliterated forms | added `kmn achen\|kemon achen\|valo achi\|ki khobor\|আপনি কেমন` |
| Z | "jolpai achar koto taka?" → UNKNOWN | ASK_PRICE lacked latin `koto` | added `koto taka\|koto takay\|koto\b\|kitne` |
| AA | "amer achar dekhan" → UNKNOWN | SEARCH_PRODUCT lacked latin "dekhan" | added `dekhan\|dekhao\|dekhaben\|dekha` |
| AB | "delivery koto din lagbe?" / "payment kivabe?" / "office kothay?" → "no info" / UNKNOWN | empty knowledge base | seeded `BehaviorRules.knowledge_base` + `sample_questions_answers` (delivery time 1-2/2-4 days, COD/bKash/Nagad 01793504010, Basundhara R/A + support hours, return policy, shelf life); RAG re-embedded (13 chunks); ASK_FAQ += `কোথায়\|kothay\|ঠিকানা` |
| AC | "2 pcs" stored as customer NAME (order shipped with wrong name) | name validation accepted digit values; quantity never parsed into the flow | quantity intercept in `awaiting_details` (parses + saves `collected["quantity"]`, re-asks the field) — plus missing `session.save()`; name validation rejects ≥4-digit values |
| AD | order total ignored quantity (qty 2 → qty 1 item) | (same root cause — intercept never persisted) | fixed by the `session.save()` above; verified qty 2 → 868.00 |
| AE | repeat order lost quantity ("আবার অর্ডার করব" → qty 1) | repeat checked after history resolution | repeat signal checked BEFORE history; `_previous_order_for` reuses product + quantity + customer; verified ×4 and explicit "আবার ২ পিস" ×2 |
| AF | flow prompts in English for a Bengali store | `_lang()` defaulted to EN; `language_detected` never persisted | `_lang()` falls back to AgentIdentity.language (bn); orchestrator Step 1b persists hint — Bengali script → bn; latin only sets en when English function words present ("hlw"/"kmn achen" leave it empty → store default) |
| AG | "na cancel" mid-selection ignored | selection state had no cancel check | CANCEL_RE checked first in `awaiting_product_selection` |
| AH | photo requests ("photo pathan", "ছবি দেখান") never sent images | SEARCH_PRODUCT direct-map → search_products only; LLM never saw send_images | new `SEND_IMAGES` intent (photos?/pictures?/images?/ছবি/ফটো, 0.85) → `_DIRECT_MAP["SEND_IMAGES"]="send_images"`; args = unique named product via `resolve_product_reference` (fallback: focus pids, then quick-search with the same unique-name rule). Verified: "amer achar er photo pathan" → exactly `amer_achar.jpg` attached; "sob product" → polite refusal |
| AI | selection prompt fired even when history resolved the product | earlier edit nested the focus-block inside the history-block | restored separate resolution steps; verified "ekta order korbo" after "amer achar dekhan" → instant order |
| AJ | send_images failed with empty pids | planner imported `_quick_catalog_search` from the wrong module (`tools` vs `state`) | import from `state.WorkflowEngine` |
| AK | Dashboard **Bot Preview** ("/db/chats/bot-preview") behaved like a DIFFERENT bot: raw `build_system_prompt`+`call_llm`+`TOOL_DEFINITIONS` loop — the LLM freely called `create_ticket` for "tetuler achar" (real user report: order-started → "tetuler achar" → "connecting you with a human agent" + ticket), wrote its own LLM-style selection question, and never sent catalog cards | `bot_preview` predated the orchestrator | `bot_preview` rewritten to run `Orchestrator(dry_run=True)`; `Orchestrator` gained `dry_run` (skips platform send, credit deduction, background memory; keeps SessionContext/Message/UsageLog writes + last_response/last_reply_id; integration guards relaxed). Preview now behaves exactly like production: intent → workflow → planner → tools → response |
| AL | product-name answer swallowed as customer NAME ("tetuler achar" while collecting details → stored as `customer_name`, then phone-validation loop) | `awaiting_details` validated any text as the field value | before validation: text that uniquely resolves to a focus product (or quick-catalog) swaps the order's `pid`/`product_name` (capturing any quantity too) and re-asks the field; exact product-name repetition re-asks instead of looping. Verified on the user's 5-turn transcript: selection → "tetuler achar" → "ঠিক আছে, tetuler achar! ... নাম জানাবেন?" (no ticket) |
| AM | no catalog cards: "ki product ache?" → text list only; "sob product er photo pathan" → empty-pids error | CATALOG intent didn't exist; `send_images` had no all-catalog fallback | new `CATALOG` intent (ki product ache/সব প্রোডাক্ট/products dekhan, 0.82, before SEARCH_PRODUCT) → template `[search_products(""), send_images]` with `send_images` expanded to ALL active pids in `_resolve_template`; `tool_send_images` falls back to all active products (≤8) when nothing requested; `_build_args("send_images")` normalizes `ProductSummary` → dicts so `resolve_product_reference` works (was silently falling back to ALL focus pids — "amer achar er photo pathan" sent 3 cards); `_search_result_instruction` no longer prints literal `send_images(pids=[...])` (LLM was echoing it) |
| AN | "hobe" (হবে) at the confirm step looped "confirm the order?" instead of creating it | CONFIRM_RE lacked Bengali confirmations | added `hobe\|হবে\|করছি\|করুন\|নিব\|নিন\|দিবেন\|দেবেন` — verified full transcript creates `ord_ffb2gg` tetuler ×5 = 995 + 70 delivery = 1065.00 |
| AO | selection-state reply didn't confirm the chosen product ("ঠিক আছে! ... নাম জানাবেন?" — no product name) | canned text omitted the selected name | selection branch now says "ঠিক আছে, {product}! {field} জানাবেন?" |
| AP | "ok bhai, ami amer achar 2 pcs order korbo" → canned greeting "I'm here to help!" — order intent completely missed, then name/phone/address turns all fell to UNKNOWN | `_SMALL_TALK_RE.search()` early-return matched the leading "ok"/"okay"/"kemon" and short-circuited BEFORE any intent scoring | early return now requires a `fullmatch` (pure chit-chat only); SMALL_TALK added to `_INTENT_PATTERNS` (0.75, ^-anchored) so "kemon achen bhai?" still scores; CREATE_ORDER overrides GREETING/SMALL_TALK when both match |
| AQ | replies flip-flopped EN/BN for a transliterated-Bengali customer ("amer achar koto?" BN → "amer achar er photo pathan please" EN → BN again) | Step 1b locked `language_detected="en"` from a SINGLE English word ("hello" turn 1, "please" turn 5) — permanent EN for the whole conversation | Step 1b requires ≥2 distinct English function words to set "en"; single polite words ("hello"/"please"/"ok") keep the store default (bn). Verified: 16-turn all-Bengali conversation with zero language drift |
| AR | "bhai ar ekta order dibo, ager moto 2 pcs" → asked "which product?" instead of repeating the last order | REPEAT_ORDER_RE had Bengali "আগের মতো" but not latin "ager moto" | added `ager moto\|ager motoi\|ager order\|like last time\|like before\|purono\|previous\|last order` — verified instant repeat order (same product+quantity+customer, ord_agdgc4 = 868.00) |
| AS | "ar delivery kobe ashbe order ta?" answered "Your order should arrive…" even with NO order in the conversation | response prompt had no rule tying "your order" to actual tool results | added prompt rule: never say "your order"/imply an existing order unless a create_order/get_order_status result is in this turn |
| AT | "150 e den" / "150 kore den" (bargaining) → "আমি বুঝতে পারিনি" | no negotiation intent — price offers fell to UNKNOWN canned reply | new `NEGOTIATE` intent (0.88, before CREATE_ORDER): price+verb patterns ("150 e den", "দাম কম", "ekom komaw", "discount den") → template plan `search_products(__focus_name__)` + response rule: politely hold the discounted price, never quote lower. Verified: "150 e den" → "দুঃখিত ভাই, ১৫০ টাকায় তেঁতুলের আচার দেওয়া সম্ভব নয়… ১৯৯ টাকায় ডিসকাউন্টে" |
| AU | "bal", "chudir vai..." (frustration/abuse) → repeated "আমি বুঝতে পারিনি" | no frustration intent | new `FRUSTRATION` intent (0.85) → planner returns [] → 0-token de-escalation template (apologize, offer help with price/order/delivery, never argue). Verified for "bal"/"chudir vai"/"dhat" |
| AV | repeated unknowns looped the same canned apology forever | no memory of the last reply | orchestrator reroutes UNKNOWN → CATALOG (text + product cards) when the PREVIOUS bot reply was also UNKNOWN. Verified: 2nd unknown message auto-sends 3 cards; 3rd unknown (after catalog) returns to the (now more helpful) apology |
| AW | "150 e den" mid-order-flow got consumed as name/quantity | workflow consumed any text in `awaiting_details` | `handle_message` yields to the pipeline for NEGOTIATE/FRUSTRATION intents — the pending field stays pending. Verified: name→phone flow, negotiation answered (399 টাকা hold), phone accepted after, state intact |
| AX | UNKNOWN messages got a canned "আমি বুঝতে পারিনি" — no AI understanding | UNKNOWN skipped the LLM entirely (planner returns [], `_greeting_or_fallback` canned) | UNKNOWN now runs `_unknown_llm_reply()`: an LLM pass with FULL context (long-term memory, conversation history, store context, focus products) with a dedicated prompt mode (`_build_prompt(unclear=True)`) — interpret typos/fragments/transliterated Bengali from context; clarify with ONE concrete question when genuinely unclear; never invent data; no tools. Logged to UsageLog (billed like a normal reply); canned text only as LLM-failure fallback. Verified: "dilivery kobe?" → delivery charges + timing from context; "zzz xyz qqq" → BN clarifying question; repeated-UNKNOWN → catalog cards still kicks in |
| AY | Bot ended EVERY reply with a pushy "আপনি কি অর্ডার করতে চান?" (4+ turns in a row) | gpt-4o default sales-mode CTA with no guardrail | Added anti-nagging rule: answer and STOP — no follow-up question, sales pitch, or "anything else?" sign-off; only exception is asking which product when the customer browsed the catalog. Rule placed in FINAL INSTRUCTION (highest-weight position; mid-prompt placement was ignored). Verified: price/size/delivery/payment/photo answers all end flat; catalog listing may end with "কোনটি পছন্দ?" |
| AZ | Billing/usage audit of the whole orchestration | See "Billing audit" section below | See below — 3 fixes: (1) 4dp rounding drift, (2) unbilled image analysis, (3) dry-run preview rows now tagged |
| BA | External product source (Monowamart ERP, live mode) tested with real baby-product images | See "External product source audit" section below | See below — 4 fixes: variation step in order workflow, SEND_IMAGES search-first, external catalog fallback, wrong-store fallback guard |
| BB | Live conversation regression: wrong-product replies ("pic dekhi" → earwax kit, "Dolna ache?" → "not in stock") | See "Wrong-product regression fixes" section below | See below — 4 fixes: focus-first SEND_IMAGES, focus-list matcher, Bengali→English synonyms, junk-word stopwords |
| BC | Agentic response loop for product intents (restores legacy multi-search behavior) | See "Agentic search loop" section below | See below — model can iterate think/search/details/send_images before replying |

Billing verified separately: per-turn `UsageLog` (planning + response_generation, shared reply_id) → `deduct_for_reply` sums via ModelPricing (gpt-4o 0.0025/0.01 per 1k in/out) → `select_for_update` deduction → `CreditTransaction` audit → `UsageSummary` F() aggregates. Live check: 122 logs / 119 deductions, latest reply deducted, balance chain consistent, renewal_date 2026-08-20. 3 orphaned logs (2.5%, from turns where the LLM call failed after logging — negligible ~0.0015 credits, acceptable).

Settings seeded for user `jihad`: AgentIdentity tone friendly/casual; BehaviorRules knowledge_base + sample_questions_answers (13 RAG chunks embedded).

### Architecture decisions (per review with user)

- **State machine (P1-9..13)**: reuse `SessionContext` (already exists) — no new `ConversationState` field on Conversation. Engine lives in new `api/ai/state.py`.
- **Specialist agents (P1-14..21)**: no 7 agent classes — specialist **prompt fragments** (SALES/SUPPORT/BILLING) appended in `response.py::_build_prompt()`, routed by intent map. Single LLM call, no routing overhead.
- **Events (P2-21..23)**: no event bus — background threads for side-effects (memory extraction already uses one). Revisit only if Celery is introduced.

### Verified flows (dev, SQLite, user `jihad`)

- Multi-turn BN order: "আমার একটু আচার দরকার" → name (করিম) → phone (01711111111) → address (মিরপুর ১০, ঢাকা) → confirm ("হ্যাঁ") → `ord_11dda4`, total 469.00, status pending.
- One-turn EN order: "I want to buy Amer Achar" with all customer fields known → `ord_gb344g` created instantly.
- Cancel during `awaiting_details` works (CANCEL_RE checked before collecting field values).
- P2 tools E2E: analytics ("how are my sales today?"), stock ("do you have Amer Achar in stock?"), tracking ("track my order ord_…").
- Proactive monitor: low_stock + subscription_expiring rules → 2 alerts dispatched as bot Messages.
- Regression: GREETING, SEARCH_PRODUCT, ASK_PRICE, CHECK_ORDER, FAQ, HUMAN_SUPPORT all reply correctly; UsageLog + AuditLog rows written per turn.
- Full 12-turn user replay (bn): hi → greeting; "ki product ache?" → 3-product catalog w/ discounts; "discunt nai jolpai er achar e?" → ৪৫০→৩৯৯; "300 takai diben?" → not possible, offers তেঁতুল ১৯৯; "delivery charge koto?" → ৭০/১২০; "Order korbo" → selection prompt; "jolpai" → Jolpaia; "ok" rejected as name; Karim Hossain / 01712345678 / banani dhaka collected; "ha" → `ord_2cf3a1` (Jolpaia, total 469.00).
- EN regression 7/7: GREETING, SEARCH (catalog listed in EN), PRICE, CHECK_ORDER, FAQ, HUMAN, UNKNOWN (canned EN reply). Note: HUMAN handoff disables AI (`disable_ai()`) — silence afterwards is intentional.
- Intent checks: "কত?" → ASK_PRICE; "কত স্টক আছে?" → ASK_STOCK; "কত দিন লাগবে?" → ASK_DELIVERY.
- Round-2 regression (fresh conversation): "amer achar dekhan" → product info; "4 pcs order korbo" → selection prompt (qty 4 kept); details message → confirmation summary; "ji" → `ord_eac4fa` Amer ×4 @ Rafiul alam/01793504010/Mirpur 10 Kajipara; "order id koto eitar?" → quotes the NEW order (not stale); "na eita to ager order" → CHECK_ORDER only, **no order created**; final "Na ami toh 4 pcs amer achar order korte chaisilam" → instant order with correct data.
- Repeat-order regression: "আবার অর্ডার করব" → reuses last product ×4; "আবার ২ পিস অর্ডার দিব" → ×2; "order this again" → ×2; "4 pcs achar order korbo" (ambiguous) → asks which product; known customer + explicit product → straight to confirmation summary.

### Billing audit (2026-07-31, row AZ)

Audit of the full money path: orchestrator deductions, UsageLog aggregation, image/audio processing cost, and ledger consistency.

**Findings & fixes:**
1. **4-decimal rounding drift (fixed)** — `deduct_for_reply` computed the exact 6-decimal cost but the balance and `CreditTransaction.amount` are 4-decimal fields, so every deduction silently under-charged (ledger 0.6463 vs exact 0.6465 over 125 replies). `total_cost` is now quantized to 4dp ONCE and used for balance, transaction, and UsageSummary — all three agree to the last digit (verified: 127/127 deductions reconcile, new deductions exact).
2. **Image analysis was unbilled (fixed)** — incoming images triggered a paid gpt-4o-mini vision call (`media.analyze_image_structured`) that wrote NO UsageLog and charged nothing (webhooks even dropped the `user`/`reply_id` params). Now: vision usage is logged under its own reply_id (`call_type="image_analysis"`, model tokens incl. image tokens) and immediately deducted via `deduct_for_reply(..., count_as_reply=False)` — tokens/credits counted in UsageSummary as an AI call, but NOT as a customer "reply" (no `messages_used`/`total_replies` inflation). Analysis is skipped entirely when the user's balance is 0. Verified: 3070/53 tokens → 0.0005 credits, tx == balance delta == summary delta.
3. **Dry-run Bot Preview usage was untagged (fixed)** — dashboard preview LLM calls are intentionally unbilled but wrote `response_generation` rows indistinguishable from real ones (96 orphan reply_ids, ~50% of today's raw UsageLog volume; UsageSummary was unaffected). They now carry `call_type="bot_preview"` so reports can exclude them.

**Verified correct (no change needed):**
- Tools are deterministic — no hidden LLM calls in `Executor`/`tools.py`; exactly 1 UsageLog row per reply (`total_ai_calls` == `total_replies` == deductions).
- `UsageSummary` uses `F()` expressions; `select_for_update()` guards deductions (note: no-op on SQLite — dev-only race risk, safe on production PostgreSQL).
- Model pricing covers all in-use models (gpt-4o, gpt-4o-mini incl. vision); unpriced models cost 0 (free) by design.
- Zero-balance → integrations disabled; renewal inline; deduction happens AFTER send, never before.
- `UsageLog`/`CreditTransaction`/`balance`/`UsageSummary` all reconcile after the fixes.

**Known residuals (not code bugs):**
- Audio transcription stays unbilled (prod has no `OPENAI_API_KEY` → placeholder text, no LLM spend; if set, Whisper would bill the merchant's OpenAI key directly, outside credit accounting).
- Proactive alerts (`back/management/commands/run_proactive_monitor.py`, manual-only) call the LLM without logging/deducting — not scheduled in prod.
- Dev data anomaly: `credits_total=50` vs `credits_remaining=999.7` (ledger was hand-edited during testing; `usage_percent()` goes negative until an admin adjustment reconciles it).

### External product source audit (2026-07-31, row BA)

Jihad's live Monowamart ERP (external provider, mode=live, 7517 baby products) tested end-to-end with real baby-product images + search + SKU + order flows.

**Verified working (live):**
- Connection: 7517 products; keyword search ("baby", "baby dress", "diaper", "bottle", "sando"), SKU search exact-match ("39955", "40204-260S", "35946MN", "41484", "40562PK"), `get_product` by external id, multi-variation products (e.g. Sando Genji: 6 sizes with per-variation stock).
- Vision on the 3 reference images (`docs/external_product_img_test_{0,1,2}.jpg`, via base64 data-URL since they're local): image 0 → SKU `40562PK` Aiwibi koala toothbrush — resolved LIVE to the exact product (Aiwibi Australia Baby ToothBrush BPA Free 2Years, [32263]); image 1 → Star glass bottle `10058` (no ERP SKU match; "glass bottle" name search finds Finer Care/Philips glass bottles); image 2 → Aveeno SPF50 sunscreen `332398` (no ERP SKU match — likely a UPC; name search pending ERP stability).
- Full image flow (ERP up): vision SKU → webhook pre-search → orchestrator SEND_IMAGES → product images + correct reply with price/stock.
- Multi-variation ORDER flow (mocked provider, real ERP payload contract): size asked FIRST ("কোন সাইজটা নেবেন? বিকল্প: 0-6 Months, 1-2 Years…"), "2-3 years" matched variation 3874, summary shows "…(2-3 Years)", ERP POST payload = `{'product_id': '2329', 'quantity': 1, 'variation_id': '3874'}` ✓, local Sale + OrderItem created.

**Fixes made during the audit:**
1. **Order workflow never captured size/variation** — `tool_create_order` hard-errors for multi-variation products, so every sized baby product order would fail. Added `awaiting_variation` state to `WorkflowEngine`: size asked before delivery details; matched via `_select_variation` (name/substring + Bengali-digit-normalized compact forms, "২-৩" ↔ "2-3"); re-asked on product swap; shown in the order summary; `variation_id` passed into `create_order` items. Single-variation products skip the step.
2. **SEND_IMAGES sent the wrong store's products** — with no focused product it fell back to the LOCAL catalog (the 3 achar rows!) for external users. SEND_IMAGES is now a search-first template (`[search_products(query=incoming_text), send_images]`), and `tool_send_images` catalog-browse fallback fetches the provider's catalog for external live stores.
3. **Wrong-store fallback on provider failure** — `tool_search_products`/`tool_get_product_details` fell through to the local DB when the ERP errored → a baby store would get achar answers. Now: external live + connection error → "catalog temporarily unavailable" instruction; clean "not found" still handled properly. Same guard added to `tool_send_images` (catalog browse AND per-PID paths — never touches local DB rows for external live users; verified with ERP down: all 3 paths return the unavailable error, internal users still get local-DB fallback).
4. **Provider swallowed connection errors** — `ExternalProvider.search/list_products/get_product` returned []/None on failure; added `self.last_error` so tools distinguish "no match" from "connection failure".

**External infra note:** `erp.monowamart.com` is UNSTABLE — repeatedly down for minutes at a time (connection refused; homepage 200/API refused intermittently). This is the ERP's infrastructure, not our code. With the fixes above, downtime now degrades gracefully (clear "catalog temporarily unavailable" message, never wrong products).

### Wrong-product regression fixes (2026-07-31, row BB)

Real live conversation (baby store, external ERP): customer asked "Dolna ache?" → bot said "not in stock" (catalog is English-named); after selecting a Mastela crib (SKU 31553), "pic dekhi" → sent the **Earwax Picker kit** pics, and "chailam crib er pic dilen earwax?" → sent a **mosquito net** instead of the crib.

**Root causes:**
1. SEND_IMAGES template re-searched the raw text first (`search_products("pic dekhi")`) — the word "pic" fanned out and fuzzy-matched "Earwax **Pic**ker", overwriting the focused product.
2. No Bengali→English synonym expansion — "dolna" found nothing in an English catalog.
3. Junk words ("pic", "dekhi", "taka", "select", "sku"…) were searched as individual queries.
4. Complaint messages ("...dilen earwax?") mention the wrongly-sent product too — an "all tokens must match the focus" rule fails; the mention of "crib" must win.

**Fixes (all in `api/ai/planner.py` + `api/ai/tools.py`):**
1. **Focus-first SEND_IMAGES** — `Planner._send_images_plan`: when the message refers to a focused product, send its images directly (no search step). Search-then-send only when nothing is focused.
2. **Focus-list matcher** — `_focus_match_for_query`: empty-token queries ("eta koto taka?", "pic dekhi") → most recent focus; token queries → focus item whose name contains the most tokens (ties → most recent). Explicit ID/SKU queries (contains a digit) skip it. Used by both the planner (SEND_IMAGES) and the external focus shortcut in `tool_search_products` (price/details follow-ups no longer fan out to junk).
3. **Bengali→English synonyms** — `_BN_EN_SYNONYMS` (~60 terms: দোলনা→cradle, খাট→bed, botol→bottle, dayapar→diaper, juto→shoes, frok→frock, …) applied in `_generate_search_queries` step 8; "Dolna ache?" now finds the Mastela cradles.
4. **Junk-word stopwords** — added image-request + chat filler words (pic/pics/photo/dekhi/dekha/chailam/taka/select/sku/kore/korbo… + ছবি/chobi) to `_STOPWORDS`; also now filtered in the latinized-word loop. "pic dekhi" yields only the full phrase (0 hits → clean no-match), never single junk words.

**Verified live (real ERP, dry-run replay of the exact reported conversation):** "Dolna ache?" → finds cradles; "Cradle ache?" → 3 Mastela products; "select SKU 31553" → crib + price; "pic dekhi" → **crib pics** (send_images only); "chailam crib er pic dilen earwax?" → **crib pics**; "eta koto taka?" → 15,500. Mock-provider runs confirm the same + the token-aware fallback (message naming a different product still searches).

### Agentic search loop (2026-07-31, row BC)

The old gemini-2.5-flash setup let the model drive the search itself (🔧 think → search_products × N → send_images → rich reply with prices + clarifying question). The orchestrator had replaced that with a deterministic planner + a single generation call with `tools=None` — the model could never re-search, so "Baby feeder price?"-style turns got thin answers and no images.

**Fix (`api/ai/response.py`):** product intents (`SEARCH_PRODUCT`, `ASK_PRICE`, `ASK_STOCK`, `ASK_DETAILS`, `COMPARE_PRODUCTS`, `RECOMMEND`, `SEND_IMAGES`, `CATALOG`) now go through `_agentic_loop()`:
- The planner's VERIFIED seed results are embedded in the prompt; the model additionally gets read-only tools: `think`, `search_products`, `get_product_details`, `send_images`, `search_knowledge_base` (order creation stays exclusively in the deterministic WorkflowEngine).
- Loop: model calls tools → executed via ToolRegistry → results appended (image URLs stripped, count only) → repeats until it replies in text. Hard cap `MAX_AGENT_ITERATIONS = 6`; prompt guides ≤3 searches ("feeder" → "feeding bottle"/"cleanser").
- Guards: (1) seed search empty + model never searched → forced corrective search pass; (2) reply claims to send photos without send_images → forced corrective pass (reuses pipeline's `_IMG_NOUN_RE`/`_SEND_CUE_RE`).
- Prompt rule added: if the exact item is unavailable but related products were found ("Vicks candy" → "Vicks BabyRub"), offer the related product with price.
- Every loop LLM call logs a `UsageLog` row under the same `reply_id` (billing aggregation unaffected); loop tool calls write `ToolCallLog`.

**Verified (mock ERP):** "Baby feeder price?" → lists 5 cleansers with prices + 5 images/cards (matches the old output); "Vicks candy…" → "candy নেই, তবে Vicks BabyRub… 480 টাকা"; "Post-partum belt ache?" → 2 belts with prices + images. Full wrong-product regression (BB) and not-found regression (aveno/bottle/cradle?/toys) still pass — focus-first SEND_IMAGES and the focus matcher are untouched by the loop.

**Cost note:** a search turn is now 2-3 LLM calls (~3k tokens each) instead of 1 — intentional, matches the old UX and the user's requirement; credits are deducted once per reply via the shared `reply_id`.

### Remaining (not started)

- P1-8 `ChannelFormatter` extraction; P2-2/3/5/8/9/10/12/13/14/15/16 tools; P2-21..23 event bus (deferred by decision); P2-24..27 observability (ToolCallLog enhancement, per-agent cost tracking).

---

## Summary

| Phase | Items | Scope | Status |
|---|---|---|---|
| P0 — Foundation | 12 items | ConversationManager, Orchestrator, Planner, Executor, ResponseGenerator, Memory system | ✅ 12/12 |
| P1 — Security, Tools, State, Agents | 21 items | Permissions, audit, refactored tools, state machine, specialist fragments | ✅ 20/21 (P1-8 ChannelFormatter pending) |
| P2 — Expansion | 27 items | Tools, proactive monitoring, event bus, observability | 🟡 9/27 |

## Dependencies

```
P0-1 through P0-8  (core pipeline) → no deps
P0-9 through P0-12 (memory) → no deps
P1-1 through P1-3  (security) → P0-5 (Executor)
P1-4 through P1-8  (tool refactor) → P0-5, P0-8
P1-9 through P1-13 (state machine) → P0-2 (Orchestrator)
P1-14 through P1-21 (agents) → P0-2, P0-3, P0-4, P0-6, P1-1
P2-1 through P2-16 (tools) → P1-4 through P1-8
P2-17 through P2-20 (proactive) → P0-2, P0-6
P2-21 through P2-23 (events) → P0-2
P2-24 through P2-27 (observability) → P0-5, P1-3
```
