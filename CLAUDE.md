# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**TheMatrixAi** is a multi-tenant SaaS platform for AI-powered customer engagement across WhatsApp, Messenger, Instagram, and Telegram. Each user gets per-platform webhook URLs; incoming messages trigger an AI pipeline that uses tools (product search, order creation, etc.) to reply.

- **Production domain**: `thematrixai.xyz`
- **Deployment**: Railway.app — PostgreSQL + Cloudflare R2 storage

## Commands

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver                              # SQLite by default in dev
python manage.py setup_billing                         # seed Plans + ModelPricing (run once)
python manage.py createsuperuser
gunicorn theMatrixAi.wsgi:application                  # production (Procfile)

# Tests
python manage.py test                                  # all tests
python manage.py test back                             # single app
python manage.py test back.tests.TestClassName         # single class
python manage.py test back.tests.TestClassName.test_method  # single method
```

Most `tests.py` files are currently empty — test coverage is minimal.

## Environment Variables

| Variable | Purpose |
|---|---|
| `ENVIRONMENT` | `"production"` enables PostgreSQL + R2; anything else → SQLite + local media |
| `POSTGRES_LOCALLY` | `True` to force PostgreSQL in dev |
| `SECRET_KEY` | Django secret key |
| `ENCRYPT_KEY` | Custom encryption key used in the app |
| `DATABASE_URL` | PostgreSQL connection string |
| `CLOUDFLARE_R2_*` | R2 bucket credentials and endpoints |
| `CLOUDFLARE_R2_PUBLIC_DOMAIN` | CDN domain for serving files |
| `OPENROUTER_API_KEY` | OpenRouter API key — used by `api/ai/providers.py` |

Database: production or `POSTGRES_LOCALLY=True` → PostgreSQL via `dj-database-url`; otherwise SQLite (`db.sqlite3`).  
Storage: Cloudflare R2 (boto3 S3-compatible) in production; local `MEDIA_ROOT` in dev.

## Architecture

### Apps & Responsibilities

| App | Responsibility |
|---|---|
| `back/` | Dashboard, products/packages/orders, conversation UI, settings views |
| `api/` | Webhook receivers, REST endpoints for ERP/mobile, AI pipeline trigger |
| `front/` | Public pages — home, pricing, login/signup/logout |
| `context/` | Per-user AI brain config — AgentIdentity, StoreConfig, BehaviorRules |
| `billing/` | Credit-based billing — Plans, balances, deductions, usage tracking |
| `msg/` | Placeholder; no models |

### URL Layout

```
/        → front/   (home, pricing, login, signup)
/db/     → back/    (dashboard, products, orders, chats, settings, billing)
/api/    → api/     (webhooks, REST — per-user routes prefixed with /<username>/)
/admin/  → Django admin
```

### Signals (load order matters)

All signals are wired in each app's `apps.py → ready()`.

| File | Signal | Effect |
|---|---|---|
| `back/signals.py` | `post_save(User)` | Creates `UserProfile` |
| `back/signals.py` | `post_save(Message)` | Updates conversation message counters (`bot_sent_count`, `customer_sent_count`, etc.) |
| `context/signals.py` | `post_save(User)` dispatch_uid=`context_create_defaults` | `get_or_create` AgentIdentity, StoreConfig, BehaviorRules |
| `context/signals.py` | `post_save(BehaviorRules)` dispatch_uid=`rag_process_both` | If Q&A / knowledge-base text changed → re-chunk + re-embed RAG sources in a background thread (see RAG below) |
| `billing/signals.py` | `post_save(User)` dispatch_uid=`billing_create_user_balance` | `get_or_create` UserBalance on free plan |

> **Note:** The AI pipeline is **not** triggered by a `post_save(Message)` signal. It is driven by a 5-second batch timer in `api/webhooks.py` (see Webhook Intake). There is no `post_save(Integration)` signal syncing `is_enabled` to conversations.

### AI Pipeline (`api/ai/`)

```
pipeline.py   — entry point: run(conversation, message) → build prompt → LLM loop → send reply → deduct credits
context.py    — assembles system prompt from AgentIdentity + StoreConfig + BehaviorRules + last 20 messages
providers.py  — thin OpenRouter wrapper (openai SDK pointed at https://openrouter.ai/api/v1); returns (msg, token_usage)
tools.py      — TOOL_DEFINITIONS (8 schemas) + execute_tool() dispatcher
sender.py     — send_reply(conversation, text, image_urls) → dispatches by platform via Graph API / Telegram Bot API
media.py      — media (image/audio) handling for the pipeline
```

**The 8 tools:**

| Tool | Purpose |
|---|---|
| `search_products` | Search catalog by name/description; prioritises featured products (supports multi-query) |
| `get_product_details` | Full product info + image URLs by PID |
| `send_images` | Retrieve and send up to 5 product images |
| `create_order` | Create a pending order after collecting customer details |
| `get_order_status` | Look up an existing order by OID |
| `update_customer` | Save/update customer contact fields on the Conversation |
| `create_ticket` | Open a `SupportTicket` (escalation / hand-off to a human) |
| `search_knowledge_base` | RAG lookup over the user's embedded knowledge base / Q&A (see RAG below) |

Key invariants:
- Max **7 tool iterations** per reply (`MAX_TOOL_ITERATIONS = 7`)
- Every LLM call is logged to `UsageLog` (in `billing/`) with a shared `reply_id` (UUID hex)
- `deduct_for_reply(user, reply_id)` is called after `send_reply` completes — never before

### RAG / Knowledge Base (`context/`)

Per-user retrieval-augmented context, separate from product search:

```
chunking.py    — split BehaviorRules Q&A + knowledge-base text into chunks
embeddings.py  — embed chunks via OpenRouter (text-embedding-3-small)
search.py      — cosine-similarity search over RAGChunk rows
models.py      — RAGChunk: embedding stored as a JSONField list of floats (no pgvector)
```

- Re-embedding is triggered by the `post_save(BehaviorRules)` signal, in a background thread, only when the source text hash changes (`_RAG_CONTENT_CACHE`). Stale chunks are deactivated (`is_active=False`) then replaced via `bulk_create`.
- Queried at runtime by the `search_knowledge_base` tool.

### Webhook Intake (`api/webhooks.py`)

- `ThreadPoolExecutor(max_workers=50)` — webhook views return `200 OK` immediately; all DB work runs in a thread
- **5-second batch timer**: incoming customer messages are written to `MessageBatch` rows; a per-conversation `threading.Timer(5.0, …)` is (re)started on each message so rapid bursts collapse into one pipeline run. `_fire_batch_pipeline` consolidates unprocessed batches into one `combined_text`, then calls `api.ai.pipeline.run()`. If a run is already in flight for that conversation, the invocation skips and the unprocessed batches wait. This — not a signal — is how the AI pipeline is launched.
- Mid-based idempotency: if a `Message` with the same `mid` already exists, the payload is dropped (Meta retries on non-200)
- WhatsApp media download is two-step: get media URL from Graph API with Bearer token, then download binary with Bearer token
- Unified message format from `api/utils/parsers.py` → `{platform, customer_id, customer_name, message_id, type, text, attachments, raw}`
- Per-user webhook URLs: `/api/<username>/webhook/<platform>/`

### Billing (`billing/`)

- Credits are the billing currency, not dollars. `ModelPricing` stores `credits_per_1k_input/output`.
- `deduct_for_reply()` in `billing/deductions.py` uses `select_for_update()` on `UserBalance` to prevent race conditions
- `UsageSummary` increments use `F()` expressions to avoid double-counting across concurrent requests
- Auto-renewal is checked inline during deduction: if `today >= balance.renewal_date` → renew first, then deduct
- When `credits_remaining` hits 0 → all user `Integration.is_enabled` are set `False`
- Seed command: `python manage.py setup_billing` (creates 4 Plans + 10 ModelPricing rows, idempotent via `update_or_create`)

**Billing models** (`billing/models.py`):
- **Plan** — name (free/basic/pro/enterprise), `monthly_credits`, `max_messages`, `allowed_models`, `price`
- **UserBalance** — OneToOne to User; `credits_remaining`, `renewal_date`, `messages_used`
- **ModelPricing** — per-model token costs
- **UsageLog** — one row per LLM call; grouped by `reply_id` for cost aggregation
- **UsageSummary** — daily aggregates per user
- **CreditTransaction** — audit trail (deductions, renewals, top-ups)

### Core Models (`back/models.py`)

- **Integration** — one per platform per user; holds `access_token`, `verify_token`, `app_secret`, `ai_model`, `is_enabled`
- **Conversation** — unique on `(user, platform, customer_id)`; tracks `is_ai_enabled`, sentiment, intent, message counters
- **Message** — `sender` choices: `customer` / `bot` / `agent`; stores `raw_payload` (JSONField) for debugging
- **MessageBatch** — holding table for incoming messages consumed by the 5-second batch timer in `api/webhooks.py`
- **SupportTicket** — escalation record created by the `create_ticket` tool
- **Product / Package / PackageItem** — catalog; package pricing = base ± item `price_delta`
- **Sale / OrderItem** — `completed`/`refunded` orders are immutable (enforced in `save()`); `oid` prefixed with `ord_`

### ID Conventions

All public IDs use `shortuuid` with prefixes:
- Products: `sku_…`
- Packages: `pac_…`
- Orders: `ord_…`

### Settings Pages (`/db/settings/`)

Single tabbed page (`back/templates/back/settings.html`) with four POST sections controlled by a hidden `section` field: `store`, `agent`, `behavior`, `ai_model`. The view (`back/views.py → settings_view`) dispatches on `request.POST['section']` and redirects back with `?tab=<section>` to preserve the active tab.

### Dead Code

`back/ftp_storage.py` and `back/mongo_models.py` are unused leftovers — do not reference or extend them.
