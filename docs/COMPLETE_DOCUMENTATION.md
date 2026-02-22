# Data Orchestration Tool — Complete Documentation

> **Date:** February 19, 2026  
> **Project:** ASM Data Orchestration Pipeline  
> **Location:** `Orchestration Pipeline/orchestrator/`

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement & Goals](#2-problem-statement--goals)
3. [Research & Framework Selection](#3-research--framework-selection)
4. [Architecture Overview](#4-architecture-overview)
5. [Data Flow & Medallion Architecture](#5-data-flow--medallion-architecture)
6. [Existing Pipeline Analysis](#6-existing-pipeline-analysis)
7. [Orchestration Database Schema](#7-orchestration-database-schema)
8. [Project Structure & File Inventory](#8-project-structure--file-inventory)
9. [Detailed File Documentation](#9-detailed-file-documentation)
10. [API Reference](#10-api-reference)
11. [CLI Reference](#11-cli-reference)
12. [Configuration Reference](#12-configuration-reference)
13. [Triggering Mechanisms](#13-triggering-mechanisms)
14. [Verification Results](#14-verification-results)
15. [Setup & Deployment Guide](#15-setup--deployment-guide)
16. [Future Enhancements](#16-future-enhancements)

---

## 1. Executive Summary

This project delivers a **production-grade Data Orchestration Tool** that unifies three existing LangGraph data pipelines into a single, schedulable, observable, and triggerable system. The tool uses a **Prefect + LangGraph hybrid architecture** where:

- **Prefect** serves as the **control plane** — handling scheduling, retries, observability, and cascade triggers
- **LangGraph** serves as the **execution engine** — running the actual data transformation logic within each pipeline layer
- **Supabase** serves as the **data store** — hosting the medallion architecture tables (Bronze, Silver, Gold) and a new `orchestration` schema for job tracking
- **FastAPI** serves as the **trigger interface** — receiving Supabase database webhooks and manual API triggers

The tool was built across **14 files** totaling approximately **80KB** of code.

---

## 2. Problem Statement & Goals

### Problem
The existing data pipeline consisted of three independently-developed LangGraph pipelines:
- **PreBronze → Bronze:** Ingests raw CSV/JSON/NDJSON files into Bronze tables
- **Bronze → Silver:** Transforms raw Bronze data into normalized Silver tables
- **Silver → Gold:** Enriches Silver data into denormalized Gold analytics tables

Each pipeline ran in isolation with no unified orchestration, no central logging, no retry logic, no scheduling, and no webhook-based triggering. This made production operations fragile, unobservable, and difficult to maintain.

### Goals
1. **Unified Orchestration:** Run all three pipelines as a single end-to-end flow, or individually
2. **Production-Grade Reliability:** Automatic retries, error tracking, and failure isolation
3. **Full Observability:** Every run, pipeline, and step logged to the database with timing, record counts, and error details
4. **Flexible Triggering:** Manual CLI, REST API, Supabase webhooks, and cron schedules
5. **Data Quality Tracking:** Aggregate DQ metrics per pipeline run and table
6. **Scalability:** Batch and real-time processing modes

---

## 3. Research & Framework Selection

### Frameworks Evaluated

| Framework | Strengths | Weaknesses | Verdict |
|-----------|-----------|------------|---------|
| **Apache Airflow** | Mature, large ecosystem | Heavy setup, DAG-based (poor for dynamic flows), weak AI agent support | ❌ Rejected |
| **Prefect** | Python-native, lightweight, great dev experience, native async/retry, easy deployment | Smaller ecosystem than Airflow | ✅ Selected as Control Plane |
| **Dagster** | Asset-centric, good observability | Opinionated, steeper learning curve | ❌ Rejected |
| **Temporal.io** | Excellent durability & fault tolerance | Complex setup, Java/Go-centric | ❌ Rejected |
| **LangGraph** | Already in use, excellent for agentic AI workflows, dynamic state management | Not designed for scheduling/observability | ✅ Retained as Execution Engine |

### Decision Rationale

**Prefect was selected** because it:
- Integrates seamlessly with Python (no DSL or YAML required)
- Supports `@task` and `@flow` decorators that wrap existing functions cleanly
- Provides built-in retries, scheduling, caching, and observability
- Has a free Prefect Cloud dashboard for monitoring
- Allows calling LangGraph pipelines as sub-tasks without rewriting them

**LangGraph was retained** as the execution engine because all three pipelines are already implemented with it, using `StateGraph` with `TypedDict` state management.

### Triggering Mechanisms Designed

1. **Real-time:** Supabase Database Webhooks → FastAPI endpoint → Event matching → Flow dispatch
2. **Batch:** Cron Schedules from `orchestration.schedule_definitions` → Prefect Deployments
3. **Manual:** CLI (`python -m orchestrator run`) or API (`POST /api/trigger`)
4. **Cascade:** Upstream pipeline completion automatically triggers downstream layers

---

## 4. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                       USER / TRIGGERS                        │
│  CLI  │  REST API  │  Supabase Webhook  │  Cron Schedule    │
└───────┴────────────┴────────────────────┴───────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  PREFECT (Control Plane)                      │
│                                                               │
│  @flow: full_ingestion_flow                                   │
│    ├── @task: run_prebronze_to_bronze()                       │
│    ├── @task: run_bronze_to_silver()                          │
│    └── @task: run_silver_to_gold()                            │
│                                                               │
│  @flow: bronze_to_gold_flow                                   │
│  @flow: single_layer_flow                                     │
│  @flow: realtime_event_flow                                   │
│                                                               │
│  Features: Retries, Scheduling, Observability, Cascade        │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  LANGGRAPH (Execution Engine)                 │
│                                                               │
│  PreBronze→Bronze (7 nodes)                                   │
│    map_tables → infer_schema → translate_values →             │
│    validate_structure → render_csv → load_into_supabase       │
│                                                               │
│  Bronze→Silver (8 nodes)                                      │
│    fetch_bronze → flatten_payloads → tag_entities →           │
│    normalize_records → validate_dq → extract_nutrition →      │
│    load_into_silver                                           │
│                                                               │
│  Silver→Gold (7 nodes)                                        │
│    fetch_silver → resolve_cuisines → transform_data →         │
│    process_ingredients → process_nutrition →                   │
│    load_into_gold → track_lineage                             │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  SUPABASE (Data Store)                        │
│                                                               │
│  orchestration.*   ← Job tracking (7 tables)                 │
│  bronze.*          ← Raw data (5 tables)                     │
│  silver.*          ← Normalized data (~25 tables)            │
│  gold.*            ← Analytics data (40+ tables)             │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. Data Flow & Medallion Architecture

### Layer Descriptions

| Layer | Schema | Tables | Purpose |
|-------|--------|--------|---------|
| **PreBronze** | Raw files | N/A | CSV, JSON, NDJSON files before ingestion |
| **Bronze** | `bronze.*` | 5 tables | Raw data as-ingested: `raw_customer_health_profiles`, `raw_customers`, `raw_ingredients`, `raw_products`, `raw_recipes` |
| **Silver** | `silver.*` | ~25 tables | Normalized, tagged, quality-checked data: `products`, `recipes`, `allergens`, `customers`, `ingredients`, `nutrition_facts`, etc. |
| **Gold** | `gold.*` | 40+ tables | Denormalized analytics-ready data: `customer_full_profiles`, `recipe_analytics`, `product_substitutions`, materialized views |
| **Neo4j** | Graph DB | Future | Knowledge graph (planned, not yet orchestrated) |

### Data Flow

```
Raw Files (CSV/JSON/NDJSON)   
    │  
    ▼  PreBronze → Bronze pipeline (LangGraph, 7 nodes)
    │  Agentic classification, schema inference, column mapping,
    │  translation, LLM validation, CSV rendering, Supabase load
    │
bronze.raw_products, bronze.raw_recipes, etc.
    │
    ▼  Bronze → Silver pipeline (LangGraph, 8 nodes)
    │  Auto-discovery, flattening, entity tagging, normalization,
    │  DQ validation, nutrition extraction, Silver load
    │
silver.products, silver.recipes, silver.allergens, etc.
    │
    ▼  Silver → Gold pipeline (LangGraph, 7 nodes)
    │  Auto-discovery, cuisine resolution, transformation,
    │  ingredient processing, nutrition facts, lineage tracking
    │
gold.recipes, gold.products, gold.customer_full_profiles, etc.
```

---

## 6. Existing Pipeline Analysis

### Pipeline 1: PreBronze → Bronze

**Source:** `prebronze-to-bronze 1/prebronze-to-bronze/prebronze/`  
**Key files:** `orchestrator.py` (176 lines), `state.py` (36 lines)  
**LangGraph nodes (7):**

| Node | Function | Description |
|------|----------|-------------|
| 1 | `map_tables` | Classifies input as one of 5 Bronze table types |
| 2 | `infer_schema_and_map_columns` | Auto-maps source columns to target schema |
| 3 | `translate_values` | Normalizes values (units, formats) |
| 4 | `validate_structure` | LLM-powered structural validation |
| 5 | `validate_data` | Data quality checks |
| 6 | `render_csv` | Renders validated data as CSV |
| 7 | `load_into_supabase` | Loads into Bronze tables via Supabase API |

**State type:** `OrchestratorState(TypedDict)` — fields include `source_name`, `ingestion_run_id`, `file_name`, `raw_input`, `detected_table`, mapped columns, validation errors, etc.  
**Input formats:** CSV, JSON, NDJSON  
**Entry point:** `run_sequential(state)` → runs all 7 nodes in sequence

### Pipeline 2: Bronze → Silver

**Source:** `bronze-to-silver 1/bronze-to-silver/bronze_to_silver/`  
**Key files:** `orchestrator.py` (388 lines), `state.py` (102 lines)  
**LangGraph nodes (8):**

| Node | Function | Description |
|------|----------|-------------|
| 1 | `fetch_bronze_records` | Fetches unprocessed records from Bronze tables |
| 2 | `flatten_payloads` | Flattens nested JSON payloads |
| 3 | `tag_entities` | LLM-powered entity tagging |
| 4 | `normalize_records` | Normalizes to Silver schema |
| 5 | `validate_data_quality` | DQ validation with rules |
| 6 | `extract_nutrition_facts` | Extracts nutrition information |
| 7 | `load_into_silver` | Loads into Silver tables |
| 8 | `post_load_cleanup` | Marks Bronze records as processed |

**State type:** `SilverTransformState(TypedDict)` — includes `bronze_records`, `silver_records`, `dq_issues`, quality scores, etc.  
**Auto-discovery:** Automatically discovers Bronze tables with unprocessed data  
**Entry point:** `transform_all_tables()` (auto-orchestrator mode) or `build_transformation_graph()` (manual mode)

### Pipeline 3: Silver → Gold

**Source:** `silver-to-gold 1/silver-to-gold/silver-to-gold/silver_to_gold/`  
**Key files:** `orchestrator.py` (400 lines), `state.py` (90 lines), `auto_orchestrator.py` (410 lines)  
**LangGraph nodes (7):**

| Node | Function | Description |
|------|----------|-------------|
| 1 | `fetch_silver_records` | Fetches records from Silver tables |
| 2 | `resolve_cuisines` | LLM-based cuisine classification |
| 3 | `transform_data` | Transforms to Gold schema |
| 4 | `process_ingredients` | Processes ingredient relationships |
| 5 | `process_nutrition` | Processes nutrition facts |
| 6 | `load_into_gold` | Loads into Gold tables |
| 7 | `track_lineage` | Records data lineage entries |

**State type:** `GoldTransformState(TypedDict)` — includes `silver_recipes`, `gold_recipes`, `data_lineage_entries`, etc.  
**Auto-discovery:** `SILVER_TO_GOLD_MAP` defines Silver→Gold table mappings, auto-detects schema changes  
**Entry point:** `transform_all_tables()` (auto-orchestrator)

---

## 7. Orchestration Database Schema

**File:** `sql/orchestration_schema.sql` (303 lines, 13KB)  
**Schema name:** `orchestration`  
**Tables:** 7  
**Indexes:** 7

### Table 1: `pipeline_definitions`
Registry of all known pipelines. Pre-seeded with 3 entries.

| Column | Type | Description |
|--------|------|-------------|
| `id` | uuid (PK) | Auto-generated |
| `pipeline_name` | varchar(100) UNIQUE | e.g. `prebronze_to_bronze` |
| `layer_from` | varchar(20) | Source layer (prebronze, bronze, silver, gold) |
| `layer_to` | varchar(20) | Target layer (bronze, silver, gold, neo4j) |
| `description` | text | Human-readable description |
| `repo_url` | varchar(500) | Git repo URL |
| `default_config` | jsonb | Default configuration |
| `is_active` | boolean | Whether pipeline is active |
| `created_at` | timestamptz | Creation timestamp |
| `updated_at` | timestamptz | Last update timestamp |

**Constraints:** `layer_from` must be one of `prebronze, bronze, silver, gold`; `layer_to` must be one of `bronze, silver, gold, neo4j`

**Seed data:**
```sql
INSERT INTO orchestration.pipeline_definitions (pipeline_name, layer_from, layer_to, description)
VALUES
    ('prebronze_to_bronze', 'prebronze', 'bronze', '...'),
    ('bronze_to_silver', 'bronze', 'silver', '...'),
    ('silver_to_gold', 'silver', 'gold', '...')
ON CONFLICT (pipeline_name) DO NOTHING;
```

### Table 2: `orchestration_runs`
Top-level record for a full end-to-end flow execution. One orchestration_run groups multiple pipeline_runs.

| Column | Type | Description |
|--------|------|-------------|
| `id` | uuid (PK) | Auto-generated |
| `flow_name` | varchar(100) | e.g. `full_ingestion`, `bronze_to_gold` |
| `flow_type` | varchar(30) | `batch`, `realtime`, or `hybrid` |
| `status` | varchar(20) | pending/queued/running/completed/failed/partially_completed/cancelled/timed_out |
| `trigger_type` | varchar(30) | manual/scheduled/webhook/event/upstream_complete/api/retry |
| `triggered_by` | varchar(255) | Who/what triggered this run |
| `layers` | text[] | Ordered list of layers to execute |
| `current_layer` | varchar(50) | Currently executing layer |
| `total_records_processed` | int | Aggregate count |
| `total_records_written` | int | Aggregate count |
| `total_dq_issues` | int | Aggregate DQ issue count |
| `total_errors` | int | Aggregate error count |
| `started_at` | timestamptz | When execution started |
| `completed_at` | timestamptz | When execution ended |
| `duration_seconds` | numeric(10,2) | Total duration |
| `config` | jsonb | Runtime configuration |
| `metadata` | jsonb | Flexible metadata |

**Indexes:** `(status, created_at DESC)`, `(flow_name, created_at DESC)`

### Table 3: `pipeline_runs`
One row per individual pipeline execution within an orchestration run.

| Column | Type | Description |
|--------|------|-------------|
| `id` | uuid (PK) | Auto-generated |
| `pipeline_id` | uuid (FK) | References `pipeline_definitions` |
| `orchestration_run_id` | uuid (FK) | References `orchestration_runs` (CASCADE) |
| `run_number` | serial | Auto-incrementing run counter |
| `status` | varchar(20) | pending/queued/running/completed/failed/retrying/cancelled/skipped/timed_out |
| `trigger_type` | varchar(30) | Same as orchestration_runs |
| `triggered_by` | varchar(255) | What triggered this specific pipeline |
| `source_table` | varchar(100) | Source table being processed |
| `target_table` | varchar(100) | Target table being written to |
| `batch_size` | int | Records per batch |
| `incremental` | boolean | Whether using incremental processing |
| `dry_run` | boolean | Whether this is a dry run |
| `run_config` | jsonb | Runtime configuration |
| `records_input` | int | Records received as input |
| `records_processed` | int | Records processed |
| `records_written` | int | Records written to target |
| `records_skipped` | int | Records skipped |
| `records_failed` | int | Records that failed |
| `dq_issues_found` | int | DQ issues detected |
| `started_at` | timestamptz | Execution start |
| `completed_at` | timestamptz | Execution end |
| `duration_seconds` | numeric(10,2) | Duration |
| `error_message` | text | Error message (if failed) |
| `error_details` | jsonb | Error details (traceback, etc.) |
| `retry_count` | int | Number of retries attempted |
| `max_retries` | int | Maximum retries allowed |

**Indexes:** `(status)`, `(orchestration_run_id)`, `(pipeline_id, created_at DESC)`

### Table 4: `pipeline_step_logs`
Per-tool execution log within a pipeline run.

| Column | Type | Description |
|--------|------|-------------|
| `id` | uuid (PK) | Auto-generated |
| `pipeline_run_id` | uuid (FK) | References `pipeline_runs` (CASCADE) |
| `step_name` | varchar(100) | e.g. `map_tables`, `flatten_payloads` |
| `step_order` | int | Execution order within the pipeline |
| `status` | varchar(20) | pending/running/completed/failed/skipped |
| `records_in` | int | Records entering this step |
| `records_out` | int | Records leaving this step |
| `records_error` | int | Records that errored in this step |
| `state_delta` | jsonb | What this step changed in the state dict |
| `started_at` | timestamptz | Step start time |
| `completed_at` | timestamptz | Step end time |
| `duration_ms` | int | Duration in milliseconds |
| `error_message` | text | Error message |
| `error_traceback` | text | Full Python traceback |

**Index:** `(pipeline_run_id, step_order)`

### Table 5: `schedule_definitions`
Cron-based schedule definitions for automated pipeline triggers.

| Column | Type | Description |
|--------|------|-------------|
| `id` | uuid (PK) | Auto-generated |
| `schedule_name` | varchar(100) UNIQUE | e.g. `daily_full_ingestion` |
| `cron_expression` | varchar(100) | Standard cron format, e.g. `0 2 * * *` |
| `flow_name` | varchar(100) | Which flow to trigger |
| `pipeline_id` | uuid (FK) | Optional link to specific pipeline |
| `run_config` | jsonb | Config to pass to the flow |
| `is_active` | boolean | Whether schedule is active |
| `last_run_at` | timestamptz | When this schedule last ran |
| `next_run_at` | timestamptz | When this schedule will next run |

**Seed data:** `daily_full_ingestion` (2 AM daily) and `weekly_usda_nutrition` (3 AM Sundays)

### Table 6: `event_triggers`
Webhook and event-based trigger definitions.

| Column | Type | Description |
|--------|------|-------------|
| `id` | uuid (PK) | Auto-generated |
| `trigger_name` | varchar(100) UNIQUE | Human-readable name |
| `event_type` | varchar(50) | supabase_insert/supabase_update/supabase_delete/webhook/api_call/file_upload/upstream_complete |
| `source_schema` | varchar(50) | Schema to watch (e.g. `bronze`) |
| `source_table` | varchar(100) | Table to watch |
| `flow_name` | varchar(100) | Flow to trigger |
| `pipeline_id` | uuid (FK) | Optional link to specific pipeline |
| `filter_config` | jsonb | Additional filter criteria |
| `debounce_seconds` | int | Debounce window |
| `is_active` | boolean | Whether trigger is active |

### Table 7: `run_dq_summary`
Aggregated data quality metrics per pipeline run and table.

| Column | Type | Description |
|--------|------|-------------|
| `id` | uuid (PK) | Auto-generated |
| `pipeline_run_id` | uuid (FK) | References `pipeline_runs` (CASCADE) |
| `table_name` | varchar(100) | Which table was assessed |
| `total_records` | int | Total records evaluated |
| `pass_count` | int | Records passing DQ checks |
| `fail_count` | int | Records failing DQ checks |
| `avg_quality_score` | numeric(5,2) | Average quality score |
| `issues_by_type` | jsonb | Breakdown of issues by type |

**Index:** `(pipeline_run_id)`

---

## 8. Project Structure & File Inventory

```
orchestrator/                           ← Project root
├── pyproject.toml                      ← Python project config & dependencies (1.1KB)
├── .env.example                        ← Environment variable template (1.3KB)
├── README.md                           ← Quick start guide (3.2KB)
├── docs/
│   └── COMPLETE_DOCUMENTATION.md       ← This file
├── sql/
│   └── orchestration_schema.sql        ← 7 tables, 7 indexes (13KB, 303 lines)
└── orchestrator/                       ← Python package
    ├── __init__.py                     ← Package init, version (346B)
    ├── __main__.py                     ← python -m orchestrator entry (98B)
    ├── config.py                       ← Pydantic Settings (1.5KB, 50 lines)
    ├── models.py                       ← 18 Pydantic models + enums (7.9KB, 240 lines)
    ├── db.py                           ← Supabase CRUD operations (12.8KB, 305 lines)
    ├── logging_utils.py                ← Step-level DB logging (7.4KB, 210 lines)
    ├── pipelines.py                    ← 3 Prefect @task wrappers (12.1KB, 321 lines)
    ├── flows.py                        ← 4 Prefect @flow definitions (14KB, 403 lines)
    ├── triggers.py                     ← Schedule + webhook dispatch (5.9KB, 152 lines)
    ├── api.py                          ← FastAPI server (6.9KB, 197 lines)
    └── cli.py                          ← CLI with 4 commands (6.8KB, 178 lines)
```

**Total lines of Python:** ~2,106 lines  
**Total lines of SQL:** 303 lines  
**Total size:** ~80KB

---

## 9. Detailed File Documentation

### 9.1 `pyproject.toml`

Python project configuration using modern `[project]` format (PEP 621).

**Core dependencies:**
- `prefect>=3.0` — Orchestration control plane
- `supabase>=2.0` — Supabase Python client
- `pydantic>=2.0` — Data validation and serialization
- `pydantic-settings>=2.0` — Environment variable configuration
- `fastapi>=0.110` — Webhook API server
- `uvicorn>=0.27` — ASGI server
- `python-dotenv>=1.0` — .env file loading

**Optional dependency groups:**
- `[dev]`: pytest, pytest-asyncio, black, ruff
- `[pipelines]`: langgraph>=0.2, langchain>=0.1, langchain-openai>=0.0.5, pandas>=2.0

**CLI entry point:** `orchestrator = "orchestrator.cli:main"`

---

### 9.2 `.env.example`

Template for environment variables. Copy to `.env` and fill in values.

```
# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key

# OpenAI (for LLM-augmented pipeline steps)
OPENAI_API_KEY=your-openai-api-key
OPENAI_MODEL_NAME=gpt-4o-mini

# Orchestrator Settings
ORCHESTRATOR_LOG_LEVEL=INFO
ORCHESTRATOR_DEFAULT_BATCH_SIZE=100
ORCHESTRATOR_MAX_RETRIES=3
ORCHESTRATOR_RETRY_DELAY_SECONDS=60

# Webhook Server
WEBHOOK_HOST=0.0.0.0
WEBHOOK_PORT=8100
WEBHOOK_SECRET=your-webhook-secret
```

---

### 9.3 `orchestrator/__init__.py`

Package initialization. Sets `__version__ = "0.1.0"` and provides a brief module docstring.

---

### 9.4 `orchestrator/__main__.py`

Module entry point allowing `python -m orchestrator` usage. Imports and calls `main()` from `cli.py`.

---

### 9.5 `orchestrator/config.py` (50 lines)

**Purpose:** Centralized configuration using Pydantic Settings.

**Class:** `OrchestratorSettings(BaseSettings)`

| Setting | Type | Default | Env Variable |
|---------|------|---------|-------------|
| `supabase_url` | str | `""` | `SUPABASE_URL` |
| `supabase_service_role_key` | str | `""` | `SUPABASE_SERVICE_ROLE_KEY` |
| `openai_api_key` | Optional[str] | None | `OPENAI_API_KEY` |
| `openai_model_name` | str | `"gpt-4o-mini"` | `OPENAI_MODEL_NAME` |
| `openai_base_url` | Optional[str] | None | `OPENAI_BASE_URL` |
| `orchestrator_log_level` | str | `"INFO"` | `ORCHESTRATOR_LOG_LEVEL` |
| `orchestrator_default_batch_size` | int | `100` | `ORCHESTRATOR_DEFAULT_BATCH_SIZE` |
| `orchestrator_max_retries` | int | `3` | `ORCHESTRATOR_MAX_RETRIES` |
| `orchestrator_retry_delay_seconds` | int | `60` | `ORCHESTRATOR_RETRY_DELAY_SECONDS` |
| `webhook_host` | str | `"0.0.0.0"` | `WEBHOOK_HOST` |
| `webhook_port` | int | `8100` | `WEBHOOK_PORT` |
| `webhook_secret` | str | `""` | `WEBHOOK_SECRET` |

**Singleton:** `settings = OrchestratorSettings()` — importable from anywhere.

---

### 9.6 `orchestrator/models.py` (240 lines)

**Purpose:** All Pydantic models matching the orchestration schema.

**Enums (5):**

| Enum | Values |
|------|--------|
| `RunStatus` | pending, queued, running, completed, failed, retrying, cancelled, skipped, timed_out, partially_completed |
| `TriggerType` | manual, scheduled, webhook, event, upstream_complete, api, retry |
| `FlowType` | batch, realtime, hybrid |
| `StepStatus` | pending, running, completed, failed, skipped |
| `EventType` | supabase_insert, supabase_update, supabase_delete, webhook, api_call, file_upload, upstream_complete |

**Models (13):**

| Model | Purpose | Fields |
|-------|---------|--------|
| `PipelineDefinition` | Pipeline registry | id, pipeline_name, layer_from, layer_to, description, repo_url, default_config, is_active |
| `OrchestrationRunCreate` | Create payload for orchestration runs | flow_name, flow_type, trigger_type, triggered_by, layers, config, metadata |
| `OrchestrationRun` | Full orchestration run with metrics | Extends `OrchestrationRunCreate` + id, status, current_layer, totals, timing |
| `PipelineRunCreate` | Create payload for pipeline runs | pipeline_name, orchestration_run_id, trigger_type, source/target_table, batch_size, incremental, dry_run |
| `PipelineRun` | Full pipeline run with metrics | Extends `PipelineRunCreate` + id, run_number, status, record counts, timing, errors, retries |
| `StepLogCreate` | Create payload for step logs | pipeline_run_id, step_name, step_order |
| `StepLog` | Full step log | Extends `StepLogCreate` + id, status, record counts, state_delta, timing, errors |
| `ScheduleDefinition` | Cron schedule | id, schedule_name, cron_expression, flow_name, run_config, is_active |
| `EventTrigger` | Webhook event trigger | id, trigger_name, event_type, source_schema/table, flow_name, filter_config, debounce_seconds |
| `RunDQSummary` | Data quality summary | id, pipeline_run_id, table_name, pass/fail counts, avg_quality_score, issues_by_type |
| `TriggerRequest` | API trigger request | flow_name, source_name, input_path, layers, batch_size, incremental, dry_run |
| `RunSummaryResponse` | Lightweight run listing | id, flow_name, status, trigger_type, total_records_written, started_at, duration |

---

### 9.7 `orchestrator/db.py` (305 lines)

**Purpose:** Supabase CRUD operations for the orchestration schema.

**Client Management:**
- `get_supabase_client()` — Returns a cached singleton Supabase client using `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`
- `_orch_table(table_name)` — Shortcut for querying tables in the `orchestration` schema

**CRUD Functions:**

| Function | Operation | Table |
|----------|-----------|-------|
| `get_pipeline_definition(pipeline_name)` | SELECT one | pipeline_definitions |
| `list_pipeline_definitions(active_only)` | SELECT many | pipeline_definitions |
| `create_orchestration_run(...)` | INSERT | orchestration_runs |
| `update_orchestration_run(run_id, **fields)` | UPDATE | orchestration_runs |
| `get_orchestration_run(run_id)` | SELECT one | orchestration_runs |
| `list_orchestration_runs(limit, status)` | SELECT many | orchestration_runs |
| `create_pipeline_run(...)` | INSERT | pipeline_runs |
| `update_pipeline_run(run_id, **fields)` | UPDATE | pipeline_runs |
| `create_step_log(...)` | INSERT | pipeline_step_logs |
| `update_step_log(step_id, **fields)` | UPDATE | pipeline_step_logs |
| `create_dq_summary(...)` | INSERT | run_dq_summary |
| `list_active_schedules()` | SELECT many | schedule_definitions |
| `list_active_event_triggers()` | SELECT many | event_triggers |

**Key behaviors:**
- `create_pipeline_run()` automatically looks up the `pipeline_id` from `pipeline_definitions` by name
- `update_step_log()` automatically sets `completed_at` when status is `"completed"` or `"failed"`
- All timestamps use UTC with `datetime.now(timezone.utc).isoformat()`

---

### 9.8 `orchestrator/logging_utils.py` (210 lines)

**Purpose:** Step-level logging that writes to `orchestration.pipeline_step_logs` in real-time during pipeline execution.

**Classes:**

**`StepLogger`** — Context-manager based step logger.

```python
step_logger = StepLogger(pipeline_run_id="...")

with step_logger.step("map_tables", order=1) as step:
    result = map_tables(state)
    step.set_records(records_in=100, records_out=95)

summary = step_logger.summarise()
```

- Creates a `pipeline_step_logs` row when entering the context
- Updates status to `"completed"` with timing on success
- Updates status to `"failed"` with error message and traceback on exception
- Emits structured log messages with emoji indicators (▶/✅/❌)
- `summarise()` returns aggregate metrics across all steps

**`_StepInfo`** — Mutable container yielded from the context manager. Provides:
- `set_records(records_in, records_out, records_error)` — Set record counts
- `set_state_delta(delta)` — Store what this tool changed in the state dict
- `to_dict()` — Serialize for aggregation

**`RunSummariser`** — Static helper to extract DQ metrics from a pipeline's final state and write a `run_dq_summary` record.

---

### 9.9 `orchestrator/pipelines.py` (321 lines)

**Purpose:** Prefect `@task`-decorated functions wrapping each LangGraph pipeline.

**Helper functions:**
- `_ensure_pipeline_on_path(pipeline_dir_name)` — Adds sibling pipeline repos to `sys.path` so their modules can be imported. Expects the directory structure:
  ```
  Orchestration Pipeline/
  ├── orchestrator/              ← this package
  ├── prebronze-to-bronze 1/     ← pipeline repo
  ├── bronze-to-silver 1/        ← pipeline repo
  └── silver-to-gold 1/          ← pipeline repo
  ```
- `_calculate_duration(start)` — Returns elapsed seconds

**Tasks (3):**

**`run_prebronze_to_bronze()`** — Wraps PreBronze→Bronze pipeline
- **Prefect config:** `retries=3`, `retry_delay_seconds=60`, `log_prints=True`
- **Parameters:** `orchestration_run_id`, `source_name`, `raw_input` (list of dicts), `trigger_type`, `triggered_by`, `config`
- **Process:**
  1. Creates `pipeline_run` record in DB
  2. Updates `orchestration_run.current_layer`
  3. Imports `prebronze.orchestrator.run_sequential` dynamically
  4. Builds initial state dict and invokes pipeline
  5. Extracts metrics: `records_loaded`, `validation_errors_count`
  6. Updates `pipeline_run` with final metrics or error details
- **Returns:** Final pipeline state dict

**`run_bronze_to_silver()`** — Wraps Bronze→Silver auto-orchestrator
- Same Prefect config as above
- **Parameters:** `orchestration_run_id`, `trigger_type`, `triggered_by`, `config`
- Calls `bronze_to_silver.auto_orchestrator.transform_all_tables()`
- Extracts: `total_processed`, `total_written`, `total_failed`, `total_dq_issues`

**`run_silver_to_gold()`** — Wraps Silver→Gold auto-orchestrator
- Same structure as Bronze→Silver
- Calls `silver_to_gold.auto_orchestrator.transform_all_tables()`

---

### 9.10 `orchestrator/flows.py` (403 lines)

**Purpose:** Prefect `@flow`-decorated functions composing pipeline tasks into end-to-end workflows.

**Flows (4):**

**`full_ingestion_flow()`** — End-to-end: PreBronze → Bronze → Silver → Gold
- Creates an `orchestration_run` with layers `["prebronze_to_bronze", "bronze_to_silver", "silver_to_gold"]`
- Runs all 3 pipeline tasks sequentially with cascade triggering
- Aggregates `total_records_written` and `total_dq_issues` across layers
- On failure: marks run as `"failed"`, captures error counts

**`bronze_to_gold_flow()`** — Bronze → Silver → Gold (skip ingestion)
- Useful when Bronze data is already loaded
- Runs `bronze_to_silver` then `silver_to_gold` with cascade

**`single_layer_flow(layer, ...)`** — Run any single layer by name
- Accepts `layer` parameter: `"prebronze_to_bronze"`, `"bronze_to_silver"`, or `"silver_to_gold"`
- Validates layer name against `LAYER_TASKS` registry
- Supports `input_path` for file-based PreBronze input

**`realtime_event_flow(event_type, source_schema, source_table, record, ...)`** — Webhook-triggered
- Maps source schema to target layer:
  - `public` → `prebronze_to_bronze`
  - `bronze` → `bronze_to_silver`
  - `silver` → `silver_to_gold`
- Dispatches to `single_layer_flow` with the appropriate parameters

**Helper functions:**
- `_load_input(path)` — Loads data from CSV, JSON, or NDJSON files
  - JSON: `json.load()`, handles both array and single-object files
  - NDJSON/JSONL: Line-by-line `json.loads()`
  - CSV: `pandas.read_csv()` → `to_dict(orient="records")`

**Registry:** `FLOW_REGISTRY` maps flow names to functions for dynamic dispatch:
```python
FLOW_REGISTRY = {
    "full_ingestion": full_ingestion_flow,
    "bronze_to_gold": bronze_to_gold_flow,
    "single_layer": single_layer_flow,
    "realtime_event": realtime_event_flow,
}
```

---

### 9.11 `orchestrator/triggers.py` (152 lines)

**Purpose:** Schedule registration and webhook event dispatching.

**Functions:**

**`register_schedules()`**
- Reads active schedules from `orchestration.schedule_definitions`
- For each schedule, looks up the corresponding flow in `FLOW_REGISTRY`
- Calls `flow_fn.serve(name=..., cron=..., parameters=...)` to register with Prefect
- Returns count of successfully registered schedules

**`handle_webhook_event(payload)`**
- Processes a Supabase database webhook payload
- Expected payload format:
  ```json
  {
    "type": "INSERT",
    "table": "raw_recipes",
    "schema": "bronze",
    "record": { ... },
    "old_record": { ... }
  }
  ```
- Maps Supabase event types (`insert/update/delete`) to internal event types (`supabase_insert/supabase_update/supabase_delete`)
- Checks `orchestration.event_triggers` for matching trigger definitions
- If a matching trigger exists: dispatches to the trigger's configured flow
- If no trigger matches: uses default routing via `realtime_event_flow`

---

### 9.12 `orchestrator/api.py` (197 lines)

**Purpose:** FastAPI application with webhook endpoints and run management APIs.

**App configuration:**
- Title: "Data Orchestrator API"
- CORS: All origins, methods, and headers allowed (development mode)
- Startup: `uvicorn orchestrator.api:app --host 0.0.0.0 --port 8100`

**Endpoints:**

| Method | Path | Handler | Description |
|--------|------|---------|-------------|
| `GET` | `/health` | `health()` | Returns `{"status": "healthy", "service": "orchestrator"}` |
| `POST` | `/webhooks/supabase` | `receive_supabase_webhook()` | Receives Supabase DB webhook events |
| `POST` | `/api/trigger` | `trigger_flow()` | Manually trigger a flow via API |
| `GET` | `/api/runs` | `list_runs()` | List recent orchestration runs (supports `limit` and `status` query params) |
| `GET` | `/api/runs/{run_id}` | `get_run()` | Get details of a specific run |
| `GET` | `/api/pipelines` | `list_pipelines()` | List all registered pipeline definitions |

**Webhook authentication:** If `WEBHOOK_SECRET` is set, verifies the `x-webhook-secret` header matches.

**Trigger endpoint:** Accepts a `TriggerRequest` Pydantic model and dynamically dispatches to the appropriate flow based on `flow_name`.

---

### 9.13 `orchestrator/cli.py` (178 lines)

**Purpose:** CLI entry point with 4 subcommands using `argparse`.

**Commands:**

**`run`** — Execute an orchestration flow
```bash
python -m orchestrator run --flow full_ingestion --source-name walmart --input /data/products.csv
python -m orchestrator run --flow bronze_to_gold
python -m orchestrator run --flow single_layer --layer silver_to_gold
```
Options: `--flow` (required), `--source-name`, `--input`, `--layer`, `--batch-size`, `--no-incremental`

**`serve`** — Start the webhook API server
```bash
python -m orchestrator serve
python -m orchestrator serve --host 127.0.0.1 --port 9000 --reload
```
Options: `--host`, `--port`, `--reload`

**`schedule`** — Manage cron schedules
```bash
python -m orchestrator schedule              # List active schedules
python -m orchestrator schedule --register   # Register schedules with Prefect
```
Options: `--register`

**`runs`** — List recent orchestration runs
```bash
python -m orchestrator runs --limit 10
python -m orchestrator runs --status failed
```
Options: `--limit` (default 20), `--status`

**Logging format:** `%(asctime)s │ %(levelname)-8s │ %(name)s │ %(message)s`

---

## 10. API Reference

### POST `/webhooks/supabase`

Receive a Supabase database webhook event.

**Headers:**
- `x-webhook-secret` (optional): Must match `WEBHOOK_SECRET` if configured

**Request Body:**
```json
{
    "type": "INSERT",
    "table": "raw_recipes",
    "schema": "bronze",
    "record": {
        "id": "...",
        "source_name": "walmart",
        "raw_payload": { ... }
    },
    "old_record": null
}
```

**Response:** `200 OK`
```json
{
    "status": "accepted",
    "result": { ... }
}
```

### POST `/api/trigger`

Manually trigger an orchestration flow.

**Request Body:**
```json
{
    "flow_name": "full_ingestion",
    "source_name": "walmart",
    "input_path": "/data/walmart_products.csv",
    "layers": ["prebronze_to_bronze", "bronze_to_silver", "silver_to_gold"],
    "batch_size": 500,
    "incremental": true,
    "dry_run": false
}
```

**Response:** `200 OK`
```json
{
    "status": "completed",
    "result": {
        "prebronze_to_bronze": { ... },
        "bronze_to_silver": { ... },
        "silver_to_gold": { ... }
    }
}
```

### GET `/api/runs`

List recent orchestration runs.

**Query Parameters:**
- `limit` (int, default 20): Number of runs to return
- `status` (string, optional): Filter by status

**Response:**
```json
{
    "runs": [
        {
            "id": "uuid",
            "flow_name": "full_ingestion",
            "status": "completed",
            "total_records_written": 1500,
            "duration_seconds": 45.23,
            ...
        }
    ],
    "count": 5
}
```

### GET `/api/runs/{run_id}`

Get details of a specific orchestration run.

**Response:** Full orchestration_run record.

### GET `/api/pipelines`

List all registered pipeline definitions.

**Response:**
```json
{
    "pipelines": [
        {
            "id": "uuid",
            "pipeline_name": "prebronze_to_bronze",
            "layer_from": "prebronze",
            "layer_to": "bronze",
            "description": "...",
            "is_active": true
        },
        ...
    ],
    "count": 3
}
```

---

## 11. CLI Reference

```
orchestrator [-h] {run,serve,schedule,runs} ...

Data Orchestration Tool — Medallion Architecture Pipeline Manager

Positional arguments:
  run                 Execute an orchestration flow
  serve               Start the webhook API server
  schedule            Manage cron schedules
  runs                List recent orchestration runs

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

orchestrator run [-h] --flow FLOW [--source-name NAME]
                 [--input PATH] [--layer LAYER]
                 [--batch-size N] [--no-incremental]

orchestrator serve [-h] [--host HOST] [--port PORT] [--reload]

orchestrator schedule [-h] [--register]

orchestrator runs [-h] [--limit N] [--status STATUS]
```

---

## 12. Configuration Reference

All configuration is loaded via Pydantic Settings from environment variables or `.env` file.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SUPABASE_URL` | ✅ | — | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | ✅ | — | Supabase service role key (full access) |
| `OPENAI_API_KEY` | ❌ | None | OpenAI API key (for LLM steps) |
| `OPENAI_MODEL_NAME` | ❌ | `gpt-4o-mini` | OpenAI model for validation/tagging |
| `OPENAI_BASE_URL` | ❌ | None | Custom OpenAI base URL |
| `ORCHESTRATOR_LOG_LEVEL` | ❌ | `INFO` | Python logging level |
| `ORCHESTRATOR_DEFAULT_BATCH_SIZE` | ❌ | `100` | Default batch size for pipelines |
| `ORCHESTRATOR_MAX_RETRIES` | ❌ | `3` | Max retries for failed tasks |
| `ORCHESTRATOR_RETRY_DELAY_SECONDS` | ❌ | `60` | Delay between retries |
| `WEBHOOK_HOST` | ❌ | `0.0.0.0` | API server bind address |
| `WEBHOOK_PORT` | ❌ | `8100` | API server port |
| `WEBHOOK_SECRET` | ❌ | `""` | Secret for webhook authentication |

---

## 13. Triggering Mechanisms

### 1. Manual (CLI)
```bash
python -m orchestrator run --flow full_ingestion --source-name walmart --input /data/file.csv
```

### 2. Manual (API)
```bash
curl -X POST http://localhost:8100/api/trigger \
  -H "Content-Type: application/json" \
  -d '{"flow_name": "full_ingestion", "source_name": "walmart"}'
```

### 3. Scheduled (Cron)
Configure in `orchestration.schedule_definitions`, then register:
```bash
python -m orchestrator schedule --register
```

### 4. Real-time (Supabase Webhooks)
1. Start the API server: `python -m orchestrator serve`
2. In Supabase Dashboard → Database → Webhooks, create a webhook:
   - URL: `http://your-server:8100/webhooks/supabase`
   - Events: INSERT, UPDATE
   - Table: Any bronze/silver table
3. Data changes automatically trigger the downstream pipeline

### 5. Cascade (Upstream Complete)
Built into the flow definitions. When `prebronze_to_bronze` completes, the `full_ingestion_flow` automatically triggers `bronze_to_silver` with `trigger_type="upstream_complete"`.

---

## 14. Verification Results

| Check | Command | Result |
|-------|---------|--------|
| SQL schema structure | Count CREATE TABLE/INDEX | ✅ 7 tables, 7 indexes |
| Python models import | `from orchestrator.models import ...` | ✅ All models serialize correctly |
| Config loads | `from orchestrator.config import settings` | ✅ Pydantic Settings reads from env |
| CLI works | `python -m orchestrator.cli --help` | ✅ 4 commands displayed correctly |
| Package version | `orchestrator.__version__` | ✅ `"0.1.0"` |

---

## 15. Setup & Deployment Guide

### Prerequisites
- Python 3.11+
- Supabase project with Bronze, Silver, Gold schemas
- OpenAI API key (for LLM-augmented pipeline steps)

### Installation

```bash
cd "Orchestration Pipeline/orchestrator"

# Install with all dependencies
pip install -e ".[pipelines]"

# Or core-only (no LangGraph/pandas)
pip install -e .
```

### Configuration

```bash
cp .env.example .env
# Edit .env with your credentials:
#   SUPABASE_URL=https://your-project.supabase.co
#   SUPABASE_SERVICE_ROLE_KEY=your-key
#   OPENAI_API_KEY=your-key
```

### Apply Orchestration Schema

Open your Supabase SQL Editor and run the contents of `sql/orchestration_schema.sql`. This creates:
- The `orchestration` schema
- 7 tables with constraints and indexes
- Seed data for pipeline definitions and schedules

### Run a Pipeline

```bash
# Full end-to-end
python -m orchestrator run --flow full_ingestion --source-name walmart --input /data/products.csv

# Just Bronze→Gold (when Bronze is already loaded)
python -m orchestrator run --flow bronze_to_gold

# Single layer
python -m orchestrator run --flow single_layer --layer bronze_to_silver
```

### Start API Server

```bash
python -m orchestrator serve
# API docs at http://localhost:8100/docs
```

### Register Schedules

```bash
python -m orchestrator schedule --register
```

### Monitor Runs

```bash
python -m orchestrator runs --limit 10
python -m orchestrator runs --status failed
```

---

## 16. Future Enhancements

Items **not yet implemented** but planned:

1. **Gold → Neo4j pipeline** — Extend the orchestrator to trigger the graph database load
2. **Prefect Cloud deployment** — Push flows as deployments to Prefect Cloud for managed scheduling
3. **Supabase Edge Functions** — Deploy as Supabase Edge Functions for serverless webhook processing
4. **Dead letter queue** — Failed records sent to a DLQ table for manual review
5. **Alerting** — Slack/Email notifications on pipeline failures
6. **Dashboard UI** — Web dashboard for monitoring runs and metrics
7. **Backfill mode** — Re-process historical data with date range filters
8. **Parallel table processing** — Process multiple Bronze tables concurrently in Bronze→Silver
9. **Automated tests** — Unit and integration tests for all modules

---

*This documentation was generated on February 19, 2026, and covers all files and design decisions made during the orchestration tool build session.*
