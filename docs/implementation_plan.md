# Data Orchestration Tool — Implementation Plan (v3)

## Goal

Build a production-grade orchestration tool that wraps **5 pipelines** (PreBronze→Bronze, Bronze→Silver, Silver→Gold, Gold→Neo4j, USDA Nutrition) into a unified, schedulable, observable orchestration layer using **Prefect** as the control plane, deployed via **Docker on Coolify**.

**From approved research (v3):**
- **Deployment:** Docker containers on Coolify (single server)
- **Pipelines:** 3 LangGraph + 1 Neo4j (8-service pipeline with LangGraph agents) + 1 CrewAI
- **Gold→Neo4j:** Wraps existing modular pipeline (catalog_batch CLI, customer_realtime worker, reconciliation)
- **Triggers:** CLI + hourly cron + Supabase webhooks + CI/CD hooks + FastAPI endpoints
- **Real-time:** B2C events → Gold → outbox → `customer_realtime` worker → Neo4j
- **Scale:** ≤ 5 vendors, up to 1M rows/batch, hourly frequency
- **Alerting:** Email (primary) + GitHub Issues (secondary)
- **Queue:** `asyncio.Queue` (Phase 1), upgrade path to pgmq/Redis
- **Dashboard:** Next.js (Phase 2)

---

## User Review Required

> [!IMPORTANT]
> **Gold→Neo4j integration approach** — The existing pipeline is already a well-structured, modular codebase with 8 services, YAML configs, agentic schema drift resolution, and its own Dockerfile. The orchestrator will **wrap** it via its CLI interface (`python -m services.catalog_batch.run --layer {layer}`), not rewrite its internals. This means:
> - Batch sync: Prefect `@task` invokes `catalog_batch.run.main(["--layer", layer])` per domain
> - Realtime sync: Prefect manages the `customer_realtime` worker as a long-running task
> - Reconciliation: Prefect schedules `reconciliation.service.main()` periodically
> - Run summaries parsed from `state/run_summaries.jsonl`

> [!WARNING]
> **Secrets management** — The Gold-to-Neo4j pipeline reads from `.env` directly (`load_dotenv`). The orchestrator will pass env vars through Docker/Coolify. Ensure `SUPABASE_URL`, `SUPABASE_KEY`, `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `AGENT_LLM_MODEL` are set in Coolify's secrets.

---

## Proposed Changes

### Component 1: Updated Orchestration SQL Schema

#### [MODIFY] [orchestration_schema.sql](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/sql/orchestration_schema.sql)

Add to the existing 7 tables:
- **`alert_log`** — tracks email/GitHub Issue dispatch history
- Update `pipeline_definitions` to seed `gold_to_neo4j` and `usda_nutrition` entries
- Update `orchestration_runs.layers` default to include `gold_to_neo4j`

---

### Component 2: Updated Models

#### [MODIFY] [models.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/models.py)

Add:
- `AlertLog` Pydantic model (matches `alert_log` table)
- `AlertType` enum (`email`, `github_issue`, `slack`)
- `AlertSeverity` enum (`info`, `warning`, `critical`)
- `Neo4jSyncResult` model — wraps run summary from the Gold-to-Neo4j pipeline
- `Neo4jLayerResult` model — per-layer (recipes/ingredients/products/customers) results

---

### Component 3: Updated Config

#### [MODIFY] [config.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/config.py)

Add environment variables for:
- `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` — Neo4j connection (passed through to Gold-to-Neo4j pipeline)
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO` — Email alerts
- `GITHUB_TOKEN`, `GITHUB_REPO` — GitHub Issues API
- `CREWAI_ENABLED` — Feature flag for CrewAI pipeline
- `AGENT_LLM_MODEL` — LLM model for agentic schema drift resolution (default: `gpt-4.1-mini`)
- `DRIFT_CONFIDENCE_THRESHOLD` — Schema drift auto-alias threshold (default: 0.85)
- `NEO4J_REALTIME_POLL_INTERVAL` — Outbox polling interval (default: 5s)

---

### Component 4: Updated Pipeline Wrappers

#### [MODIFY] [pipelines.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/pipelines.py)

Add three new `@task` wrappers:

**`run_gold_to_neo4j_batch(layer, config)`**
- Imports `services.catalog_batch.run.main` from the Gold-to-Neo4j pipeline
- Invokes `main(["--layer", layer])` where layer ∈ {recipes, ingredients, products, customers, all}
- Parses `state/run_summaries.jsonl` for run metrics (rows_fetched, duration_ms, status)
- Creates `pipeline_run` → logs steps → updates metrics

