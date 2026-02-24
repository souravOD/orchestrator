# Trigger Implementation Guide — Real-Time, Bulk & Hybrid Strategies

> **Last updated:** 2026-02-24  
> **Scope:** How to build and deploy every trigger type for the orchestration pipeline,  
> with special focus on **real-time webhooks for Gold→Neo4j** and **bulk triggers**.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Trigger Taxonomy](#2-trigger-taxonomy)
3. [Real-Time Triggers — Webhooks for Gold→Neo4j](#3-real-time-triggers--webhooks-for-goldneo4j)
4. [Bulk Triggers](#4-bulk-triggers)
5. [Hybrid Strategy — Real-Time + Bulk Together](#5-hybrid-strategy--real-time--bulk-together)
6. [Code Gaps & Implementation Checklist](#6-code-gaps--implementation-checklist)
7. [Database Schema for Triggers](#7-database-schema-for-triggers)
8. [End-to-End Implementation Plan](#8-end-to-end-implementation-plan)
9. [Configuration Reference](#9-configuration-reference)

---

## 1. Architecture Overview

The orchestration pipeline uses a **medallion architecture** with four data layers, each backed by LangGraph AI pipelines:

```
  ┌──────────────────────────────────────────────────────────────────────────────────┐
  │                           TRIGGER LAYER                                          │
  │                                                                                  │
  │   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
  │   │  Manual API  │  │  Supabase    │  │  Cron        │  │  File Upload │        │
  │   │  /api/trigger│  │  Webhooks    │  │  Scheduler   │  │  (future)    │        │
  │   └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘        │
  │          │                  │                  │                 │                │
  │          └──────────────────┴──────────────────┴─────────────────┘                │
  │                                     │                                            │
  │                                     ▼                                            │
  │                          ┌─────────────────────┐                                 │
  │                          │  FastAPI Orchestrator │ (port 8100)                    │
  │                          │  - triggers.py        │                               │
  │                          │  - api.py             │                               │
  │                          └──────────┬────────────┘                               │
  └─────────────────────────────────────┼────────────────────────────────────────────┘
                                        │
                                        ▼
  ┌──────────────────────────────────────────────────────────────────────────────────┐
  │                           FLOW LAYER (flows.py)                                  │
  │                                                                                  │
  │   full_ingestion_flow ─► bronze_to_gold_flow ─► single_layer_flow               │
  │   neo4j_batch_sync_flow ─► neo4j_reconciliation_flow ─► neo4j_realtime_worker   │
  │   multi_source_ingestion_flow ─► realtime_event_flow                             │
  └──────────────────────────────────────┬───────────────────────────────────────────┘
                                         │
                                         ▼
  ┌──────────────────────────────────────────────────────────────────────────────────┐
  │                        PIPELINE LAYER (pipelines.py)                              │
  │                                                                                  │
  │   PreBronze → Bronze → Silver → Gold → Neo4j                                    │
  │   (Each is a Prefect @task with timeout, cancellation, metrics)                  │
  └──────────────────────────────────────────────────────────────────────────────────┘
```

Every trigger ultimately calls a **flow function** from `flows.py`, which orchestrates one or more **pipeline tasks** from `pipelines.py`.

---

## 2. Trigger Taxonomy

| Trigger Type | Latency | Use Case | Current Status |
|---|---|---|---|
| **Manual API** (`POST /api/trigger`) | On-demand | Testing, dashboard, one-offs | ✅ Working |
| **Batch API** (`POST /api/trigger-batch`) | On-demand | Multi-source parallel ingestion | ✅ Working |
| **Supabase DB Webhook** (`POST /webhooks/supabase`) | ~Seconds | Auto-trigger on INSERT/UPDATE/DELETE | 🔴 Needs DB fix |
| **Cron Schedule** (Prefect/APScheduler) | Time-based | Daily, hourly, weekly jobs | 🔴 Needs DB fix + strategy |
| **File Upload** (Supabase Storage) | ~Seconds | Vendor drops CSV/JSON to bucket | ⚪ Not built |
| **Neo4j Batch Sync API** (`POST /api/neo4j/sync`) | On-demand | Direct Gold→Neo4j push | ✅ Working |
| **Upstream Complete** (internal) | Chained | Layer N done → Layer N+1 starts | ✅ Working (built-in) |

---

## 3. Real-Time Triggers — Webhooks for Gold→Neo4j

This is the core question: **how do we make Gold→Neo4j sync happen automatically in real-time when Gold data changes?**

### 3.1 What Triggers Are Needed

You need **3 types of real-time triggers** for Gold→Neo4j:

```
  ┌─────────────────────────────────────────────────────────────────────────┐
  │                    GOLD → NEO4J REAL-TIME TRIGGERS                      │
  │                                                                         │
  │  TRIGGER 1: Gold Table Change Webhook                                   │
  │  ─────────────────────────────────────                                  │
  │  When: INSERT/UPDATE on any gold.* table                                │
  │  What: Fires Supabase webhook → orchestrator                           │
  │  Action: Routes to neo4j_batch_sync_flow or realtime worker            │
  │  Tables: gold.dim_recipes, gold.dim_ingredients,                       │
  │          gold.dim_products, gold.dim_customers                          │
  │                                                                         │
  │  TRIGGER 2: Gold Outbox Poller (Customer Realtime)                      │
  │  ─────────────────────────────────────                                  │
  │  When: Continuously (polling every 5s)                                  │
  │  What: Polls gold.customer_outbox table for unprocessed events          │
  │  Action: Pushes B2C events to Neo4j in near-real-time                   │
  │  Service: customer_realtime (long-running worker)                       │
  │                                                                         │
  │  TRIGGER 3: Reconciliation Cron (Drift Detection)                       │
  │  ─────────────────────────────────────                                  │
  │  When: Daily at 3:30 AM (cron schedule)                                 │
  │  What: Compares Gold counts/checksums vs Neo4j                          │
  │  Action: Detects drift, proposes LLM-guided backfills                   │
  └─────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Trigger 1 — Supabase Webhook on Gold Tables

This is the **primary real-time trigger**. When the Silver→Gold pipeline finishes writing to `gold.*` tables, Supabase fires a webhook to the orchestrator.

#### How the routing works (already in code)

In [triggers.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/triggers.py#L136-L145), there's already a **default routing rule**:

```python
# Route Gold schema events to Neo4j batch sync
if source_schema == "gold":
    logger.info("📡 Gold schema event detected — routing to neo4j_batch_sync")
    from .flows import neo4j_batch_sync_flow
    return neo4j_batch_sync_flow(
        layer="all",
        trigger_type="webhook",
        triggered_by=f"gold.{source_table}",
    )
```

**This means:** Any INSERT/UPDATE/DELETE on `gold.*` tables is automatically routed to `neo4j_batch_sync_flow`. The code logic is there — it just needs the infrastructure set up.

#### What Supabase webhooks you need to create

| # | Webhook Name | Table | Events | Target URL |
|---|---|---|---|---|
| 1 | `gold-recipes-sync` | `gold.dim_recipes` | INSERT, UPDATE | `https://<host>/webhooks/supabase` |
| 2 | `gold-ingredients-sync` | `gold.dim_ingredients` | INSERT, UPDATE | `https://<host>/webhooks/supabase` |
| 3 | `gold-products-sync` | `gold.dim_products` | INSERT, UPDATE | `https://<host>/webhooks/supabase` |
| 4 | `gold-customers-sync` | `gold.dim_customers` | INSERT, UPDATE | `https://<host>/webhooks/supabase` |

> [!IMPORTANT]
> **Debouncing is critical.** If a batch load writes 500 rows to `gold.dim_recipes`, Supabase will fire 500 individual webhooks. Without debouncing, you'd run `neo4j_batch_sync` 500 times. You need either:
>
> - **A debounce timer** in `triggers.py` (wait N seconds after first event, batch subsequent events)
> - **The concurrency guard** (`_guard_concurrent`) which already prevents duplicate runs
> - **A Supabase database function** that only fires the webhook after the batch completes

#### Implementation: Webhook Debouncer

Here's the debounce mechanism to add to `triggers.py`:

```python
import time
import threading
from collections import defaultdict

_pending_gold_syncs: dict[str, float] = {}  # table → timestamp of first event
_debounce_lock = threading.Lock()
_DEBOUNCE_SECONDS = 30  # Wait 30s after last event before triggering sync

def _debounced_neo4j_sync(source_table: str) -> dict | None:
    """
    Debounce Gold→Neo4j sync triggers.
    
    When a Gold table receives rapid writes (batch load), this ensures
    we only trigger ONE sync after the writes settle down.
    """
    now = time.time()
    
    with _debounce_lock:
        if source_table in _pending_gold_syncs:
            # Update the timestamp — reset the debounce window
            _pending_gold_syncs[source_table] = now
            return {"debounced": True, "table": source_table}
        
        _pending_gold_syncs[source_table] = now
    
    # Start a background timer
    def _fire_after_debounce():
        time.sleep(_DEBOUNCE_SECONDS)
        with _debounce_lock:
            last_event = _pending_gold_syncs.pop(source_table, 0)
        
        # Only fire if no new events arrived during the wait
        if time.time() - last_event >= _DEBOUNCE_SECONDS - 1:
            from .flows import neo4j_batch_sync_flow
            neo4j_batch_sync_flow(
                layer="all",
                trigger_type="webhook",
                triggered_by=f"gold.{source_table}",
            )
    
    thread = threading.Thread(target=_fire_after_debounce, daemon=True)
    thread.start()
    return {"debounced": True, "table": source_table, "will_fire_in_seconds": _DEBOUNCE_SECONDS}
```

#### Event Trigger Rules to Seed in Database

These go into the `event_triggers` table for explicit routing:

```sql
INSERT INTO orchestration.event_triggers
    (trigger_name, event_type, source_schema, source_table, flow_name, 
     debounce_seconds, filter_config)
VALUES
    ('gold_recipes_to_neo4j', 'supabase_insert', 'gold', 'dim_recipes', 
     'neo4j_batch_sync', 30, '{"layer": "recipes"}'),
    ('gold_ingredients_to_neo4j', 'supabase_insert', 'gold', 'dim_ingredients', 
     'neo4j_batch_sync', 30, '{"layer": "ingredients"}'),
    ('gold_products_to_neo4j', 'supabase_insert', 'gold', 'dim_products', 
     'neo4j_batch_sync', 30, '{"layer": "products"}'),
    ('gold_customers_to_neo4j', 'supabase_insert', 'gold', 'dim_customers', 
     'neo4j_batch_sync', 30, '{"layer": "customers"}'),
    ('gold_update_to_neo4j', 'supabase_update', 'gold', NULL, 
     'neo4j_batch_sync', 30, '{"layer": "all"}')
ON CONFLICT (trigger_name) DO NOTHING;
```

### 3.3 Trigger 2 — Customer Realtime Outbox Poller

For **B2C events** (customer orders, preferences, profile changes), the Gold-to-Neo4j pipeline has a **customer_realtime service** that polls an outbox table.

#### How it works

```
  gold.customer_outbox table             customer_realtime service
  ┌────────────────────────────┐         ┌────────────────────────┐
  │ id │ event_type │ payload  │         │ Poll every 5 seconds   │
  │ 1  │ order_new  │ {...}    │ ◄────── │ Read unprocessed rows  │
  │ 2  │ pref_upd   │ {...}    │         │ Push to Neo4j          │
  │ 3  │ profile    │ {...}    │         │ Mark as processed      │
  └────────────────────────────┘         └────────────────────────┘
```

#### Trigger needed

This is a **long-running worker**, not a one-shot trigger. It's started via:

```bash
# Via API
POST /api/trigger
{
  "flow_name": "neo4j_realtime_worker"
}

# Via CLI
python -m orchestrator.cli serve  # starts with the server
```

The flow is already defined in [flows.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/flows.py#L672-L718) as `neo4j_realtime_worker_flow`.

**Configuration needed:** Set `NEO4J_REALTIME_POLL_INTERVAL=5` in `.env` (default is 5 seconds).

### 3.4 Trigger 3 — Reconciliation Cron

The reconciliation service compares Gold and Neo4j to detect drift. It should run on a schedule.

**Already seeded in DB:**

```sql
-- Runs daily at 3:30 AM
('daily_reconciliation', '30 3 * * *', 'neo4j_reconciliation', '{}')
```

**Trigger implementation** depends on the cron strategy (see [Section 4.2](#42-cron-scheduled-triggers)).

---

## 4. Bulk Triggers

### 4.1 For Bulk Data Ingestion (PreBronze → Gold)

When you have large datasets to process (initial load, vendor bulk upload, periodic refresh):

#### Option A: Batch API Endpoint (Recommended)

Use `POST /api/trigger-batch` for **parallel multi-source ingestion**:

```bash
curl -X POST https://<host>/api/trigger-batch \
  -H "Content-Type: application/json" \
  -d '{
    "flow_name": "full_ingestion",
    "sources": [
      {"source_name": "usda", "vendor_id": "vendor-uuid-1"},
      {"source_name": "vendor_a", "vendor_id": "vendor-uuid-2"},
      {"source_name": "vendor_b", "vendor_id": "vendor-uuid-3"}
    ],
    "batch_size": 500,
    "incremental": false
  }'
```

**How it works internally:**

```
  POST /api/trigger-batch
        │
        ▼
  ┌──────────────────────────────────────────────────┐
  │  For each source:                                 │
  │    1. Create orchestration_runs row (run_id)      │
  │    2. Return run_ids immediately to caller        │
  │                                                   │
  │  In background:                                   │
  │    multi_source_ingestion_flow()                  │
  │    ├─ ParallelSourceRunner (asyncio.Semaphore)    │
  │    ├─ Max concurrency: 2 (configurable)           │
  │    ├─ Source 1: full_ingestion_flow(usda)          │
  │    ├─ Source 2: full_ingestion_flow(vendor_a)      │ ← runs in parallel
  │    └─ Source 3: full_ingestion_flow(vendor_b)      │ ← queued until slot opens
  └──────────────────────────────────────────────────┘
```

**Key settings:**

| Env Variable | Default | Purpose |
|---|---|---|
| `PARALLEL_MAX_CONCURRENCY` | 2 | Max sources running simultaneously |
| `PARALLEL_WORK_DIR` | `/tmp/orchestrator-work` | Isolation directory |
| `PARALLEL_QUEUE_WARN_THRESHOLD` | 10 | Alert if backlog exceeds this |

#### Option B: Cron-Triggered Bulk (Overnight Batch)

For periodic bulk processing, use a cron schedule:

```sql
-- Nightly bulk ingestion at 2 AM
INSERT INTO orchestration.schedule_definitions
    (schedule_name, cron_expression, flow_name, run_config)
VALUES
    ('nightly_bulk_ingestion', '0 2 * * *', 'full_ingestion',
     '{"source_name": "all", "batch_size": 1000, "incremental": true}');
```

#### Option C: File-Drop Trigger (Future)

When a vendor drops a CSV to Supabase Storage, auto-trigger ingestion:

```
  Vendor uploads  →  Supabase Storage webhook  →  Orchestrator downloads file
  product_data.csv   fires to /webhooks/supabase    runs full_ingestion_flow
```

### 4.2 For Bulk Neo4j Sync (Gold → Neo4j)

When you need to **bulk-load all Gold data into Neo4j** (initial setup, full resync, disaster recovery):

#### Direct API Trigger

```bash
# Sync all layers (recipes → ingredients → products → customers)
curl -X POST https://<host>/api/neo4j/sync?layer=all

# Sync a specific layer
curl -X POST https://<host>/api/neo4j/sync?layer=recipes
```

**How it works internally:**

```
  POST /api/neo4j/sync?layer=all
        │
        ▼
  neo4j_batch_sync_flow(layer="all")
        │
        ▼
  Neo4jPipelineAdapter.run_all_layers()
        │
        ├─ run_batch_sync("recipes")     → catalog_batch/run.py → _run_layer()
        ├─ run_batch_sync("ingredients") → catalog_batch/run.py → _run_layer()
        ├─ run_batch_sync("products")    → catalog_batch/run.py → _run_layer()
        └─ run_batch_sync("customers")   → catalog_batch/run.py → _run_layer()
```

#### Cron-Scheduled Bulk Sync

Already seeded:

```sql
-- Hourly sync — catches any missed real-time events
('hourly_neo4j_sync', '0 * * * *', 'neo4j_batch_sync', '{"layer": "all"}')
```

### 4.3 Cron/Scheduled Triggers — Strategy Recommendation

The scheduling code exists in [triggers.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/triggers.py#L22-L75) but needs the DB helper and a runtime strategy.

#### Recommended: APScheduler (In-Process)

```python
# Add to api.py startup
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

@app.on_event("startup")
async def _start_scheduler():
    schedules = db.list_active_schedules()  # ← needs the missing DB function
    for sched in schedules:
        scheduler.add_job(
            _trigger_scheduled_flow,
            trigger=CronTrigger.from_crontab(sched["cron_expression"]),
            id=sched["schedule_name"],
            kwargs={"flow_name": sched["flow_name"], "config": sched["run_config"]},
            replace_existing=True,
        )
    scheduler.start()

async def _trigger_scheduled_flow(flow_name: str, config: dict):
    """Called by the scheduler — dispatches to the flow registry."""
    from .flows import FLOW_REGISTRY
    flow_fn = FLOW_REGISTRY.get(flow_name)
    if flow_fn:
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, lambda: flow_fn(
            trigger_type="scheduled",
            triggered_by=f"scheduler:{flow_name}",
            config=config,
        ))
```

---

## 5. Hybrid Strategy — Real-Time + Bulk Together

The recommended production strategy uses **both** real-time and bulk triggers together as a safety net:

```
  ┌─────────────── REAL-TIME PATH (Primary) ───────────────────┐
  │                                                             │
  │  Gold table INSERT/UPDATE                                   │
  │       │                                                     │
  │       ▼                                                     │
  │  Supabase Webhook → /webhooks/supabase                      │
  │       │                                                     │
  │       ▼                                                     │
  │  triggers.py: source_schema == "gold"                       │
  │       │                                                     │
  │       ▼  (with 30s debounce)                                │
  │  neo4j_batch_sync_flow(layer based on table)                │
  │       │                                                     │
  │       ▼                                                     │
  │  Neo4j updated within ~30-60 seconds                        │
  └─────────────────────────────────────────────────────────────┘

  ┌─────────────── BULK PATH (Safety Net) ─────────────────────┐
  │                                                             │
  │  Hourly Cron (0 * * * *)                                    │
  │       │                                                     │
  │       ▼                                                     │
  │  neo4j_batch_sync_flow(layer="all")                         │
  │       │                                                     │
  │       ▼                                                     │
  │  Catches anything the real-time path missed                 │
  │  (uses checkpoint-based incremental sync)                   │
  └─────────────────────────────────────────────────────────────┘

  ┌─────────────── RECONCILIATION (Audit) ─────────────────────┐
  │                                                             │
  │  Daily Cron (30 3 * * *)                                    │
  │       │                                                     │
  │       ▼                                                     │
  │  neo4j_reconciliation_flow()                                │
  │       │                                                     │
  │       ▼                                                     │
  │  Compares Gold vs Neo4j — detects drift                     │
  │  Proposes LLM-guided backfills if needed                    │
  └─────────────────────────────────────────────────────────────┘
```

### Why This Works

| Concern | Solution |
|---|---|
| **Real-time changes** | Supabase webhook fires within seconds |
| **Batch loads (500+ rows)** | Debouncer collects events, fires once |
| **Missed webhooks** | Hourly cron catches gaps |
| **Data drift** | Daily reconciliation detects & backfills |
| **Webhook failures** | DLQ auto-retries (1min → 5min → 30min) |
| **Neo4j downtime** | Batch sync is idempotent, replays safely |

---

## 6. Code Gaps & Implementation Checklist

### 🔴 P0 — Must Fix Before Triggers Work

| # | Gap | File | Fix |
|---|---|---|---|
| 1 | `list_active_event_triggers()` missing | [db.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/db.py) | Add Supabase query function |
| 2 | `list_active_schedules()` missing | [db.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/db.py) | Add Supabase query function |
| 3 | No debounce on Gold webhooks | [triggers.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/triggers.py) | Add debounce mechanism |
| 4 | Supabase webhooks not configured | Supabase Dashboard | Create 4 webhooks on gold.* tables |
| 5 | `WEBHOOK_SECRET` not set | `.env` | Configure shared secret |

### 🟡 P1 — Needed for Production

| # | Gap | File | Fix |
|---|---|---|---|
| 6 | Cron scheduler not implemented | [api.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/api.py) | Add APScheduler at startup |
| 7 | No per-layer Neo4j webhook routing | [triggers.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/triggers.py) | Route table→specific layer |
| 8 | Seed event_triggers for Gold tables | SQL schema | INSERT trigger rules |
| 9 | `apscheduler` not in dependencies | `pyproject.toml` | Add `apscheduler>=3.10` |

### 🟢 P2 — Nice to Have

| # | Gap | File | Fix |
|---|---|---|---|
| 10 | File upload trigger | triggers.py | New handler for Storage events |
| 11 | Webhook signature verification (HMAC) | api.py | Replace simple secret with HMAC |
| 12 | Trigger metrics dashboard | dashboard | Add trigger-type breakdown |

### Code Fix: Missing DB Functions

```python
# Add to db.py

def list_active_event_triggers() -> list:
    """Return all active event trigger definitions."""
    result = (
        _orch_table("event_triggers")
        .select("*")
        .eq("is_active", True)
        .execute()
    )
    return result.data or []


def list_active_schedules() -> list:
    """Return all active schedule definitions."""
    result = (
        _orch_table("schedule_definitions")
        .select("*")
        .eq("is_active", True)
        .execute()
    )
    return result.data or []
```

---

## 7. Database Schema for Triggers

The orchestration schema already has all the tables needed. Here's how they relate to triggers:

```
  ┌───────────────────────────────┐    ┌──────────────────────────────────┐
  │  event_triggers               │    │  schedule_definitions            │
  │  ─────────────────            │    │  ─────────────────               │
  │  trigger_name (unique)        │    │  schedule_name (unique)          │
  │  event_type (varchar)         │    │  cron_expression (varchar)       │
  │  source_schema (varchar)      │    │  flow_name (varchar)             │
  │  source_table (varchar)       │    │  run_config (jsonb)              │
  │  flow_name (varchar)          │    │  is_active (bool)                │
  │  filter_config (jsonb)        │    │  last_run_at / next_run_at       │
  │  debounce_seconds (int)       │    └───────────────┬──────────────────┘
  │  is_active (bool)             │                    │
  └───────────────┬───────────────┘                    │
                  │                                    │
                  │           triggers.py               │
                  │     ┌────────────────────┐          │
                  └────►│ handle_webhook_    │◄─────────┘
                        │ event()            │
                        │                    │
                        │ register_          │
                        │ schedules()        │
                        └─────────┬──────────┘
                                  │
                                  ▼
                  ┌────────────────────────────────┐
                  │  orchestration_runs             │
                  │  (one row per trigger event)    │
                  │                                 │
                  │  trigger_type: webhook/         │
                  │    scheduled/api/event           │
                  │  triggered_by: gold.dim_recipes  │
                  └────────────────────────────────┘
```

### Supported `trigger_type` Values

| Value | When Used |
|---|---|
| `manual` | CLI or direct function call |
| `api` | `POST /api/trigger` or `POST /api/trigger-batch` |
| `webhook` | Supabase DB webhook via `POST /webhooks/supabase` |
| `scheduled` | Cron schedule fires |
| `event` | Real-time database event (outbox poller) |
| `upstream_complete` | Previous layer finished (internal chaining) |
| `retry` | Retrying a failed run |

### Supported `event_type` Values (for `event_triggers` table)

| Value | Source |
|---|---|
| `supabase_insert` | Supabase DB webhook — INSERT |
| `supabase_update` | Supabase DB webhook — UPDATE |
| `supabase_delete` | Supabase DB webhook — DELETE |
| `webhook` | Generic webhook |
| `api_call` | External API call |
| `file_upload` | Supabase Storage event |
| `upstream_complete` | Pipeline chaining |

---

## 8. End-to-End Implementation Plan

### Phase 1: Fix Core Gaps (Day 1)

```
  1. Add list_active_event_triggers() to db.py        (~10 lines)
  2. Add list_active_schedules() to db.py              (~10 lines)
  3. Set WEBHOOK_SECRET in .env                        (1 line)
  4. Test webhook endpoint with curl mock payload      (manual)
```

### Phase 2: Configure Supabase Webhooks (Day 1-2)

```
  1. Deploy orchestrator to Coolify (public URL)
  2. Create 4 webhooks in Supabase Dashboard:
     - gold.dim_recipes     → INSERT, UPDATE
     - gold.dim_ingredients → INSERT, UPDATE
     - gold.dim_products    → INSERT, UPDATE
     - gold.dim_customers   → INSERT, UPDATE
  3. Set x-webhook-secret header in each webhook
  4. Seed event_triggers table with routing rules
  5. Test: INSERT a row into gold.dim_recipes,
     verify neo4j_batch_sync_flow fires
```

### Phase 3: Add Debounce + Scheduler (Day 2-3)

```
  1. Add debounce mechanism to triggers.py
  2. Install apscheduler: pip install apscheduler
  3. Add scheduler startup to api.py
  4. Test cron triggers fire correctly
```

### Phase 4: Production Validation (Day 3-5)

```
  1. Run a full_ingestion_flow → verify Gold webhook fires → Neo4j syncs
  2. Run a multi-source batch → verify parallel execution
  3. Simulate webhook failure → verify DLQ catches & retries
  4. Wait for hourly cron → verify Neo4j sync runs
  5. Wait for daily reconciliation → verify drift detection
```

---

## 9. Configuration Reference

### Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `WEBHOOK_SECRET` | `` | Shared secret for Supabase webhook auth |
| `WEBHOOK_HOST` | `0.0.0.0` | FastAPI bind address |
| `WEBHOOK_PORT` | `8100` | FastAPI port |
| `DLQ_MAX_RETRIES` | `3` | Max retries for failed webhooks |
| `DLQ_RETRY_DELAYS_MINUTES` | `1,5,30` | Backoff delays between retries |
| `DLQ_POLL_INTERVAL_SECONDS` | `30` | How often DLQ worker checks for retries |
| `PARALLEL_MAX_CONCURRENCY` | `2` | Max parallel sources in batch mode |
| `NEO4J_URI` | `` | Neo4j connection URI |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | `` | Neo4j password |
| `NEO4J_REALTIME_POLL_INTERVAL` | `5` | Customer realtime poller interval (secs) |

### API Endpoints for Triggers

| Method | Endpoint | Trigger Type | Purpose |
|---|---|---|---|
| `POST` | `/api/trigger` | Manual/API | Trigger a single flow |
| `POST` | `/api/trigger-batch` | Bulk API | Trigger parallel multi-source |
| `POST` | `/webhooks/supabase` | Webhook (real-time) | Receive Supabase DB events |
| `POST` | `/api/neo4j/sync` | Manual/API | Direct Gold→Neo4j sync |
| `POST` | `/api/runs/{id}/retry` | Retry | Re-run a failed run |
| `GET` | `/api/neo4j/sync-status` | — | Check latest sync status |
| `GET` | `/api/neo4j/reconciliation` | — | Check reconciliation results |

### Flow Registry

| Flow Name | Trigger Types | Layers Run |
|---|---|---|
| `full_ingestion` | api, webhook, scheduled | PreBronze → Bronze → Silver → Gold → Neo4j |
| `bronze_to_gold` | api, webhook | Bronze → Silver → Gold |
| `single_layer` | api, webhook | Any single layer |
| `realtime_event` | webhook | Determined by source schema |
| `neo4j_batch_sync` | api, webhook, scheduled | Gold → Neo4j (batch) |
| `neo4j_reconciliation` | scheduled | Gold ↔ Neo4j (drift check) |
| `neo4j_realtime_worker` | api, event | Gold → Neo4j (outbox poller) |
| `multi_source_ingestion` | api | Parallel full_ingestion × N sources |
