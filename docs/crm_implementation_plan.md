# MatrixAI CRM — Implementation Plan & Task Tracker

> Status: **IN PROGRESS** — Phase 1 (Foundation)
> Based on: `docs/crm_update_plan.md` (product spec)
> Scope decision (confirmed with product owner): **Internal MatrixAI Sales CRM first**, architected so the same CRM can later be offered per-tenant. Prospects first; converted prospects become platform customers.

---

## 1. Scope & Product Decisions

- **Internal CRM**: MatrixAI's own sales team manages prospects (leads) — from first contact → qualification → demo → negotiation → closed-won → onboarding → support.
- **Tenancy-ready**: all core models carry a nullable `tenant = FK(User)` field. `NULL` today = MatrixAI internal records. A later per-tenant rollout filters rows by tenant — no schema change needed.
- **Staff accounts**: Django `User` + `StaffProfile` (role: owner / manager / staff / support). `jihad` is seeded as owner.
- **Won deal → Customer**: conversion creates a real platform User (tenant) via the existing signals (UserProfile, context defaults, billing balance), assigns package/plan/credits, and hands off to implementation (support) staff.
- **Lead sources**: website forms (`front.Survay`, `front.Contact`) → automatic; manual + referral → manual entry. Social-channel auto-creation (Messenger/WhatsApp/Instagram/Telegram) is implemented behind a settings flag, ready for when MatrixAI connects its own pages or the per-tenant rollout.

## 2. Architecture

```
crm/  (new Django app)
├── models.py        StaffProfile, PipelineStage, Company, Lead, Activity,
│                    CallLog, Meeting, Task, Followup, SalesScript, FAQ,
│                    Customer, Notification, CrmSetting
├── services.py      lead lifecycle (create/assign/stage/convert), activity
│                    logging, onboarding, auto-lead-from-conversation (gated)
├── permissions.py   role decorators + staff guard middleware
├── views.py         page views + AJAX endpoints
├── urls.py          /crm/... routes
├── admin.py         registrations
├── apps.py          ready() wiring
├── signals.py       Conversation post_save hook (gated lead auto-creation)
├── management/commands/seed_demo_crm.py
├── templates/crm/   base.html + page templates + partials/
└── static/crm/      crm.css, crm.js, crm-kanban.js, crm-calendar.js
```

### URL map (`/crm/`)

| URL | Page | Access |
|---|---|---|
| `/crm/` | Dashboard (stat cards, funnel chart, activity timeline, upcoming tasks) | all staff |
| `/crm/leads/` | Leads table (search/filter/sort/paginate/bulk) | all staff (staff: assigned only) |
| `/crm/leads/new/` | Create lead | manager+ |
| `/crm/leads/<id>/` | Lead detail (two-column + timeline) | assigned staff+ |
| `/crm/leads/<id>/edit/` | Edit lead | assigned staff+ |
| `/crm/companies/` · `/crm/companies/<id>/` | Company list / detail | all staff |
| `/crm/pipeline/` | Kanban board (drag → stage) | all staff |
| `/crm/customers/` · `/crm/customers/<id>/` | Won customers + detail tabs | all staff, support |
| `/crm/calls/` | Call logs list + start-call modal | staff+ |
| `/crm/demo/` | Demo schedule + list | staff+ |
| `/crm/followups/` | Followups (today/tomorrow/week/overdue) | all staff |
| `/crm/calendar/` | Monthly calendar | all staff |
| `/crm/tasks/` | Tasks | all staff |
| `/crm/scripts/` · `/crm/faq/` | Sales scripts / FAQ search | all staff |
| `/crm/team/` | Team dashboard + leaderboard | manager+ |
| `/crm/reports/` | Sales/revenue/conversion charts | manager+ |
| `/crm/settings/` | Stages, sources, industries, tags, staff, scripts | owner/manager |
| `/crm/ajax/...` | AJAX endpoints (drag, quick update, toggle, log) | all staff |

