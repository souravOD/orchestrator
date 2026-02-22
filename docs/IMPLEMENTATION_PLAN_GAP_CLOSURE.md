# Implementation Plan: Closing the Orchestrator Gaps (Phases 1 & 2)

> Based on [FRAMEWORK_COMPARISON.md](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/docs/FRAMEWORK_COMPARISON.md) and [ORCHESTRATOR_ARCHITECTURE.md](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/docs/ORCHESTRATOR_ARCHITECTURE.md).

---

## Phase Overview

| Phase | Features | Estimated Effort |
|-------|----------|-----------------|
| **Phase 1** — Quick Wins | Configurable timeouts, concurrent run prevention, input validation, success notifications, LangGraph tool metrics, LLM token tracking | ~1.5 days |
| **Phase 2** — Medium Effort | Webhook DLQ (+ auto-retry), pipeline contracts (strict), cancellation mechanism, checkpoint/resume, dashboard updates | ~3-4 days |

---

## Decisions Finalized

| Decision | Choice |
|----------|--------|
| Contract strictness | **Lenient** (`extra = "allow"`) initially, transition to strict after pipeline teams update return dicts |
| DLQ retry timing | **Conservative** — 1min → 5min → 30min, max 3 retries |
| LLM tracking scope | **All 4 pipelines** including Gold→Neo4j |
| Dashboard | **Scoped separately** — backend APIs first, dashboard UI after |

---

## Phase 1 — Quick Wins

---

### Feature 1: Configurable Per-Pipeline Timeouts

**No default timeouts** applied. Instead, we make timeouts **configurable per pipeline** via:

1. The `pipeline_definitions` table (a new `timeout_seconds` column)
2. The dashboard (editable per-pipeline settings page)
3. The API / CLI (pass `timeout_seconds` in config)

> [!NOTE]
> Timeouts are read dynamically at task runtime. If `timeout_seconds` is `NULL` (the default), no timeout is applied. The Prefect `@task` decorator does not support dynamic `timeout_seconds`, so we implement timeouts ourselves using Python's `signal` module (Unix) or a `threading.Timer` (cross-platform).

#### [MODIFY] [orchestration_schema.sql](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/sql/orchestration_schema.sql)

```sql
-- Add timeout_seconds to pipeline_definitions (NULL = no timeout)
ALTER TABLE orchestration.pipeline_definitions
    ADD COLUMN timeout_seconds int DEFAULT NULL;

COMMENT ON COLUMN orchestration.pipeline_definitions.timeout_seconds
    IS 'Optional per-pipeline timeout in seconds. NULL means no timeout.';
```

#### [MODIFY] [db.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/db.py)

- Add `get_pipeline_timeout(pipeline_name)` → returns `int | None`
- Add `update_pipeline_definition(pipeline_name, **fields)` for dashboard updates

#### [MODIFY] [pipelines.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/pipelines.py)

Add a `_run_with_timeout(func, args, timeout_seconds)` helper that wraps pipeline calls:

```python
import signal
import threading

class PipelineTimeoutError(Exception):
    """Raised when a pipeline exceeds its configured timeout."""

def _run_with_timeout(func, kwargs, timeout_seconds: int | None):
    """Run func(**kwargs) with an optional timeout."""
    if timeout_seconds is None:
        return func(**kwargs)

    result = [None]
    exception = [None]

    def target():
        try:
            result[0] = func(**kwargs)
        except Exception as e:
            exception[0] = e

    thread = threading.Thread(target=target)
    thread.start()
    thread.join(timeout=timeout_seconds)

    if thread.is_alive():
        raise PipelineTimeoutError(
            f"Pipeline timed out after {timeout_seconds}s"
        )
    if exception[0]:
        raise exception[0]
    return result[0]
```

Each task wrapper checks for a configured or per-run timeout:

```python
# In run_prebronze_to_bronze, after creating the pipeline_run:
timeout = cfg.get("timeout_seconds") or db.get_pipeline_timeout("prebronze_to_bronze")

try:
    final_state = _run_with_timeout(run_sequential, {"state": state}, timeout)
except PipelineTimeoutError:
    db.update_pipeline_run(run_id, status="timed_out", ...)
    raise
```

#### [MODIFY] [api.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/api.py)

Add `PUT /api/pipelines/{name}/settings` endpoint to update `timeout_seconds` from the dashboard.

---

