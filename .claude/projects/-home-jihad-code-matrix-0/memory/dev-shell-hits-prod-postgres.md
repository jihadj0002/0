---
name: dev-shell-hits-prod-postgres
description: Running manage.py in this dev checkout connects to the PRODUCTION Postgres DB, not SQLite
metadata:
  type: project
---

In this working copy, `venv/bin/python manage.py ...` prints "Using Postgres Database" and connects to the **production** Postgres (real users/conversations), despite CLAUDE.md saying dev defaults to SQLite. So shell queries here read live data.

**Why:** A `DATABASE_URL` / Postgres env is configured locally, so `dj-database-url` overrides the SQLite default.

**How to apply:** Treat `manage.py shell`/migrations as touching prod — read-only queries are fine, but never run destructive ORM writes, migrations, or management commands casually. To inspect real AI behavior, user `jihad` (live MonowaMart external catalog, `is_external=True`) is the demo account; conversation 1790 is a good reference thread. Use `python3`/`venv/bin/python` — bare `python` is not on PATH.
