# AI Agent Implementation Task List — TheMatrixAi

Phase planning: 2026-07-30 | Total items: 48

---

## 🔴 P0 — Foundation (must have for any agent to work)

- [ ] **P0-1**: `ConversationManager` — pure Python class that loads conversation state, customer profile, products, orders, business settings, AI personality, and memory into a `ConversationContext` dataclass. No LLM involved.
- [ ] **P0-2**: `Orchestrator` — entry point that receives `ConversationContext`, runs intent detection, calls planner, dispatches to executor, then response generator. Replaces current `pipeline.py`.
- [ ] **P0-3**: `IntentDetector` — lightweight classifier (rule-based + small model) that maps incoming message to intent (SEARCH_PRODUCT, CREATE_ORDER, CHECK_ORDER, ASK_PRICE, GREETING, FAQ, HUMAN_SUPPORT, etc.). High-confidence intents bypass full planner.
- [ ] **P0-4**: `Planner` — takes intent + context, produces a sequence of tool calls. Example: intent=CREATE_ORDER → [search_products, check_inventory, calculate_price, create_cart]. Returned as a list of `PlanStep` objects.
- [ ] **P0-5**: `Executor` — runs tools from the plan deterministically (pure Python, no LLM). Each tool returns a `ToolResult` (structured JSON). Results are collected for the response generator.
- [ ] **P0-6**: `ResponseGenerator` — after all tools finish, LLM generates the final natural-language reply from structured tool results. Prevents hallucination of product names/prices/stock.
- [ ] **P0-7**: `ToolResult` / `PlanStep` / `ConversationContext` dataclasses — shared data types across all components.
- [ ] **P0-8**: `ToolRegistry` — centralized registry where each app registers its tools. Tools have: name, description, parameters (JSON schema), permission level, timeout, retry policy, cost estimate.

## 🔴 P0 — Memory System

- [ ] **P0-9**: `SessionContext` model — tracks current workflow step, verification status, pending actions, collected data. One per conversation.
- [ ] **P0-10**: `MemoryEntry` model — long-term user memory (preferences, facts, behavior patterns). Fields: user, conversation (nullable), memory_type (preference/fact/behavior/context), key, value (JSON), confidence, expires_at, is_active.
- [ ] **P0-11**: `MemoryManager` (memory.py) — CRUD for MemoryEntry. Functions: `store_fact()`, `recall()`, `forget()`, `summarize_memory()` (produces condensed summary for context window).
- [ ] **P0-12**: Background memory extraction — after each conversation turn, extract facts from exchange and persist as MemoryEntry. Runs async.

## 🟡 P1 — Security & Tool Layer

- [ ] **P1-1**: `ToolPermission` model — user role + tool_name + can_execute. Roles: staff, manager, owner, support_agent.
- [ ] **P1-2**: Permission check in `Executor` — every tool call verifies the user's role has permission before execution.
- [ ] **P1-3**: `AuditLog` model — full audit trail for every tool execution: user, tool_name, arguments, result_summary, execution_time_ms, timestamp, ip_address, actor_role.
- [ ] **P1-4**: Refactor `send_images` tool — extract from tools.py into separate module. Add permission, timeout, retry.
- [ ] **P1-5**: Refactor `search_products` tool — same extraction. Add multi-query fan-out as a configurable parameter.
- [ ] **P1-6**: Refactor `create_order` tool — same extraction. Add delivery_zone from state machine, not re-asked each time.
- [ ] **P1-7**: Refactor `get_order_status` / `update_customer` / `create_ticket` / `search_knowledge_base` — all into individual tool modules.
- [ ] **P1-8**: Create `ChannelFormatter` — converts final response + images + cards into platform-specific format (WhatsApp text, Messenger generic template, Telegram photo + caption, Instagram reply). Extracts from current `sender.py`.

## 🟡 P1 — State Machine & Workflows

- [ ] **P1-9**: `ConversationState` model — explicit state field on Conversation: browsing, product_selected, cart, checkout, payment, completed, escalated. Not just intent string.
- [ ] **P1-10**: State transition rules — which transitions are valid (e.g., cannot go from browsing → completed without going through cart → checkout → payment).
- [ ] **P1-11**: Workflow templates — JSON-defined multi-step flows. Example: `upgrade_plan.json` → [verify_owner, show_pricing, confirm, update_stripe, update_subscription, enable_features, send_receipt].
- [ ] **P1-12**: Workflow engine — loads workflow template, creates `SessionContext`, advances step-by-step on each message, persists partial progress.
- [ ] **P1-13**: Handle disambiguation — when customer says "another one" or "the second one", the state machine resolves the reference from `SessionContext`.