### Feature 2: Concurrent Run Prevention

#### What is "Concurrent Run Prevention Scope"?

**The problem**: If two people (or two cron jobs) trigger `full_ingestion` at the same time on the same source data, both flows run in parallel and write duplicate records to the database.

**"Scope"** means: *which of the 7 registered flows should have this guard?*

| Flow | Should Guard? | Reasoning |
|------|:---:|-----------|
| `full_ingestion` | ✅ | Heavy, writes to all layers — duplicates are dangerous |
| `bronze_to_gold` | ✅ | Same concern — transforms shared tables |
| `single_layer` | ✅ | Could cause duplicates on the same layer |
| `neo4j_batch_sync` | ✅ | Concurrent syncs can create duplicate Neo4j nodes |
| `neo4j_reconciliation` | ✅ | Reconciliation with concurrent writes yields incorrect drift reports |
| `realtime_event` | ❌ | Lightweight, processes one record at a time — concurrency is fine |
| `neo4j_realtime_worker` | ❌ | Long-running poller — designed to be always-on, only one instance expected |

#### [MODIFY] [db.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/db.py)

Add `has_running_flow(flow_name: str) -> bool` — checks for `status='running'` rows.

#### [MODIFY] [flows.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/flows.py)

Add a `_guard_concurrent(flow_name)` helper called at the top of each guarded flow:

```python
class ConcurrentRunError(Exception):
    """Raised when a flow is already running."""

def _guard_concurrent(flow_name: str):
    existing = db.list_orchestration_runs(status="running", flow_name=flow_name)
    # Filter out stale runs (> 24h old, likely orphaned)
    active = [r for r in existing if not _is_stale(r)]
    if active:
        raise ConcurrentRunError(
            f"Flow '{flow_name}' already running: {active[0]['id']}. "
            f"Cancel it first or wait for it to finish."
        )
```

---

### Feature 3: Input Validation (Pydantic)

#### [MODIFY] [models.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/models.py)

```python
class IngestionInput(BaseModel):
    """Validated input for full_ingestion and single_layer flows."""
    source_name: str = Field(..., min_length=1, description="Vendor/data source name")
    raw_input: Optional[List[Dict[str, Any]]] = None
    input_path: Optional[str] = None

    @model_validator(mode="after")
    def at_least_one_input(self):
        if not self.raw_input and not self.input_path:
            raise ValueError("Either 'raw_input' or 'input_path' must be provided")
        return self

    @field_validator("raw_input")
    @classmethod
    def non_empty_if_provided(cls, v):
        if v is not None and len(v) == 0:
            raise ValueError("raw_input cannot be an empty list")
        return v
```

#### [MODIFY] [flows.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/flows.py)

Validate at the top of `full_ingestion_flow`:

```python
validated = IngestionInput(
    source_name=source_name,
    raw_input=raw_input,
    input_path=input_path,
)
```

---

### Feature 4: Success Notifications

#### [MODIFY] [alerts.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/alerts.py)

Update `AlertDispatcher.dispatch()` to support `severity="info"` for success messages. Currently filters to warning/critical only — we relax the filter.

#### [MODIFY] [flows.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/flows.py)

At the end of each successful flow completion, call:

```python
from .alerts import send_alert
send_alert(
    title=f"✅ {flow_name} completed successfully",
    message=f"Written={total_written}, DQ issues={total_dq}, Duration={duration}s",
    severity="info",
    pipeline_name=flow_name,
    run_id=str(orch_run_id),
)
```

Channels: **Email + GitHub Issues** (as configured today).

---

### Feature 5: LangGraph Tool Metrics & LLM Token Tracking

> [!IMPORTANT]
> This feature requires **pipeline team cooperation**. The orchestrator cannot introspect LangGraph internals — the data must come from the pipelines themselves via an expanded return dict.

#### What Pipeline Teams Need to Do

This is the **exact specification** to share with each pipeline team. The orchestrator already reads return dicts from `run_sequential()` and `transform_all_tables()`. We need them to add new keys.

---

##### 📋 Pipeline Team Guide: Adding Metrics to Your Return Dict

**Goal**: Report tool-level execution metrics and LLM token usage so the orchestrator can track performance and cost.

**Contract change type**: MINOR version bump (backward-compatible additions).

###### Step 1: Add Tool-Level Metrics

Add a `tool_metrics` key to your return dict. This is a list of objects, one per LangGraph tool invocation during the run:

