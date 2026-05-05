# TASKS.md — TheMatrixAi Master Task List

## How to Use
- **Status**: `[ ]` Todo · `[~]` Next Up · `[x]` Done
- **Priority**: `P1` Do now · `P2` Do next · `P3` Do later
- **Agent**: `@backend` · `@api` · `@ai-pipeline` · `@billing` · `@frontend` · `@security`
- Update this file as work progresses. Add notes under tasks when relevant.

---

## EPIC 1 — Webhook & Message Intake `P1`
> Receive incoming messages from all platforms and persist them. Foundation for everything else.

| Status | Priority | Agent | Task |
|--------|----------|-------|------|
| [x] | P1 | @api | Create unified webhook receiver endpoint per platform (`/api/<username>/webhook/whatsapp/`, `/messenger/`, `/instagram/`, `/telegram/`) |
| [x] | P1 | @security | Implement signature verification for each platform (HMAC-SHA256 for Meta, secret token for Telegram) |
| [x] | P1 | @api | Parse incoming message payload per platform into a unified internal format |
| [x] | P1 | @backend | Extend `Message` model to store raw platform payload for debugging |
| [x] | P1 | @api | Return `200 OK` immediately on webhook receipt, defer processing via `ThreadPoolExecutor` |
| [x] | P2 | @api | Handle all message types: text, image, audio, document, location |
| [x] | P2 | @api | WhatsApp media auto-download → store to R2 (extend existing `api/utils/files.py`) |
| [~] | P2 | @security | Rate limit webhook endpoints per integration (e.g. `django-ratelimit` per `username` path param) |

---

## EPIC 2 — Context Engine App (`context/`) `P1`
> New Django app that stores and serves the AI's "brain config" per user. Required before AI pipeline.

| Status | Priority | Agent | Task |
|--------|----------|-------|------|
| [x] | P1 | @backend | Create `context` Django app with `apps.py` and initial migration |
| [x] | P1 | @backend | `AgentIdentity` model: name, role/job, tone, communication style, language, profile image |
| [x] | P1 | @backend | `StoreConfig` model: store name, address, WhatsApp number, delivery charge, support hours (open/close time), timezone |
| [x] | P1 | @backend | `BehaviorRules` model: greeting message, out-of-hours message, chit-chat enabled (bool), chit-chat style |
| [~] | P2 | @backend | `KnowledgeBase` model: title, content chunks, vector embeddings (JSON or pgvector column) |
| [ ] | P2 | @api | API endpoint to serve full context bundle for a user (`/api/context/<user>/`) |
| [ ] | P2 | @security | Scope all context queries to authenticated user — no cross-tenant leaks |

---

## EPIC 3 — AI Pipeline & Tool Orchestration `P1`
> Core AI processing: message in → multi-step LLM calls → response assembled → sent to platform.

| Status | Priority | Agent | Task |
|--------|----------|-------|------|
| [x] | P1 | @ai-pipeline | Build pipeline runner: load context → call LLM → parse tool calls → execute tools → loop until done |
| [x] | P1 | @ai-pipeline | Define all tool schemas: `search_products`, `get_product_details`, `create_order`, `get_order_status`, `update_customer`, `transfer_chat`, `send_images` |
| [x] | P1 | @ai-pipeline | Implement each tool function with proper DB queries and R2 image URL generation |
| [x] | P1 | @ai-pipeline | Context builder: assemble system prompt from `AgentIdentity` + `StoreConfig` + `BehaviorRules` + last N messages |
| [x] | P1 | @backend | `UsageLog` model: `user`, `reply_id` (UUID grouping calls per reply), `model`, `input_tokens`, `output_tokens`, `call_type`, `timestamp` |
| [x] | P1 | @ai-pipeline | Log every LLM call to `UsageLog` with `reply_id` to group calls for one customer reply |
| [x] | P2 | @ai-pipeline | Multi-model support: allow per-integration model selection (e.g., fast model for search, smart model for final reply) |
| [x] | P2 | @ai-pipeline | Max tool call guard: limit to 5 LLM calls per reply to prevent runaway loops |
| [x] | P2 | @ai-pipeline | Response assembler: combine text + image URLs into platform-specific send format |
| [x] | P2 | @ai-pipeline | Send response via platform API (WhatsApp Cloud API, Messenger Send API, etc.) |
| [x] | P2 | @ai-pipeline | Graceful degradation: if a tool call fails, AI continues without that data |
| [ ] | P3 | @ai-pipeline | Vector search integration: embed product descriptions → search by query similarity |
| [~] | P3 | @ai-pipeline | Voice message transcription (openrouter, openai or equivalent) before sending to AI |
| [ ] | P3 | @ai-pipeline | Pluggable model provider: support OpenRouter, OpenAI, Anthropic (Claude), and others via common interface |

---

## EPIC 4 — Billing & Credits System `P2`
> Track every token used, deduct credits per reply, enforce plan limits, manage renewals.

