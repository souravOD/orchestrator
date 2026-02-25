"""
Flow Definitions
=================

Prefect ``@flow``-decorated functions that compose pipeline tasks into
end-to-end orchestration workflows.

Available flows
---------------

- ``full_ingestion_flow``      – PreBronze → Bronze → Silver → Gold
- ``bronze_to_gold_flow``      – Bronze → Silver → Gold  (bronze already loaded)
- ``single_layer_flow``        – Run any single layer by name
- ``realtime_event_flow``      – Lightweight flow for real-time DB events
- ``neo4j_batch_sync_flow``    – Gold → Neo4j batch sync (all layers)
- ``neo4j_reconciliation_flow`` – Gold↔Neo4j drift detection
- ``neo4j_realtime_worker_flow`` – Customer realtime outbox poller
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from prefect import flow

from . import db
from .config import settings
from .pipelines import (
    FlowCancelledError,
    PipelineTimeoutError,
    run_bronze_to_silver,
    run_gold_to_neo4j_batch,
    run_gold_to_neo4j_realtime,
    run_gold_to_neo4j_reconciliation,
    run_prebronze_to_bronze,
    run_silver_to_gold,
    run_usda_nutrition_fetch,
)

logger = logging.getLogger(__name__)


class ConcurrentRunError(RuntimeError):
    """Raised when a flow is already running and cannot start a new one."""
    def __init__(self, flow_name: str):
        super().__init__(f"Flow '{flow_name}' is already running. Concurrent runs are not allowed.")


def _guard_concurrent(flow_name: str, source_name: Optional[str] = None) -> None:
    """Raise ConcurrentRunError if the flow already has a running run.

    When *source_name* is provided, the guard is per-source so that
    different sources can run the same flow in parallel.
    """
    if db.has_running_flow(flow_name, source_name=source_name):
        label = f"{flow_name}/{source_name}" if source_name else flow_name
        raise ConcurrentRunError(label)


def _send_success_notification(flow_name: str, orch_run_id: str, duration: float, **extra) -> None:
    """Best-effort success notification via AlertDispatcher."""
    try:
        from .alerts import send_alert
        details = ", ".join(f"{k}={v}" for k, v in extra.items() if v)
        send_alert(
            title=f"✅ {flow_name} completed in {duration:.1f}s",
            message=details or "No additional details",
            severity="info",
            pipeline_name=flow_name,
            run_id=str(orch_run_id),
        )
    except Exception as exc:
        logger.debug("Success notification failed (non-fatal): %s", exc)


# ══════════════════════════════════════════════════════
# Flow 1: Full Ingestion (PreBronze → Bronze → Silver → Gold)
# ══════════════════════════════════════════════════════

@flow(
    name="full_ingestion",
    description="End-to-end pipeline: PreBronze → Bronze → Silver → Gold → Neo4j",
    log_prints=True,
)
def full_ingestion_flow(
    source_name: str,
    raw_input: Optional[List[Dict[str, Any]]] = None,
    input_path: Optional[str] = None,
    vendor_id: Optional[str] = None,
    storage_bucket: Optional[str] = None,
    storage_path: Optional[str] = None,
    trigger_type: str = "manual",
    triggered_by: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    resume_from_run_id: Optional[str] = None,
    orch_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Full medallion pipeline execution.

    Provide data as either ``raw_input`` (list of dicts) or
    ``input_path`` (path to a CSV/JSON/NDJSON file that the
    PreBronze pipeline will read).

    If ``orch_run_id`` is supplied the flow reuses that pre-created
    orchestration_runs row instead of inserting a new one.  The API
    layer uses this so it can return the run_id immediately.
    """
    cfg = config or {}
    start = time.time()

    # Per-source concurrency guard
    _guard_concurrent("full_ingestion", source_name=source_name)

    # If storage params provided, build a storage:// URI for _load_input
    if raw_input is None and storage_bucket and storage_path:
        input_path = f"storage://{storage_bucket}/{storage_path}"

    # Input validation
    from .models import IngestionInput
    validated = IngestionInput(
        source_name=source_name,
        raw_input=raw_input,
        input_path=input_path,
    )

    # Load input from file if needed
    if validated.raw_input is None and validated.input_path:
        raw_input = _load_input(validated.input_path)
    else:
        raw_input = validated.raw_input
    if raw_input is None:
        raw_input = []

    # Create or reuse orchestration run
    if orch_run_id:
        logger.info("🔄 Orchestration run %s — full_ingestion started (pre-created)", orch_run_id)
    else:
        orch_run = db.create_orchestration_run(
            flow_name="full_ingestion",
            trigger_type=trigger_type,
            triggered_by=triggered_by,
            layers=["prebronze_to_bronze", "usda_nutrition_fetch", "bronze_to_silver", "silver_to_gold", "gold_to_neo4j"],
            config=cfg,
            source_name=source_name,
            vendor_id=vendor_id,
        )
        orch_run_id = orch_run["id"]
        logger.info("🔄 Orchestration run %s — full_ingestion started", orch_run_id)

    results: Dict[str, Any] = {}
    layer_timings: Dict[str, float] = {}
    total_written = 0
    total_dq = 0
    errors = 0

    # Determine completed layers if resuming
    completed_layers: set = set()
    if resume_from_run_id:
        completed = db.list_completed_pipeline_runs(resume_from_run_id)
        completed_layers = {r["pipeline_name"] for r in completed}
        logger.info("⏭️ Resuming from run %s. Skipping: %s", resume_from_run_id, completed_layers)

    try:
        # ── Layer 1: PreBronze → Bronze ──
        if "prebronze_to_bronze" not in completed_layers:
            layer_start = time.time()
            bronze_result = run_prebronze_to_bronze(
                orchestration_run_id=orch_run_id,
                source_name=source_name,
                raw_input=raw_input,
                trigger_type=trigger_type,
                triggered_by=triggered_by,
                config=cfg,
            )
            layer_timings["prebronze_to_bronze"] = round(time.time() - layer_start, 2)
            results["prebronze_to_bronze"] = bronze_result
            total_written += bronze_result.get("records_loaded", 0)
        else:
            logger.info("⏭️ Skipping prebronze_to_bronze (already completed)")
            bronze_result = {}  # Needed for detected_table check below

        # ── Layer 1.5: USDA Nutrition Fetch (conditional) ──
        if "usda_nutrition_fetch" not in completed_layers:
            # Only trigger if PreBronze wrote to raw_recipes
            detected_table = bronze_result.get("detected_table", "") if bronze_result else ""
            if detected_table == "raw_recipes" or cfg.get("force_usda_fetch"):
                layer_start = time.time()
                try:
                    usda_result = run_usda_nutrition_fetch(
                        orchestration_run_id=orch_run_id,
                        trigger_type="upstream_complete",
                        triggered_by="prebronze_to_bronze",
                        config=cfg,
                    )
                    layer_timings["usda_nutrition_fetch"] = round(time.time() - layer_start, 2)
                    results["usda_nutrition_fetch"] = usda_result
                    total_written += usda_result.get("inserted", 0)
                    logger.info("✅ USDA Nutrition Fetch completed")
                except Exception as usda_exc:
                    # USDA fetch failure should not fail the overall ingestion
                    logger.warning("⚠️ USDA Nutrition Fetch failed (non-fatal): %s", usda_exc)
                    results["usda_nutrition_fetch"] = {"status": "failed", "error": str(usda_exc)}
                    layer_timings["usda_nutrition_fetch"] = round(time.time() - layer_start, 2)
            else:
                logger.info("⏭️ Skipping USDA fetch (detected_table=%s, not raw_recipes)", detected_table)
        else:
            logger.info("⏭️ Skipping usda_nutrition_fetch (already completed)")

        # ── Layer 2: Bronze → Silver ──
        if "bronze_to_silver" not in completed_layers:
            layer_start = time.time()
            silver_result = run_bronze_to_silver(
                orchestration_run_id=orch_run_id,
                trigger_type="upstream_complete",
                triggered_by="prebronze_to_bronze",
                config=cfg,
            )
            layer_timings["bronze_to_silver"] = round(time.time() - layer_start, 2)
            results["bronze_to_silver"] = silver_result
            total_written += silver_result.get("total_written", 0)
            total_dq += silver_result.get("total_dq_issues", 0)
        else:
            logger.info("⏭️ Skipping bronze_to_silver (already completed)")

        # ── Layer 3: Silver → Gold ──
        if "silver_to_gold" not in completed_layers:
            layer_start = time.time()
            gold_result = run_silver_to_gold(
                orchestration_run_id=orch_run_id,
                trigger_type="upstream_complete",
                triggered_by="bronze_to_silver",
                config=cfg,
            )
            layer_timings["silver_to_gold"] = round(time.time() - layer_start, 2)
            results["silver_to_gold"] = gold_result
            total_written += gold_result.get("total_written", 0)
        else:
            logger.info("⏭️ Skipping silver_to_gold (already completed)")

        # ── Layer 4: Gold → Neo4j (best-effort) ──
        if "gold_to_neo4j" not in completed_layers:
            layer_start = time.time()
            try:
                neo4j_result = run_gold_to_neo4j_batch(
                    orchestration_run_id=orch_run_id,
                    layer="all",
                    trigger_type="upstream_complete",
                    triggered_by="silver_to_gold",
                    config=cfg,
                )
                results["gold_to_neo4j"] = neo4j_result
                logger.info("✅ Gold→Neo4j sync completed as part of full ingestion")
            except Exception as neo4j_exc:
                # Neo4j sync failure should not fail the overall ingestion
                logger.warning("⚠️ Gold→Neo4j sync failed (non-fatal): %s", neo4j_exc)
                results["gold_to_neo4j"] = {"status": "failed", "error": str(neo4j_exc)}
            finally:
                layer_timings["gold_to_neo4j"] = round(time.time() - layer_start, 2)
        else:
            logger.info("⏭️ Skipping gold_to_neo4j (already completed)")

        # Mark success
        duration = round(time.time() - start, 2)
        db.update_orchestration_run(
            orch_run_id,
            status="completed",
            total_records_written=total_written,
            total_dq_issues=total_dq,
            completed_at=db._utcnow(),
            duration_seconds=duration,
        )

        # Store layer timing instrumentation
        try:
            db.update_orchestration_run_metadata(orch_run_id, {
                "layer_timings": layer_timings,
                "end_to_end_seconds": duration,
            })
        except Exception:
            logger.debug("Failed to store layer timings (non-fatal)")

        logger.info(
            "✅ Full ingestion completed in %.1fs. Total written=%d | Layer timings: %s",
            duration, total_written, layer_timings,
        )

        _send_success_notification(
            "full_ingestion", orch_run_id, duration,
            total_written=total_written, total_dq=total_dq,
        )
        results["_layer_timings"] = layer_timings
        return results

    except Exception as exc:
        errors += 1
        db.update_orchestration_run(
            orch_run_id,
            status="failed",
            total_errors=errors,
            total_records_written=total_written,
            total_dq_issues=total_dq,
            completed_at=db._utcnow(),
            duration_seconds=round(time.time() - start, 2),
        )
        logger.error("❌ Full ingestion FAILED: %s", exc)
        raise


