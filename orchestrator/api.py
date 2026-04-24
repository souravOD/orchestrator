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

import asyncio
import hashlib
import hmac
import json
import logging
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from . import db
from .config import settings
from .models import BatchTriggerRequest, RunSummaryResponse, TriggerRequest
from .triggers import handle_webhook_event

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Data Orchestrator API",
    description="Production-grade orchestration for medallion architecture pipelines",
    version="0.1.0",
)


@app.on_event("startup")
async def _start_dlq_worker():
    """Start the DLQ background retry worker."""
    from .dlq_worker import dlq_background_loop
    asyncio.create_task(dlq_background_loop())
    logger.info("DLQ background worker scheduled")


@app.on_event("startup")
async def _start_realtime_workers():
    """Auto-start N parallel outbox workers if enabled.

    Each worker runs as an independent daemon thread, claiming events
    via SELECT FOR UPDATE SKIP LOCKED — no coordination needed.

    Scale by changing NEO4J_REALTIME_WORKERS env var (default: 3).
    """
    import os
    import threading

    if os.getenv("NEO4J_REALTIME_AUTOSTART", "true").lower() != "true":
        logger.info("Realtime workers disabled (NEO4J_REALTIME_AUTOSTART=false)")
        return

    worker_count = int(os.getenv("NEO4J_REALTIME_WORKERS", "3"))
    batch_size = int(os.getenv("NEO4J_WORKER_BATCH_SIZE", "50"))
    poll_interval = int(os.getenv("NEO4J_REALTIME_POLL_INTERVAL", "5"))
    lock_timeout = int(os.getenv("NEO4J_WORKER_LOCK_TIMEOUT", "300"))
    hostname = os.getenv("HOSTNAME", "orch")[:8]

    def _run_worker(worker_id: str):
        try:
            from .neo4j_adapter import _ensure_pipeline_importable
            _ensure_pipeline_importable()
            from services.customer_realtime.service import OutboxWorker
            worker = OutboxWorker(
                worker_id=worker_id,
                batch_size=batch_size,
                poll_interval=poll_interval,
                lock_timeout=lock_timeout,
            )
            worker.run_loop()
        except Exception as exc:
            logger.error("Worker %s crashed: %s", worker_id, exc, exc_info=True)

    for i in range(worker_count):
        worker_id = f"w-{hostname}-{i:02d}"
        thread = threading.Thread(
            target=_run_worker,
            args=(worker_id,),
            name=f"outbox-worker-{i}",
            daemon=True,
        )
        thread.start()
        logger.info("🟢 Outbox worker started: %s", worker_id)

    logger.info(
        "🔴 %d outbox workers launched (batch_size=%d, poll=%ds, lock_timeout=%ds)",
        worker_count, batch_size, poll_interval, lock_timeout,
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

@app.post("/webhooks/supabase", status_code=202)
async def receive_supabase_webhook(
    request: Request,
    x_webhook_secret: Optional[str] = Header(None, alias="x-webhook-secret"),
):
    """
    Receive a Supabase database webhook event.

    Returns 202 Accepted immediately and processes the event in a
    background thread.  This is required because pg_net (the PostgreSQL
    HTTP extension that sends these webhooks) has a 5-second timeout,
    but batch sync flows take 30-120 seconds.

    If background processing fails, the payload is sent to the
    dead-letter queue (DLQ) for automatic retry.
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

    # Dispatch to background thread — return HTTP response immediately.
    asyncio.create_task(asyncio.to_thread(_process_webhook_background, payload))

    return {
        "status": "accepted",
        "type": payload.get("type"),
        "table": payload.get("table"),
    }


def _process_webhook_background(payload: Dict[str, Any]) -> None:
    """
    Background wrapper for webhook processing with DLQ error handling.

    Runs in a thread pool executor so the HTTP response is not blocked.
    """
    try:
        result = handle_webhook_event(payload)
        logger.info(
            "Webhook processed successfully: %s on %s.%s",
            payload.get("type"),
            payload.get("schema"),
            payload.get("table"),
        )
    except Exception as exc:
        # ── Non-retryable errors: don't pollute the DLQ ──
        exc_str = str(exc)
        if any(p in exc_str for p in (
            "ConcurrentRunError", "already running", "concurrent flow",
            "handled_by_outbox",
        )):
            logger.info(
                "Webhook skipped (non-retryable, not sent to DLQ): %s.%s — %s",
                payload.get("schema"), payload.get("table"), exc,
            )
            return

        logger.error("Webhook background processing failed — sending to DLQ: %s", exc)
        next_retry = (
            datetime.now(timezone.utc) + timedelta(minutes=1)
        ).isoformat()
        try:
            db.create_dead_letter(
                payload=payload,
                error_message=str(exc),
                error_details={"traceback": traceback.format_exc()},
                next_retry_at=next_retry,
                max_retries=settings.dlq_max_retries,
            )
        except Exception as dlq_exc:
            logger.error("Failed to insert into DLQ: %s", dlq_exc)


# ══════════════════════════════════════════════════════
# Manual Trigger Endpoint
# ══════════════════════════════════════════════════════

@app.post("/api/trigger")
async def trigger_flow(request: TriggerRequest):
    """
    Manually trigger an orchestration flow via API.

    Returns immediately with ``{"status": "accepted", "run_id": "..."}``
    while the flow runs in the background.  Poll ``GET /api/runs/{id}``
    for progress.
    """
    from .flows import FLOW_REGISTRY
    from . import db

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

    source_name = request.source_name or "api"

    try:
        # Determine layers based on flow
        if request.flow_name == "full_ingestion":
            layers = ["prebronze_to_bronze", "usda_nutrition_fetch", "bronze_to_silver", "silver_to_gold"]
        elif request.flow_name == "bronze_to_gold":
            layers = ["bronze_to_silver", "silver_to_gold"]
        else:
            layers = request.layers or ["bronze_to_silver"]

        # ── Set environment BEFORE creating run so it lands in correct DB ──
        environment = request.environment
        db.set_env(environment)

        # ── Create the orchestration_runs row NOW so we can return run_id ──
        orch_run = db.create_orchestration_run(
            flow_name=request.flow_name,
            trigger_type="api",
            triggered_by="api:/api/trigger",
            layers=layers,
            config=config,
            source_name=source_name,
            vendor_id=request.vendor_id,
        )
        orch_run_id = orch_run["id"]

        # Build kwargs depending on flow type
        kwargs: Dict[str, Any] = {
            "trigger_type": "api",
            "triggered_by": "api:/api/trigger",
            "config": config,
            "vendor_id": request.vendor_id,
            "orch_run_id": orch_run_id,
        }

        if request.flow_name == "full_ingestion":
            kwargs["source_name"] = source_name
            if request.storage_bucket and request.storage_path:
                kwargs["storage_bucket"] = request.storage_bucket
                kwargs["storage_path"] = request.storage_path
            else:
                kwargs["input_path"] = request.input_path

        elif request.flow_name == "single_layer":
            kwargs["layer"] = request.layers[0] if request.layers else "bronze_to_silver"
            kwargs["source_name"] = source_name

        elif request.flow_name == "bronze_to_gold":
            kwargs["source_name"] = source_name

        # Dispatch to background thread — API returns immediately.
        def _run():
            db.set_env(environment)  # re-set for thread context
            # In test mode, prefix storage paths with testing/
            if environment == "testing" and "storage_path" in kwargs:
                kwargs["storage_path"] = f"testing/{kwargs.get('storage_path', '')}"
            try:
                flow_fn(**kwargs)
            except Exception as exc:
                logger.error("API trigger flow failed: %s", exc)
                try:
                    db.update_orchestration_run(
                        orch_run_id,
                        status="failed",
                        total_errors=1,
                        completed_at=db._utcnow(),
                    )
                except Exception as update_exc:
                    logger.warning("Failed to mark trigger run %s as failed: %s", orch_run_id, update_exc)

        asyncio.create_task(asyncio.to_thread(_run))

        # Reset to production for subsequent API requests in this context
        db.set_env("production")

        return {
            "status": "accepted",
            "run_id": str(orch_run_id),
            "flow_name": request.flow_name,
        }

    except Exception as exc:
        logger.error("API trigger failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ══════════════════════════════════════════════════════
# Batch Trigger Endpoint (Multi-Source Parallel)
# ══════════════════════════════════════════════════════

@app.post("/api/trigger-batch")
async def trigger_batch(request: BatchTriggerRequest):
    """
    Trigger parallel ingestion for multiple data sources.

    Returns immediately with a ``batch_id`` and per-source ``run_id``s.
    Sources are processed in parallel with a concurrency limit
    (see ``PARALLEL_MAX_CONCURRENCY`` env var, default 2).
    """
    from .flows import multi_source_ingestion_flow

    # Pre-create an orchestration_run for each source so we can return run IDs now
    source_configs = []
    pre_created_run_ids: Dict[str, str] = {}
    source_results = []

    config = {
        "batch_size": request.batch_size,
        "incremental": request.incremental,
        "dry_run": request.dry_run,
    }

    try:
        for src in request.sources:
            orch_run = db.create_orchestration_run(
                flow_name=request.flow_name,
                trigger_type="api",
                triggered_by="api:/api/trigger-batch",
                layers=["prebronze_to_bronze", "usda_nutrition_fetch", "bronze_to_silver",
                        "silver_to_gold"],
                config=config,
                source_name=src.source_name,
                vendor_id=src.vendor_id,
            )
            run_id = orch_run["id"]
            pre_created_run_ids[src.source_name] = run_id
            source_configs.append(src.model_dump())
            source_results.append({
                "source_name": src.source_name,
                "run_id": str(run_id),
                "status": "queued",
            })

        # Generate batch ID
        import uuid
        batch_id = str(uuid.uuid4())[:12]

        # Dispatch to background — API returns immediately
        loop = asyncio.get_event_loop()
        loop.run_in_executor(
            None,
            lambda: multi_source_ingestion_flow(
                sources=source_configs,
                trigger_type="api",
                triggered_by="api:/api/trigger-batch",
                config=config,
                pre_created_run_ids=pre_created_run_ids,
            ),
        )

        logger.info(
            "🚀 Batch %s dispatched: %d sources (concurrency=%d)",
            batch_id, len(request.sources), settings.parallel_max_concurrency,
        )

        return {
            "status": "accepted",
            "batch_id": batch_id,
            "sources": source_results,
            "concurrency_limit": settings.parallel_max_concurrency,
            "queue_depth": len(request.sources),
        }

    except Exception as exc:
        logger.error("Batch trigger failed: %s", exc)
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


@app.put("/api/pipelines/{name}/settings")
async def update_pipeline_settings(name: str, request: Request):
    """Update a pipeline's settings (e.g. timeout_seconds, is_active)."""
    pipeline_def = db.get_pipeline_definition(name)
    if not pipeline_def:
        raise HTTPException(status_code=404, detail=f"Pipeline not found: {name}")

    body = await request.json()
    allowed_fields = {"timeout_seconds", "is_active", "description"}
    updates = {k: v for k, v in body.items() if k in allowed_fields}

    if not updates:
        raise HTTPException(
            status_code=400,
            detail=f"No valid fields to update. Allowed: {allowed_fields}",
        )

    result = db.update_pipeline_definition(name, **updates)
    logger.info("Updated pipeline %s settings: %s", name, updates)
    return {"pipeline": name, "updated": updates, "result": result}


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    """Cancel a running orchestration flow (cooperative DB polling)."""
    run = db.get_orchestration_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run["status"] not in ("pending", "running", "queued"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel run with status '{run['status']}'",
        )
    db.update_orchestration_run(
        run_id,
        status="cancelled",
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
    logger.info("Cancelled orchestration run %s", run_id)
    return {"cancelled": True, "run_id": run_id}


@app.post("/api/runs/{run_id}/retry")
async def retry_run(run_id: str):
    """Resume a failed run from the last completed layer."""
    original = db.get_orchestration_run(run_id)
    if not original:
        raise HTTPException(status_code=404, detail="Run not found")
    if original["status"] != "failed":
        raise HTTPException(
            status_code=400,
            detail="Can only retry failed runs",
        )

    from .flows import FLOW_REGISTRY
    flow_name = original.get("flow_name")
    flow_fn = FLOW_REGISTRY.get(flow_name)
    if not flow_fn:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown flow: {flow_name}",
        )

    original_config = original.get("config", {}) or {}
    kwargs: Dict[str, Any] = {
        "trigger_type": "retry",
        "triggered_by": f"retry:{run_id}",
        "config": original_config,
        "resume_from_run_id": run_id,
    }

    # Re-add flow-specific params from the original config
    if flow_name == "full_ingestion":
        kwargs["source_name"] = original_config.get("source_name", "retry")
        kwargs["input_path"] = original_config.get("input_path")

    try:
        result = flow_fn(**kwargs)
        return {"status": "retried", "result": _safe_serialise(result)}
    except Exception as exc:
        logger.error("Retry of run %s failed: %s", run_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


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
    return _run_flow_endpoint(
        neo4j_batch_sync_flow,
        {"layer": layer, "trigger_type": "api", "triggered_by": "api:/api/neo4j/sync"},
        "Neo4j sync",
    )


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


@app.post("/api/neo4j/embeddings")
async def trigger_embedding_backfill():
    """Trigger a semantic embedding backfill pass."""
    from .flows import neo4j_embedding_backfill_flow
    return _run_flow_endpoint(
        neo4j_embedding_backfill_flow,
        {"trigger_type": "api", "triggered_by": "api:/api/neo4j/embeddings"},
        "Embedding backfill",
    )


@app.post("/api/neo4j/graphsage/retrain")
async def trigger_graphsage_retrain():
    """Trigger a GraphSAGE model retraining."""
    from .flows import neo4j_graphsage_retrain_flow
    return _run_flow_endpoint(
        neo4j_graphsage_retrain_flow,
        {"trigger_type": "api", "triggered_by": "api:/api/neo4j/graphsage/retrain"},
        "GraphSAGE retrain",
    )


@app.post("/api/neo4j/graphsage/inference")
async def trigger_graphsage_inference():
    """Trigger GraphSAGE inference for new nodes."""
    from .flows import neo4j_graphsage_inference_flow
    return _run_flow_endpoint(
        neo4j_graphsage_inference_flow,
        {"trigger_type": "api", "triggered_by": "api:/api/neo4j/graphsage/inference"},
        "GraphSAGE inference",
    )


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
# Dead-Letter Queue Endpoints
# ══════════════════════════════════════════════════════

@app.get("/api/dead-letters")
async def list_dead_letters(
    status: Optional[str] = None,
    limit: int = 50,
):
    """List DLQ entries, optionally filtered by status."""
    entries = db.list_dead_letters(status=status, limit=limit)
    return {"dead_letters": entries, "count": len(entries)}


@app.post("/api/dead-letters/{entry_id}/retry")
async def retry_dead_letter(entry_id: str):
    """Manually retry a specific DLQ entry."""
    entries = db.list_dead_letters(limit=1)
    entry = next((e for e in db.list_dead_letters(limit=200) if e["id"] == entry_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail=f"DLQ entry not found: {entry_id}")
    if entry["status"] in ("resolved", "discarded"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot retry entry with status '{entry['status']}'",
        )

    try:
        result = handle_webhook_event(entry["payload"])
        db.update_dead_letter(
            entry_id,
            status="resolved",
            resolved_at=datetime.now(timezone.utc).isoformat(),
            retry_count=entry.get("retry_count", 0) + 1,
        )
        return {"status": "resolved", "result": _safe_serialise(result)}
    except Exception as exc:
        db.update_dead_letter(
            entry_id,
            error_message=str(exc),
            retry_count=entry.get("retry_count", 0) + 1,
            last_retry_at=datetime.now(timezone.utc).isoformat(),
        )
        raise HTTPException(status_code=500, detail=f"Retry failed: {exc}")


@app.delete("/api/dead-letters/{entry_id}")
async def discard_dead_letter(entry_id: str):
    """Discard a DLQ entry (sets status to 'discarded')."""
    result = db.update_dead_letter(entry_id, status="discarded")
    if not result:
        raise HTTPException(status_code=404, detail=f"DLQ entry not found: {entry_id}")
    return {"status": "discarded", "entry_id": entry_id}


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
# Flow Registry
# ══════════════════════════════════════════════════════

@app.get("/api/flows")
async def list_flows():
    """List all registered flows with metadata."""
    from .flows import FLOW_REGISTRY
    flows = []
    for name, fn in FLOW_REGISTRY.items():
        # Try @flow(description=...) first, then docstring
        desc = ""
        if hasattr(fn, "description") and fn.description:
            desc = fn.description
        elif fn.__doc__:
            desc = fn.__doc__.strip().split("\n")[0]
        flows.append({"name": name, "description": desc})
    return {"flows": flows, "count": len(flows)}


# ══════════════════════════════════════════════════════
# Data Sources
# ══════════════════════════════════════════════════════

@app.get("/api/data-sources")
async def list_data_sources(
    category: Optional[str] = None,
    status: Optional[str] = None,
):
    """List all data sources with latest cursor progress."""
    sources = db.list_data_sources(category=category, status=status)
    # Enrich each source with its latest cursor
    for src in sources:
        cursor = db.get_latest_cursor(src["id"])
        src["latest_cursor"] = cursor
    return {"data_sources": sources, "count": len(sources)}


@app.get("/api/source-names")
async def list_source_names():
    """Lightweight list of source names for console dropdown."""
    sources = db.list_data_sources(active_only=True)
    return {
        "sources": [
            {
                "source_name": s["source_name"],
                "category": s["category"],
                "status": s["status"],
            }
            for s in sources
        ]
    }


@app.get("/api/test/status")
async def test_env_status():
    """Check if testing Supabase environment is configured."""
    from .config import settings
    configured = bool(
        settings.supabase_test_url and settings.supabase_test_service_role_key
    )
    return {
        "configured": configured,
        "url": settings.supabase_test_url[:40] + "..." if configured else "",
    }


@app.get("/api/data-sources/{source_id}")
async def get_data_source(source_id: str):
    """Get a single data source with full cursor history."""
    source = db.get_data_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Data source not found")
    cursors = db.list_data_source_cursors(source_id, limit=50)
    return {"source": source, "cursors": cursors}


@app.post("/api/data-sources/{source_id}/ingest")
async def trigger_data_source_ingest(
    source_id: str,
    request: Request,
):
    """Trigger ingestion for a data source from its current cursor position."""
    source = db.get_data_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Data source not found")

    body = await request.json()
    batch_size = body.get("batch_size", 100)
    dry_run = body.get("dry_run", False)

    # Determine cursor start from latest completed cursor
    latest = db.get_latest_cursor(source_id)
    cursor_start = latest["cursor_end"] if latest else 0
    remaining = (source.get("total_records") or 0) - cursor_start
    if remaining <= 0:
        return {"status": "skipped", "message": "All records already ingested"}

    record_count = min(body.get("record_count", remaining), remaining)

    environment = body.get("environment", "production")
    db.set_env(environment)  # set BEFORE creating run

    storage_path = source["storage_path"]
    # In test mode, prefix storage path to read from testing/ subfolder
    if environment == "testing":
        storage_path = f"testing/{storage_path}"

    # Pre-create orchestration run so we can return the ID immediately
    orch_run = db.create_orchestration_run(
        flow_name="full_ingestion",
        trigger_type="api",
        triggered_by="dashboard:/api/data-sources/ingest",
        layers=["prebronze_to_bronze", "usda_nutrition_fetch", "bronze_to_silver", "silver_to_gold"],
        config={
            "batch_size": batch_size,
            "dry_run": dry_run,
            "cursor_start": cursor_start,
            "record_count": record_count,
            "data_source_id": source_id,
        },
        source_name=source["source_name"],
    )
    run_id = orch_run["id"]

    # Dispatch in background
    from .flows import FLOW_REGISTRY
    flow_fn = FLOW_REGISTRY.get("full_ingestion")

    def _run():
        db.set_env(environment)  # re-set for thread context
        try:
            flow_fn(
                source_name=source["source_name"],
                storage_bucket=source["storage_bucket"],
                storage_path=storage_path,
                trigger_type="api",
                triggered_by="dashboard:/api/data-sources/ingest",
                config={
                    "batch_size": batch_size,
                    "dry_run": dry_run,
                    "cursor_start": cursor_start,
                    "record_count": record_count,
                },
                orch_run_id=run_id,
            )
        except Exception as exc:
            logger.error("Data source ingest failed: %s", exc)
            # Mark the pre-created run as failed so it doesn't stay zombie
            try:
                db.update_orchestration_run(
                    run_id,
                    status="failed",
                    total_errors=1,
                    completed_at=db._utcnow(),
                )
            except Exception as update_exc:
                logger.warning("Failed to mark ingest run %s as failed: %s", run_id, update_exc)

    asyncio.create_task(asyncio.to_thread(_run))
    db.set_env("production")  # reset for subsequent API requests
    return {"status": "started", "run_id": run_id, "source_name": source["source_name"]}


# ══════════════════════════════════════════════════════
# Schedules & Event Triggers
# ══════════════════════════════════════════════════════

@app.get("/api/schedules")
async def list_schedules():
    """List all schedule definitions."""
    schedules = db.list_all_schedules()
    return {"schedules": schedules, "count": len(schedules)}


@app.post("/api/schedules/{schedule_id}/trigger")
async def trigger_schedule(schedule_id: str):
    """Manually execute a schedule's flow immediately."""
    schedule = db.get_schedule(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    flow_name = schedule["flow_name"]
    run_config = schedule.get("run_config", {}) or {}

    from .flows import FLOW_REGISTRY
    flow_fn = FLOW_REGISTRY.get(flow_name)
    if not flow_fn:
        raise HTTPException(status_code=400, detail=f"Unknown flow: {flow_name}")

    # Validate: flows like full_ingestion require source_name
    requires_source = flow_name in ("full_ingestion", "bronze_to_gold", "single_layer")
    source_name = run_config.get("source_name")
    if requires_source and not source_name:
        raise HTTPException(
            status_code=400,
            detail=f"Schedule '{schedule['schedule_name']}' targets flow '{flow_name}' "
                   f"which requires 'source_name' in run_config.",
        )

    # Pre-create run — set env FIRST so it lands in correct DB
    environment = run_config.get("environment", "production")
    db.set_env(environment)

    orch_run = db.create_orchestration_run(
        flow_name=flow_name,
        trigger_type="manual_schedule",
        triggered_by=f"dashboard:schedule:{schedule['schedule_name']}",
        config=run_config,
        source_name=source_name,
    )
    run_id = orch_run["id"]

    def _run():
        db.set_env(environment)  # re-set for thread context
        try:
            kwargs = {
                "trigger_type": "manual_schedule",
                "triggered_by": f"dashboard:schedule:{schedule['schedule_name']}",
                "config": run_config,
                "orch_run_id": run_id,
            }
            if source_name:
                kwargs["source_name"] = source_name

            # Extract flow-specific args from run_config — gated by flow
            # so we don't pass e.g. 'layer' to full_ingestion (TypeError)
            flow_arg_map = {
                "full_ingestion": {"input_path", "storage_bucket", "storage_path", "vendor_id"},
                "single_layer": {"layer", "input_path"},
                "bronze_to_gold": {"vendor_id"},
            }
            allowed_keys = flow_arg_map.get(flow_name, set())
            for key in allowed_keys:
                if key in run_config:
                    kwargs[key] = run_config[key]

            # In test mode, prefix storage paths with testing/
            if environment == "testing" and "storage_path" in kwargs:
                kwargs["storage_path"] = f"testing/{kwargs['storage_path']}"

            flow_fn(**kwargs)
        except Exception as exc:
            logger.error("Manual schedule trigger failed: %s", exc)
            # Mark the pre-created run as failed so it doesn't stay zombie
            try:
                db.update_orchestration_run(
                    run_id,
                    status="failed",
                    total_errors=1,
                    completed_at=db._utcnow(),
                )
            except Exception as update_exc:
                logger.warning("Failed to mark schedule run %s as failed: %s", run_id, update_exc)

    asyncio.create_task(asyncio.to_thread(_run))
    db.set_env("production")  # reset for subsequent API requests
    return {"status": "started", "run_id": run_id, "flow_name": flow_name}


@app.patch("/api/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, request: Request):
    """Update a schedule definition (toggle active, change cron, etc)."""
    body = await request.json()
    schedule = db.get_schedule(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    updated = db.update_schedule(schedule_id, **body)
    return updated


@app.get("/api/event-triggers")
async def list_event_triggers():
    """List all event trigger definitions."""
    triggers = db.list_all_event_triggers()
    return {"triggers": triggers, "count": len(triggers)}


@app.patch("/api/event-triggers/{trigger_id}")
async def update_event_trigger_endpoint(trigger_id: str, request: Request):
    """Toggle an event trigger active/inactive."""
    body = await request.json()
    updated = db.update_event_trigger(trigger_id, **body)
    if not updated:
        raise HTTPException(status_code=404, detail="Trigger not found")
    return updated


# ══════════════════════════════════════════════════════
# SSE Log Streaming
# ══════════════════════════════════════════════════════

@app.get("/api/runs/{run_id}/logs")
async def stream_run_logs(run_id: str):
    """SSE stream of pipeline step logs for a running orchestration run.

    Polls pipeline_step_logs every 2 seconds and sends new rows as
    Server-Sent Events. Ends when the run reaches a terminal status.
    """
    orch_run = db.get_orchestration_run(run_id)
    if not orch_run:
        raise HTTPException(status_code=404, detail="Run not found")

    async def event_generator():
        last_seen_count = 0
        terminal_statuses = {
            "completed", "failed", "cancelled",
            "timed_out", "partially_completed",
        }

        while True:
            # Collect all step logs across pipeline runs
            pipeline_runs = db.list_pipeline_runs(orchestration_run_id=run_id)
            all_steps = []
            for pr in pipeline_runs:
                steps = db.list_step_logs(pipeline_run_id=pr["id"], limit=500)
                for s in steps:
                    s["pipeline_name"] = pr.get("pipeline_name", "")
                all_steps.extend(steps)

            # Sort by execution order
            all_steps.sort(
                key=lambda x: x.get("started_at") or x.get("created_at") or ""
            )

            # Send only new steps since last poll
            new_steps = all_steps[last_seen_count:]
            for step in new_steps:
                level = "ERROR" if step.get("status") == "failed" else (
                    "WARNING" if step.get("records_error", 0) > 0 else "INFO"
                )
                data = json.dumps({
                    "type": "log",
                    "timestamp": step.get("started_at") or step.get("created_at"),
                    "level": level,
                    "step": step.get("step_name", ""),
                    "pipeline": step.get("pipeline_name", ""),
                    "status": step.get("status", ""),
                    "records_in": step.get("records_in", 0),
                    "records_out": step.get("records_out", 0),
                    "duration_ms": step.get("duration_ms"),
                    "error": step.get("error_message"),
                })
                yield f"data: {data}\n\n"
            last_seen_count = len(all_steps)

            # Check if run reached terminal status
            current_run = db.get_orchestration_run(run_id)
            if current_run and current_run.get("status") in terminal_statuses:
                final_data = json.dumps({
                    "type": "complete",
                    "status": current_run["status"],
                    "duration_seconds": current_run.get("duration_seconds"),
                    "total_records_written": current_run.get("total_records_written", 0),
                })
                yield f"data: {final_data}\n\n"
                break

            await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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


def _run_flow_endpoint(flow_fn, flow_kwargs: dict, endpoint_name: str):
    """Run a flow and return JSON with correct HTTP status code.

    Returns 200 on success/skipped, 500 on logical failure.
    Infrastructure exceptions propagate as 500 via the except block.
    """
    try:
        result = flow_fn(**flow_kwargs)
        serialised = _safe_serialise(result)

        # Inspect task-level status from the result dict
        task_status = result.get("status") if isinstance(result, dict) else None

        if task_status == "failed":
            logger.error("\u274c %s returned failure: %s", endpoint_name, result.get("error"))
            return JSONResponse(
                status_code=500,
                content={
                    "status": "failed",
                    "error": str(result.get("error", "Task reported failure")),
                    "result": serialised,
                },
            )

        return {"status": "completed", "result": serialised}

    except Exception as exc:
        logger.error("%s trigger failed: %s", endpoint_name, exc)
        raise HTTPException(status_code=500, detail=str(exc))
