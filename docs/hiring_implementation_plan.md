# MatrixAI Hiring Forum — Implementation Plan & Task Tracker

> Status: **COMPLETE** — 9 hiring tests + full suite green, E2E smoke verified.
> Scope (confirmed with product owner): public careers page where sales applicants submit name/skills/CV/details; owner & managers review applicants, shortlist, hire (create CRM staff accounts → `/crm/` access), and schedule group meetings.
> Hired members get **CRM staff access only** (no tenant dashboard / integrations).
> Group meeting = **one meeting record that bulk-invites selected candidates**.

---

## 1. Scope & Product Decisions

- **Public application form** at `/careers/apply/`: candidates (sales roles) submit full name, email, phone, position, years of experience, skills, expected salary, availability, city, photo + CV upload, cover letter.
- **Admin management** at `/crm/hiring/` inside the CRM "Manage" section (owner/manager only channel, reuses role decorators + sidebar gating).
- **Candidate lifecycle**: `applied → shortlisted → interview_scheduled → hired|rejected`.
- **Hire action** creates a real Django `User` + `crm.StaffProfile` (role `staff` by default, configurable) so the person can log in and work the CRM. Temp credentials shown once to the owner.
- **Group meeting**: a single `HiringMeeting` (title, datetime, platform zoom/google_meet/offline, link, notes) with a multi-select of candidates → `MeetingAttendee` rows; participants auto-marked `interview_scheduled`.
- **Notifications**: in-app state changes always visible; emails attempted best-effort via `send_mail` when SMTP is configured (settings take optional env vars). Silent no-op otherwise.
- **Files**: CV/photo stored through the existing media storage (local `MEDIA_ROOT` in dev, Cloudflare R2 in production) — no new infra.

## 2. Architecture

```
hiring/  (new Django app)
├── models.py        CandidateApplication, HiringMeeting, MeetingAttendee
├── services.py      create_application (phone normalize + dedupe + spam guard),
│                    hire_candidate (User + StaffProfile), notify_candidate,
│                    bulk meeting helpers
├── views.py         public (apply/thanks) + admin (index/detail/status/hire/
│                    delete/meetings/meeting_new)
├── urls.py          public routes (/careers/) — app_name = "hiring"
├── admin_urls.py    admin routes (/crm/hiring/) — namespace hiring_admin
├── admin.py         registrations
├── apps.py          AppConfig ("hiring")
├── management/commands/seed_hiring.py   (dev seed: sample candidates + meeting)
├── tests.py         services + views + permission tests
├── templates/hiring/  apply.html, thanks.html, index.html,
│                      candidate_detail.html, meetings.html, meeting_form.html
```

### URL map

| URL | Page | Access |
|---|---|---|
| `/careers/` | Public apply form | public |
| `/careers/thanks/` | Post-submit confirmation | public |
| `/crm/hiring/` | Candidates list (stats, search, filters, bulk actions) | owner/manager |
| `/crm/hiring/<uid>/` | Candidate detail (profile, CV/photo, notes, actions) | owner/manager |
| `/crm/hiring/<uid>/hold/action/` | shortlist / reject / hire / delete (POST) | owner/manager |
| `/crm/hiring/meetings/` | Meetings list + status toggles (complete/cancel) | owner/manager |
| `/crm/hiring/meetings/new/` | Group meeting form with candidate multi-select | owner/manager |

### Statuses

`applied` → `shortlisted` → `interview_scheduled` → `hired` | `rejected` (from any active state).

## 3. Data Model

| Model | Key fields | Notes |
|---|---|---|
| `CandidateApplication` | uid (`can_`), name, email, phone, position, experience_years, skills, expected_salary, availability, city, photo, cv, cover_letter, status, notes, source (website/manual), hired_user FK, created_at | dedupe by email; phone normalized via `crm.services.normalize_phone` |
| `HiringMeeting` | title, datetime, platform (zoom/google_meet/offline), link, status (scheduled/completed/cancelled), notes, created_at | group interview record |
| `MeetingAttendee` | meeting FK, candidate FK, rsvp (invited/attended/no_show) | links candidates to a meeting |