```python
# Your pipeline's return dict should now include:
{
    # ... existing keys (total_processed, total_written, etc.) ...

    "tool_metrics": [
        {
            "tool_name": "classify_table",      # LangGraph tool/node name
            "duration_ms": 1234,                 # Wall-clock time in milliseconds
            "records_in": 500,                   # Records this tool received
            "records_out": 498,                  # Records this tool produced
            "status": "completed",               # "completed" | "failed" | "skipped"
            "error": None,                       # Error message if failed, else None
            "metadata": {}                       # Optional: any tool-specific data
        },
        {
            "tool_name": "map_columns",
            "duration_ms": 856,
            "records_in": 498,
            "records_out": 498,
            "status": "completed",
            "error": None,
            "metadata": {"columns_mapped": 12}
        },
        # ... one entry per tool invocation ...
    ]
}
```

**How to capture this in your LangGraph pipeline:**

```python
import time

def your_langgraph_tool(state):
    start = time.time()
    records_in = len(state.get("records", []))

    try:
        # ... your tool logic ...
        records_out = len(result_records)

        state.setdefault("tool_metrics", []).append({
            "tool_name": "your_tool_name",
            "duration_ms": int((time.time() - start) * 1000),
            "records_in": records_in,
            "records_out": records_out,
            "status": "completed",
            "error": None,
        })
    except Exception as e:
        state.setdefault("tool_metrics", []).append({
            "tool_name": "your_tool_name",
            "duration_ms": int((time.time() - start) * 1000),
            "records_in": records_in,
            "records_out": 0,
            "status": "failed",
            "error": str(e),
        })
        raise

    return state
```

###### Step 2: Add LLM Token Usage

Add an `llm_usage` key to your return dict with aggregated token counts and cost:

```python
{
    # ... existing keys ...

    "llm_usage": {
        "total_prompt_tokens": 15420,       # Total input tokens across all LLM calls
        "total_completion_tokens": 3280,    # Total output tokens
        "total_tokens": 18700,              # Sum of prompt + completion
        "total_cost_usd": 0.0187,           # Estimated cost (based on model pricing)
        "model": "gpt-4o-mini",             # Primary model used
        "llm_calls": 12,                    # Number of LLM invocations

        # Optional: per-call breakdown
        "calls": [
            {
                "tool_name": "classify_table",
                "model": "gpt-4o-mini",
                "prompt_tokens": 1200,
                "completion_tokens": 350,
                "cost_usd": 0.0015,
                "duration_ms": 890
            },
            # ...
        ]
    }
}
```

**How to capture this using LangChain's built-in callback:**

```python
from langchain_community.callbacks import get_openai_callback

def run_sequential(state):
    with get_openai_callback() as cb:
        # ... all your LangGraph execution here ...
        result = graph.invoke(state)

    # Append LLM usage to the result
    result["llm_usage"] = {
        "total_prompt_tokens": cb.prompt_tokens,
        "total_completion_tokens": cb.completion_tokens,
        "total_tokens": cb.total_tokens,
        "total_cost_usd": round(cb.total_cost, 6),
        "model": os.environ.get("OPENAI_MODEL_NAME", "gpt-4o-mini"),
        "llm_calls": cb.successful_requests,
    }
    return result
```

> [!WARNING]
> **`get_openai_callback()` only works with OpenAI models via LangChain.** If you use other providers, you'll need their equivalent callback handler or manual token counting.

###### Step 3: Version Bump

After adding these keys, bump your `pyproject.toml` version:

```toml
# This is a MINOR change (new keys, no breaking changes)
version = "1.1.0"  # was 1.0.0
```

###### Summary of New Required Return Dict Keys

| Key | Type | Required? | Description |
|-----|------|-----------|-------------|
| `tool_metrics` | `list[dict]` | **Yes** | Per-tool execution metrics (see schema above) |
| `llm_usage` | `dict` | **Yes** | Aggregated LLM token usage and cost |

These are **additive** (MINOR version bump). Existing keys remain unchanged.

---

#### Orchestrator-Side Changes

##### [MODIFY] [orchestration_schema.sql](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/sql/orchestration_schema.sql)

