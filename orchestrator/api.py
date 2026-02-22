"""
API Server
===========

FastAPI application with webhook endpoints and run management APIs.

Start with::

    uvicorn orchestrator.api:app --host 0.0.0.0 --port 8100

Or via the CLI::

    python -m orchestrator.cli serve
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .config import settings
from .models import RunSummaryResponse, TriggerRequest
from .triggers import handle_webhook_event

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Data Orchestrator API",
    description="Production-grade orchestration for medallion architecture pipelines",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════
# Health Check
# ══════════════════════════════════════════════════════

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "orchestrator"}


# ══════════════════════════════════════════════════════
# Webhook Endpoint (Supabase Database Webhooks)
# ══════════════════════════════════════════════════════

@app.post("/webhooks/supabase")
async def receive_supabase_webhook(
    request: Request,
    x_webhook_secret: Optional[str] = Header(None, alias="x-webhook-secret"),
):
    """
    Receive a Supabase database webhook event.

    Supabase sends POST requests with a JSON body containing:
    - type: INSERT / UPDATE / DELETE
    - table: table name
    - schema: schema name
    - record: the new/updated row
    - old_record: the previous row (for UPDATE/DELETE)
    """
    # Verify webhook secret if configured
    if settings.webhook_secret:
        if x_webhook_secret != settings.webhook_secret:
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

    payload = await request.json()
    logger.info(
        "Received webhook: %s on %s.%s",
        payload.get("type"),
        payload.get("schema"),
        payload.get("table"),
    )

    try:
        result = handle_webhook_event(payload)
        return {"status": "accepted", "result": _safe_serialise(result)}
    except Exception as exc:
        logger.error("Webhook processing failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ══════════════════════════════════════════════════════
# Manual Trigger Endpoint
# ══════════════════════════════════════════════════════

@app.post("/api/trigger")
async def trigger_flow(request: TriggerRequest):
    """
    Manually trigger an orchestration flow via API.

    Example request body::

        {
            "flow_name": "full_ingestion",
            "source_name": "walmart",
            "input_path": "/data/walmart_products.csv",
            "batch_size": 500
        }
    """
    from .flows import FLOW_REGISTRY

    flow_fn = FLOW_REGISTRY.get(request.flow_name)
    if not flow_fn:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown flow: {request.flow_name}. "
                   f"Available: {list(FLOW_REGISTRY.keys())}",
        )

    config = {
        "batch_size": request.batch_size,
        "incremental": request.incremental,
        "dry_run": request.dry_run,
    }

    try:
        # Build kwargs depending on flow type
        kwargs: Dict[str, Any] = {
            "trigger_type": "api",
            "triggered_by": "api:/api/trigger",
            "config": config,
        }

        if request.flow_name == "full_ingestion":
            kwargs["source_name"] = request.source_name or "api"
            kwargs["input_path"] = request.input_path

        elif request.flow_name == "single_layer":
            kwargs["layer"] = request.layers[0] if request.layers else "bronze_to_silver"
            kwargs["source_name"] = request.source_name

        result = flow_fn(**kwargs)
        return {"status": "completed", "result": _safe_serialise(result)}

    except Exception as exc:
        logger.error("API trigger failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ══════════════════════════════════════════════════════
# Run Management Endpoints
# ══════════════════════════════════════════════════════

@app.get("/api/runs")
async def list_runs(
    limit: int = 20,
    status: Optional[str] = None,
):
    """List recent orchestration runs."""
    runs = db.list_orchestration_runs(limit=limit, status=status)
    return {"runs": runs, "count": len(runs)}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    """Get details of a specific orchestration run, including pipeline runs."""
    orch_run = db.get_orchestration_run(run_id)
    if not orch_run:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return orch_run


@app.get("/api/pipelines")
async def list_pipelines():
    """List all registered pipeline definitions."""
    pipelines = db.list_pipeline_definitions()
    return {"pipelines": pipelines, "count": len(pipelines)}


# ══════════════════════════════════════════════════════
# Dashboard Stats + Run Detail
# ══════════════════════════════════════════════════════

@app.get("/api/stats")
async def dashboard_stats():
    """Aggregated stats for the dashboard overview."""
    runs = db.list_orchestration_runs(limit=100)
    alerts = db.get_alerts(limit=50)

    total = len(runs)
    completed = sum(1 for r in runs if r.get("status") == "completed")
    failed = sum(1 for r in runs if r.get("status") == "failed")
    running = sum(1 for r in runs if r.get("status") in ("running", "pending"))

    durations = [
        r["duration_seconds"]
        for r in runs
        if r.get("duration_seconds") is not None
    ]
    avg_duration = round(sum(durations) / len(durations), 1) if durations else 0

    total_records = sum(r.get("total_records_written", 0) or 0 for r in runs)
    active_alerts = sum(
        1 for a in alerts
        if a.get("severity") in ("critical", "warning")
    )

    return {
        "total_runs": total,
        "completed": completed,
        "failed": failed,
        "running": running,
        "success_rate": round(completed / total * 100, 1) if total > 0 else 0,
        "avg_duration_seconds": avg_duration,
        "total_records_written": total_records,
        "active_alerts": active_alerts,
    }


@app.get("/api/runs/{run_id}/steps")
async def get_run_steps(run_id: str):
    """Get pipeline runs and their step logs for a specific orchestration run."""
    orch_run = db.get_orchestration_run(run_id)
    if not orch_run:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

    pipeline_runs = db.list_pipeline_runs(orchestration_run_id=run_id)

    # Attach step logs to each pipeline run
    for pr in pipeline_runs:
        pr["steps"] = db.list_step_logs(pipeline_run_id=pr["id"])

    return {
        "orchestration_run": orch_run,
        "pipeline_runs": pipeline_runs,
    }

@app.post("/api/neo4j/sync")
async def trigger_neo4j_sync(layer: str = "all"):
    """Trigger a Gold→Neo4j batch sync."""
    from .flows import neo4j_batch_sync_flow
    try:
        result = neo4j_batch_sync_flow(
            layer=layer,
            trigger_type="api",
            triggered_by="api:/api/neo4j/sync",
        )
        return {"status": "completed", "result": _safe_serialise(result)}
    except Exception as exc:
        logger.error("Neo4j sync trigger failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/neo4j/sync-status")
async def neo4j_sync_status():
    """Get the latest Neo4j sync run status."""
    runs = db.list_orchestration_runs(limit=5, status=None)
    neo4j_runs = [r for r in runs if r.get("flow_name") == "neo4j_batch_sync"]
    return {"latest_runs": neo4j_runs[:3]}


@app.get("/api/neo4j/reconciliation")
async def neo4j_reconciliation_status():
    """Get the latest reconciliation results."""
    runs = db.list_orchestration_runs(limit=5, status=None)
    recon_runs = [r for r in runs if r.get("flow_name") == "neo4j_reconciliation"]
    return {"latest_runs": recon_runs[:3]}


# ══════════════════════════════════════════════════════
# Alert Endpoints
# ══════════════════════════════════════════════════════

@app.get("/api/alerts")
async def list_alerts(
    limit: int = 50,
    severity: Optional[str] = None,
    pipeline_name: Optional[str] = None,
):
    """List recent alerts."""
    alerts = db.get_alerts(limit=limit, severity=severity, pipeline_name=pipeline_name)
    return {"alerts": alerts, "count": len(alerts)}


# ══════════════════════════════════════════════════════
# Coolify Webhook
# ══════════════════════════════════════════════════════

@app.post("/webhooks/coolify")
async def receive_coolify_webhook(request: Request):
    """Handle post-deploy webhook from Coolify."""
    payload = await request.json()
    logger.info("Received Coolify webhook: %s", payload.get("event", "unknown"))
    return {"status": "acknowledged", "event": payload.get("event")}


# ══════════════════════════════════════════════════════
# Pipeline Health
# ══════════════════════════════════════════════════════

@app.get("/api/pipelines/{name}/health")
async def pipeline_health(name: str):
    """Get health status for a specific pipeline."""
    pipeline_def = db.get_pipeline_definition(name)
    if not pipeline_def:
        raise HTTPException(status_code=404, detail=f"Pipeline not found: {name}")

    recent_runs = db.list_orchestration_runs(limit=5)
    pipeline_runs = [r for r in recent_runs if name in r.get("layers", [])]

    last_run = pipeline_runs[0] if pipeline_runs else None
    return {
        "pipeline": name,
        "active": pipeline_def.get("is_active", False),
        "last_run_status": last_run.get("status") if last_run else None,
        "last_run_at": last_run.get("started_at") if last_run else None,
    }


# ══════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════

def _safe_serialise(obj: Any) -> Any:
    """Make an object JSON-serialisable for API responses."""
    if isinstance(obj, dict):
        return {k: _safe_serialise(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_safe_serialise(v) for v in obj]
    if hasattr(obj, "__dict__"):
        return str(obj)
    return obj