| Status | Priority | Agent | Task |
|--------|----------|-------|------|
| [x] | P2 | @billing | `Plan` model: name, monthly_credits, max_messages_per_month, allowed_models (JSON), price_per_month |
| [x] | P2 | @billing | `UserBalance` model: credits_remaining, credits_total, renewal_date, FK to Plan |
| [x] | P2 | @billing | `ModelPricing` model: model_id, credits_per_1k_input, credits_per_1k_output |
| [x] | P2 | @billing | `UsageSummary` model: per user per day — total_replies, total_ai_calls, total_tokens_in, total_tokens_out, total_credits_used |
| [x] | P2 | @billing | Credit deduction function: sum `UsageLog` for `reply_id` → calculate cost → atomic deduct from `UserBalance` |
| [x] | P2 | @billing | Trigger deduction after AI pipeline completes each reply |
| [x] | P2 | @billing | When balance hits 0: disable all user `Integration.is_enabled` |
| [x] | P2 | @billing | Monthly renewal job: reset `credits_remaining` to `plan.monthly_credits`, advance `renewal_date` |
| [x] | P2 | @security | Use `select_for_update()` on `UserBalance` during deduction to prevent race conditions |
| [ ] | P3 | @billing | Manual top-up: admin UI/endpoint to add credits to user balance (staff only) — `top_up()` function exists in `billing/deductions.py`, needs a view |
| [ ] | P3 | @billing | Plan upgrade/downgrade: prorate credits on change |
| [x] | P3 | @billing | Usage alerts: log LOW_BALANCE warning when credits drop below 20% (fires inside deduct_for_reply) |
| [x] | P3 | @billing | Pre-flight check: verify user has sufficient credits before starting AI pipeline |

---

## EPIC 5 — Frontend Settings & Dashboard `P3`
> User-facing pages to configure everything: store, agent, billing, usage stats.

| Status | Priority | Agent | Task |
|--------|----------|-------|------|
| [x] | P3 | @frontend | Store Config tab in Settings (`/db/settings/`): name, address, delivery charge, support hours, WP number |
| [x] | P3 | @frontend | Agent Identity tab in Settings: name, role, tone, language, profile image upload |
| [x] | P3 | @frontend | Behavior Rules tab in Settings: greeting, out-of-hours message, chit-chat toggle + style |
| [x] | P3 | @frontend | AI Model tab in Settings: model selector per integration, enable/disable AI toggle |
| [x] | P3 | @frontend | Billing dashboard (`/db/billing/`): credits remaining, usage bar, 7-day chart, month totals, transaction history, plans grid |
| [x] | P3 | @frontend | Usage stats embedded in billing page: messages/day chart, tokens used, AI calls, credits breakdown |
| [x] | P3 | @frontend | Dashboard AI Credits mini-card: shows remaining credits, usage %, link to billing |
| [x] | P3 | @backend | Views and URL routes for all settings pages (`settings_view`, `billing_dashboard`) |
| [x] | P3 | @security | All settings views have `@login_required` and user-scoped queries confirmed |
| [~] | P3 | @frontend | Knowledge base manager (`/db/settings/knowledge/`): add/edit/delete FAQ and policy entries — needs `KnowledgeBase` model first (Epic 2) |

---

## EPIC 6 — Cross-Cutting & Infrastructure `P2`
> Tasks that span multiple epics or are needed to glue everything together.

| Status | Priority | Agent | Task |
|--------|----------|-------|------|
| [x] | P2 | @backend | Register `context` and `billing` apps in `INSTALLED_APPS` in `settings.py` |
| [x] | P2 | @backend | Wire AI pipeline trigger: after `Message` saved (signal) → check if AI enabled → start pipeline |
| [~] | P2 | @security | Audit all new endpoints: authentication, user-scoped queries, no sensitive data in responses |
| [ ] | P2 | @api | Platform send client: abstraction layer to send text/images to WhatsApp, Messenger, Instagram, Telegram |
| [ ] | P3 | @backend | Conversation `transfer_flag` field: mark conversation as transferred (AI disabled, agent notified) |
| [ ] | P3 | @ai-pipeline | Cross-sell logic: after order created, AI suggests related products based on order contents |
| [ ] | P3 | @ai-pipeline | Open-ended question strategy: AI asks one qualifying question when product intent is unclear |

---

## Up Next (Recommended Order)

> Highest-value tasks that are unblocked and ready to start.

1. **`[x]` Pre-flight credit check** — done: `api/ai/pipeline.py:run()` checks `credits_remaining <= 0` before first LLM call.
2. **`[x]` Usage alerts (20% threshold)** — done: `billing/deductions.py` logs `LOW_BALANCE` warning when `pct < 0.2`.
3. **`[~]` KnowledgeBase model** `@backend P2` — unblocks voice/knowledge features; add to `context/models.py`, migrate.
4. **`[~]` Voice message transcription** `@ai-pipeline P3` — in `api/webhooks.py` audio handling, call OpenRouter Whisper-compatible endpoint before pipeline.
5. **`[~]` Security audit** `@security P2` — review all webhook + settings + billing endpoints for auth gaps and cross-tenant query leaks.
6. **`[~]` Rate limit webhooks** `@security P2` — add `django-ratelimit` per `username` on all webhook `POST` handlers.
7. **`[~]` Knowledge base manager UI** `@frontend P3` — blocked on #2 (KnowledgeBase model); CRUD page at `/db/settings/knowledge/`.

---

## Notes & Decisions
- AI pipeline is synchronous (easier to debug); move to async/Celery if latency becomes an issue
- Vector DB: start with pgvector (PostgreSQL extension); migrate to Pinecone/Qdrant if scale demands
- Multi-model: `call_llm(model, messages, tools)` in `api/ai/providers.py` is the single abstraction point
- Billing deduction is post-reply; pre-flight check (Epic 4 P3) will add the guard before pipeline runs
- All settings are on one tabbed page (`/db/settings/`) not separate URLs — simpler UX, one form POST per section
- `top_up()` function exists in `billing/deductions.py` but has no view/admin endpoint yet
