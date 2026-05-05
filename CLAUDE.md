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
python manage.py runserver          # SQLite by default in dev
python manage.py setup_billing      # seed Plans + ModelPricing (run once)
python manage.py createsuperuser
python manage.py test
gunicorn theMatrixAi.wsgi:application   # production (Procfile)
```

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
/api/    → api/     (webhooks, REST)
/admin/  → Django admin
```

### Signals (load order matters)

All signals are wired in each app's `apps.py → ready()`.

| File | Signal | Effect |
|---|---|---|
| `back/signals.py` | `post_save(User)` | Creates `UserProfile` |
| `back/signals.py` | `post_save(Integration)` | Syncs `is_enabled` to all related `Conversation` rows |
| `back/signals.py` | `post_save(Message)` | Updates conversation message counters + last-message preview |
| `back/signals.py` | `post_save(Message)` dispatch_uid=`trigger_ai_pipeline` | If customer message + AI enabled → runs `api.ai.pipeline.run()` synchronously |
| `context/signals.py` | `post_save(User)` | `get_or_create` AgentIdentity, StoreConfig, BehaviorRules |
| `billing/signals.py` | `post_save(User)` | `get_or_create` UserBalance on free plan |

### AI Pipeline (`api/ai/`)

```
pipeline.py   — entry point: run(conversation, message) → build prompt → LLM loop → send reply → deduct credits
context.py    — assembles system prompt from AgentIdentity + StoreConfig + BehaviorRules + last 20 messages
providers.py  — thin OpenRouter wrapper (openai SDK pointed at https://openrouter.ai/api/v1); returns (msg, token_usage)
tools.py      — TOOL_DEFINITIONS (7 schemas) + execute_tool() dispatcher; tools: search_products, get_product_details,
                 send_images, create_order, get_order_status, update_customer, transfer_chat
sender.py     — send_reply(conversation, text, image_urls) → dispatches by platform via Graph API / Telegram Bot API
```

Key invariants:
- Max **5 tool iterations** per reply (`MAX_TOOL_ITERATIONS = 5`)
- Every LLM call is logged to `UsageLog` with a shared `reply_id` (UUID hex)
- `deduct_for_reply(user, reply_id)` is called after `send_reply` completes — never before

### Webhook Intake (`api/webhooks.py`)

- `ThreadPoolExecutor(max_workers=20)` — webhook views return `200 OK` immediately; all DB work runs in a thread
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
- Seed command: `python manage.py setup_billing` (creates 4 Plans + 10 ModelPricing rows)

### Core Models (`back/models.py`)

- **Integration** — one per platform per user; holds `access_token`, `verify_token`, `app_secret`, `ai_model`, `is_enabled`
- **Conversation** — unique on `(user, platform, customer_id)`; tracks `is_ai_enabled`, sentiment, intent, message counters
- **Message** — `sender` choices: `customer` / `bot` / `agent`; stores `raw_payload` (JSONField) for debugging
- **Product / Package / PackageItem** — catalog; package pricing = base ± item `price_delta`
- **Sale / OrderItem** — `completed`/`refunded` orders are immutable (enforced in `save()`); `oid` prefixed with `ord_`
- **UsageLog** — one row per LLM call; grouped by `reply_id` for billing aggregation

### ID Conventions

All public IDs use `shortuuid` with prefixes:
- Products: `sku_…`
- Packages: `pac_…`
- Orders: `ord_…`

### Settings Pages (`/db/settings/`)

Single tabbed page (`back/templates/back/settings.html`) with four POST sections controlled by a hidden `section` field: `store`, `agent`, `behavior`, `ai_model`. The view (`back/views.py → settings_view`) dispatches on `request.POST['section']` and redirects back with `?tab=<section>` to preserve the active tab.