**`run_gold_to_neo4j_realtime(config)`**
- Starts `customer_realtime.service.main()` as a long-running worker
- Monitors outbox event processing
- Reports processed/failed/retried counts

**`run_usda_nutrition(ingredients, config)`**
- Imports and invokes the CrewAI `EnhancedNutritionFetchOrchestrator`

---

### Component 5: Updated Flow Definitions

#### [MODIFY] [flows.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/flows.py)

Add/update flows:
- **`full_ingestion`** — Now runs 4 stages: PB→B→S→G → Neo4j sync (all layers)
- **`neo4j_batch_sync`** — Standalone Gold→Neo4j batch flow; runs `run_gold_to_neo4j_batch` per layer in order: recipes → ingredients → products → customers
- **`neo4j_realtime_worker`** — Manages the `customer_realtime` worker process
- **`neo4j_reconciliation`** — Runs `reconciliation.service.main()` to detect drift
- **`usda_nutrition`** — Standalone CrewAI nutrition enrichment flow
- **`realtime_event`** — Updated to trigger `neo4j_batch_sync` (not full pipeline)

---

### Component 6: Alerting System (NEW)

#### [NEW] [alerts.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/alerts.py)

New module for dispatching alerts:

- `EmailAlertSender` — SMTP-based email dispatch
  - Sends on pipeline failure, DQ threshold breach, missed schedule, reconciliation drift
  - HTML-formatted with run link, error details, metrics
- `GitHubIssueAlertSender` — GitHub Issues API
  - Creates issue with labels (`pipeline-failure`, severity level)
  - Includes run ID, error traceback, pipeline name, timestamps
- `AlertDispatcher` — Unified interface
  - `dispatch_alert(run, severity, channels)` — Sends to all configured channels
  - Logs to `alert_log` table
  - Deduplication: won't re-alert for same run within window

**New alert triggers from Gold-to-Neo4j integration:**
- Schema drift unresolved (SchemaDriftError) → critical email + GitHub issue
- Reconciliation drift > threshold → warning email
- Agent-proposed aliases on safety-critical tables → needs_review email

---

### Component 7: Updated Trigger System

#### [MODIFY] [triggers.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/triggers.py)

Updates:
- `handle_webhook_event()` — Now routes Gold table events to `neo4j_batch_sync` flow
- `setup_schedules()` — Registers `hourly_neo4j_sync`, `daily_reconciliation`, and `weekly_usda_nutrition` schedules
- Add CI/CD post-deploy webhook handler

#### [MODIFY] [api.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/api.py)

Add endpoints:
- `POST /webhooks/coolify` — Post-deploy trigger from Coolify
- `GET /api/alerts` — List recent alerts
- `GET /api/pipelines/{name}/health` — Pipeline health status
- `GET /api/neo4j/sync-status` — Latest Neo4j sync results per layer
- `GET /api/neo4j/reconciliation` — Latest reconciliation drift report

---

### Component 8: Updated DB Layer

#### [MODIFY] [db.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/db.py)

Add CRUD operations for `alert_log` table:
- `create_alert_log(alert)`
- `get_alerts(filters, limit)`

---

### Component 9: Docker Deployment

#### [NEW] [Dockerfile](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/Dockerfile)

Production Dockerfile:
- Base: `python:3.11-slim`
- Install orchestrator with `pip install -e ".[pipelines]"`
- Expose port 8100
- CMD: `python -m orchestrator serve`
- Gold-to-Neo4j pipeline accessed as a git submodule or pip-installed package

#### [MODIFY] [.env.example](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/.env.example)

Add Neo4j, SMTP, GitHub, CrewAI, and drift-related environment variables.

---

### Component 10: Gold-to-Neo4j Pipeline Integration (NEW)

#### [NEW] [neo4j_adapter.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/neo4j_adapter.py)

Adapter module that bridges the Gold-to-Neo4j pipeline with the orchestrator:

```python
class Neo4jPipelineAdapter:
    """Wraps Gold-to-Neo4j services for orchestrator invocation."""

    LAYER_ORDER = ["recipes", "ingredients", "products", "customers"]

    def run_batch_sync(self, layer: str) -> Neo4jSyncResult:
        """Invoke catalog_batch.run.main() for a single layer."""

    def run_all_layers(self) -> list[Neo4jSyncResult]:
        """Run all layers in order, collecting results."""

    def start_realtime_worker(self) -> None:
        """Start customer_realtime outbox poller."""

    def run_reconciliation(self) -> ReconciliationReport:
        """Run reconciliation service and parse plans."""

    def parse_run_summaries(self, layer: str) -> Neo4jSyncResult:
        """Parse state/run_summaries.jsonl for latest run."""
```

