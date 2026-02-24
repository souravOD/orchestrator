# Triggering Workflow Analysis

> **Audience:** Beginners / anyone new to the orchestrator
>
> This document explains *how* data ingestion pipelines get started — what
> "triggers" them — and walks through every option we have.

---

## Table of Contents

1. [What Is a "Trigger"?](#1-what-is-a-trigger)
2. [The Big Picture — How Data Flows](#2-the-big-picture)
3. [Trigger Option 1 — Manual API Call](#3-trigger-option-1--manual-api-call)
4. [Trigger Option 2 — Supabase Database Webhook](#4-trigger-option-2--supabase-database-webhook)
5. [Trigger Option 3 — Scheduled (Cron) Runs](#5-trigger-option-3--scheduled-cron-runs)
6. [Trigger Option 4 — File Upload](#6-trigger-option-4--file-upload)
7. [Comparison Table](#7-comparison-table)
8. [Recommendations](#8-recommendations)

---

## 1. What Is a "Trigger"?

Think of a trigger like an **alarm clock for your pipeline**.

Your pipeline doesn't run on its own — something needs to say *"Hey, start
processing data now!"* That "something" is the **trigger**.

There are different ways to ring that alarm:

- **You press a button** (Manual API) — like manually setting off your alarm
- **Something changes in the database** (Webhook) — like a motion sensor that
  activates when it detects movement
- **A timer goes off** (Cron Schedule) — like a recurring alarm at 2 AM every day
- **A file appears** (File Upload) — like a mailbox sensor that alerts you when
  mail arrives

Each approach has trade-offs. Let's walk through them.

---

## 2. The Big Picture

Here's how data flows through the system, regardless of which trigger starts it:

```
 ┌─────────────────────────────────────────────────────────────────┐
 │                        TRIGGER                                  │
 │   (something says "start!")                                     │
 │                                                                 │
 │   • Manual API call        ─┐                                   │
 │   • Supabase webhook        ├──►  Orchestrator API Server       │
 │   • Cron schedule           │     (FastAPI on port 8100)        │
 │   • File upload            ─┘                                   │
 └─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │                    ORCHESTRATION FLOW                            │
 │                                                                 │
 │   The orchestrator creates a "run" record in the database,      │
 │   then executes each layer in order:                            │
 │                                                                 │
 │   Layer 1: PreBronze → Bronze   (raw data → structured tables)  │
 │   Layer 2: Bronze → Silver      (cleaning, tagging, quality)    │
 │   Layer 3: Silver → Gold        (enrichment, nutrition, etc.)   │
 │   Layer 4: Gold → Neo4j         (graph database sync)           │
 │                                                                 │
 │   Each layer is a LangGraph pipeline with AI-powered tools.     │
 └─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │                     RESULTS                                     │
 │                                                                 │
 │   • orchestration_runs table updated with status & metrics      │
 │   • Dashboard shows progress, timing, and errors                │
 │   • Alerts sent if something fails                              │
 └─────────────────────────────────────────────────────────────────┘
```

**The key point:** All triggers do the same thing — they tell the orchestrator
*"run this flow for this data source."* The difference is *who* or *what*
initiates that message, and *when*.

---

## 3. Trigger Option 1 — Manual API Call

### What is it?

You (or a script, or the dashboard) sends an HTTP request to the orchestrator
saying "run this pipeline now." It's like clicking a "Run" button.

### How it works

```
You (or Dashboard)                  Orchestrator Server
       │                                    │
       │  POST /api/trigger                 │
       │  { "flow_name": "full_ingestion",  │
       │    "source_name": "usda" }         │
       │ ─────────────────────────────────► │
       │                                    │  Creates orchestration_run
       │  { "status": "accepted",           │  in database
       │    "run_id": "abc-123" }           │
       │ ◄───────────────────────────────── │
       │                                    │  Runs pipeline in background
       │                                    │  (you don't have to wait)
```

### Example commands

**Single source:**

```bash
curl -X POST http://localhost:8100/api/trigger \
  -H "Content-Type: application/json" \
  -d '{
    "flow_name": "full_ingestion",
    "source_name": "usda"
  }'
```

**Multiple sources in parallel (batch):**

```bash
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

### Pros & Cons

| ✅ Pros | ❌ Cons |
|---------|---------|
| Works right now, no setup needed | Someone has to press the button |
| Full control over when things run | Easy to forget to run it |
| Can pass custom parameters | Doesn't scale to many sources |
| Dashboard makes it visual | |

### When to use

- **Testing and development** — you want to run things manually and watch
- **One-off imports** — a vendor sends a new dataset, you import it once
- **Dashboard users** — clicking "Run" in the UI

### External setup needed

**None!** This works as soon as the server starts.

---

## 4. Trigger Option 2 — Supabase Database Webhook

### What is it?

Supabase watches your database tables. When a row is inserted or updated,
Supabase automatically sends a message to your orchestrator saying
*"Hey, new data just arrived!"*

Think of it like a doorbell — when someone (new data) arrives at the door
(database table), the bell rings (webhook fires) and you answer
(pipeline runs).

### How it works

```
Vendor uploads data
to Supabase table
       │
       ▼
┌──────────────┐     Webhook (HTTP POST)      ┌──────────────────┐
│   Supabase   │ ──────────────────────────►  │  Orchestrator    │
│   Database   │   "Hey, new row in           │  /webhooks/      │
│              │    bronze.raw_recipes!"       │  supabase        │
└──────────────┘                              └──────────────────┘
                                                       │
                                                       ▼
                                              ┌──────────────────┐
                                              │  handle_webhook  │
                                              │  _event()        │
                                              │                  │
                                              │  "This is a      │
                                              │  bronze INSERT,  │
                                              │  so I'll run     │
                                              │  bronze_to_gold" │
                                              └──────────────────┘
```

### What makes it powerful

The orchestrator has an `event_triggers` table that acts like a **routing
table**. It maps events to flows:

| Event | Table | Flow |
|-------|-------|------|
| `supabase_insert` | `bronze.raw_recipes` | `full_ingestion` |
| `supabase_update` | `gold.products` | `neo4j_batch_sync` |

When a webhook comes in, the system checks this table:
*"Insert on bronze.raw_recipes? That maps to full_ingestion → let's go!"*

If no explicit rule matches, it uses smart defaults:

- Gold schema events → `neo4j_batch_sync`
- Everything else → `realtime_event_flow`

### What you need to set up (external)

**Step 1: Deploy your orchestrator to Coolify** so it has a public URL.

```
Example: https://orchestrator.yourdomain.com
```

**Step 2: Create a webhook in Supabase Dashboard.**

1. Go to your Supabase project
2. Navigate to **Database → Webhooks** (or **Integrations → Webhooks**)
3. Click **Create a new webhook**
4. Fill in:

| Field | Value |
|-------|-------|
| Name | `orchestrator-pipeline-trigger` |
| Table | Select the table(s) you want to watch |
| Events | ☑️ Insert ☑️ Update (choose what makes sense) |
| Type | HTTP Request |
| Method | POST |
| URL | `https://orchestrator.yourdomain.com/webhooks/supabase` |
| Headers | `x-webhook-secret: your-secret-value-here` |

**Step 3: Set the webhook secret in your `.env` file.**

```bash
WEBHOOK_SECRET=your-secret-value-here
```

This must match the header you configured in Supabase. It's like a password
that proves the request is really from Supabase and not a random attacker.

### Safety net: Dead-Letter Queue

What happens if the webhook arrives but the pipeline crashes?

The orchestrator has a **dead-letter queue (DLQ)**. If processing fails:

1. The failed event is saved to the `webhook_dead_letter` table
2. The DLQ worker automatically retries: after 1 min, 5 min, then 30 min
3. After 3 retries, the event is marked "exhausted"
4. You can manually retry or discard via the API

This means **no data is lost** even if things temporarily break.

### Pros & Cons

| ✅ Pros | ❌ Cons |
|---------|---------|
| Fully automatic — no human needed | Requires external setup in Supabase |
| Real-time — runs seconds after data arrives | Your server must be publicly accessible |
| Dead-letter queue prevents data loss | Can get noisy if many rows change at once |
| Configurable routing via `event_triggers` | Need to manage webhook secret securely |

### When to use

- **Production automated pipelines** — data arrives, pipeline runs
- **Real-time processing** — you need results within minutes of data landing
- **Vendor integrations** — vendors push data to Supabase, pipeline auto-runs

---

## 5. Trigger Option 3 — Scheduled (Cron) Runs

### What is it?

The pipeline runs automatically at set times, like an alarm clock.
For example: *"Run the full ingestion every day at 2 AM"* or
*"Sync USDA data every Sunday at 3 AM."*

"Cron" is short for "chronograph" — it's the Unix/Linux way of scheduling
recurring tasks. A cron expression like `0 2 * * *` means "at minute 0 of
hour 2, every day, every month, every weekday" = **2:00 AM daily**.

### Cron expression cheat sheet

```
┌─────── minute (0-59)
│ ┌───── hour (0-23)
│ │ ┌─── day of month (1-31)
│ │ │ ┌─ month (1-12)
│ │ │ │ ┌ day of week (0-6, 0=Sunday)
│ │ │ │ │
* * * * *

Examples:
0 2 * * *     = Every day at 2:00 AM
0 3 * * 0     = Every Sunday at 3:00 AM
*/30 * * * *  = Every 30 minutes
0 0 1 * *     = First day of every month at midnight
```

### Pre-configured schedules in our system

The database comes with these schedules already seeded:

| Schedule Name | Cron | When | What it does |
|--------------|------|------|-------------|
| `daily_full_ingestion` | `0 2 * * *` | 2 AM daily | Runs `full_ingestion` for all sources |
| `weekly_usda_nutrition` | `0 3 * * 0` | Sunday 3 AM | Runs `full_ingestion` for USDA only |
| `hourly_neo4j_sync` | `0 * * * *` | Every hour | Syncs Gold → Neo4j |
| `daily_reconciliation` | `30 3 * * *` | 3:30 AM daily | Checks Neo4j data consistency |

### How to implement it — 3 options

#### Option A: In-Process Scheduler (⭐ Recommended)

**What it means:** We add a small scheduling library inside the FastAPI app
itself. When the server starts, it reads the schedules from the database and
sets timers. When a timer fires, it calls the same API internally.

```
┌──────────────────────────────────────────┐
│           FastAPI Server                  │
│                                          │
│   ┌─────────────┐    ┌───────────────┐   │
│   │  Scheduler   │──►│ /api/trigger  │   │
│   │  (built-in)  │    │              │   │
│   │              │    │ Runs the     │   │
│   │ "It's 2 AM,  │    │ pipeline     │   │
│   │  time to go!"│    │              │   │
│   └─────────────┘    └───────────────┘   │
└──────────────────────────────────────────┘
```

**Pros:**

- No extra services to run or manage
- Schedules stored in database — change them without redeploying
- Works with your existing Coolify/Docker setup
- I can implement this entirely in code

**Cons:**

- If the server restarts at 2 AM, the scheduled run might be missed
- Only works with a single server instance (no horizontal scaling)

**Best for:** Your current setup (Coolify + Docker, single instance)

---

#### Option B: Coolify Cron Jobs

**What it means:** You configure Coolify (your hosting platform) to run
a `curl` command at scheduled times. Coolify handles the timing, and it
just calls your API endpoint.

```
┌──────────────┐                    ┌──────────────────┐
│   Coolify    │   curl POST        │  Orchestrator    │
│   Cron Job   │ ────────────────►  │  /api/trigger    │
│              │   Every day        │                  │
│   "0 2 * *"  │   at 2 AM         │  Runs pipeline   │
└──────────────┘                    └──────────────────┘
```

**What you'd configure in Coolify:**

```bash
# Daily full ingestion at 2 AM
0 2 * * * curl -X POST https://orchestrator.yourdomain.com/api/trigger \
  -H "Content-Type: application/json" \
  -d '{"flow_name": "full_ingestion", "source_name": "all"}'

# Weekly USDA sync on Sundays at 3 AM
0 3 * * 0 curl -X POST https://orchestrator.yourdomain.com/api/trigger \
  -H "Content-Type: application/json" \
  -d '{"flow_name": "full_ingestion", "source_name": "usda"}'
```

**Pros:**

- Very simple — just curl commands
- Coolify manages the schedule reliably
- Works even if the server restarts

**Cons:**

- You have to configure it manually in the Coolify dashboard
- Schedules are split between Coolify and your database (not ideal)
- Harder to change schedules — need to update Coolify config

**Best for:** Quick setup if you're already comfortable with Coolify

---

#### Option C: Prefect Server (Full Orchestration Platform)

**What it means:** Prefect is a dedicated workflow orchestration platform
(think of it like Airflow, but more modern). You'd run a Prefect server
alongside your app, and it handles scheduling, retries, monitoring, etc.

```
┌──────────────┐    registers     ┌──────────────────┐
│  Orchestrator │ ──────────────► │   Prefect Server  │
│  (startup)    │    schedules    │                   │
└──────────────┘                  │  "I'll run        │
                                  │  full_ingestion   │
                                  │  at 2 AM daily"   │
                                  │                   │
                                  │  ─── 2 AM ───►    │
                                  │  Calls flow_fn()  │
                                  └──────────────────┘
```

**Pros:**

- Most powerful and reliable scheduling
- Beautiful dashboard for monitoring scheduled runs
- Built-in retry logic, concurrency controls, and alerting
- Industry-standard tool for data engineering

**Cons:**

- **Need to run a separate Prefect server** (extra Docker container, extra cost)
- **Need a Prefect worker/agent** to actually execute flows
- More complex to set up and maintain
- Overkill for a small number of schedules

**Best for:** Large-scale production with many schedules and teams

---

## 6. Trigger Option 4 — File Upload

### What is it?

When someone uploads a CSV or JSON file to a Supabase Storage bucket,
the pipeline automatically starts processing it.

Think of it like a **drop box**: put a file in, processing happens
automatically.

### How it would work (not yet built)

```
Vendor/User                     Supabase Storage        Orchestrator
     │                                │                      │
     │  Uploads vendor_data.csv       │                      │
     │ ─────────────────────────────► │                      │
     │                                │  Webhook fires       │
     │                                │ ───────────────────► │
     │                                │                      │ Downloads file
     │                                │                      │ from Storage
     │                                │                      │
     │                                │                      │ Runs full
     │                                │                      │ ingestion
     │                                │                      │ pipeline
```

### Current status

- The `event_triggers` schema supports `event_type = 'file_upload'` ✅
- **No code handles this yet** — it would need to be built
- Would require configuring Supabase Storage webhooks

### Pros & Cons

| ✅ Pros | ❌ Cons |
|---------|---------|
| Super user-friendly — just upload a file | Not built yet |
| Great for non-technical vendors | Need Supabase Storage setup |
| Could add a dashboard drag-and-drop UI | File format validation needed |

### When to use

- **Vendor onboarding** — vendors upload their product data files
- **Dashboard users** — upload via the web UI
- **Batch imports** — periodic data dumps from partners

---

## 7. Comparison Table

| Feature | Manual API | Webhook | Scheduled | File Upload |
|---------|-----------|---------|-----------|-------------|
| **Automatic?** | ❌ Manual | ✅ Yes | ✅ Yes | ✅ Yes |
| **Real-time?** | When you call it | ~Seconds | At set times | When file arrives |
| **Setup effort** | None | Medium | Low–Medium | Medium |
| **Code status** | ✅ Working | 🔴 Needs fix¹ | 🔴 Needs impl² | ⚪ Not built |
| **External config?** | None | Supabase Dashboard | Depends on option | Supabase Storage |
| **Best for** | Testing, one-offs | Production automation | Recurring batch jobs | Vendor data drops |
| **Reliability** | You control it | DLQ catches failures | Depends on option | TBD |

> ¹ Missing `list_active_event_triggers()` function in `db.py`
>
> ² Scheduling code exists but missing `list_active_schedules()` in `db.py`, and
> need to choose between Option A/B/C

---

## 8. Recommendations

For your current stage (deploying on Coolify, starting with a few data sources):

### Phase 1 — Get running (Now)

1. **Use Manual API triggers** for testing and development
2. **Fix the 2 missing DB functions** so webhooks and schedules don't crash
3. **Implement Option A (in-process scheduler)** for automated cron runs

### Phase 2 — Automate (After deployment)

1. **Set up Supabase webhook** pointing to your Coolify URL
2. **Configure webhook for the tables you care about** (e.g., `prebronze` inserts)
3. **Test the full automated loop**: data arrives → webhook fires → pipeline runs

### Phase 3 — Enhance (When needed)

1. **Build the file upload trigger** when you start onboarding vendors
2. **Consider upgrading to Prefect** if you need 10+ scheduled jobs
3. **Add monitoring/alerting** for failed triggers

### Decision needed from you

> **Which scheduling strategy do you prefer?**
>
> - **Option A** (in-process scheduler — recommended, I build it into the app)
> - **Option B** (Coolify cron — you configure curl commands in Coolify)
> - **Option C** (Prefect server — full orchestration platform)
>
> This is the main decision that determines next steps.
