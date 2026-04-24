"""
Database Layer
===============

Supabase client helper and CRUD operations for the ``orchestration`` schema.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

import contextvars

from .config import settings

logger = logging.getLogger(__name__)

# ── Environment context ──────────────────────────────
# Controls which Supabase project is used for DB operations.
# Defaults to "production"; set to "testing" at the API/flow layer.
_active_env: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_active_env", default="production"
)


def set_env(env: str = "production") -> None:
    """Set the active environment for the current async context / thread."""
    _active_env.set(env)


def get_env() -> str:
    """Return the current active environment."""
    return _active_env.get()


# ── Supabase client singletons ───────────────────────

_prod_client = None
_test_client = None


def _get_prod_client():
    """Return a cached production Supabase client."""
    global _prod_client
    if _prod_client is not None:
        return _prod_client
    try:
        from supabase import create_client
    except ImportError:
        raise ImportError(
            "supabase is not installed. Run: pip install supabase"
        )
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env"
        )
    _prod_client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    logger.info("Supabase PRODUCTION client initialised for %s", settings.supabase_url)
    return _prod_client


def _get_test_client():
    """Return a cached testing Supabase client."""
    global _test_client
    if _test_client is not None:
        return _test_client
    try:
        from supabase import create_client
    except ImportError:
        raise ImportError(
            "supabase is not installed. Run: pip install supabase"
        )
    if not settings.supabase_test_url or not settings.supabase_test_service_role_key:
        raise RuntimeError(
            "SUPABASE_TEST_URL and SUPABASE_TEST_SERVICE_ROLE_KEY must be set "
            "to use the testing environment"
        )
    _test_client = create_client(settings.supabase_test_url, settings.supabase_test_service_role_key)
    logger.info("Supabase TESTING client initialised for %s", settings.supabase_test_url)
    return _test_client


def get_supabase_client():
    """Return cached Supabase client for the ACTIVE environment.

    Call ``set_env("testing")`` before invoking this in a test context.
    """
    env = _active_env.get()
    if env == "testing":
        return _get_test_client()
    return _get_prod_client()


def get_storage_client():
    """Return the PRODUCTION Supabase client for storage-bucket access.

    Storage buckets (raw-data, orchestration-logs, testing-orchestration-logs)
    always live in the production project.
    """
    return _get_prod_client()


def _orch_table(table_name: str):
    """Shortcut: query a table in the orchestration schema."""
    client = get_supabase_client()
    return client.schema("orchestration").table(table_name)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ══════════════════════════════════════════════════════
# Pipeline Definitions
# ══════════════════════════════════════════════════════

def get_pipeline_definition(pipeline_name: str) -> Optional[Dict[str, Any]]:
    """Lookup a pipeline definition by name."""
    result = (
        _orch_table("pipeline_definitions")
        .select("*")
        .eq("pipeline_name", pipeline_name)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


def list_pipeline_definitions(active_only: bool = True) -> List[Dict[str, Any]]:
    """List all registered pipeline definitions."""
    q = _orch_table("pipeline_definitions").select("*")
    if active_only:
        q = q.eq("is_active", True)
    return (q.execute()).data or []


# ══════════════════════════════════════════════════════
# Orchestration Runs
# ══════════════════════════════════════════════════════

_FLOW_GROUP_MAP = {
    "neo4j_graphsage_retrain": "graphsage",
    "neo4j_graphsage_inference": "graphsage",
}


def create_orchestration_run(
    flow_name: str,
    trigger_type: str = "manual",
    triggered_by: Optional[str] = None,
    flow_type: str = "batch",
    layers: Optional[List[str]] = None,
    config: Optional[Dict[str, Any]] = None,
    source_name: Optional[str] = None,
    vendor_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Insert a new orchestration_run and return the record.

    If a partial unique index prevents a duplicate running row, the
    INSERT will raise a unique-violation that we convert to a
    ``ConcurrentRunError`` so the flow layer can handle it uniformly.
    """
    payload = {
        "flow_name": flow_name,
        "flow_type": flow_type,
        "status": "running",
        "trigger_type": trigger_type,
        "triggered_by": triggered_by,
        "layers": layers or [
            "prebronze_to_bronze", "bronze_to_silver", "silver_to_gold"
        ],
        "started_at": _utcnow(),
        "config": config or {},
    }
    # Set flow_group for mutual-exclusion enforcement
    flow_group = _FLOW_GROUP_MAP.get(flow_name)
    if flow_group:
        payload["flow_group"] = flow_group
    if source_name:
        payload["source_name"] = source_name
    if vendor_id:
        payload["vendor_id"] = vendor_id
    try:
        result = _orch_table("orchestration_runs").insert(payload).execute()
    except Exception as exc:
        err = str(exc).lower()
        if "unique" in err or "duplicate" in err or "23505" in err:
            from .flows import ConcurrentRunError
            raise ConcurrentRunError(flow_name) from exc
        raise
    row = (result.data or [None])[0]
    logger.info("Created orchestration_run %s (flow=%s, source=%s)", row["id"], flow_name, source_name)
    return row