### Roles & permissions

| Capability | Owner | Manager | Staff | Support |
|---|---|---|---|---|
| View all leads / assign / team / reports | ✅ | ✅ | – | – |
| View own assigned leads, call, update, change stage, notes, close | ✅ | ✅ | ✅ | – |
| Create staff users | ✅ | ✅ | – | – |
| Customers, tickets, AI status | ✅ | ✅ | ✅ | ✅ |
| CRM settings | ✅ | ✅ | – | – |

Enforcement: `@crm_role_required("manager", "owner")` decorator + a small middleware that redirects non-owner staff away from `/db/` (owner dashboard) and lands staff on `/crm/` after login.

## 3. Data Model

| Model | Key fields | Notes |
|---|---|---|
| `StaffProfile` | user OneToOne, role, phone, title, active | owner/manager/staff/support |
| `PipelineStage` | name, order, color, is_won, is_lost, tenant | kanban columns |
| `Lead` | name, phone, email, company FK, website, industry, source, stage FK, score, budget, expected_value, assigned_to FK, created_by FK, next_followup, last_contact, notes, tags JSON, lost_reason, converted, tenant | phone dedupe |
| `Company` | name, industry, website, employees, address, owner FK, notes, tenant | |
| `Activity` | lead FK, type, description, created_by, timestamp, data JSON | immutable timeline |
| `CallLog` | lead FK, staff FK, duration, outcome, summary, next_followup, recording, tags | outcomes: no_answer/busy/interested/not_interested/wrong_number/call_later/meeting_scheduled/demo_scheduled |
| `Meeting` | lead FK, staff, datetime, platform, link, checklist, status, notes | zoom/meet/offline |
| `Task` | lead FK nullable, assigned_to, priority, deadline, completed, title | |
| `Followup` | lead FK, due, kind (call/whatsapp/email/visit), done | drives followups page + calendar |
| `SalesScript` | title, category, content, active | ckeditor |
| `FAQ` | question, answer, category | search + copy |
| `Customer` | lead OneToOne, platform_user FK, package, renewal_date, status, owner | created at conversion |
| `Notification` | user FK, message, url, read, created | bell badge |
| `CrmSetting` | key, value JSON | flags (social auto-lead on/off), defaults |

**Status vs Stage**: stage is the source of truth. Hot/Warm/Cold = score buckets. Won/Lost = `is_won`/`is_lost` stages. Filters use stage/assigned/source/date/score.

## 4. Integration with Existing MatrixAI

- **Website leads**: wire `front.Survay` + `front.Contact` submissions → `crm.services.create_lead()` (source=website).
- **Social leads (gated)**: `crm/signals.py` `post_save(Conversation)` receiver; only active when `CrmSetting("social_lead_creation")` is true. Dedupe by phone/email; conversation link stored for later customer linking.
- **Customer profile linking**: `Customer.platform_user` → widgets showing Integration status per platform, AI enabled, recent conversations count, order/sales history, usage summary.
- **Onboarding after closed-won** (in `services.py`):
  1. Create platform `User` (username from email/phone) — signals auto-create UserProfile, context defaults, billing balance.
  2. Set `UserProfile.plan`, `UserBalance.plan` + credits + renewal + `CreditTransaction(plan_change)`.
  3. Create `Customer` row, assign implementation staff (support role).
  4. Welcome note + notification to assigned staff.

## 5. UI/UX Design

- New shell `crm/templates/crm/base.html`: white bg, `#2563eb` accent, Inter, rounded cards + glass effects, fixed collapsible sidebar, sticky topbar (global lead search, notification bell with unread count, profile dropdown).
- Partials: status pills, avatars, badges, stat cards, modal/drawer, toast, empty-state, pagination, chart wrapper.
- CDN libs only where needed: **SortableJS** (kanban), **Chart.js** (charts), **FullCalendar** (calendar), **flatpickr** (datetime). Everything else vanilla `fetch()` JS — matches codebase conventions.
- Pages: dashboard, leads, lead detail, pipeline, customers, companies, calls, meetings, followups, calendar, tasks, scripts, faq, team, reports, settings.