```sql
-- Store tool-level metrics per pipeline run
CREATE TABLE orchestration.pipeline_tool_metrics (
    id              uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    pipeline_run_id uuid NOT NULL REFERENCES orchestration.pipeline_runs(id) ON DELETE CASCADE,
    tool_name       varchar(100) NOT NULL,
    duration_ms     int,
    records_in      int DEFAULT 0,
    records_out     int DEFAULT 0,
    status          varchar(20) DEFAULT 'completed',
    error_message   text,
    metadata        jsonb DEFAULT '{}',
    created_at      timestamptz DEFAULT now()
);

CREATE INDEX idx_tool_metrics_run ON orchestration.pipeline_tool_metrics (pipeline_run_id);

-- Store LLM usage per pipeline run
CREATE TABLE orchestration.pipeline_llm_usage (
    id                      uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    pipeline_run_id         uuid NOT NULL REFERENCES orchestration.pipeline_runs(id) ON DELETE CASCADE,
    model                   varchar(100),
    total_prompt_tokens     int DEFAULT 0,
    total_completion_tokens int DEFAULT 0,
    total_tokens            int DEFAULT 0,
    total_cost_usd          numeric(10,6) DEFAULT 0,
    llm_calls               int DEFAULT 0,
    call_details            jsonb DEFAULT '[]',
    created_at              timestamptz DEFAULT now()
);

CREATE INDEX idx_llm_usage_run ON orchestration.pipeline_llm_usage (pipeline_run_id);
```

##### [MODIFY] [db.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/db.py)

Add `create_tool_metrics(pipeline_run_id, metrics_list)` and `create_llm_usage(pipeline_run_id, usage_dict)`.

##### [MODIFY] [pipelines.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/pipelines.py)

After each pipeline returns, extract and store the new metrics:

```python
# After pipeline execution succeeds:
tool_metrics = result.get("tool_metrics", [])
if tool_metrics:
    db.create_tool_metrics(run_id, tool_metrics)

llm_usage = result.get("llm_usage")
if llm_usage:
    db.create_llm_usage(run_id, llm_usage)
```

---

## Phase 2 — Medium Effort

---

### Feature 6: Webhook Dead-Letter Queue (DLQ) with Auto-Retry

#### [MODIFY] [orchestration_schema.sql](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/sql/orchestration_schema.sql)

```sql
CREATE TABLE orchestration.webhook_dead_letter (
    id              uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    payload         jsonb NOT NULL,
    error_message   text,
    error_details   jsonb,
    retry_count     int DEFAULT 0,
    max_retries     int DEFAULT 3,
    status          varchar(20) DEFAULT 'pending',
    next_retry_at   timestamptz,
    last_retry_at   timestamptz,
    resolved_at     timestamptz,
    created_at      timestamptz DEFAULT now(),

    CONSTRAINT dlq_status_check CHECK (
        status IN ('pending', 'retrying', 'resolved', 'exhausted', 'discarded')
    )
);

CREATE INDEX idx_dlq_status ON orchestration.webhook_dead_letter (status, next_retry_at);

COMMENT ON TABLE orchestration.webhook_dead_letter
    IS 'Retry strategy: Conservative exponential backoff — 1min, 5min, 30min (max 3 retries)';
```

#### [MODIFY] [db.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/db.py)

Add CRUD: `create_dead_letter`, `list_dead_letters`, `get_retryable_dead_letters`, `update_dead_letter`.

#### [MODIFY] [triggers.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/triggers.py)

Wrap `handle_webhook_event` failure path:

```python
try:
    result = handle_webhook_event(payload)
except Exception as exc:
    db.create_dead_letter(
        payload=payload,
        error_message=str(exc),
        error_details={"traceback": traceback.format_exc()},
        next_retry_at=utcnow() + timedelta(minutes=1),  # first retry in 1 min
    )
```

#### [NEW] [dlq_worker.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/dlq_worker.py)

Background worker that auto-retries DLQ entries with **conservative exponential backoff** (1min → 5min → 30min):

