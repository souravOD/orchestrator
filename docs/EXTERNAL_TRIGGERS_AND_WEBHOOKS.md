# External Triggers & Webhooks — End-to-End Setup Guide

> **Last updated:** 2026-02-23
>
> This document maps every trigger path the orchestrator supports,
> what external configuration each one requires, and the known code
> gaps that must be fixed before they work.

---

## Overview

The orchestrator has **4 trigger paths** for running pipelines.
Only one (Manual API) works out of the box today.

| # | Trigger Path | Status | External Setup? |
|---|-------------|--------|-----------------|
| 1 | Supabase Database Webhooks | 🔴 Broken (missing DB helper) | Yes — Supabase Dashboard |
| 2 | Cron Schedules | 🔴 Broken (missing DB helper) | Yes — Prefect or alternative |
| 3 | Manual API Trigger | 🟢 Works | None |
| 4 | File Upload Trigger | ⚪ Not implemented | Yes — Supabase Storage |

---

## Path 1: Supabase Database Webhooks (Real-time)

**Purpose:** Automatically trigger flows when rows are inserted/updated
in Supabase tables (e.g., new data lands in `bronze` schema).

### What exists in code

- `POST /webhooks/supabase` endpoint in `api.py`
- `handle_webhook_event()` in `triggers.py` — routes events to flows
- `event_triggers` table in schema with filter-based routing rules
- DLQ (dead-letter queue) with auto-retry for failed webhooks

### What's missing

| Setup | Where | Details |
|-------|-------|---------|
| **Create webhook in Supabase** | Supabase Dashboard → Database → Webhooks | Point at your deployed server |
| **Set `WEBHOOK_SECRET`** | `.env` + Supabase webhook config | Code checks `x-webhook-secret` header |
| **Configure trigger tables** | Supabase webhook settings | Specify which schemas/tables fire events |
| **Public URL** | Coolify deployment | Supabase can't reach `localhost` |

### Example Supabase webhook configuration

```
URL:     https://<your-coolify-domain>/webhooks/supabase
Method:  POST
Events:  INSERT on bronze.*
Headers: x-webhook-secret: <your-secret>
```

### ⚠️ Code gap

`db.list_active_event_triggers()` is called in `triggers.py` (line 132)
but **does not exist in `db.py`**. The webhook handler will crash when
it tries to match incoming events against the `event_triggers` table.

**Fix needed:** Add a function to `db.py`:

```python
def list_active_event_triggers() -> list:
    result = (
        _orch_table("event_triggers")
        .select("*")
        .eq("is_active", True)
        .execute()
    )
    return result.data or []
```

---

## Path 2: Cron Schedules (Automated)

**Purpose:** Run flows on a recurring schedule (e.g., daily full
ingestion at 2 AM, weekly USDA sync on Sundays).

### What exists in code

- `register_schedules()` in `triggers.py` — reads `schedule_definitions`
  and registers Prefect cron deployments
- `cmd_schedule` CLI command calls `register_schedules()`
- Schema seeds two default schedules:

| Schedule | Cron | Flow |
|----------|------|------|
| `daily_full_ingestion` | `0 2 * * *` (2 AM daily) | `full_ingestion` |
| `weekly_usda_nutrition` | `0 3 * * 0` (Sun 3 AM) | `full_ingestion` (source=usda) |

### What's missing

| Setup | Where | Details |
|-------|-------|---------|
| **Prefect server or Prefect Cloud** | External | `flow.serve()` requires a Prefect infrastructure |
| **Prefect worker/agent** | Deployment config | Without a worker, cron triggers won't fire |
| **Call `register_schedules()`** | CLI or app startup | Not auto-registered — must run manually |

### ⚠️ Code gap

`db.list_active_schedules()` is called in `triggers.py` (line 37) but
**does not exist in `db.py`**. Schedule registration will crash.

**Fix needed:** Add a function to `db.py`:

```python
def list_active_schedules() -> list:
    result = (
        _orch_table("schedule_definitions")
        .select("*")
        .eq("is_active", True)
        .execute()
    )
    return result.data or []
```

### Alternative: No-Prefect cron strategies

If you want to avoid running a full Prefect server, consider:

| Approach | Pros | Cons |
|----------|------|------|
| **Coolify built-in cron** | Zero extra infra, uses Coolify's scheduler | Tied to Coolify platform |
| **In-process `asyncio` scheduler** | No external deps, runs inside your FastAPI app | Loses schedule on restart, single-instance only |
| **External cron + curl** | Simple, works everywhere | Requires cron access on the host |
| **APScheduler library** | Mature Python scheduler, persistent job store | Extra dependency |

---

## Path 3: Manual API Trigger ✅

**Purpose:** On-demand triggering via API calls from the dashboard,
CLI, or external systems.

### Endpoints

| Endpoint | Purpose |
|----------|---------|
| `POST /api/trigger` | Trigger a single source |
| `POST /api/trigger-batch` | Trigger multiple sources in parallel |

### Usage examples

```bash
# Single source
curl -X POST http://localhost:8100/api/trigger \
  -H "Content-Type: application/json" \
  -d '{"flow_name": "full_ingestion", "source_name": "usda"}'

# Batch (parallel)
curl -X POST http://localhost:8100/api/trigger-batch \
  -H "Content-Type: application/json" \
  -d '{
    "sources": [
      {"source_name": "usda"},
      {"source_name": "vendor_a"},
      {"source_name": "vendor_b"}
    ]
  }'
```

**No external setup required.** Works as soon as the server is running.

---

## Path 4: File Upload Trigger (Future)

**Purpose:** Auto-trigger ingestion when a vendor uploads a CSV/JSON
file to Supabase Storage.

### Schema support

The `event_triggers` table already supports `event_type = 'file_upload'`,
but no code handles this event type today.

### Potential implementation

1. **Supabase Storage webhook** → fires on bucket object creation
2. **New handler in `triggers.py`** → downloads the file, passes to
   `full_ingestion_flow` as `storage_bucket` + `storage_path`
3. **Dashboard UI** → drag-and-drop upload that triggers the API

---

## Action Items (Priority Order)

| Priority | Action | Effort | Details |
|----------|--------|--------|---------|
| 🔴 P0 | Add `list_active_event_triggers()` to `db.py` | Small | ~10 lines |
| 🔴 P0 | Add `list_active_schedules()` to `db.py` | Small | ~10 lines |
| 🔴 P0 | Configure Supabase webhook → Coolify URL | Config | Dashboard only |
| 🔴 P0 | Set `WEBHOOK_SECRET` in `.env` and Supabase | Config | One-time |
| 🟡 P1 | Decide cron strategy (Prefect vs alternative) | Design | See options above |
| 🟡 P1 | Implement chosen cron strategy | Medium | Depends on choice |
| 🟢 P2 | Implement file upload trigger | Medium | New handler + Storage config |