## 6. Task Tracker

> **Status: ALL PHASES COMPLETE** — 16/16 tests passing, full page sweep green.

### Phase 1 — Foundation
- [x] Write this plan document
- [x] 1.1 Create `crm` app skeleton (models.py, apps.py, urls.py, views.py, admin.py) + INSTALLED_APPS + root URL include at `/crm/`
- [x] 1.2 Models: StaffProfile, PipelineStage, Company, Lead, Activity + initial migration
- [x] 1.3 Roles & permissions: decorators, staff guard middleware, owner seed (jihad)
- [x] 1.4 Login redirect: staff → `/crm/`; non-owner staff blocked from `/db/`
- [x] 1.5 `crm/base.html` shell (sidebar/topbar/theme/partials) + empty dashboard page
- [x] 1.6 `seed_demo_crm` management command (stages, sample staff, sample leads)

### Phase 2 — Core CRM
- [x] 2.1 Leads list: search, filters, sorting, pagination, bulk actions
- [x] 2.2 Quick-create lead modal + full create/edit forms + phone dedupe
- [x] 2.3 Lead detail: two-column layout, Activity timeline, notes, stage/assign/score/followup controls
- [x] 2.4 Companies CRUD + detail (with linked leads)
- [x] 2.5 Activity auto-logging service (create/assign/stage-change/note)
- [x] 2.6 Permission enforcement on every view (staff = assigned leads only)

### Phase 3 — Sales Operations
- [x] 3.1 Kanban pipeline (drag → stage AJAX, card quick-edit, column totals)
- [x] 3.2 Tasks (CRUD, priority, status, per-lead)
- [x] 3.3 Followups page (today/tomorrow/week/overdue + one-click call/WhatsApp/email/complete)
- [x] 3.4 Calendar (FullCalendar: meetings, tasks, followups, calls)
- [x] 3.5 Call logs (start-call popup: duration/outcome/summary/next-followup/tags; list + filters)
- [x] 3.6 Demo scheduling (form, checklist, status, calendar integration)
- [x] 3.7 Sales Scripts (categories, editor, active toggle)
- [x] 3.8 FAQ (search, categories, click-to-copy, send)
- [x] 3.9 Customers: conversion flow (create platform User + plan/package/credits) + detail tabs (overview / AI setup / tickets / notes / timeline)
- [x] 3.10 Reports (revenue, conversion funnel, lead sources, top performers, monthly growth)
- [x] 3.11 Team dashboard (per-staff stats, leaderboard)
- [x] 3.12 Settings (stages CRUD w/ color+order, sources, industries, tags, staff management)
- [x] 3.13 Notifications (bell, unread badge: assignment/followup-due/won)

### Phase 4 — Integration & Automation
- [x] 4.1 Website lead auto-creation (Survay/Contact → Lead)
- [x] 4.2 Social lead auto-creation hook (gated) + customer ↔ Conversation/Sale linking
- [x] 4.3 AI setup panel (integration status, AI toggle, prompt/KB links)
- [x] 4.4 Onboarding workflow (welcome, credits, implementation assignment)
- [x] 4.5 Support role views (customers, tickets, AI status)
- [x] 4.6 Tests: services, permissions, lead lifecycle, stage transitions + full suite run (16/16 OK)
- [x] 4.7 Polish: empty states, toasts, responsive, final seed pass

## 7. Verification

- `python manage.py makemigrations crm && python manage.py migrate`
- `python manage.py seed_demo_crm`
- `python manage.py test crm` + full suite (`python manage.py test`)
- Manual walkthrough: login as owner → dashboard → create lead → assign → kanban drag → call log → demo → convert → customer tabs → reports → settings
- Staff-role walkthrough: staff sees only assigned leads; support sees customers/tickets only; manager sees team/reports.