## 🟡 P1 — Specialist Agents

- [ ] **P1-14**: `BaseAgent` class — shared agent scaffold with: domain_prompt, tool_subset, permission_level, context_builder.
- [ ] **P1-15**: `SupportAgent` — FAQ, tickets, policies, return/exchange. Tools: search_knowledge_base, create_ticket, find_previous_tickets, escalation.
- [ ] **P1-16**: `SalesAgent` — product discovery, recommendations, upselling. Tools: search_products, get_product_details, send_images, compare_products, recommend_products, check_inventory.
- [ ] **P1-17**: `BillingAgent` — plans, invoices, upgrades. Tools: get_current_plan, get_invoice_history, upgrade_plan, downgrade_plan, cancel_subscription, change_payment_method.
- [ ] **P1-18**: `StoreAgent` — connected store management, sync status. Tools: get_connected_stores, check_sync_status, reconnect_store, refresh_token, sync_now.
- [ ] **P1-19**: `AnalyticsAgent` — business metrics. Tools: get_sales_summary, get_top_products, get_abandoned_carts, compare_periods, get_inventory_alerts.
- [ ] **P1-20**: `ContentAgent` — content generation. Tools: generate_product_description, generate_seo_title, generate_meta_description, generate_faq, translate_content.
- [ ] **P1-21**: Agent routing in `Orchestrator` — based on intent + context, route to correct specialist agent. Orchestrator holds the master conversation state; specialist agents return results.

## 🟢 P2 — Expanded Tool Layer (Nice to Have)

- [ ] **P2-1**: `check_inventory` tool — stock level by SKU with alerts at configurable thresholds.
- [ ] **P2-2**: `compare_products` tool — side-by-side comparison of up to 4 products (price, features, stock).
- [ ] **P2-3**: `recommend_products` tool — based on purchase history + browsing behavior + collaborative filtering.
- [ ] **P2-4**: `find_previous_tickets` tool — search customer ticket history by keyword/status/date range.
- [ ] **P2-5**: `get_coupons` tool — available discounts/promotions for the customer.
- [ ] **P2-6**: `get_payment_link` tool — generate payment link for pending order.
- [ ] **P2-7**: `track_shipment` tool — real-time tracking info by order ID.
- [ ] **P2-8**: `book_appointment` tool — calendar booking for demo/onboarding/support.
- [ ] **P2-9**: `send_email` tool — send follow-up, invoice, documentation via email.
- [ ] **P2-10**: `notify_team` tool — Slack/Teams/Discord notification for events.
- [ ] **P2-11**: `get_sales_summary` tool — revenue, conversion, AOV, top products by period.
- [ ] **P2-12**: `get_analytics` tool — trend analysis, compare periods, export data.
- [ ] **P2-13**: `generate_content` tool — product descriptions, SEO titles, meta descriptions, ad copy.
- [ ] **P2-14**: `translate_content` tool — multi-language product listing translation.
- [ ] **P2-15**: `check_sync_status` tool — integration health check for each connected store.
- [ ] **P2-16**: `reconnect_platform` tool — OAuth re-auth flow for expired tokens.

## 🟢 P2 — Proactive Intelligence

- [ ] **P2-17**: `ProactiveRule` model — user-configurable rules: event_type (sync_failure, low_stock, token_expiry, subscription_expiring), is_enabled, notify_channel.
- [ ] **P2-18**: Proactive monitor service — periodic checks (via management command or Celery beat) against registered rules. Runs outside the message flow.
- [ ] **P2-19**: Alert dispatch — when a rule triggers, push proactive message via the conversation channel. Uses existing sender.py infrastructure.
- [ ] **P2-20**: Proactive agent prompt — specialized system prompt for proactive messages (different tone, shorter, action-oriented).

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

## Summary

| Phase | Items | Scope |
|---|---|---|
| P0 — Foundation | 12 items | ConversationManager, Orchestrator, Planner, Executor, ResponseGenerator, Memory system |
| P1 — Security, Tools, State, Agents | 21 items | Permissions, audit, refactored tools, state machine, 7 specialist agents |
| P2 — Expansion | 15 items | Proactive monitoring, event bus, async, observability |

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
