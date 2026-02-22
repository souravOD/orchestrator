# Data Orchestrator

Production-grade orchestration tool for the **Medallion Architecture** data pipelines.

## Architecture

```
Prefect (Control Plane)
├── Scheduling, retries, observability, cascade triggers
├── @flow: full_ingestion, bronze_to_gold, single_layer, realtime_event
└── @task wrappers for each LangGraph pipeline
        ↓
LangGraph (Execution Engine)
├── PreBronze → Bronze  (7 nodes)
├── Bronze   → Silver   (8 nodes)
└── Silver   → Gold     (7 nodes)
        ↓
Supabase (Data Store)
├── orchestration.* (job tracking, 7 tables)
├── bronze.* → silver.* → gold.*
└── Webhooks for real-time triggers
```

## Quick Start

```bash
# 1. Install
cd orchestrator
pip install -e ".[pipelines]"

# 2. Configure
cp .env.example .env
# Edit .env with your Supabase and OpenAI credentials

# 3. Apply orchestration schema
# Run sql/orchestration_schema.sql in your Supabase SQL Editor

# 4. Run a pipeline
python -m orchestrator run --flow full_ingestion --source-name walmart --input /data/products.csv

# 5. Start webhook server
python -m orchestrator serve

# 6. Register cron schedules
python -m orchestrator schedule --register
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `run --flow <name>` | Execute a flow (full_ingestion, bronze_to_gold, single_layer) |
| `serve` | Start FastAPI webhook server on port 8100 |
| `schedule --register` | Register cron schedules from orchestration.schedule_definitions |
| `runs --limit 20` | List recent orchestration runs |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/webhooks/supabase` | Receive Supabase DB webhook events |
| `POST` | `/api/trigger` | Manually trigger a flow |
| `GET` | `/api/runs` | List recent orchestration runs |
| `GET` | `/api/runs/{id}` | Get run details |
| `GET` | `/api/pipelines` | List registered pipelines |

## Flows

- **full_ingestion** — PreBronze → Bronze → Silver → Gold (end-to-end)
- **bronze_to_gold** — Bronze → Silver → Gold (skip ingestion)
- **single_layer** — Run any single layer by name
- **realtime_event** — Lightweight flow for webhook-triggered events

## Project Structure

```
orchestrator/
├── pyproject.toml          # Dependencies & project config
├── .env.example            # Environment variable template
├── README.md               # This file
├── sql/
│   └── orchestration_schema.sql
└── orchestrator/
    ├── __init__.py
    ├── __main__.py         # python -m orchestrator
    ├── cli.py              # CLI entry point
    ├── config.py           # Pydantic Settings
    ├── models.py           # Pydantic data models
    ├── db.py               # Supabase CRUD
    ├── pipelines.py        # @task wrappers
    ├── flows.py            # @flow definitions
    ├── triggers.py         # Schedule + webhook dispatch
    ├── api.py              # FastAPI server
    └── logging_utils.py    # Step-level DB logging
```
