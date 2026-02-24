# End-to-End Pipeline Walkthrough

> **Audience:** Anyone who needs to understand how this orchestration pipeline works — from first HTTP request to final Neo4j sync. No prior knowledge assumed.
>
> **Last Updated:** 2026-02-23

---

## Table of Contents

1. [What Is This Pipeline?](#1-what-is-this-pipeline)
2. [The Big Picture](#2-the-big-picture)
3. [How the Pipeline Starts](#3-how-the-pipeline-starts)
4. [Step-by-Step: What Happens After You Hit "Go"](#4-step-by-step-what-happens-after-you-hit-go)
5. [Deep Dive: Each Layer in Detail](#5-deep-dive-each-layer-in-detail)
6. [How the Orchestrator Tracks Everything](#6-how-the-orchestrator-tracks-everything)
7. [How Layers Know to Run (Trigger Mechanism)](#7-how-layers-know-to-run-trigger-mechanism)
8. [Error Handling, Retries, and Recovery](#8-error-handling-retries-and-recovery)
9. [Cancellation](#9-cancellation)
10. [Alerting on Failures](#10-alerting-on-failures)
11. [Parallel / Batch Ingestion](#11-parallel--batch-ingestion)
12. [All Available Flows](#12-all-available-flows)
13. [Configuration and Environment Variables](#13-configuration-and-environment-variables)
14. [Database Schema Overview](#14-database-schema-overview)
15. [File-by-File Module Guide](#15-file-by-file-module-guide)
16. [FAQ](#16-faq)

---

## 1. What Is This Pipeline?

This orchestrator manages a **Medallion Architecture** data pipeline. Raw data (CSV, JSON, API responses) enters the system and gets progressively cleaned, enriched, and structured through 4 layers:

```
Raw Data → [PreBronze→Bronze] → [Bronze→Silver] → [Silver→Gold] → [Gold→Neo4j]
              Layer 1              Layer 2           Layer 3          Layer 4
              "Ingest"           "Standardize"      "Enrich"      "Graph Sync"
```

Each layer is a separate **LangGraph AI pipeline** (a Python program that uses LLMs to intelligently process data). The orchestrator's job is to:

1. **Start** these pipelines in the right order
2. **Track** their progress in a Supabase database
3. **Handle errors** — retry, alert, or gracefully fail
4. **Record metrics** — how many records processed, how long each step took, how much the LLMs cost

The orchestrator does **NOT** process data itself. It coordinates the pipelines that do.

---

## 2. The Big Picture

```
┌──────────────────────────────────────────────────────────────────┐
│                         ORCHESTRATOR                              │
│                                                                   │
│  ┌───────────┐    ┌──────────┐    ┌───────────┐   ┌───────────┐ │
│  │  FastAPI   │───▶│  Flows   │───▶│ Pipelines │──▶│    DB     │ │
│  │  (api.py)  │    │(flows.py)│    │(pipelines │   │  (db.py)  │ │
│  │           │    │          │    │   .py)     │   │           │ │
│  └───────────┘    └──────────┘    └───────────┘   └───────────┘ │
│       ▲                                  │              │        │
│       │                                  ▼              ▼        │
│  HTTP Request              LangGraph Pipelines      Supabase     │
│  (from User,               (separate repos)        (PostgreSQL)  │
│   Dashboard,                                                     │
│   or Webhook)                                                    │
└──────────────────────────────────────────────────────────────────┘
```

### Key Components

| Component | File | One-Line Description |
|-----------|------|---------------------|
| **API Server** | `api.py` | Receives HTTP requests, returns immediately, dispatches work in background |
| **Flows** | `flows.py` | Defines the "recipes" — which layers run in what order |
| **Pipeline Wrappers** | `pipelines.py` | Wraps each LangGraph pipeline with tracking, timeouts, and error handling |
| **Database Layer** | `db.py` | All CRUD operations against Supabase |
| **Configuration** | `config.py` | All environment variables and settings |
| **Contracts** | `contracts.py` | Validates what each pipeline returns |
| **Alerts** | `alerts.py` | Sends Email / GitHub Issue alerts on failures |
| **Logging** | `logging_utils.py` | Step-level logging to the database |
| **Triggers** | `triggers.py` | Webhook event handling and schedule registration |
| **Neo4j Adapter** | `neo4j_adapter.py` | Bridge to the Gold→Neo4j pipeline |

---

## 3. How the Pipeline Starts

There are **3 ways** to start the pipeline:

### 3A. Manual API Trigger (Primary Method) ✅

This is the main way to start a pipeline today.

**Endpoint:** `POST /api/trigger`

**Example Request:**

```bash
curl -X POST http://localhost:8100/api/trigger \
  -H "Content-Type: application/json" \
  -d '{
    "flow_name": "full_ingestion",
    "source_name": "usda",
    "input_path": "/data/usda_products.csv",
    "batch_size": 100,
    "incremental": true,
    "dry_run": false
  }'
```

**Example Response (returned immediately, within milliseconds):**

```json
{
  "status": "accepted",
  "run_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "flow_name": "full_ingestion"
}
```

The API returns **immediately** — it does NOT wait for the pipeline to finish. The pipeline runs in the background. You use the `run_id` to check progress later.

**What can you send in the request?**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `flow_name` | string | Yes | Which flow to run (e.g., `"full_ingestion"`, `"bronze_to_gold"`, `"single_layer"`) |
| `source_name` | string | No | The data source name (e.g., `"usda"`, `"walmart"`) |
| `input_path` | string | No | Path to a CSV/JSON file to ingest |
| `storage_bucket` | string | No | Supabase Storage bucket name (alternative to `input_path`) |
| `storage_path` | string | No | Path within the storage bucket |
| `vendor_id` | string | No | Optional vendor identifier |
| `layers` | list | No | For `single_layer` flow — which layer to run |
| `batch_size` | int | No | Records per batch (default: 100) |
| `incremental` | bool | No | Process only new records? (default: true) |
| `dry_run` | bool | No | Simulate without writing? (default: false) |

### 3B. Batch API Trigger (Multiple Sources in Parallel)

**Endpoint:** `POST /api/trigger-batch`

Triggers the **same pipeline for multiple data sources at once**, running them in parallel with a configurable concurrency limit.

```bash
curl -X POST http://localhost:8100/api/trigger-batch \
  -H "Content-Type: application/json" \
  -d '{
    "sources": [
      {"source_name": "usda", "input_path": "/data/usda.csv"},
      {"source_name": "walmart", "input_path": "/data/walmart.csv"},
      {"source_name": "kroger", "input_path": "/data/kroger.csv"}
    ],
    "batch_size": 100,
    "incremental": true
  }'
```

This returns a `batch_id` and individual `run_id` for each source. By default, 2 sources run at a time (configurable via `PARALLEL_MAX_CONCURRENCY` env var).

### 3C. CLI Trigger (Command Line)

```bash
python -m orchestrator.cli run full_ingestion \
  --source usda \
  --input-path /data/usda.csv \
  --batch-size 100
```

### 3D. Supabase Webhook (Event-Driven)

**Endpoint:** `POST /api/webhook/supabase`

When configured, Supabase sends an HTTP POST to this endpoint whenever a row changes in a watched table. The orchestrator then decides which pipeline to trigger based on the event.

### 3E. Dashboard (UI)

The Next.js dashboard has a "Trigger Run" button that calls `POST /api/trigger` behind the scenes.

---

## 4. Step-by-Step: What Happens After You Hit "Go"

Here is every single thing that happens when you send `POST /api/trigger` with `flow_name: "full_ingestion"`:

### Phase 1: API Receives the Request (api.py)

```
Step 1.1  →  FastAPI receives POST /api/trigger
Step 1.2  →  Validates the request body against the TriggerRequest Pydantic model
Step 1.3  →  Looks up "full_ingestion" in the FLOW_REGISTRY dictionary
              (this is a dict mapping flow names → flow functions)
Step 1.4  →  Determines the layers: ["prebronze_to_bronze", "bronze_to_silver",
              "silver_to_gold", "gold_to_neo4j"]
Step 1.5  →  Creates an orchestration_run record in Supabase DB:
              {
                id: "uuid-auto-generated",
                flow_name: "full_ingestion",
                status: "pending",
                trigger_type: "api",
                triggered_by: "api:/api/trigger",
                layers: ["prebronze_to_bronze", "bronze_to_silver", ...],
                source_name: "usda",
                started_at: "2026-02-23T23:01:20Z"
              }
Step 1.6  →  Returns {"status": "accepted", "run_id": "..."} to the caller
Step 1.7  →  Dispatches full_ingestion_flow() to a BACKGROUND THREAD
              (the API thread is now free to handle more requests)
```

> **Key Insight:** The API is "fire-and-forget." It creates a tracking record, starts the work in the background, and returns immediately. The caller polls `GET /api/runs/{run_id}` to check progress.

### Phase 2: Flow Starts (flows.py)

```
Step 2.1  →  full_ingestion_flow() begins executing in the background thread
Step 2.2  →  Concurrency Guard: Checks if full_ingestion is already running
              for this source_name. If yes → raises ConcurrentRunError
              (prevents two pipelines from processing the same source
              simultaneously)
Step 2.3  →  Input Validation: Validates source_name and input using
              IngestionInput Pydantic model
Step 2.4  →  Loads input data:
              - If raw_input was provided → uses it directly
              - If input_path was provided → reads the file (CSV, JSON, NDJSON)
              - If storage:// URI → downloads from Supabase Storage
Step 2.5  →  Reuses the orchestration_run created by the API (via orch_run_id)
              (does NOT create a duplicate row)
Step 2.6  →  Initializes tracking variables:
              results = {}, layer_timings = {}, total_written = 0, errors = 0
Step 2.7  →  Begins sequential layer execution...
```

### Phase 3: Layer 1 — PreBronze → Bronze (pipelines.py)

```
Step 3.1  →  flow calls run_prebronze_to_bronze() as a Prefect task
Step 3.2  →  CANCELLATION CHECK: Reads the orchestration_run from DB.
              If status == "cancelled" → raises FlowCancelledError (stops here)
Step 3.3  →  Creates a pipeline_run record in DB:
              {
                id: "pipeline-run-uuid",
                pipeline_name: "prebronze_to_bronze",
                orchestration_run_id: "parent-orch-uuid",
                trigger_type: "manual",
                status: "running",
                started_at: "2026-02-23T23:01:21Z"
              }
Step 3.4  →  Updates orchestration_run.current_layer = "prebronze_to_bronze"
Step 3.5  →  IMPORTS the PreBronze pipeline package:
              from prebronze.orchestrator import run_sequential
              (This is the LangGraph pipeline from the prebronze-to-bronze repo)
Step 3.6  →  Builds the pipeline state:
              {
                "source_name": "usda",
                "ingestion_run_id": "pipeline-run-uuid",
                "raw_input": [... list of dicts ...],
                "batch_size": 100,
                "incremental": true
              }
Step 3.7  →  Checks for a configured TIMEOUT in the pipeline_definitions table
Step 3.8  →  RUNS the LangGraph pipeline: run_sequential(state)
              (this is where actual data processing happens — the LLM reads
              the raw data, validates schemas, maps fields, and writes to
              the "bronze" tables in Supabase)
Step 3.9  →  Pipeline returns a result dict:
              {
                "records_loaded": 847,
                "validation_errors_count": 3,
                "validation_errors": [...],
                "tool_metrics": [...],    ← per-LangGraph-tool performance data
                "llm_usage": {...}        ← token counts and cost
              }
Step 3.10 →  Updates pipeline_run in DB with final metrics:
              status: "completed", records_written: 847, records_failed: 3, ...
Step 3.11 →  Stores tool_metrics and llm_usage in separate DB tables
Step 3.12 →  CONTRACT VALIDATION (lenient): Validates the result dict against
              PreBronzeResult Pydantic model. Logs warnings but does NOT fail
              if extra/missing fields
Step 3.13 →  Records layer timing: layer_timings["prebronze_to_bronze"] = 45.23
Step 3.14 →  Returns result to the flow
```

### Phase 4: Layer 2 — Bronze → Silver (pipelines.py)

```
Step 4.1  →  flow calls run_bronze_to_silver() as a Prefect task
              NOTE: trigger_type = "upstream_complete"
                    triggered_by = "prebronze_to_bronze"
              (this is how the system records that Layer 2 was triggered
              by Layer 1 completing — NOT by a webhook or manual trigger)
Step 4.2  →  CANCELLATION CHECK (same as Layer 1)
Step 4.3  →  Creates a new pipeline_run record for this layer
Step 4.4  →  Updates orchestration_run.current_layer = "bronze_to_silver"
Step 4.5  →  IMPORTS: from bronze_to_silver.auto_orchestrator import transform_all_tables
Step 4.6  →  RUNS: transform_all_tables()
              (This pipeline auto-discovers all Bronze tables that have
              unprocessed data. It reads from bronze.* tables, applies
              standardization rules via LLM, and writes to silver.* tables)
Step 4.7  →  Returns: {"total_processed": 847, "total_written": 830,
              "total_failed": 17, "total_dq_issues": 5}
Step 4.8  →  Updates pipeline_run with metrics
Step 4.9  →  CONTRACT VALIDATION against TransformResult
Step 4.10 →  Records layer timing: layer_timings["bronze_to_silver"] = 62.10
```

### Phase 5: Layer 3 — Silver → Gold (pipelines.py)

```
Step 5.1  →  flow calls run_silver_to_gold()
              trigger_type = "upstream_complete"
              triggered_by = "bronze_to_silver"
Step 5.2  →  CANCELLATION CHECK
Step 5.3  →  Creates pipeline_run record
Step 5.4  →  Updates orchestration_run.current_layer = "silver_to_gold"
Step 5.5  →  IMPORTS: from silver_to_gold.auto_orchestrator import transform_all_tables
Step 5.6  →  RUNS: transform_all_tables()
              (Enriches silver data — adds computed fields, applies business
              rules, generates product embeddings, produces the final "Gold"
              tables that are ready for production use)
Step 5.7  →  Returns: {"total_processed": 830, "total_written": 825,
              "total_failed": 5}
Step 5.8  →  Updates pipeline_run with metrics
Step 5.9  →  CONTRACT VALIDATION against TransformResult
Step 5.10 →  Records layer timing: layer_timings["silver_to_gold"] = 38.50
```

### Phase 6: Layer 4 — Gold → Neo4j (pipelines.py) ⚠️ Best-Effort

```
Step 6.1  →  flow calls run_gold_to_neo4j_batch() INSIDE a try/except
              (if it fails, the overall pipeline is still "completed")
              trigger_type = "upstream_complete"
              triggered_by = "silver_to_gold"
Step 6.2  →  CANCELLATION CHECK
Step 6.3  →  Creates pipeline_run record
Step 6.4  →  Updates orchestration_run.current_layer = "gold_to_neo4j"
Step 6.5  →  Imports Neo4jPipelineAdapter (built into orchestrator)
Step 6.6  →  RUNS: adapter.run_all_layers()
              (Syncs Gold Supabase tables → Neo4j graph database.
              Order: recipes → ingredients → products → customers)
Step 6.7  →  If success: updates pipeline_run as completed
              If failure: logs warning, marks as failed, BUT does NOT
              raise an exception (the overall ingestion still succeeds)
Step 6.8  →  Records layer timing: layer_timings["gold_to_neo4j"] = 15.70
```

> **Why is Layer 4 "best-effort"?** Neo4j sync is important but not critical. If the data is in Gold (Supabase), it's already usable. Neo4j is an optimization for graph queries. The pipeline should not fail just because Neo4j is temporarily down.

### Phase 7: Completion (flows.py)

```
Step 7.1  →  Calculate total duration: 161.53 seconds
Step 7.2  →  Update orchestration_run in DB:
              {
                status: "completed",
                total_records_written: 2502,
                total_dq_issues: 5,
                completed_at: "2026-02-23T23:03:42Z",
                duration_seconds: 161.53
              }
Step 7.3  →  Store instrumentation metadata:
              {
                "layer_timings": {
                  "prebronze_to_bronze": 45.23,
                  "bronze_to_silver": 62.10,
                  "silver_to_gold": 38.50,
                  "gold_to_neo4j": 15.70
                },
                "end_to_end_seconds": 161.53
              }
Step 7.4  →  Send success notification (if alerting is configured)
Step 7.5  →  Return results dict to caller
Step 7.6  →  ✅ DONE. Background thread exits.
```

---

## 5. Deep Dive: Each Layer in Detail

### Layer 1: PreBronze → Bronze

| Attribute | Value |
|-----------|-------|
| **Purpose** | Ingest raw data (CSV, JSON, API) into standardized Bronze tables |
| **Source Code** | `prebronze-to-bronze` repo |
| **Entry Function** | `prebronze.orchestrator.run_sequential(state)` |
| **Input** | `raw_input` (list of dicts) + `source_name` |
| **Output** | Records written to `bronze.*` tables in Supabase |
| **AI Used** | LangGraph agents for schema mapping and validation |
| **Retries** | Configurable via `ORCHESTRATOR_MAX_RETRIES` (default: 3) |
| **Timeout** | Per pipeline_definitions DB table (configurable) |

### Layer 2: Bronze → Silver

| Attribute | Value |
|-----------|-------|
| **Purpose** | Standardize, clean, and normalize Bronze data |
| **Source Code** | `bronze-to-silver` repo |
| **Entry Function** | `bronze_to_silver.auto_orchestrator.transform_all_tables()` |
| **Input** | Auto-discovers `bronze.*` tables with unprocessed data |
| **Output** | Cleaned records in `silver.*` tables |
| **AI Used** | LangGraph agents for data quality checks, deduplication |
| **Key Feature** | Auto-discovery — no need to specify which tables |

### Layer 3: Silver → Gold

| Attribute | Value |
|-----------|-------|
| **Purpose** | Enrich data, apply business rules, produce production-ready tables |
| **Source Code** | `silver-to-gold` repo |
| **Entry Function** | `silver_to_gold.auto_orchestrator.transform_all_tables()` |
| **Input** | Auto-discovers `silver.*` tables with unprocessed data |
| **Output** | Final records in `gold.*` tables |
| **AI Used** | LangGraph agents for enrichment, entity resolution |

### Layer 4: Gold → Neo4j

| Attribute | Value |
|-----------|-------|
| **Purpose** | Sync Gold tables to Neo4j graph database for relationship queries |
| **Source Code** | `Gold-to-Neo4j_with_agentic_checks` repo (adapted via `neo4j_adapter.py`) |
| **Entry Point** | `Neo4jPipelineAdapter.run_all_layers()` |
| **Input** | `gold.*` tables in Supabase |
| **Output** | Nodes and relationships in Neo4j |
| **Sync Order** | recipes → ingredients → products → customers |
| **Best-Effort** | Yes — failure doesn't crash the pipeline |

---

## 6. How the Orchestrator Tracks Everything

The orchestrator maintains a **3-level tracking hierarchy** in Supabase:

```
orchestration_runs (Level 1 — the "job")
├── pipeline_runs (Level 2 — each layer)
│   ├── pipeline_step_logs (Level 3 — each AI tool/node)
│   └── run_dq_summary (DQ metrics per table)
└── metadata (JSONB — timing, instrumentation)
```

### Level 1: `orchestration_runs` (One row per trigger)

Tracks the **overall** pipeline execution.

| Column | Description |
|--------|-------------|
| `id` | UUID primary key |
| `flow_name` | Which flow was triggered (e.g., `"full_ingestion"`) |
| `status` | `pending` → `running` → `completed` / `failed` / `cancelled` |
| `trigger_type` | `"api"`, `"webhook"`, `"scheduled"`, `"manual"` |
| `triggered_by` | Who/what started it (e.g., `"api:/api/trigger"`) |
| `source_name` | Data source (e.g., `"usda"`) |
| `current_layer` | Which layer is currently running |
| `layers` | List of all layers in this run |
| `total_records_written` | Sum across all layers |
| `total_dq_issues` | Sum of data quality issues |
| `total_errors` | Count of layer failures |
| `started_at` | When the run began |
| `completed_at` | When the run ended |
| `duration_seconds` | Total wall-clock time |
| `metadata` | JSONB with layer timings, instrumentation |

### Level 2: `pipeline_runs` (One row per layer)

Tracks each **layer's** execution within the orchestration run.

| Column | Description |
|--------|-------------|
| `id` | UUID primary key |
| `pipeline_name` | `"prebronze_to_bronze"`, `"bronze_to_silver"`, etc. |
| `orchestration_run_id` | Links to the parent orchestration_run |
| `status` | `"running"` → `"completed"` / `"failed"` / `"timed_out"` |
| `records_input` | How many records went in |
| `records_processed` | How many were processed |
| `records_written` | How many were successfully written |
| `records_failed` | How many failed |
| `dq_issues_found` | Data quality issues detected |
| `error_message` | Error text if failed |
| `error_details` | Full traceback (JSONB) |
| `duration_seconds` | Layer execution time |

### Level 3: `pipeline_step_logs` (One row per AI tool invocation)

Tracks individual **tool/node** executions within a LangGraph pipeline.

| Column | Description |
|--------|-------------|
| `pipeline_run_id` | Links to the parent pipeline_run |
| `step_name` | Tool/node name (e.g., `"map_tables"`, `"validate_schema"`) |
| `step_order` | Execution order (1, 2, 3...) |
| `status` | `"pending"` → `"completed"` / `"failed"` |
| `records_in` / `records_out` | Record counts for this step |
| `duration_ms` | How long this tool took (milliseconds) |
| `state_delta` | What the tool changed in the pipeline state |

### Querying Progress

**Check a run's status:**

```bash
curl http://localhost:8100/api/runs/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

**Response:**

```json
{
  "id": "a1b2c3d4-...",
  "flow_name": "full_ingestion",
  "status": "running",
  "current_layer": "bronze_to_silver",
  "total_records_written": 847,
  "started_at": "2026-02-23T23:01:20Z",
  "pipeline_runs": [
    {
      "pipeline_name": "prebronze_to_bronze",
      "status": "completed",
      "records_written": 847,
      "duration_seconds": 45.23
    },
    {
      "pipeline_name": "bronze_to_silver",
      "status": "running",
      "records_written": 0,
      "duration_seconds": null
    }
  ]
}
```

---

## 7. How Layers Know to Run (Trigger Mechanism)

> **This is a critical concept.** Layers do NOT use webhooks, message queues, or inter-process communication to trigger each other. The mechanism is much simpler.

### Answer: Sequential Function Calls

The `full_ingestion_flow()` function in `flows.py` calls each layer **one after another in a simple sequential loop**:

```python
# Simplified version of what full_ingestion_flow does:

def full_ingestion_flow(source_name, raw_input, ...):
    # Layer 1
    bronze_result = run_prebronze_to_bronze(raw_input=raw_input, ...)
    
    # Layer 2 (only runs AFTER Layer 1 returns)
    silver_result = run_bronze_to_silver(...)
    
    # Layer 3 (only runs AFTER Layer 2 returns)
    gold_result = run_silver_to_gold(...)
    
    # Layer 4 (only runs AFTER Layer 3 returns)
    neo4j_result = run_gold_to_neo4j_batch(...)
```

That's it. **There's no asynchronous event bus, no webhook-between-layers, no message queue.** Each layer is a synchronous Python function call. When Layer 1 finishes, its function returns, and the next line of code calls Layer 2.

### How the System Records "Who Triggered Whom"

Even though layers are called sequentially, the system uses the `trigger_type` and `triggered_by` fields to record the provenance:

```
Layer 1 (PreBronze→Bronze):
  trigger_type: "api" (or "manual")
  triggered_by: "api:/api/trigger"

Layer 2 (Bronze→Silver):
  trigger_type: "upstream_complete"    ← "my upstream layer finished"
  triggered_by: "prebronze_to_bronze"  ← "this is who triggered me"

Layer 3 (Silver→Gold):
  trigger_type: "upstream_complete"
  triggered_by: "bronze_to_silver"

Layer 4 (Gold→Neo4j):
  trigger_type: "upstream_complete"
  triggered_by: "silver_to_gold"
```

This creates an audit trail showing the chain of causation, even though the actual mechanism is just sequential function calls.

### What About Layer 2 Reading From Layer 1's Output?

Layer 2 does **NOT** receive Layer 1's output as a function parameter. Instead:

- Layer 1 **writes data to Supabase Bronze tables** (e.g., `bronze.raw_products`, `bronze.raw_recipes`)
- Layer 2 **queries those same Bronze tables** for unprocessed records (auto-discovery)

So the "handoff" between layers is **the Supabase database itself**. Layer 1 writes, Layer 2 reads.

```
Layer 1 writes  →  [Supabase bronze.* tables]  ←  Layer 2 reads from
```

This applies to every layer transition:

- Layer 1 writes to `bronze.*` → Layer 2 reads from `bronze.*`
- Layer 2 writes to `silver.*` → Layer 3 reads from `silver.*`
- Layer 3 writes to `gold.*` → Layer 4 reads from `gold.*`

### Why Not Use Webhooks Between Layers?

Webhooks (or message queues) between layers would add:

- Complexity (need a message broker)
- Fragility (what if the webhook fails?)
- Latency (network round-trips)
- State management headaches (tracking which message triggered which layer)

Since all layers run in the **same Python process**, simple function calls are far more reliable and simpler to debug.

---

## 8. Error Handling, Retries, and Recovery

### What Happens When a Layer Fails?

```
                 Layer 1         Layer 2         Layer 3       Layer 4
                   ✅      →       ❌       →     (skipped)    (skipped)
                   │                │
                   │                └→ pipeline_run.status = "failed"
                   │                   pipeline_run.error_message = "..."
                   │                   pipeline_run.error_details = {traceback}
                   │
                   └→ orchestration_run.status = "failed"
                      orchestration_run.total_errors = 1
```

If **any layer fails** (except Layer 4 which is best-effort):

1. The pipeline_run for that layer is marked `"failed"` with the error details
2. The overall orchestration_run is marked `"failed"`
3. Subsequent layers do NOT run
4. An alert is dispatched (if configured)

### Automatic Retries (Prefect)

Each layer wrapper is decorated with Prefect's `@task` with retry settings:

```python
@task(
    retries=3,              # Retry up to 3 times (from ORCHESTRATOR_MAX_RETRIES)
    retry_delay_seconds=60, # Wait 60 seconds between retries (from ORCHESTRATOR_RETRY_DELAY_SECONDS)
)
def run_prebronze_to_bronze(...):
```

So if Layer 1 fails, Prefect will automatically retry it up to 3 times with a 60-second delay. Only after all retries are exhausted does the failure propagate to the flow level.

### Timeout Protection

Each layer can have a configurable timeout stored in the `pipeline_definitions` DB table:

```
Pipeline: prebronze_to_bronze  →  timeout_seconds: 600 (10 minutes)
Pipeline: bronze_to_silver     →  timeout_seconds: 900 (15 minutes)
```

If a layer exceeds its timeout:

1. A `PipelineTimeoutError` is raised
2. The pipeline_run is marked `"timed_out"`
3. The flow fails (same as any other error)

### Manual Resume / Retry

If a run fails, you can **resume from the last completed layer**:

```bash
curl -X POST http://localhost:8100/api/runs/a1b2c3d4.../retry
```

This:

1. Checks which layers completed successfully (e.g., Layer 1 completed, Layer 2 failed)
2. Creates a **new** orchestration_run
3. Runs `full_ingestion_flow` with `resume_from_run_id` pointing to the failed run
4. The flow skips completed layers and starts from where it failed

```
Original Run:  Layer 1 ✅ → Layer 2 ❌ (failed)
Retry Run:     Layer 1 ⏭️ (skipped) → Layer 2 🔄 (retrying) → Layer 3 → Layer 4
```

---

## 9. Cancellation

### How to Cancel a Running Pipeline

```bash
curl -X POST http://localhost:8100/api/runs/a1b2c3d4.../cancel
```

### How It Works (Cooperative Cancellation)

The orchestrator uses **cooperative cancellation via DB polling** — there is no "force kill" mechanism.

1. User calls `POST /api/runs/{id}/cancel`
2. API sets `orchestration_run.status = "cancelled"` in the database
3. Before each layer starts, the pipeline wrapper calls `_check_cancellation()`:

   ```python
   def _check_cancellation(orchestration_run_id):
       run = db.get_orchestration_run(orchestration_run_id)
       if run and run.get("status") == "cancelled":
           raise FlowCancelledError(orchestration_run_id)
   ```

4. When the current layer finishes, the next layer's cancellation check sees `"cancelled"` status and raises `FlowCancelledError`
5. The flow stops gracefully

> **Limitation:** If a layer is in the middle of processing (e.g., an LLM call that takes 5 minutes), cancellation won't take effect until that layer function returns. It's checked **between** layers, not **within** a layer.

---

## 10. Alerting on Failures

When a layer fails, the orchestrator can send alerts via:

### Email (SMTP)

```python
# Requires these env vars:
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=alerts@your-company.com
SMTP_PASSWORD=app-password
ALERT_EMAIL_FROM=alerts@your-company.com
ALERT_EMAIL_TO=team@your-company.com
```

### GitHub Issues

```python
# Requires these env vars:
GITHUB_TOKEN=ghp_xxxxxxxxxxxx
GITHUB_REPO=your-org/your-repo
```

When a critical failure occurs (e.g., Gold→Neo4j sync fails), the alerting system:

1. Logs the alert to the `orchestration.alert_log` table
2. Sends an HTML email (if SMTP configured)
3. Creates a GitHub Issue with the error details (if GitHub configured)
4. Applies **deduplication** — if the same error occurred in the last 5 minutes, it won't send duplicate alerts

---

## 11. Parallel / Batch Ingestion

For processing multiple data sources simultaneously:

```
POST /api/trigger-batch
├── Source: "usda"     → [Layer1 → Layer2 → Layer3 → Layer4]  ─┐
├── Source: "walmart"  → [Layer1 → Layer2 → Layer3 → Layer4]  ─┤─ concurrency = 2
├── Source: "kroger"   → [       waits in queue...           ] ─┘
```

### How It Works

1. `ParallelSourceRunner` creates an `asyncio.Semaphore(max_concurrency=2)`
2. Each source gets its own:
   - **Job ID** (UUID for tracking)
   - **Work directory** (`/tmp/orchestrator-work/{job-id}/`)
   - **Scoped logger** (log lines prefixed with `[job_id=abc source=usda]`)
3. Sources run in parallel up to the concurrency limit
4. If a source fails, other sources continue — **fire-and-settle** semantics
5. Each source records its own timing, metrics, and error state independently

---

## 12. All Available Flows

The `FLOW_REGISTRY` in `flows.py` contains **8 registered flows**:

| Flow Name | What It Does | Layers |
|-----------|-------------|--------|
| `full_ingestion` | Full pipeline: Raw → Bronze → Silver → Gold → Neo4j | All 4 |
| `bronze_to_gold` | Skip prebronze, start from Bronze | Layers 2-3 |
| `single_layer` | Run just one specific layer | 1 layer |
| `realtime_event` | Handle a single webhook event | Depends on event |
| `neo4j_batch_sync` | Sync Gold tables to Neo4j | Layer 4 only |
| `neo4j_reconciliation` | Detect drift between Gold and Neo4j | Layer 4 variant |
| `neo4j_realtime_worker` | Long-running Neo4j outbox poller | Layer 4 variant |
| `multi_source_ingestion` | Parallel ingestion for multiple sources | All 4 × N sources |

---

## 13. Configuration and Environment Variables

All settings are loaded from `.env` via Pydantic's `BaseSettings`:

### Required

| Variable | Description |
|----------|-------------|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key for DB access |

### Required by Pipelines (Not the Orchestrator Itself)

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | OpenAI API key (used by LangGraph pipelines) |
| `OPENAI_MODEL_NAME` | Model to use (default: `gpt-4o-mini`) |

### Optional — Neo4j

| Variable | Default | Description |
|----------|---------|-------------|
| `NEO4J_URI` | `""` | Neo4j connection URI |
| `NEO4J_USER` | `"neo4j"` | Neo4j username |
| `NEO4J_PASSWORD` | `""` | Neo4j password |

### Optional — Alerting

| Variable | Default | Description |
|----------|---------|-------------|
| `SMTP_HOST` | `""` | SMTP server for email alerts |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` | `""` | SMTP username |
| `SMTP_PASSWORD` | `""` | SMTP password |
| `ALERT_EMAIL_FROM` | `""` | From address |
| `ALERT_EMAIL_TO` | `""` | Comma-separated recipients |
| `GITHUB_TOKEN` | `""` | GitHub PAT for issue creation |
| `GITHUB_REPO` | `""` | GitHub repo (e.g., `org/repo`) |

### Optional — Orchestrator Tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `ORCHESTRATOR_LOG_LEVEL` | `"INFO"` | Python log level |
| `ORCHESTRATOR_DEFAULT_BATCH_SIZE` | `100` | Default records per batch |
| `ORCHESTRATOR_MAX_RETRIES` | `3` | Max retries per layer |
| `ORCHESTRATOR_RETRY_DELAY_SECONDS` | `60` | Delay between retries |
| `PARALLEL_MAX_CONCURRENCY` | `2` | Max parallel sources |
| `WEBHOOK_SECRET` | `""` | Secret for authenticating Supabase webhooks |
| `WEBHOOK_PORT` | `8100` | API server port |

---

## 14. Database Schema Overview

All orchestration data lives in the `orchestration` schema in Supabase. Key tables:

```
orchestration.pipeline_definitions    ← Pipeline configs (timeout, is_active)
orchestration.orchestration_runs      ← One row per triggered run
orchestration.pipeline_runs           ← One row per layer execution
orchestration.pipeline_step_logs      ← One row per LangGraph tool call
orchestration.run_dq_summary          ← Data quality reports per table
orchestration.tool_metrics            ← LangGraph tool performance data
orchestration.llm_usage               ← LLM token counts and costs
orchestration.alert_log               ← History of all alerts sent
orchestration.schedule_definitions    ← Cron job definitions (future)
orchestration.event_trigger_definitions ← Webhook routing rules (future)
orchestration.webhook_dlq             ← Failed webhook events for retry
```

---

## 15. File-by-File Module Guide

| File | Lines | Purpose |
|------|-------|---------|
| `api.py` | 619 | FastAPI app — all HTTP endpoints (trigger, run management, dashboard, webhooks) |
| `flows.py` | 803 | Flow definitions — orchestrates which layers run in what order |
| `pipelines.py` | 744 | Layer wrappers — imports and runs each LangGraph pipeline with tracking |
| `db.py` | 688 | Database CRUD — creates/reads/updates orchestration records in Supabase |
| `config.py` | 89 | Configuration — reads all env vars via Pydantic BaseSettings |
| `models.py` | 325 | Pydantic models — request/response schemas, DB record models |
| `contracts.py` | 69 | Pipeline return value contracts — validates what each pipeline returns |
| `alerts.py` | 379 | Alerting — Email (SMTP) + GitHub Issues dispatch |
| `triggers.py` | 186 | Webhook event routing + schedule registration |
| `logging_utils.py` | 260 | Step-level logging — `StepLogger`, `RunSummariser`, `SourceScopedLogger` |
| `neo4j_adapter.py` | 339 | Neo4j bridge — wraps Gold-to-Neo4j pipeline for orchestrator use |
| `parallel.py` | ~300 | Parallel runner — manages concurrent multi-source ingestion |
| `cli.py` | ~295 | CLI commands — `run`, `serve`, `schedule`, `list-runs` |

---

## 16. FAQ

### Q: Does the orchestrator use Apache Kafka, RabbitMQ, or any message queue?

**No.** All layer-to-layer communication is via sequential function calls within a single Python process. Data handoff happens through Supabase tables.

### Q: Can I run just one layer (e.g., only Bronze→Silver)?

**Yes.** Use the `single_layer` flow:

```bash
curl -X POST http://localhost:8100/api/trigger \
  -d '{"flow_name": "single_layer", "layers": ["bronze_to_silver"]}'
```

### Q: What happens if the server crashes mid-pipeline?

The orchestration_run will remain in `"running"` status forever. There is no automatic recovery. You would need to manually check the DB, determine which layers completed, and either retry the run or create a new one.

### Q: Is the API server stateless?

**Yes.** All state is in Supabase. You can restart the API server at any time without losing data. However, any in-progress pipeline runs will be lost (they run as background threads in the same process).

### Q: Can two pipelines run at the same time for different sources?

**Yes.** The concurrency guard in `full_ingestion_flow` is **per-source-name**. So `usda` and `walmart` can run simultaneously. But two `usda` runs at the same time are blocked.

### Q: Where do LLM costs show up?

If the pipeline teams add `llm_usage` to their return dict, the orchestrator stores it in the `orchestration.llm_usage` table with fields like `total_tokens`, `total_cost_usd`, and `model`.

### Q: Do I need Prefect running?

The codebase uses Prefect's `@flow` and `@task` decorators. For local dev, Prefect runs in "ephemeral mode" (no server needed). For production with scheduled runs, you would need a Prefect server — or use the recommended in-process scheduler alternative.
