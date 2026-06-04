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

## EPIC 7 — Multi-Source Product Architecture `P1`
> Today every product lives in the internal `Product` model. Users must be able to keep that default **or** connect an external source (WooCommerce, Shopify, …). A central resolver abstracts all product/order reads so the AI pipeline never cares where a product comes from. Existing groundwork: `Sale.source` (`internal`/`external`) and `Sale.external_order_id` already exist.

| Status | Priority | Agent | Task |
|--------|----------|-------|------|
| [x] | P1 | @backend | `ProductSource` model: `user` FK, `provider` (`internal`/`woocommerce`/`shopify`/`external`), display name, `store_url`, encrypted creds (`consumer_key`/`consumer_secret` for Woo, `api_key`/`access_token` for Shopify), `order_endpoint_url`/`order_endpoint_auth`, `is_active` (single active, enforced in `save()`), `mode` (`live`/`sync`), `status`, `last_synced`, `sid` (`src_`) — migration `0018` |
| [x] | P1 | @backend | Add `source` FK (→ `ProductSource`, null=internal) + `external_id` to `Product` so synced items map back to their origin store |
| [x] | P1 | @api | Provider abstraction: base `ProductProvider` (`test_connection`, `list_products`, `get_product`, `search`, `create_order`, `get_order_status`) under `api/products/providers/`; normalized product dict shape |
| [x] | P1 | @api | `WooCommerceProvider` — REST API v3, basic auth with consumer key/secret + store URL; list/get/search products, create + read orders |
| [x] | P1 | @api | `ShopifyProvider` — Admin API 2024-01, store URL + access token; variant-aware products + orders |
| [x] | P1 | @api | `InternalProvider` — wraps the existing `Product` ORM behind the same interface (internal order creation stays in `api/ai/tools.py`); `ExternalProvider` for generic order-endpoint stores |
| [x] | P1 | @api | **Central resolver** `api/products/factory.py` — `get_active_source(user)` / `get_provider(user)` / `is_external(user)`; tools dispatch through it instead of querying `Product` directly |
| [x] | P1 | @ai-pipeline | Route `search_products`, `get_product_details`, `send_images` through the resolver (live fetch when `mode=live`; synced cache / internal path unchanged otherwise) |
| [x] | P2 | @api | Sync engine `api/products/sync.py` — pull external catalog → upsert local `Product` rows on `(source, external_id)`; management command `sync_products --user` + on-demand "Sync now" button |
| [x] | P2 | @api | Order routing — `api/products/orders.py:push_order_to_source(sale)` builds payload from `Sale`+items, pushes to Woo/Shopify/external endpoint, records `Sale.source`/`external_order_id`/`updated_to_web`; called from `create_order` after the local Sale commits |
| [~] | P2 | @api | `get_order_status` — provider method exists; AI tool still reads the local `Sale` (system of record, stores `external_order_id`). Enrich with live external status if needed later. |
| [x] | P2 | @frontend | Product Sources UI (`/db/sources`) — connect source, provider-aware cred fields, "Test connection", "Sync now", activate/edit/delete, status + last-synced; "Product Sources" button + active-source notice on Products page |
| [x] | P1 | @security | Credentials encrypted via `back/crypto.py` (Fernet derived from `ENCRYPT_KEY`); all source/product queries scoped per user; secrets never rendered back into forms or admin |
| [ ] | P3 | @api | Inbound store webhooks (optional) — Woo/Shopify product & order update webhooks → keep local cache fresh |

---

## EPIC 8 — One-Click OAuth Connect (Meta) `P1`
> Replace the manual flow (copy webhook URL, paste verify token + app secret + page token) with a single **Connect with Facebook** button: OAuth → pick page → auto-provision the `Integration`, subscribe the page to the app webhook, and the AI starts running immediately.

| Status | Priority | Agent | Task |
|--------|----------|-------|------|
| [ ] | P1 | @api | Configure the Meta App: OAuth redirect URI + scopes (`pages_messaging`, `pages_show_list`, `pages_manage_metadata`, `instagram_basic`, `instagram_manage_messages`) |
| [ ] | P1 | @frontend | "Connect with Facebook" button on the Integration page (replaces manual token/webhook fields) |
| [ ] | P1 | @api | OAuth start endpoint → Meta consent screen; OAuth callback → exchange `code` for short-lived, then long-lived user token |
| [ ] | P1 | @api | Fetch user pages (`GET /me/accounts`); let the user select which page(s) to connect |
| [ ] | P1 | @api | Store page access token + page id on `Integration` (`access_token`, `integration_id`), set `is_connected=True` |
| [ ] | P1 | @api | Auto-subscribe the page to the app webhook (`POST /{page-id}/subscribed_apps`) — no manual webhook URL/verify-token entry |
| [ ] | P1 | @backend | App-level webhook endpoint + single verify token; route inbound events by `page_id` → `Integration` (replaces per-user verify tokens for OAuth-connected pages) |
| [ ] | P2 | @api | Instagram connect via the IG business account linked to the connected page |
| [ ] | P2 | @backend | Long-lived token refresh before expiry; mark integration disconnected + notify on refresh failure |
| [ ] | P2 | @frontend | Integration page: connection status (Connected/Disconnected), page name/avatar, Disconnect button |
| [ ] | P1 | @security | OAuth `state` CSRF token, validated redirect URI, tokens stored encrypted |
| [ ] | P2 | @api | Disconnect flow — unsubscribe page (`DELETE /{page-id}/subscribed_apps`), clear tokens, set `is_connected=False` |