Position values mirror CRM staff roles: `sales_staff`, `sales_executive`, `sales_manager`, `support`.

## 4. Integration with Existing MatrixAI

- **Hire → StaffProfile**: reuses the same mechanics as `crm.views.settings` staff creation — create `User` (username from email unless taken), set a temp password, `StaffProfile.objects.update_or_create(role=...)`. Existing `post_save(User)` signals (UserProfile / context / billing balance) fire harmlessly.
- **Login/guard reuse**: staff land on `/crm/` at login (`front.views.login_view`); `CrmStaffGuardMiddleware` keeps staff away from `/db/`.
- **Admin nav**: new "Hiring" link in `crm/base.html` Manage section (already `owner/manager`-only).
- **CV storage**: `FileField`/`ImageField` on `CandidateApplication`, rendered via `MEDIA_URL` — production R2 handled by the shared storage config.

## 5. UI/UX

- **Public**: clean Tailwind two-column apply page (matches `front/forum.html` visual language), honeypot spam field, inline success message.
- **Admin**: extends `crm/base.html` using existing `.panel`, `.crm-table`, `.btn`, `.pill` classes; stat cards on index; detail page shows resume/photo, notes, timeline of status; meeting form uses multi-select candidate list with "every shortlisted" shortcut.

## 6. Task Tracker

> **Phase 1 — Foundation**: app skeleton, models, migration
> **Phase 2 — Public**: apply + thanks
> **Phase 3 — Admin ops**: list/detail/actions/hire
> **Phase 4 — Meetings**: group scheduling + bulk invite
> **Phase 5 — Hardening**: tests, notifications, seed, verification

### Phase 1 — Foundation
- [x] 1.1 Write this plan document
- [x] 1.2 Create `hiring` app (INSTALLED_APPS, apps.py, models, admin, urls, admin_urls) + initial migration
- [x] 1.3 Root URL includes: public `/careers/` + admin `/crm/hiring/`; CRM sidebar nav item

### Phase 2 — Public application
- [x] 2.1 Services: `create_application` (normalize/dedupe/validate), honeypot spam guard
- [x] 2.2 Public views `apply` / `thanks`
- [x] 2.3 Public templates (`apply.html`, `thanks.html`) + media upload handling

### Phase 3 — Admin operations
- [x] 3.1 Hiring index: stat cards, searchable/filterable table, actions column
- [x] 3.2 Candidate detail page (profile, CV/photo download, notes)
- [x] 3.3 Status actions: shortlist / reject
- [x] 3.4 `hire_candidate` service: create a per-file User + StaffProfile, mark hired, temp-credentials flash, notify best-effort
- [x] 3.5 Delete candidate

### Phase 4 — Group meetings
- [x] 4.1 `HiringMeeting` + `MeetingAttendee` models
- [x] 4.2 Meeting list with status toggles (complete/cancel)
- [x] 4.3 Meeting form with candidate multi-select ("bulk invite") → attendees, marks `interview_scheduled`
- [x] 4.4 Meetings/index quick action "Schedule meeting"

### Phase 5 — Hardening & verification
- [x] 5.1 Optional SMTP block (env-driven) + notification helper, best-effort send
- [x] 5.2 `seed_hiring` management command (sample candidates + a meeting)
- [x] 5.3 Tests: services (create/dedupe), hire (StaffProfile + /crm/ access), bulk meeting (attendees + status), permissions (staff cannot access)
- [x] 5.4 Full verification: makemigrations → migrate → check → test; smoke the public form + admin flows

## 7. Verification

```bash
python manage.py makemigrations hiring
python manage.py migrate
python manage.py check
python manage.py test hiring
python manage.py seed_hiring
python manage.py runserver   # /careers/ + /crm/hiring/ (owner login)
```