---

### Component 11: Updated Tests

#### [MODIFY] tests/

Update all existing test files to cover:
- New pipeline wrappers (`gold_to_neo4j_batch`, `gold_to_neo4j_realtime`, `usda_nutrition`)
- Alert system (email + GitHub Issue dispatch with mocks)
- New API endpoints
- Updated flow definitions
- `Neo4jPipelineAdapter` (mocked subprocess/import)

#### [NEW] [test_alerts.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/tests/test_alerts.py)

#### [NEW] [test_neo4j_adapter.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/tests/test_neo4j_adapter.py)

---

## Updated File Structure

```
orchestrator/
├── Dockerfile                  # NEW — Docker deployment
├── pyproject.toml              # MODIFIED — add neo4j deps
├── .env.example                # MODIFIED — add new env vars
├── README.md                   # MODIFIED — update docs
├── sql/
│   └── orchestration_schema.sql  # MODIFIED — add alert_log table
└── orchestrator/
    ├── __init__.py
    ├── __main__.py
    ├── cli.py                  # MODIFIED — add alert/neo4j commands
    ├── config.py               # MODIFIED — add Neo4j/SMTP/GitHub/drift config
    ├── models.py               # MODIFIED — add alert + neo4j result models
    ├── db.py                   # MODIFIED — add alert CRUD
    ├── pipelines.py            # MODIFIED — add neo4j batch/realtime/crewai wrappers
    ├── flows.py                # MODIFIED — add neo4j_batch_sync/realtime/reconciliation flows
    ├── triggers.py             # MODIFIED — add neo4j event routing + reconciliation schedule
    ├── api.py                  # MODIFIED — add coolify/alert/neo4j endpoints
    ├── alerts.py               # NEW — email + GitHub Issues dispatch
    ├── neo4j_adapter.py        # NEW — Gold-to-Neo4j pipeline bridge
    └── logging_utils.py
```

---

## Phased Delivery

### Phase 1 (This Implementation)
- [x] Updated SQL schema (8 tables)
- [x] Updated models, config, DB layer
- [x] Neo4j adapter module (wraps Gold-to-Neo4j pipeline)
- [x] Neo4j + CrewAI pipeline wrappers
- [x] Updated flow definitions (4→7 flows, full_ingestion chains Neo4j)
- [x] Alerting system (email + GitHub Issues)
- [x] Updated triggers (Gold schema → Neo4j routing) + API endpoints
- [x] Updated CLI (neo4j sync/reconcile/realtime + alerts commands)
- [x] Updated pyproject.toml (httpx + neo4j deps)
- [x] Dockerfile for Coolify deployment
- [x] Updated tests (118 pass)

### Phase 2 (Dashboard)
- [ ] Next.js monitoring dashboard
- [ ] Supabase RLS policies for dashboard
- [ ] Real-time WebSocket updates in dashboard

### Phase 3 (Scale)
- [ ] Migrate from asyncio.Queue to pgmq/Redis
- [ ] Separate worker containers
- [ ] Parallel pipeline execution

---

## Verification Plan

### Automated Tests
```bash
cd orchestrator
pip install -e ".[dev]"
pytest tests/ -v --tb=short
```

Tests cover:
1. All Pydantic models (including new alert + neo4j result models)
2. Config loading with Neo4j/SMTP/GitHub/drift env vars
3. DB CRUD (mocked Supabase) including alert_log
4. All 5+ pipeline wrappers (mocked execution)
5. All 7 flow definitions
6. Neo4j adapter (mocked Gold-to-Neo4j imports)
7. Alert dispatch (mocked SMTP + mocked GitHub API)
8. All API endpoints (FastAPI TestClient)
9. CLI argument parsing

### Docker Verification
```bash
cd orchestrator
docker build -t orchestrator:latest .
docker run --env-file .env -p 8100:8100 orchestrator:latest
curl http://localhost:8100/health
```

### Manual Verification
1. Review SQL schema → confirm 8 tables
2. Review `alerts.py` → confirm email + GitHub Issue formats
3. Review `Dockerfile` → confirm Coolify-compatible
4. Review `neo4j_adapter.py` → confirm it correctly wraps `catalog_batch.run.main()`
5. Review `pipelines.py` → confirm Neo4j + CrewAI wrappers

> [!IMPORTANT]
> Full end-to-end testing requires Supabase credentials, Neo4j instance, and the Gold-to-Neo4j pipeline repo available. Phase 1 verification focuses on structure, imports, mocked execution, and Docker build.