```python
# Retry delays: 1 minute, 5 minutes, 30 minutes
RETRY_DELAYS = [1, 5, 30]  # in minutes

async def retry_dead_letters():
    """Called periodically (every 30s) to retry pending DLQ entries."""
    retryable = db.get_retryable_dead_letters()  # next_retry_at <= now()
    for entry in retryable:
        try:
            handle_webhook_event(entry["payload"])
            db.update_dead_letter(entry["id"], status="resolved", resolved_at=utcnow())
        except Exception as exc:
            new_count = entry["retry_count"] + 1
            if new_count >= entry["max_retries"]:
                db.update_dead_letter(entry["id"], status="exhausted", retry_count=new_count)
                # Alert: webhook exhausted all retries
                send_alert(
                    title="Webhook DLQ exhausted",
                    message=f"Webhook {entry['id']} failed after {new_count} retries: {exc}",
                    severity="warning",
                )
            else:
                # Conservative backoff: 1min → 5min → 30min
                delay_minutes = RETRY_DELAYS[min(new_count - 1, len(RETRY_DELAYS) - 1)]
                db.update_dead_letter(
                    entry["id"],
                    status="pending",
                    retry_count=new_count,
                    next_retry_at=utcnow() + timedelta(minutes=delay_minutes),
                    last_retry_at=utcnow(),
                )
```

Registered as a FastAPI background task on startup.

#### [MODIFY] [api.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/api.py)

New endpoints:

- `GET /api/dead-letters` — list DLQ entries (filterable by status)
- `POST /api/dead-letters/{id}/retry` — manual retry of a specific entry
- `DELETE /api/dead-letters/{id}` — discard (sets status to `discarded`)

---

### Feature 7: Pipeline Contract Interfaces (Lenient → Strict)

> [!NOTE]
> Starting **lenient** (`extra = "allow"`) so existing pipelines don't break. Once pipeline teams have updated their return dicts to strip extra keys, we flip to `extra = "forbid"`.

#### [NEW] [contracts.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/contracts.py)

```python
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional

class ToolMetric(BaseModel):
    tool_name: str
    duration_ms: Optional[int] = None
    records_in: int = 0
    records_out: int = 0
    status: str = "completed"
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

class LLMUsage(BaseModel):
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    model: Optional[str] = None
    llm_calls: int = 0
    calls: List[Dict[str, Any]] = Field(default_factory=list)

class PreBronzeResult(BaseModel):
    """Contract for PreBronze→Bronze pipeline returns."""
    records_loaded: int
    validation_errors_count: int = 0
    validation_errors: List[Any] = Field(default_factory=list)
    tool_metrics: List[ToolMetric] = Field(default_factory=list)
    llm_usage: Optional[LLMUsage] = None

    class Config:
        extra = "allow"  # LENIENT initially — switch to "forbid" after pipeline teams update

class TransformResult(BaseModel):
    """Contract for Bronze→Silver and Silver→Gold pipeline returns."""
    total_processed: int
    total_written: int
    total_failed: int = 0
    total_dq_issues: int = 0
    tool_metrics: List[ToolMetric] = Field(default_factory=list)
    llm_usage: Optional[LLMUsage] = None

    class Config:
        extra = "allow"  # LENIENT initially — switch to "forbid" after pipeline teams update
```

#### [MODIFY] [pipelines.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/pipelines.py)