# ══════════════════════════════════════════════════════
# Flow 2: Bronze → Gold (skip PreBronze)
# ══════════════════════════════════════════════════════

@flow(
    name="bronze_to_gold",
    description="Transform pipeline: Bronze → Silver → Gold (Bronze already loaded)",
    log_prints=True,
)
def bronze_to_gold_flow(
    trigger_type: str = "manual",
    triggered_by: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    source_name: Optional[str] = None,
    vendor_id: Optional[str] = None,
    orch_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run Bronze→Silver and Silver→Gold in sequence.

    If ``orch_run_id`` is supplied the flow reuses that pre-created
    orchestration_runs row.
    """
    cfg = config or {}
    start = time.time()

    _guard_concurrent("bronze_to_gold", source_name=source_name)

    if not orch_run_id:
        orch_run = db.create_orchestration_run(
            flow_name="bronze_to_gold",
            trigger_type=trigger_type,
            triggered_by=triggered_by,
            layers=["bronze_to_silver", "silver_to_gold"],
            config=cfg,
            source_name=source_name,
            vendor_id=vendor_id,
        )
        orch_run_id = orch_run["id"]
    results: Dict[str, Any] = {}

    try:
        silver_result = run_bronze_to_silver(
            orchestration_run_id=orch_run_id,
            trigger_type=trigger_type,
            triggered_by=triggered_by,
            config=cfg,
        )
        results["bronze_to_silver"] = silver_result

        gold_result = run_silver_to_gold(
            orchestration_run_id=orch_run_id,
            trigger_type="upstream_complete",
            triggered_by="bronze_to_silver",
            config=cfg,
        )
        results["silver_to_gold"] = gold_result

        db.update_orchestration_run(
            orch_run_id,
            status="completed",
            total_records_written=(
                silver_result.get("total_written", 0) +
                gold_result.get("total_written", 0)
            ),
            completed_at=db._utcnow(),
            duration_seconds=round(time.time() - start, 2),
        )
        return results

    except Exception as exc:
        db.update_orchestration_run(
            orch_run_id,
            status="failed",
            completed_at=db._utcnow(),
            duration_seconds=round(time.time() - start, 2),
        )
        raise


# ══════════════════════════════════════════════════════
# Flow 3: Single Layer
# ══════════════════════════════════════════════════════

LAYER_TASKS = {
    "prebronze_to_bronze": run_prebronze_to_bronze,
    "usda_nutrition_fetch": run_usda_nutrition_fetch,
    "bronze_to_silver": run_bronze_to_silver,
    "silver_to_gold": run_silver_to_gold,
}


@flow(
    name="single_layer",
    description="Run a single pipeline layer by name",
    log_prints=True,
)
def single_layer_flow(
    layer: str,
    source_name: Optional[str] = None,
    raw_input: Optional[List[Dict[str, Any]]] = None,
    input_path: Optional[str] = None,
    trigger_type: str = "manual",
    triggered_by: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run a single pipeline layer.  Useful for re-runs or targeted
    processing.
    """
    cfg = config or {}

    if layer not in LAYER_TASKS:
        raise ValueError(
            f"Unknown layer: {layer}. "
            f"Valid layers: {list(LAYER_TASKS.keys())}"
        )

    orch_run = db.create_orchestration_run(
        flow_name=f"single_layer:{layer}",
        trigger_type=trigger_type,
        triggered_by=triggered_by,
        layers=[layer],
        config=cfg,
    )
    orch_run_id = orch_run["id"]
    start = time.time()

    try:
        if layer == "prebronze_to_bronze":
            if raw_input is None and input_path:
                raw_input = _load_input(input_path)
            result = run_prebronze_to_bronze(
                orchestration_run_id=orch_run_id,
                source_name=source_name or "unknown",
                raw_input=raw_input or [],
                trigger_type=trigger_type,
                triggered_by=triggered_by,
                config=cfg,
            )
        else:
            task_fn = LAYER_TASKS[layer]
            result = task_fn(
                orchestration_run_id=orch_run_id,
                trigger_type=trigger_type,
                triggered_by=triggered_by,
                config=cfg,
            )

        db.update_orchestration_run(
            orch_run_id,
            status="completed",
            completed_at=db._utcnow(),
            duration_seconds=round(time.time() - start, 2),
        )
        return result

    except Exception:
        db.update_orchestration_run(
            orch_run_id,
            status="failed",
            completed_at=db._utcnow(),
            duration_seconds=round(time.time() - start, 2),
        )
        raise


# ══════════════════════════════════════════════════════
# Flow 4: Real-time Event
# ══════════════════════════════════════════════════════

@flow(
    name="realtime_event",
    description="Lightweight flow for real-time database events",
    log_prints=True,
)
def realtime_event_flow(
    event_type: str,
    source_schema: str,
    source_table: str,
    record: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Handle a real-time database event (e.g. Supabase webhook).

    This flow determines which pipeline(s) to trigger based on the
    event source and dispatches accordingly.
    """
    cfg = config or {}

    # Determine the target layer based on source schema
    layer_mapping = {
        "public": "prebronze_to_bronze",   # raw data → bronze
        "bronze": "bronze_to_silver",       # bronze → silver
        "silver": "silver_to_gold",         # silver → gold
    }

    target_layer = layer_mapping.get(source_schema)
    if not target_layer:
        logger.warning(
            "No pipeline mapping for schema=%s, table=%s. Skipping.",
            source_schema, source_table,
        )
        return {"skipped": True, "reason": f"No mapping for {source_schema}.{source_table}"}

    logger.info(
        "📡 Real-time event: %s on %s.%s → triggering %s",
        event_type, source_schema, source_table, target_layer,
    )

    return single_layer_flow(
        layer=target_layer,
        source_name=f"realtime:{source_table}",
        raw_input=[record] if target_layer == "prebronze_to_bronze" else None,
        trigger_type="webhook",
        triggered_by=f"{source_schema}.{source_table}",
        config=cfg,
    )


# ══════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════

def _load_input(path: str) -> List[Dict[str, Any]]:
    """Load data from a file (CSV, JSON, NDJSON) or Supabase Storage URI."""
    # Handle Supabase Storage URIs (storage://bucket/path)
    if path.startswith("storage://"):
        parts = path.replace("storage://", "").split("/", 1)
        bucket, file_path = parts[0], parts[1]
        try:
            _ensure_pipeline_on_path("prebronze-to-bronze")
            from prebronze.orchestrator import load_input_from_storage
            return load_input_from_storage(bucket, file_path)
        except ImportError:
            raise ImportError(
                "PreBronze pipeline's load_input_from_storage() is not available. "
                "Install the prebronze-to-bronze package or contact the PreBronze team."
            )

    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    suffix = file_path.suffix.lower()

    if suffix == ".json":
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else [data]

    elif suffix in (".ndjson", ".jsonl"):
        records = []
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    elif suffix == ".csv":
        try:
            import pandas as pd
            df = pd.read_csv(file_path)
            return df.to_dict(orient="records")
        except ImportError:
            raise ImportError("pandas is required for CSV files: pip install pandas")

    else:
        raise ValueError(f"Unsupported file format: {suffix}. Use .json, .ndjson, or .csv")


# ══════════════════════════════════════════════════════
# Flow 5: Neo4j Batch Sync
# ══════════════════════════════════════════════════════

@flow(name="neo4j_batch_sync", retries=0)
def neo4j_batch_sync_flow(
    layer: str = "all",
    trigger_type: str = "manual",
    triggered_by: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Gold → Neo4j batch sync flow.

    Runs the catalog_batch service for the specified layer(s).
    Layer order: recipes → ingredients → products → customers.
    """
    _guard_concurrent("neo4j_batch_sync")

    orch_run = db.create_orchestration_run(
        flow_name="neo4j_batch_sync",
        trigger_type=trigger_type,
        triggered_by=triggered_by,
        flow_type="batch",
        layers=["gold_to_neo4j"],
        config=config,
    )
    orch_run_id = orch_run["id"]
    start = time.time()

    try:
        result = run_gold_to_neo4j_batch(
            orchestration_run_id=orch_run_id,
            layer=layer,
            trigger_type=trigger_type,
            triggered_by=triggered_by,
            config=config,
        )
        db.update_orchestration_run(
            orch_run_id,
            status="completed",
            completed_at=db._utcnow(),
            duration_seconds=time.time() - start,
        )
        return result

    except Exception as exc:
        db.update_orchestration_run(
            orch_run_id,
            status="failed",
            total_errors=1,
            completed_at=db._utcnow(),
            duration_seconds=time.time() - start,
        )
        raise


# ══════════════════════════════════════════════════════
# Flow 6: Neo4j Reconciliation
# ══════════════════════════════════════════════════════

@flow(name="neo4j_reconciliation", retries=0)
def neo4j_reconciliation_flow(
    trigger_type: str = "scheduled",
    triggered_by: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run reconciliation between Supabase Gold and Neo4j.

    Detects drift via count/checksum comparison and proposes
    LLM-guided backfills.
    """
    _guard_concurrent("neo4j_reconciliation")

    orch_run = db.create_orchestration_run(
        flow_name="neo4j_reconciliation",
        trigger_type=trigger_type,
        triggered_by=triggered_by,
        flow_type="batch",
        layers=["gold_to_neo4j"],
        config=config,
    )
    orch_run_id = orch_run["id"]
    start = time.time()

    try:
        result = run_gold_to_neo4j_reconciliation(
            orchestration_run_id=orch_run_id,
            trigger_type=trigger_type,
            triggered_by=triggered_by,
            config=config,
        )
        db.update_orchestration_run(
            orch_run_id,
            status="completed",
            completed_at=db._utcnow(),
            duration_seconds=time.time() - start,
        )
        return result

    except Exception as exc:
        db.update_orchestration_run(
            orch_run_id,
            status="failed",
            total_errors=1,
            completed_at=db._utcnow(),
            duration_seconds=time.time() - start,
        )
        raise


# ══════════════════════════════════════════════════════
# Flow 7: Neo4j Realtime Worker
# ══════════════════════════════════════════════════════

@flow(name="neo4j_realtime_worker", retries=1, retry_delay_seconds=60)
def neo4j_realtime_worker_flow(
    trigger_type: str = "manual",
    triggered_by: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Start the customer_realtime outbox poller.

    This is a persistent / long-running flow that polls the Gold
    outbox table and pushes B2C events to Neo4j in near-real-time.
    """
    orch_run = db.create_orchestration_run(
        flow_name="neo4j_realtime_worker",
        trigger_type=trigger_type,
        triggered_by=triggered_by,
        flow_type="realtime",
        layers=["gold_to_neo4j"],
        config=config,
    )
    orch_run_id = orch_run["id"]
    start = time.time()

    try:
        result = run_gold_to_neo4j_realtime(
            orchestration_run_id=orch_run_id,
            trigger_type=trigger_type,
            triggered_by=triggered_by,
            config=config,
        )
        db.update_orchestration_run(
            orch_run_id,
            status="completed",
            completed_at=db._utcnow(),
            duration_seconds=time.time() - start,
        )
        return result

    except Exception as exc:
        db.update_orchestration_run(
            orch_run_id,
            status="failed",
            total_errors=1,
            completed_at=db._utcnow(),
            duration_seconds=time.time() - start,
        )
        raise


# ══════════════════════════════════════════════════════
# Flow 8: Multi-Source Parallel Ingestion
# ══════════════════════════════════════════════════════

@flow(
    name="multi_source_ingestion",
    description="Parallel ingestion of multiple data sources with concurrency limiting",
    log_prints=True,
)
def multi_source_ingestion_flow(
    sources: List[Dict[str, Any]],
    trigger_type: str = "api",
    triggered_by: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    pre_created_run_ids: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Run multiple data sources through the ingestion pipeline in parallel.

    Uses ``ParallelSourceRunner`` with an ``asyncio.Semaphore`` to limit
    concurrency (default: 2 sources at a time).

    Parameters
    ----------
    sources
        List of source configs, each with at least ``source_name``.
    pre_created_run_ids
        Optional mapping of source_name → pre-created orch_run_id
        (used by the API layer to return run IDs immediately).
    """
    import asyncio
    from .parallel import ParallelSourceRunner

    runner = ParallelSourceRunner()
    cfg = config or {}

    logger.info(
        "🚀 Multi-source ingestion: %d sources, concurrency=%d",
        len(sources), runner.max_concurrency,
    )

    # Run the async parallel runner in the event loop
    loop = asyncio.new_event_loop()
    try:
        batch_result = loop.run_until_complete(
            runner.run_sources(
                sources=sources,
                flow_name="full_ingestion",
                trigger_type=trigger_type,
                triggered_by=triggered_by or "multi_source_ingestion",
                config=cfg,
                pre_created_run_ids=pre_created_run_ids,
            )
        )
    finally:
        loop.close()

    return {
        "batch_id": batch_result.batch_id,
        "total_sources": batch_result.total_sources,
        "completed": batch_result.completed,
        "failed": batch_result.failed,
        "total_duration_seconds": batch_result.total_duration_seconds,
        "concurrency_limit": batch_result.concurrency_limit,
        "source_results": batch_result.source_results,
    }


# ══════════════════════════════════════════════════════
# Flow Registry (for dispatching by name)
# ══════════════════════════════════════════════════════

FLOW_REGISTRY = {
    "full_ingestion": full_ingestion_flow,
    "bronze_to_gold": bronze_to_gold_flow,
    "single_layer": single_layer_flow,
    "realtime_event": realtime_event_flow,
    "neo4j_batch_sync": neo4j_batch_sync_flow,
    "neo4j_reconciliation": neo4j_reconciliation_flow,
    "neo4j_realtime_worker": neo4j_realtime_worker_flow,
    "multi_source_ingestion": multi_source_ingestion_flow,
}