---

## EPIC 9 — Billing Fixes & Media Cost `P1`
> Fix a credit-leak bug and start billing for image and voice analysis.

| Status | Priority | Agent | Task |
|--------|----------|-------|------|
| [ ] | P1 | @billing | **BUG: default model not deducting.** In `deduct_for_reply` the `if pricing:` guard silently sets cost = 0 when the logged `model` has no active `ModelPricing` row. The default (`providers.DEFAULT_MODEL = "openai/gpt-4o-mini"`) must match a seeded `model_id` exactly so default-model replies always deduct. |
| [ ] | P1 | @ai-pipeline | Ensure `_log` records the **resolved** model id actually sent (not OpenRouter's returned variant) so it matches `ModelPricing.model_id` |
| [ ] | P1 | @billing | No silent free usage — log a WARNING (and/or apply a fallback default price) whenever a `UsageLog.model` has no matching `ModelPricing`, instead of charging 0 |
| [ ] | P2 | @billing | Image analysis billing — account for vision input tokens / per-image surcharge in `ModelPricing` + `UsageLog` and include in the reply deduction |
| [ ] | P2 | @billing | Voice/audio billing — bill transcription (per-second or per-token) when audio messages are transcribed before the pipeline |
| [ ] | P2 | @backend | Extend `UsageLog.call_type` to distinguish `text` / `vision` / `transcription` calls for accurate reporting |
| [ ] | P2 | @ai-pipeline | Log transcription + vision calls to `UsageLog` under the same `reply_id` so they roll into the reply's deduction |
| [ ] | P2 | @billing | Update `setup_billing` seed with image + audio/transcription pricing rows |

---

## Up Next (Recommended Order)

> Highest-value tasks that are unblocked and ready to start.

1. **`[ ]` BUG — default model not deducting** `@billing P1` — `deduct_for_reply` charges 0 when the logged model has no `ModelPricing` row. Highest priority: it's a live credit leak (Epic 9).
2. **`[x]` Multi-source product architecture** `@backend/@api P1` — done: `ProductSource` model, `api/products/` providers (Woo/Shopify/Internal/External) + resolver + sync + order push, AI tools wired, `/db/sources` UI. Remaining: inbound store webhooks (P3), live external `get_order_status` (P2).
3. **`[ ]` One-click Meta OAuth connect** `@api P1` — Connect button → OAuth → auto-subscribe page webhook (Epic 8).
4. **`[x]` Pre-flight credit check** — done: `api/ai/pipeline.py:run()` checks `credits_remaining <= 0` before first LLM call.
5. **`[x]` Usage alerts (20% threshold)** — done: `billing/deductions.py` logs `LOW_BALANCE` warning when `pct < 0.2`.
6. **`[~]` KnowledgeBase model** `@backend P2` — unblocks voice/knowledge features; add to `context/models.py`, migrate.
7. **`[~]` Voice message transcription** `@ai-pipeline P3` — in `api/webhooks.py` audio handling, call OpenRouter Whisper-compatible endpoint before pipeline (pairs with Epic 9 voice billing).
8. **`[~]` Security audit** `@security P2` — review all webhook + settings + billing endpoints for auth gaps and cross-tenant query leaks.
9. **`[~]` Rate limit webhooks** `@security P2` — add `django-ratelimit` per `username` on all webhook `POST` handlers.
10. **`[~]` Knowledge base manager UI** `@frontend P3` — blocked on #6 (KnowledgeBase model); CRUD page at `/db/settings/knowledge/`.

---

## Notes & Decisions
- AI pipeline is synchronous (easier to debug); move to async/Celery if latency becomes an issue
- Vector DB: start with pgvector (PostgreSQL extension); migrate to Pinecone/Qdrant if scale demands
- Multi-model: `call_llm(model, messages, tools)` in `api/ai/providers.py` is the single abstraction point
- Billing deduction is post-reply; pre-flight check (Epic 4 P3) will add the guard before pipeline runs
- All settings are on one tabbed page (`/db/settings/`) not separate URLs — simpler UX, one form POST per section
- `top_up()` function exists in `billing/deductions.py` but has no view/admin endpoint yet