def update_orchestration_run(
    run_id: str | UUID,
    *,
    status: Optional[str] = None,
    current_layer: Optional[str] = None,
    total_records_processed: Optional[int] = None,
    total_records_written: Optional[int] = None,
    total_dq_issues: Optional[int] = None,
    total_errors: Optional[int] = None,
    completed_at: Optional[str] = None,
    duration_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """Update fields on an orchestration_run."""
    patch: Dict[str, Any] = {}
    if status is not None:
        patch["status"] = status
    if current_layer is not None:
        patch["current_layer"] = current_layer
    if total_records_processed is not None:
        patch["total_records_processed"] = total_records_processed
    if total_records_written is not None:
        patch["total_records_written"] = total_records_written
    if total_dq_issues is not None:
        patch["total_dq_issues"] = total_dq_issues
    if total_errors is not None:
        patch["total_errors"] = total_errors
    if completed_at is not None:
        patch["completed_at"] = completed_at
    if duration_seconds is not None:
        patch["duration_seconds"] = duration_seconds
    if not patch:
        return {}
    result = (
        _orch_table("orchestration_runs")
        .update(patch)
        .eq("id", str(run_id))
        .execute()
    )
    return (result.data or [{}])[0]


def get_orchestration_run(run_id: str | UUID) -> Optional[Dict[str, Any]]:
    """Fetch a single orchestration_run by id."""
    result = (
        _orch_table("orchestration_runs")
        .select("*")
        .eq("id", str(run_id))
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


def list_orchestration_runs(
    limit: int = 20,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List recent orchestration runs."""
    q = (
        _orch_table("orchestration_runs")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
    )
    if status:
        q = q.eq("status", status)
    return (q.execute()).data or []


# ══════════════════════════════════════════════════════
# Pipeline Runs
# ══════════════════════════════════════════════════════

def create_pipeline_run(
    pipeline_name: str,
    orchestration_run_id: str | UUID,
    trigger_type: str = "manual",
    triggered_by: Optional[str] = None,
    source_table: Optional[str] = None,
    target_table: Optional[str] = None,
    batch_size: int = 100,
    incremental: bool = True,
    dry_run: bool = False,
    run_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Insert a new pipeline_run and return the record."""
    pipeline_def = get_pipeline_definition(pipeline_name)
    pipeline_id = pipeline_def["id"] if pipeline_def else None

    payload = {
        "pipeline_id": pipeline_id,
        "orchestration_run_id": str(orchestration_run_id),
        "status": "running",
        "trigger_type": trigger_type,
        "triggered_by": triggered_by,
        "source_table": source_table,
        "target_table": target_table,
        "batch_size": batch_size,
        "incremental": incremental,
        "dry_run": dry_run,
        "run_config": run_config or {},
        "started_at": _utcnow(),
    }
    result = _orch_table("pipeline_runs").insert(payload).execute()
    row = (result.data or [None])[0]
    logger.info(
        "Created pipeline_run %s (pipeline=%s, orch_run=%s)",
        row["id"], pipeline_name, orchestration_run_id,
    )
    return row


def update_pipeline_run(
    run_id: str | UUID,
    *,
    status: Optional[str] = None,
    records_input: Optional[int] = None,
    records_processed: Optional[int] = None,
    records_written: Optional[int] = None,
    records_skipped: Optional[int] = None,
    records_failed: Optional[int] = None,
    dq_issues_found: Optional[int] = None,
    completed_at: Optional[str] = None,
    duration_seconds: Optional[float] = None,
    error_message: Optional[str] = None,
    error_details: Optional[Dict[str, Any]] = None,
    run_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Update fields on a pipeline_run."""
    patch: Dict[str, Any] = {}
    for field_name in (
        "status", "records_input", "records_processed", "records_written",
        "records_skipped", "records_failed", "dq_issues_found",
        "completed_at", "duration_seconds", "error_message", "error_details",
        "run_config",
    ):
        val = locals()[field_name]
        if val is not None:
            patch[field_name] = val
    if not patch:
        return {}
    result = (
        _orch_table("pipeline_runs")
        .update(patch)
        .eq("id", str(run_id))
        .execute()
    )
    return (result.data or [{}])[0]


# ══════════════════════════════════════════════════════
# Step Logs
# ══════════════════════════════════════════════════════

def create_step_log(
    pipeline_run_id: str | UUID,
    step_name: str,
    step_order: int,
) -> Dict[str, Any]:
    """Insert a pending step log entry."""
    payload = {
        "pipeline_run_id": str(pipeline_run_id),
        "step_name": step_name,
        "step_order": step_order,
        "status": "running",
        "started_at": _utcnow(),
    }
    result = _orch_table("pipeline_step_logs").insert(payload).execute()
    return (result.data or [{}])[0]


def update_step_log(
    step_id: str | UUID,
    *,
    status: Optional[str] = None,
    records_in: Optional[int] = None,
    records_out: Optional[int] = None,
    records_error: Optional[int] = None,
    state_delta: Optional[Dict[str, Any]] = None,
    duration_ms: Optional[int] = None,
    error_message: Optional[str] = None,
    error_traceback: Optional[str] = None,
) -> Dict[str, Any]:
    """Update a step log entry."""
    patch: Dict[str, Any] = {}
    for field_name in (
        "status", "records_in", "records_out", "records_error",
        "state_delta", "duration_ms", "error_message", "error_traceback",
    ):
        val = locals()[field_name]
        if val is not None:
            patch[field_name] = val
    if status in ("completed", "failed"):
        patch["completed_at"] = _utcnow()
    if not patch:
        return {}
    result = (
        _orch_table("pipeline_step_logs")
        .update(patch)
        .eq("id", str(step_id))
        .execute()
    )
    return (result.data or [{}])[0]


# ══════════════════════════════════════════════════════
# DQ Summary
# ══════════════════════════════════════════════════════

def create_dq_summary(
    pipeline_run_id: str | UUID,
    table_name: str,
    total_records: int = 0,
    pass_count: int = 0,
    fail_count: int = 0,
    avg_quality_score: Optional[float] = None,
    issues_by_type: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Insert a DQ summary row."""
    payload = {
        "pipeline_run_id": str(pipeline_run_id),
        "table_name": table_name,
        "total_records": total_records,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "avg_quality_score": avg_quality_score,
        "issues_by_type": issues_by_type or {},
    }
    result = _orch_table("run_dq_summary").insert(payload).execute()
    return (result.data or [{}])[0]


# ══════════════════════════════════════════════════════
# Pipeline Runs — Queries
# ══════════════════════════════════════════════════════

def list_pipeline_runs(
    orchestration_run_id: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """List pipeline runs, optionally filtered by orchestration run."""
    q = (
        _orch_table("pipeline_runs")
        .select("*")
        .order("started_at", desc=True)
        .limit(limit)
    )
    if orchestration_run_id:
        q = q.eq("orchestration_run_id", orchestration_run_id)
    return (q.execute()).data or []


# ══════════════════════════════════════════════════════
# Step Logs — Queries
# ══════════════════════════════════════════════════════

def list_step_logs(
    pipeline_run_id: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """List step logs, optionally filtered by pipeline run."""
    q = (
        _orch_table("pipeline_step_logs")
        .select("*")
        .order("step_order", desc=False)
        .limit(limit)
    )
    if pipeline_run_id:
        q = q.eq("pipeline_run_id", pipeline_run_id)
    return (q.execute()).data or []


# ══════════════════════════════════════════════════════
# Schedules & Event Triggers
# ══════════════════════════════════════════════════════

def list_active_schedules() -> List[Dict[str, Any]]:
    """List active schedule definitions."""
    return (
        _orch_table("schedule_definitions")
        .select("*")
        .eq("is_active", True)
        .execute()
    ).data or []


def list_active_event_triggers() -> List[Dict[str, Any]]:
    """List active event trigger definitions."""
    return (
        _orch_table("event_triggers")
        .select("*")
        .eq("is_active", True)
        .execute()
    ).data or []


def list_all_schedules() -> List[Dict[str, Any]]:
    """List ALL schedule definitions (including inactive) for dashboard display."""
    return (
        _orch_table("schedule_definitions")
        .select("*")
        .order("schedule_name", desc=False)
        .execute()
    ).data or []


def get_schedule(schedule_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single schedule definition by id."""
    result = (
        _orch_table("schedule_definitions")
        .select("*")
        .eq("id", str(schedule_id))
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


def update_schedule(schedule_id: str, **fields: Any) -> Dict[str, Any]:
    """Update a schedule definition (is_active, cron_expression, etc)."""
    allowed = {"is_active", "cron_expression", "run_config", "flow_name"}
    patch = {k: v for k, v in fields.items() if k in allowed}
    patch["updated_at"] = _utcnow()
    if not patch or patch.keys() == {"updated_at"}:
        return {}
    result = (
        _orch_table("schedule_definitions")
        .update(patch)
        .eq("id", str(schedule_id))
        .execute()
    )
    return (result.data or [{}])[0]


def list_all_event_triggers() -> List[Dict[str, Any]]:
    """List ALL event trigger definitions (including inactive) for dashboard."""
    return (
        _orch_table("event_triggers")
        .select("*")
        .order("trigger_name", desc=False)
        .execute()
    ).data or []


def update_event_trigger(trigger_id: str, **fields: Any) -> Dict[str, Any]:
    """Update an event trigger definition (is_active, debounce_seconds, etc)."""
    allowed = {"is_active", "debounce_seconds", "filter_config"}
    patch = {k: v for k, v in fields.items() if k in allowed}
    patch["updated_at"] = _utcnow()
    if not patch or patch.keys() == {"updated_at"}:
        return {}
    result = (
        _orch_table("event_triggers")
        .update(patch)
        .eq("id", str(trigger_id))
        .execute()
    )
    return (result.data or [{}])[0]


# ══════════════════════════════════════════════════════
# Data Sources
# ══════════════════════════════════════════════════════

def list_data_sources(
    category: Optional[str] = None,
    status: Optional[str] = None,
    active_only: bool = False,
) -> List[Dict[str, Any]]:
    """List all data sources, optionally filtered by category/status."""
    q = (
        _orch_table("data_sources")
        .select("*")
        .order("category", desc=False)
    )
    if category:
        q = q.eq("category", category)
    if status:
        q = q.eq("status", status)
    if active_only:
        q = q.eq("is_active", True)
    return (q.execute()).data or []


def get_data_source(source_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single data source by id."""
    result = (
        _orch_table("data_sources")
        .select("*")
        .eq("id", str(source_id))
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


def get_data_source_by_name(source_name: str) -> Optional[Dict[str, Any]]:
    """Fetch a single data source by source_name."""
    result = (
        _orch_table("data_sources")
        .select("*")
        .eq("source_name", source_name)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


def update_data_source(source_id: str, **fields: Any) -> Dict[str, Any]:
    """Update a data source (status, total_ingested, last_ingested_at, etc)."""
    allowed = {"status", "total_ingested", "last_ingested_at", "is_active"}
    patch = {k: v for k, v in fields.items() if k in allowed}
    patch["updated_at"] = _utcnow()
    if not patch or patch.keys() == {"updated_at"}:
        return {}
    result = (
        _orch_table("data_sources")
        .update(patch)
        .eq("id", str(source_id))
        .execute()
    )
    return (result.data or [{}])[0]


# ══════════════════════════════════════════════════════
# Data Source Cursors
# ══════════════════════════════════════════════════════

def list_data_source_cursors(
    data_source_id: str,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """List cursor records for a data source (most recent first)."""
    return (
        _orch_table("data_source_cursors")
        .select("*")
        .eq("data_source_id", str(data_source_id))
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    ).data or []


def get_latest_cursor(data_source_id: str) -> Optional[Dict[str, Any]]:
    """Get the most recent cursor for a data source (for resumable ingestion)."""
    result = (
        _orch_table("data_source_cursors")
        .select("*")
        .eq("data_source_id", str(data_source_id))
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


def create_data_source_cursor(
    data_source_id: str,
    cursor_start: int = 0,
    cursor_end: int = 0,
    batch_size: int = 100,
    orchestration_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new cursor batch record for tracking ingestion progress."""
    payload: Dict[str, Any] = {
        "data_source_id": str(data_source_id),
        "cursor_type": "row_offset",
        "cursor_start": cursor_start,
        "cursor_end": cursor_end,
        "batch_size": batch_size,
        "status": "pending",
        "records_processed": 0,
        "records_written": 0,
    }
    if orchestration_run_id:
        payload["orchestration_run_id"] = str(orchestration_run_id)
    result = _orch_table("data_source_cursors").insert(payload).execute()
    row = (result.data or [None])[0]
    logger.info(
        "Created data_source_cursor %s (source=%s, start=%d, end=%d)",
        row["id"], data_source_id, cursor_start, cursor_end,
    )
    return row


def update_data_source_cursor(cursor_id: str, **fields: Any) -> Dict[str, Any]:
    """Update a cursor record (status, records_processed, etc)."""
    allowed = {
        "status", "cursor_end", "records_processed", "records_written",
        "records_skipped", "records_failed", "error_message", "error_details",
        "completed_at",
    }
    patch = {k: v for k, v in fields.items() if k in allowed}
    if not patch:
        return {}
    result = (
        _orch_table("data_source_cursors")
        .update(patch)
        .eq("id", str(cursor_id))
        .execute()
    )
    return (result.data or [{}])[0]


# ══════════════════════════════════════════════════════
# Alert Log
# ══════════════════════════════════════════════════════

def create_alert_log(
    alert_type: str,
    severity: str,
    title: str,
    message: Optional[str] = None,
    pipeline_name: Optional[str] = None,
    run_id: Optional[str] = None,
    dispatch_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Insert an alert log entry."""
    payload = {
        "alert_type": alert_type,
        "severity": severity,
        "title": title,
        "message": message,
        "pipeline_name": pipeline_name,
        "run_id": run_id,
        "dispatch_result": dispatch_result or {},
    }
    result = _orch_table("alert_log").insert(payload).execute()
    return (result.data or [{}])[0]


def get_alerts(
    limit: int = 50,
    severity: Optional[str] = None,
    pipeline_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List recent alerts, optionally filtered."""
    q = (
        _orch_table("alert_log")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
    )
    if severity:
        q = q.eq("severity", severity)
    if pipeline_name:
        q = q.eq("pipeline_name", pipeline_name)
    return (q.execute()).data or []


# ══════════════════════════════════════════════════════
# Pipeline Definitions — Settings
# ══════════════════════════════════════════════════════

def get_pipeline_timeout(pipeline_name: str) -> int | None:
    """Return the configured timeout in seconds for a pipeline, or None."""
    defn = get_pipeline_definition(pipeline_name)
    if defn:
        return defn.get("timeout_seconds")
    return None


def update_pipeline_definition(
    pipeline_name: str,
    **fields: Any,
) -> Dict[str, Any]:
    """Update arbitrary fields on a pipeline definition (e.g. timeout_seconds)."""
    allowed = {"timeout_seconds", "is_active", "default_config", "description"}
    patch = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not patch:
        return {}
    patch["updated_at"] = _utcnow()
    result = (
        _orch_table("pipeline_definitions")
        .update(patch)
        .eq("pipeline_name", pipeline_name)
        .execute()
    )
    return (result.data or [{}])[0]


# ══════════════════════════════════════════════════════
# Concurrent Run Prevention
# ══════════════════════════════════════════════════════

def has_running_flow(flow_name: str, source_name: Optional[str] = None) -> bool:
    """Check if a flow already has a run with status='running'.

    When *source_name* is provided, the check is scoped to that source,
    allowing different sources to run the same flow in parallel.
    """
    query = (
        _orch_table("orchestration_runs")
        .select("id")
        .eq("flow_name", flow_name)
        .eq("status", "running")
    )
    if source_name:
        query = query.eq("source_name", source_name)
    result = query.limit(1).execute()
    return len(result.data or []) > 0


def update_orchestration_run_metadata(
    run_id: str | UUID,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge instrumentation metadata into the orchestration_run's metadata JSONB.

    This is used by the parallel runner to store layer_timings, queue_depth,
    job_id, and other per-source operational data.
    """
    # Fetch current metadata and merge
    existing = get_orchestration_run(str(run_id))
    current_meta = (existing or {}).get("metadata", {}) or {}
    current_meta.update(metadata)

    result = (
        _orch_table("orchestration_runs")
        .update({"metadata": current_meta})
        .eq("id", str(run_id))
        .execute()
    )
    return (result.data or [{}])[0]


# ══════════════════════════════════════════════════════
# Tool Metrics & LLM Usage
# ══════════════════════════════════════════════════════

def create_tool_metrics(
    pipeline_run_id: str | UUID,
    metrics_list: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Batch-insert tool-level metrics for a pipeline run."""
    if not metrics_list:
        return []
    rows = [
        {
            "pipeline_run_id": str(pipeline_run_id),
            "tool_name": m.get("tool_name") or m.get("tool", "unknown"),
            "duration_ms": round(m["duration_ms"]) if m.get("duration_ms") is not None else None,
            "records_in": m.get("records_in", 0),
            "records_out": m.get("records_out", 0),
            "status": m.get("status", "completed"),
            "error_message": m.get("error"),
            "metadata": m.get("metadata", {}),
        }
        for m in metrics_list
    ]
    result = _orch_table("pipeline_tool_metrics").insert(rows).execute()
    logger.info(
        "Stored %d tool metrics for pipeline_run %s",
        len(rows), pipeline_run_id,
    )
    return result.data or []


def create_llm_usage(
    pipeline_run_id: str | UUID,
    usage: Dict[str, Any],
) -> Dict[str, Any]:
    """Insert LLM usage summary for a pipeline run."""
    if not usage:
        return {}
    payload = {
        "pipeline_run_id": str(pipeline_run_id),
        "model": usage.get("model"),
        "total_prompt_tokens": usage.get("total_prompt_tokens") or usage.get("prompt_tokens", 0),
        "total_completion_tokens": usage.get("total_completion_tokens") or usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "total_cost_usd": usage.get("total_cost_usd") or usage.get("total_cost", 0),
        "llm_calls": usage.get("llm_calls", 0),
        "call_details": usage.get("calls", []),
    }
    result = _orch_table("pipeline_llm_usage").insert(payload).execute()
    logger.info(
        "Stored LLM usage for pipeline_run %s (tokens=%d, cost=$%.4f)",
        pipeline_run_id,
        usage.get("total_tokens", 0),
        usage.get("total_cost_usd", 0),
    )
    return (result.data or [{}])[0]


# ══════════════════════════════════════════════════════
# Webhook Dead-Letter Queue
# ══════════════════════════════════════════════════════

def create_dead_letter(
    payload: Dict[str, Any],
    error_message: str,
    error_details: Optional[Dict[str, Any]] = None,
    next_retry_at: Optional[str] = None,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """Insert a failed webhook event into the dead-letter queue."""
    row = {
        "payload": payload,
        "error_message": error_message,
        "error_details": error_details or {},
        "max_retries": max_retries,
        "status": "pending",
        "next_retry_at": next_retry_at,
    }
    result = _orch_table("webhook_dead_letter").insert(row).execute()
    logger.warning(
        "Webhook sent to DLQ: %s (next_retry=%s)",
        error_message, next_retry_at,
    )
    return (result.data or [{}])[0]


def list_dead_letters(
    status: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """List DLQ entries, optionally filtered by status."""
    query = (
        _orch_table("webhook_dead_letter")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
    )
    if status:
        query = query.eq("status", status)
    result = query.execute()
    return result.data or []


def get_retryable_dead_letters() -> List[Dict[str, Any]]:
    """Get DLQ entries that are due for retry (next_retry_at <= now)."""
    result = (
        _orch_table("webhook_dead_letter")
        .select("*")
        .in_("status", ["pending", "retrying"])
        .lte("next_retry_at", _utcnow())
        .order("next_retry_at", desc=False)
        .limit(50)
        .execute()
    )
    return result.data or []


def update_dead_letter(
    dead_letter_id: str | UUID,
    **fields: Any,
) -> Dict[str, Any]:
    """Update a DLQ entry (status, retry_count, next_retry_at, etc.)."""
    allowed = {
        "status", "retry_count", "next_retry_at",
        "last_retry_at", "resolved_at", "error_message",
    }
    patch = {k: v for k, v in fields.items() if k in allowed}
    if not patch:
        return {}
    result = (
        _orch_table("webhook_dead_letter")
        .update(patch)
        .eq("id", str(dead_letter_id))
        .execute()
    )
    return (result.data or [{}])[0]


# ══════════════════════════════════════════════════════
# Checkpoint / Resume Helpers
# ══════════════════════════════════════════════════════

def list_completed_pipeline_runs(
    orchestration_run_id: str | UUID,
) -> List[Dict[str, Any]]:
    """Return pipeline runs that completed successfully for a given orch run."""
    result = (
        _orch_table("pipeline_runs")
        .select("id, pipeline_name, status, run_config")
        .eq("orchestration_run_id", str(orchestration_run_id))
        .eq("status", "completed")
        .execute()
    )
    return result.data or []
