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

from .config import settings

logger = logging.getLogger(__name__)

# ── Supabase client singleton ─────────────────────────

_client = None


def get_supabase_client():
    """Return a cached Supabase client instance."""
    global _client
    if _client is not None:
        return _client
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
    _client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    logger.info("Supabase client initialised for %s", settings.supabase_url)
    return _client


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

def create_orchestration_run(
    flow_name: str,
    trigger_type: str = "manual",
    triggered_by: Optional[str] = None,
    flow_type: str = "batch",
    layers: Optional[List[str]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Insert a new orchestration_run and return the record."""
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
    result = _orch_table("orchestration_runs").insert(payload).execute()
    row = (result.data or [None])[0]
    logger.info("Created orchestration_run %s (flow=%s)", row["id"], flow_name)
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
) -> Dict[str, Any]:
    """Update fields on a pipeline_run."""
    patch: Dict[str, Any] = {}
    for field_name in (
        "status", "records_input", "records_processed", "records_written",
        "records_skipped", "records_failed", "dq_issues_found",
        "completed_at", "duration_seconds", "error_message", "error_details",
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
# Schedules & Event Triggers (read-only from DB)
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