Validate pipeline return dicts against the lenient contract (logs warnings for extra keys but doesn't fail):

```python
from .contracts import PreBronzeResult, TransformResult

# In run_prebronze_to_bronze, after pipeline returns:
try:
    validated = PreBronzeResult(**final_state)
except ValidationError as e:
    logger.warning("Contract validation warning: %s", e)
    # Don't fail — lenient mode allows extra keys

# In run_bronze_to_silver / run_silver_to_gold:
try:
    validated = TransformResult(**result)
except ValidationError as e:
    logger.warning("Contract validation warning: %s", e)
```

On `ValidationError` in lenient mode, the pipeline_run **still succeeds** — a warning is logged. Once pipeline teams confirm return dicts are clean, switch `extra = "allow"` → `extra = "forbid"` to enforce strictly.

---

### Feature 8: Cancellation Mechanism

#### Pros and Cons: Cooperative DB Polling vs. Prefect-Native Cancellation

| Aspect | Cooperative DB Polling | Prefect-Native `cancel()` |
|--------|:-----:|:-----:|
| **How it works** | Check a `cancelled` flag in Supabase between pipeline layers | Call Prefect's `cancel_flow_run()` API from the server |
| **✅ Pros** | Works anywhere (no Prefect server needed), full control, simple to understand, works with Coolify | Built-in, propagates through entire task tree, immediate |
| **❌ Cons** | Only checks between layers (can't cancel mid-pipeline), requires polling, adds latency | Requires Prefect Cloud or self-hosted Prefect server, doesn't work in local/standalone mode, adds infrastructure dependency |
| **Granularity** | Between-layer only (4 checkpoints at most) | Can cancel mid-task if using Prefect's async tasks |
| **Infrastructure** | Zero — just a DB column | Prefect Server + API access |
| **Deployment** | Works on Coolify as-is | Need Prefect Orion/Cloud on Coolify |

> [!TIP]
> **Recommendation**: **Start with cooperative DB polling** since your current deployment doesn't use Prefect Server. It covers the primary use case (cancelling long-running multi-layer flows). You can layer Prefect-native cancellation on top later if you add Prefect Cloud.

#### Implementation: Cooperative DB Polling

##### [MODIFY] [pipelines.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/pipelines.py)

Add a check before each pipeline task runs:

```python
class FlowCancelledError(Exception):
    """Raised when a flow is cancelled via the API."""

def _check_cancellation(orchestration_run_id: str):
    """Check if this flow has been cancelled."""
    run = db.get_orchestration_run(orchestration_run_id)
    if run and run.get("status") == "cancelled":
        raise FlowCancelledError(f"Flow {orchestration_run_id} was cancelled")

# Called at the start of every task wrapper:
def run_prebronze_to_bronze(orchestration_run_id, ...):
    _check_cancellation(orchestration_run_id)
    # ... rest of the task ...
```

##### [MODIFY] [api.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/api.py)

```python
@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    """Cancel a running orchestration flow."""
    run = db.get_orchestration_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run["status"] not in ("pending", "running", "queued"):
        raise HTTPException(400, f"Cannot cancel run with status '{run['status']}'")

    db.update_orchestration_run(run_id, status="cancelled", completed_at=db._utcnow())
    return {"cancelled": True, "run_id": run_id}
```

---

### Feature 9: Checkpoint / Resume (Option A — New Run)

When resuming a failed run, create a **brand-new** `orchestration_run` linked to the original via `metadata.resumed_from`. This gives a cleaner audit trail.

#### [MODIFY] [db.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/db.py)

Add `list_completed_pipeline_runs(orchestration_run_id)` → returns pipeline names with `status='completed'`.

#### [MODIFY] [flows.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/flows.py)

Add `resume_from_run_id` parameter to `full_ingestion_flow` and `bronze_to_gold_flow`:

```python
def full_ingestion_flow(
    source_name: str,
    ...,
    resume_from_run_id: Optional[str] = None,  # NEW
):
    # Determine which layers to skip
    completed_layers = set()
    if resume_from_run_id:
        completed = db.list_completed_pipeline_runs(resume_from_run_id)
        completed_layers = {r["pipeline_name"] for r in completed}
        logger.info("Resuming from run %s. Skipping: %s", resume_from_run_id, completed_layers)

    # Create NEW orchestration run with lineage
    orch_run = db.create_orchestration_run(
        flow_name="full_ingestion",
        ...,
        config={**cfg, "resumed_from": resume_from_run_id},
    )

    # Layer execution with skip logic:
    if "prebronze_to_bronze" not in completed_layers:
        bronze_result = run_prebronze_to_bronze(...)
    else:
        logger.info("⏭️ Skipping prebronze_to_bronze (already completed)")

    if "bronze_to_silver" not in completed_layers:
        silver_result = run_bronze_to_silver(...)
    # ... etc.
```

#### [MODIFY] [api.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/api.py)

```python
@app.post("/api/runs/{run_id}/retry")
def retry_run(run_id: str):
    """Resume a failed run from the last completed layer."""
    original = db.get_orchestration_run(run_id)
    if not original:
        raise HTTPException(404, "Run not found")
    if original["status"] != "failed":
        raise HTTPException(400, "Can only retry failed runs")

    # Re-trigger the same flow with resume_from_run_id
    flow_fn = FLOW_REGISTRY.get(original["flow_name"])
    original_config = original.get("config", {})
    return flow_fn(
        **original_config,
        trigger_type="retry",
        triggered_by=f"retry:{run_id}",
        resume_from_run_id=run_id,
    )
```

---

### Feature 10: Dashboard Updates (Scoped Separately)

> [!IMPORTANT]
> Dashboard UI work is **deferred** until the backend APIs are complete and tested. This section is a reference for the future dashboard implementation.

The dashboard will need UI additions for the new features:

| Component | What to Add | Depends on API |
|-----------|-------------|----------------|
| **Pipeline Settings** | Editable timeout_seconds field per pipeline | `PUT /api/pipelines/{name}/settings` |
| **Run Detail Page** | "Cancel" and "Retry" buttons | `POST /api/runs/{id}/cancel`, `POST /api/runs/{id}/retry` |
| **DLQ Page** | New page listing dead-letter entries with "Retry" and "Discard" buttons | `GET/POST/DELETE /api/dead-letters` |
| **Metrics Tab** | Per-run tab showing tool_metrics timeline and LLM usage breakdown (tokens, cost) | Reads from `pipeline_tool_metrics` + `pipeline_llm_usage` tables |
| **Success Indicators** | Green success banners on the runs list page | Reads `status = 'completed'` from `orchestration_runs` |

---

## Summary: Files Changed

### New Files

| File | Purpose |
|------|---------|
| [NEW] `orchestrator/contracts.py` | Pydantic models for strict pipeline return dict validation |
| [NEW] `orchestrator/dlq_worker.py` | Background auto-retry worker for dead-letter queue |

### Modified Files

| File | Changes |
|------|---------|
| [orchestration_schema.sql](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/sql/orchestration_schema.sql) | Add `timeout_seconds` column, `webhook_dead_letter` table, `pipeline_tool_metrics` table, `pipeline_llm_usage` table |
| [config.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/config.py) | Add DLQ retry settings |
| [db.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/db.py) | Add ~8 new CRUD methods (timeouts, concurrency check, DLQ, tool metrics, LLM usage, completed pipeline runs) |
| [models.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/models.py) | Add `IngestionInput` validation model |
| [pipelines.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/pipelines.py) | Add timeout wrapper, cancellation check, contract validation, metrics extraction |
| [flows.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/flows.py) | Add concurrency guards, input validation, success notifications, resume logic |
| [triggers.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/triggers.py) | Add DLQ on webhook failure |
| [alerts.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/alerts.py) | Support `severity="info"` for success notifications |
| [api.py](file:///c:/Users/Sourav%20Patil/Desktop/ASM/Orchestration%20Pipeline/orchestrator/orchestrator/api.py) | Add endpoints: pipeline settings, cancel, retry, dead-letters CRUD |

---

## Verification Plan

### Automated Tests

```bash
cd "c:\Users\Sourav Patil\Desktop\ASM\Orchestration Pipeline\orchestrator"
python -m pytest tests/ -v
```

New test files/additions:

- `tests/test_contracts.py` — validate `PreBronzeResult`, `TransformResult` with valid/invalid data
- `tests/test_flows.py` — concurrency guards, input validation, resume skip logic
- `tests/test_triggers.py` — DLQ creation on webhook failure
- `tests/test_api.py` — new endpoint tests (cancel, retry, dead-letters, pipeline settings)

### Manual Verification

1. **Timeouts**: Set `timeout_seconds=5` on a test pipeline via API, trigger a long run, verify `timed_out` status
2. **Concurrency**: Fire two `POST /api/trigger` calls simultaneously → second should fail with `ConcurrentRunError`
3. **DLQ**: Send a webhook with a payload that triggers a crash → verify DLQ entry appears → verify auto-retry fires
4. **Cancel**: Start `full_ingestion`, immediately call `POST /api/runs/{id}/cancel` → verify next layer is skipped
5. **Resume**: Start `full_ingestion`, let PreBronze succeed, fail at Bronze→Silver → call `POST /api/runs/{id}/retry` → verify PreBronze is skipped

---

## Decisions Log

| # | Question | Decision | Date |
|---|----------|----------|------|
| 1 | Contract strictness | **Lenient** (`extra = "allow"`) initially, switch to strict after pipeline teams update | 2026-02-22 |
| 2 | DLQ retry timing | **Conservative**: 1min → 5min → 30min, max 3 retries | 2026-02-22 |
| 3 | LLM tracking scope | **All 4 pipelines** including Gold→Neo4j (agentic schema drift uses LLM) | 2026-02-22 |
| 4 | Dashboard scope | **Separate** — backend APIs built first, dashboard UI scoped later | 2026-02-22 |
| 5 | Cancellation approach | **Cooperative DB polling** (no Prefect Server dependency) | 2026-02-22 |
| 6 | Resume semantics | **Option A** — new orchestration_run with `resumed_from` metadata | 2026-02-22 |
