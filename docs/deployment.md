# Deploying CIPHER

Everything in this document that can be automated has been. What remains
needs accounts and credentials that only you have, so this is a runbook
rather than a script.

Target, per `architecture.md` Section 19, all on free tiers:

| Piece | Where | Why |
| --- | --- | --- |
| Frontend (Next.js) | Vercel | First-party Next.js host, free tier, HTTPS included |
| Backend (FastAPI) | Render or Railway | Runs the container, free tier, HTTPS included |
| Database | Supabase | Already in use; nothing changes |

---

## Before you deploy anything

Run the preflight harness against your local system and get a clean result:

```bash
cd services/backend && python -m scripts.preflight
```

"Done" means preflight passed. It is the only check that exercises the real
chains — a green test suite has been green while `/memory/graph` returned 503
against real Postgres, because the test fake reimplements that query in
Python and never runs the SQL.

---

## 1. Database

The database is already live. The only deployment step is making sure the
schema matches the code:

```bash
cd services/backend && python -m alembic upgrade head
```

Run this against `MIGRATION_DATABASE_URL` (the session-mode pooler, port
5432), not the transaction-mode pooler — `CREATE INDEX` and `CREATE EXTENSION`
do not survive transaction pooling.

**Run migrations before deploying the code that needs them.** A backend that
starts against an older schema fails on the first request that touches a
missing column, which looks like a code bug and is not.

---

## 2. Backend

The container is built from the **repository root**, because the build
context needs more than `services/backend/`:

```bash
docker build -f services/backend/Dockerfile -t cipher-backend .
docker run --rm -p 8000:8000 --env-file .env cipher-backend
```

`.env` is passed at runtime and never built into the image — `.dockerignore`
excludes it. An image layer is readable by anyone who can pull the image, and
deleting a file in a later layer does not remove it from an earlier one.

### On Render

1. New → Web Service → connect this repository.
2. Runtime **Docker**, Dockerfile path `services/backend/Dockerfile`, and
   **Docker build context `.`** (the repository root, not the service
   directory). This is the setting most likely to be wrong; the build fails
   immediately if it is, which is the good case.
3. Health check path: `/health/ready`.
4. Environment variables — copy from your `.env`, with these changes:

   | Variable | Production value |
   | --- | --- |
   | `APP_ENV` | `production` |
   | `FRONTEND_URL` | your Vercel URL, exactly, no trailing slash |
   | `DATABASE_URL` | the transaction-mode pooler (port 6543) |
   | `MIGRATION_DATABASE_URL` | the session-mode pooler (port 5432) |
   | `AUTOMATION_ENABLED` | **leave unset** — see the warning below |

`FRONTEND_URL` is the CORS allowlist (`app/main.py`). A trailing slash or an
`http://` where the browser sends `https://` produces a CORS failure that
reads like a network problem.

> **Do not enable automation on a hosted backend.** The Phase 7 actions —
> opening a URL, launching an application — act on *the machine the backend
> runs on*. On your laptop that is the point; on a Render instance it is a
> stranger's container, where `open_url` does nothing useful and `open_app`
> is meaningless. `AUTOMATION_ENABLED` is off by default; leave it that way
> in production and use it locally only.

### Cold starts

Render's free tier sleeps a service after inactivity. The first request after
a sleep waits for a container start plus a database connection — often 30
seconds or more. That is not a bug to chase; it is the free tier. If it
matters, the fix is a paid instance, not a code change.

---

## 3. Frontend

1. Vercel → New Project → import this repository.
2. **Root directory: `apps/web`.** Without this, Vercel builds the repository
   root and finds no Next.js app.
3. Environment variable: `NEXT_PUBLIC_API_URL` = your Render backend URL.

`NEXT_PUBLIC_` values are **baked into the build**, not read at runtime.
Changing this variable requires a redeploy; changing it in the dashboard
alone does nothing, which is a genuinely confusing hour if you do not know it.

---

## 4. After deploying

Set `FRONTEND_URL` on the backend to the real Vercel URL and redeploy the
backend — the two services each need to know the other's address, so the
first deploy of the pair always takes two passes.

Then check, in this order:

```bash
curl https://<backend>/health          # process is up
curl https://<backend>/health/ready    # database is reachable
curl https://<backend>/models          # keys are loaded
```

Then send one real message through the deployed frontend. That exercises
CORS, the database, the LLM keys and the memory pipeline in one go, which no
individual endpoint check does.

---

## What is deliberately not automated

**Auto-deploy from `main`.** The blueprint suggests it and CI does not do it.
Pushing to `main` currently runs tests; it does not ship. That is a decision
worth making once, knowingly, with the deploy credentials in hand — and
against a project where `main` is committed to directly, an auto-deploy turns
every commit into a release.

**Preflight in CI.** It makes real, billable calls with real keys. CI has
neither, and giving it both to run a check that is meant to be read by a
human is a poor trade. Run it yourself before a release.

---

## Rolling back

Vercel and Render both keep previous deployments and can promote one in a
click; that is the fastest way back.

A migration is the exception. `alembic downgrade -1` exists and every
migration in this project implements `downgrade()`, but a downgrade that
drops a column drops the data in it. Before rolling back across a migration,
decide whether the data matters — that is not a question to answer at speed
during an incident.
