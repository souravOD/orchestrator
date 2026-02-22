"""
Pipeline Wrappers
==================

Prefect ``@task``-decorated functions that wrap each existing LangGraph
pipeline.  Each wrapper:

1. Creates a ``pipeline_run`` record in the orchestration schema.
2. Invokes the underlying LangGraph pipeline.
3. Logs step-level results via ``StepLogger``.
4. Updates the ``pipeline_run`` record with final metrics.
5. Returns the pipeline's final state dict for downstream tasks.
"""

from __future__ import annotations

import concurrent.futures
import logging
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from prefect import task

from . import db
from .config import settings
from .logging_utils import RunSummariser, StepLogger

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════

def _ensure_pipeline_on_path(pipeline_dir_name: str):
    """
    Add the pipeline repo's directory to sys.path so its modules
    can be imported.  Expects the sibling repos to live next to
    the ``orchestrator/`` directory, e.g.::

        Orchestration Pipeline/
        ├── orchestrator/          ← this package
        ├── prebronze-to-bronze 1/
        ├── bronze-to-silver 1/
        └── silver-to-gold 1/
    """
    base = Path(__file__).resolve().parent.parent.parent  # Orchestration Pipeline/
    candidates = sorted(base.glob(f"{pipeline_dir_name}*"))
    for candidate in candidates:
        # Find the inner package directory (e.g. prebronze-to-bronze/prebronze-to-bronze/)
        inner_dirs = sorted(candidate.glob("*/"))
        for inner in inner_dirs:
            if inner.is_dir() and not inner.name.startswith("."):
                path_str = str(inner)
                if path_str not in sys.path:
                    sys.path.insert(0, path_str)
                    logger.debug("Added to sys.path: %s", path_str)
                return
    logger.warning("Could not find pipeline directory for: %s", pipeline_dir_name)


def _calculate_duration(start: float) -> float:
    """Return elapsed seconds since ``start``."""
    return round(time.time() - start, 2)


class PipelineTimeoutError(Exception):
    """Raised when a pipeline exceeds its configured timeout."""
    def __init__(self, pipeline_name: str, timeout: int):
        self.pipeline_name = pipeline_name
        self.timeout = timeout
        super().__init__(f"Pipeline '{pipeline_name}' timed out after {timeout}s")


class FlowCancelledError(Exception):
    """Raised when a flow has been cancelled via the API."""
    def __init__(self, orchestration_run_id: str):
        self.orchestration_run_id = orchestration_run_id
        super().__init__(f"Flow {orchestration_run_id} was cancelled")


def _check_cancellation(orchestration_run_id: str) -> None:
    """Check if the parent orchestration run has been cancelled."""
    run = db.get_orchestration_run(orchestration_run_id)
    if run and run.get("status") == "cancelled":
        raise FlowCancelledError(orchestration_run_id)


def _run_with_timeout(
    fn: Callable[..., Any],
    args: tuple = (),
    kwargs: dict | None = None,
    timeout: int | None = None,
    pipeline_name: str = "unknown",
) -> Any:
    """
    Execute ``fn(*args, **kwargs)`` with an optional timeout.

    Uses a thread-pool so the calling Prefect task isn't blocked.
    If *timeout* is ``None``, runs without any limit.
    """
    kwargs = kwargs or {}
    if timeout is None:
        return fn(*args, **kwargs)
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeout:
            raise PipelineTimeoutError(pipeline_name, timeout)


def _store_metrics(pipeline_run_id: str, result: Dict[str, Any]) -> None:
    """
    Persist tool_metrics and llm_usage from a pipeline result dict.

    Silently skips if the keys are missing (pipeline hasn't added them yet).
    """
    tool_metrics = result.get("tool_metrics", [])
    if tool_metrics:
        try:
            db.create_tool_metrics(pipeline_run_id, tool_metrics)
        except Exception as exc:
            logger.warning("Failed to store tool metrics: %s", exc)

    llm_usage = result.get("llm_usage")
    if llm_usage:
        try:
            db.create_llm_usage(pipeline_run_id, llm_usage)
        except Exception as exc:
            logger.warning("Failed to store LLM usage: %s", exc)


def _validate_contract(result: Dict[str, Any], contract_cls: type, pipeline_name: str) -> None:
    """
    Validate a pipeline result against its Pydantic contract (lenient mode).

    Logs warnings but does NOT fail the pipeline run.
    """
    try:
        contract_cls(**result)
        logger.debug("Contract validated for %s", pipeline_name)
    except Exception as exc:
        logger.warning(
            "⚠️ Contract validation warning for %s: %s (lenient — not failing)",
            pipeline_name, exc,
        )


# ══════════════════════════════════════════════════════
# Task 1: PreBronze → Bronze
# ══════════════════════════════════════════════════════

@task(
    name="prebronze_to_bronze",
    retries=settings.orchestrator_max_retries,
    retry_delay_seconds=settings.orchestrator_retry_delay_seconds,
    log_prints=True,
)
def run_prebronze_to_bronze(
    orchestration_run_id: str,
    source_name: str,
    raw_input: List[Dict[str, Any]],
    trigger_type: str = "manual",
    triggered_by: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Wrap the PreBronze→Bronze LangGraph pipeline as a Prefect task.

    Parameters
    ----------
    orchestration_run_id : str
        Parent orchestration run id.
    source_name : str
        Vendor / data source name (e.g. "walmart", "usda").
    raw_input : list[dict]
        The raw records to ingest.
    config : dict, optional
        Extra configuration to merge into the pipeline state.

    Returns
    -------
    dict
        Final pipeline state after execution.
    """
    cfg = config or {}
    start = time.time()

    # Cancellation check before starting
    _check_cancellation(orchestration_run_id)

    # 1. Create pipeline run record
    pipeline_run = db.create_pipeline_run(
        pipeline_name="prebronze_to_bronze",
        orchestration_run_id=orchestration_run_id,
        trigger_type=trigger_type,
        triggered_by=triggered_by,
        batch_size=cfg.get("batch_size", settings.orchestrator_default_batch_size),
        incremental=cfg.get("incremental", True),
        run_config=cfg,
    )
    run_id = pipeline_run["id"]
    db.update_orchestration_run(orchestration_run_id, current_layer="prebronze_to_bronze")

    try:
        # 2. Import the pipeline
        _ensure_pipeline_on_path("prebronze-to-bronze")
        from prebronze.orchestrator import run_sequential  # type: ignore
        from prebronze.state import OrchestratorState  # type: ignore

        # 3. Build initial state
        state: Dict[str, Any] = {
            "source_name": source_name,
            "ingestion_run_id": run_id,
            "raw_input": raw_input,
            **cfg,
        }

        # 4. Run pipeline with timeout
        timeout = db.get_pipeline_timeout("prebronze_to_bronze")
        logger.info("🚀 Starting PreBronze→Bronze (source=%s, records=%d, timeout=%s)", source_name, len(raw_input), timeout)
        final_state = _run_with_timeout(
            run_sequential, args=(state,), timeout=timeout, pipeline_name="prebronze_to_bronze",
        )

        # 5. Extract metrics
        records_written = final_state.get("records_loaded", 0)
        records_failed = final_state.get("validation_errors_count", 0)
        dq_issues = len(final_state.get("validation_errors", []))

        # 6. Update pipeline run
        db.update_pipeline_run(
            run_id,
            status="completed",
            records_input=len(raw_input),
            records_processed=len(raw_input),
            records_written=records_written,
            records_failed=records_failed,
            dq_issues_found=dq_issues,
            completed_at=db._utcnow(),
            duration_seconds=_calculate_duration(start),
        )

        # 7. Persist tool metrics + LLM usage
        _store_metrics(run_id, final_state)

        # 8. Contract validation (lenient)
        from .contracts import PreBronzeResult
        _validate_contract(final_state, PreBronzeResult, "prebronze_to_bronze")

        logger.info("✅ PreBronze→Bronze completed. Written=%d, Failed=%d", records_written, records_failed)
        return final_state

    except PipelineTimeoutError:
        db.update_pipeline_run(
            run_id,
            status="timed_out",
            completed_at=db._utcnow(),
            duration_seconds=_calculate_duration(start),
            error_message=f"Timed out after {db.get_pipeline_timeout('prebronze_to_bronze')}s",
        )
        logger.error("⏰ PreBronze→Bronze TIMED OUT")
        raise
    except Exception as exc:
        db.update_pipeline_run(
            run_id,
            status="failed",
            completed_at=db._utcnow(),
            duration_seconds=_calculate_duration(start),
            error_message=str(exc),
            error_details={"traceback": traceback.format_exc()},
        )
        logger.error("❌ PreBronze→Bronze FAILED: %s", exc)
        raise


# ══════════════════════════════════════════════════════
# Task 2: Bronze → Silver
# ══════════════════════════════════════════════════════

@task(
    name="bronze_to_silver",
    retries=settings.orchestrator_max_retries,
    retry_delay_seconds=settings.orchestrator_retry_delay_seconds,
    log_prints=True,
)
def run_bronze_to_silver(
    orchestration_run_id: str,
    trigger_type: str = "manual",
    triggered_by: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Wrap the Bronze→Silver LangGraph auto-orchestrator as a Prefect task.

    Uses ``auto_orchestrator.transform_all_tables()`` to auto-discover
    all bronze tables that have unprocessed data.
    """
    cfg = config or {}
    start = time.time()

    _check_cancellation(orchestration_run_id)

    pipeline_run = db.create_pipeline_run(
        pipeline_name="bronze_to_silver",
        orchestration_run_id=orchestration_run_id,
        trigger_type=trigger_type,
        triggered_by=triggered_by,
        batch_size=cfg.get("batch_size", settings.orchestrator_default_batch_size),
        incremental=cfg.get("incremental", True),
        run_config=cfg,
    )
    run_id = pipeline_run["id"]
    db.update_orchestration_run(orchestration_run_id, current_layer="bronze_to_silver")

    try:
        _ensure_pipeline_on_path("bronze-to-silver")
        from bronze_to_silver.auto_orchestrator import transform_all_tables  # type: ignore

        timeout = db.get_pipeline_timeout("bronze_to_silver")
        logger.info("🚀 Starting Bronze→Silver (auto-discovery mode, timeout=%s)", timeout)
        result = _run_with_timeout(
            transform_all_tables, timeout=timeout, pipeline_name="bronze_to_silver",
        )

        total_processed = result.get("total_processed", 0)
        total_written = result.get("total_written", 0)
        total_failed = result.get("total_failed", 0)
        total_dq = result.get("total_dq_issues", 0)

        db.update_pipeline_run(
            run_id,
            status="completed",
            records_input=total_processed,
            records_processed=total_processed,
            records_written=total_written,
            records_failed=total_failed,
            dq_issues_found=total_dq,
            completed_at=db._utcnow(),
            duration_seconds=_calculate_duration(start),
        )

        _store_metrics(run_id, result)

        # Contract validation (lenient)
        from .contracts import TransformResult
        _validate_contract(result, TransformResult, "bronze_to_silver")

        logger.info("✅ Bronze→Silver completed. Written=%d, DQ=%d", total_written, total_dq)
        return result

    except PipelineTimeoutError:
        db.update_pipeline_run(
            run_id,
            status="timed_out",
            completed_at=db._utcnow(),
            duration_seconds=_calculate_duration(start),
            error_message=f"Timed out after {db.get_pipeline_timeout('bronze_to_silver')}s",
        )
        logger.error("⏰ Bronze→Silver TIMED OUT")
        raise
    except Exception as exc:
        db.update_pipeline_run(
            run_id,
            status="failed",
            completed_at=db._utcnow(),
            duration_seconds=_calculate_duration(start),
            error_message=str(exc),
            error_details={"traceback": traceback.format_exc()},
        )
        logger.error("❌ Bronze→Silver FAILED: %s", exc)
        raise


# ══════════════════════════════════════════════════════
# Task 3: Silver → Gold
# ══════════════════════════════════════════════════════

@task(
    name="silver_to_gold",
    retries=settings.orchestrator_max_retries,
    retry_delay_seconds=settings.orchestrator_retry_delay_seconds,
    log_prints=True,
)
def run_silver_to_gold(
    orchestration_run_id: str,
    trigger_type: str = "manual",
    triggered_by: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Wrap the Silver→Gold LangGraph auto-orchestrator as a Prefect task.

    Uses ``auto_orchestrator.transform_all_tables()`` to auto-discover
    all silver tables that have unprocessed data.
    """
    cfg = config or {}
    start = time.time()

    _check_cancellation(orchestration_run_id)

    pipeline_run = db.create_pipeline_run(
        pipeline_name="silver_to_gold",
        orchestration_run_id=orchestration_run_id,
        trigger_type=trigger_type,
        triggered_by=triggered_by,
        batch_size=cfg.get("batch_size", settings.orchestrator_default_batch_size),
        incremental=cfg.get("incremental", True),
        run_config=cfg,
    )
    run_id = pipeline_run["id"]
    db.update_orchestration_run(orchestration_run_id, current_layer="silver_to_gold")

    try:
        _ensure_pipeline_on_path("silver-to-gold")
        from silver_to_gold.auto_orchestrator import transform_all_tables  # type: ignore

        timeout = db.get_pipeline_timeout("silver_to_gold")
        logger.info("🚀 Starting Silver→Gold (auto-discovery mode, timeout=%s)", timeout)
        result = _run_with_timeout(
            transform_all_tables, timeout=timeout, pipeline_name="silver_to_gold",
        )

        total_processed = result.get("total_processed", 0)
        total_written = result.get("total_written", 0)
        total_failed = result.get("total_failed", 0)

        db.update_pipeline_run(
            run_id,
            status="completed",
            records_input=total_processed,
            records_processed=total_processed,
            records_written=total_written,
            records_failed=total_failed,
            completed_at=db._utcnow(),
            duration_seconds=_calculate_duration(start),
        )

        _store_metrics(run_id, result)

        # Contract validation (lenient)
        from .contracts import TransformResult
        _validate_contract(result, TransformResult, "silver_to_gold")

        logger.info("✅ Silver→Gold completed. Written=%d, Failed=%d", total_written, total_failed)
        return result

    except PipelineTimeoutError:
        db.update_pipeline_run(
            run_id,
            status="timed_out",
            completed_at=db._utcnow(),
            duration_seconds=_calculate_duration(start),
            error_message=f"Timed out after {db.get_pipeline_timeout('silver_to_gold')}s",
        )
        logger.error("⏰ Silver→Gold TIMED OUT")
        raise
    except Exception as exc:
        db.update_pipeline_run(
            run_id,
            status="failed",
            completed_at=db._utcnow(),
            duration_seconds=_calculate_duration(start),
            error_message=str(exc),
            error_details={"traceback": traceback.format_exc()},
        )
        logger.error("❌ Silver→Gold FAILED: %s", exc)
        raise


# ══════════════════════════════════════════════════════
# Task 4: Gold → Neo4j (Batch Sync)
# ══════════════════════════════════════════════════════

@task(name="gold_to_neo4j_batch", retries=1, retry_delay_seconds=120)
def run_gold_to_neo4j_batch(
    orchestration_run_id: str,
    layer: str = "all",
    trigger_type: str = "manual",
    triggered_by: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Wrap the Gold→Neo4j batch sync as a Prefect task.

    Uses ``Neo4jPipelineAdapter`` to invoke the catalog_batch runner.
    """
    cfg = config or {}
    start = time.time()

    _check_cancellation(orchestration_run_id)

    pipeline_run = db.create_pipeline_run(
        pipeline_name="gold_to_neo4j",
        orchestration_run_id=orchestration_run_id,
        trigger_type=trigger_type,
        triggered_by=triggered_by,
        source_table="gold.*",
        target_table="neo4j.*",
        run_config=cfg,
    )
    run_id = pipeline_run["id"]
    db.update_orchestration_run(orchestration_run_id, current_layer="gold_to_neo4j")

    try:
        from .neo4j_adapter import Neo4jPipelineAdapter

        adapter = Neo4jPipelineAdapter()

        if layer == "all":
            result = adapter.run_all_layers()
        else:
            layer_result = adapter.run_batch_sync(layer)
            result = type("Obj", (), {
                "status": layer_result.status,
                "total_rows_fetched": layer_result.tables.get("_total_rows_fetched", 0),
                "total_duration_ms": layer_result.duration_ms,
                "error": layer_result.error,
                "to_dict": lambda self=None: {
                    "layer": layer_result.layer,
                    "status": layer_result.status,
                    "tables": layer_result.tables,
                    "duration_ms": layer_result.duration_ms,
                    "error": layer_result.error,
                },
            })()

        result_dict = result.to_dict() if hasattr(result, "to_dict") else {}

        if result.status == "failed":
            db.update_pipeline_run(
                run_id,
                status="failed",
                completed_at=db._utcnow(),
                duration_seconds=_calculate_duration(start),
                error_message=result.error or "Batch sync failed",
            )
            # Alert on failure
            try:
                from .alerts import send_alert
                send_alert(
                    title=f"Gold→Neo4j batch sync failed (layer={layer})",
                    message=result.error or "Unknown error",
                    severity="critical",
                    pipeline_name="gold_to_neo4j",
                    run_id=str(run_id),
                )
            except Exception:
                pass  # Alert dispatch is best-effort
            logger.error("❌ Gold→Neo4j batch sync FAILED: %s", result.error)
        else:
            db.update_pipeline_run(
                run_id,
                status="completed",
                records_processed=getattr(result, "total_rows_fetched", 0),
                records_written=getattr(result, "total_rows_fetched", 0),
                completed_at=db._utcnow(),
                duration_seconds=_calculate_duration(start),
            )
            logger.info("✅ Gold→Neo4j batch sync completed (layer=%s)", layer)

        _store_metrics(run_id, result_dict)

        return result_dict

    except Exception as exc:
        db.update_pipeline_run(
            run_id,
            status="failed",
            completed_at=db._utcnow(),
            duration_seconds=_calculate_duration(start),
            error_message=str(exc),
            error_details={"traceback": traceback.format_exc()},
        )
        # Alert on exception
        try:
            from .alerts import send_alert
            send_alert(
                title=f"Gold→Neo4j batch sync exception (layer={layer})",
                message=str(exc),
                severity="critical",
                pipeline_name="gold_to_neo4j",
                run_id=str(run_id),
                error_details={"traceback": traceback.format_exc()},
            )
        except Exception:
            pass
        logger.error("❌ Gold→Neo4j batch sync FAILED: %s", exc)
        raise


# ══════════════════════════════════════════════════════
# Task 5: Gold → Neo4j (Reconciliation)
# ══════════════════════════════════════════════════════

@task(name="gold_to_neo4j_reconciliation", retries=0)
def run_gold_to_neo4j_reconciliation(
    orchestration_run_id: str,
    trigger_type: str = "scheduled",
    triggered_by: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run reconciliation between Supabase Gold and Neo4j.

    Detects drift (count/checksum mismatches) and proposes backfills.
    """
    start = time.time()

    pipeline_run = db.create_pipeline_run(
        pipeline_name="gold_to_neo4j",
        orchestration_run_id=orchestration_run_id,
        trigger_type=trigger_type,
        triggered_by=triggered_by,
        source_table="gold.*",
        target_table="neo4j.*",
        run_config={"mode": "reconciliation"},
    )
    run_id = pipeline_run["id"]

    try:
        from .neo4j_adapter import Neo4jPipelineAdapter

        adapter = Neo4jPipelineAdapter()
        report = adapter.run_reconciliation()

        db.update_pipeline_run(
            run_id,
            status="completed",
            records_processed=report.tables_checked,
            records_failed=report.drifts_detected,
            completed_at=db._utcnow(),
            duration_seconds=_calculate_duration(start),
        )

        # Alert if drifts detected
        if report.drifts_detected > 0:
            try:
                from .alerts import send_alert
                send_alert(
                    title=f"Neo4j reconciliation: {report.drifts_detected} drift(s) detected",
                    message=f"Checked {report.tables_checked} tables, found "
                            f"{report.drifts_detected} drifts, "
                            f"{report.backfills_proposed} backfills proposed.",
                    severity="warning",
                    pipeline_name="gold_to_neo4j",
                    run_id=str(run_id),
                )
            except Exception:
                pass

        logger.info(
            "✅ Neo4j reconciliation completed. Drifts=%d, Backfills=%d",
            report.drifts_detected, report.backfills_proposed,
        )
        return report.to_dict()

    except Exception as exc:
        db.update_pipeline_run(
            run_id,
            status="failed",
            completed_at=db._utcnow(),
            duration_seconds=_calculate_duration(start),
            error_message=str(exc),
            error_details={"traceback": traceback.format_exc()},
        )
        logger.error("❌ Neo4j reconciliation FAILED: %s", exc)
        raise


# ══════════════════════════════════════════════════════
# Task 6: Gold → Neo4j (Realtime Worker)
# ══════════════════════════════════════════════════════

@task(name="gold_to_neo4j_realtime", retries=1, retry_delay_seconds=30)
def run_gold_to_neo4j_realtime(
    orchestration_run_id: str,
    trigger_type: str = "event",
    triggered_by: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Start the customer_realtime outbox poller as a long-running task.

    This task is blocking and intended to run as a persistent worker.
    """
    start = time.time()

    pipeline_run = db.create_pipeline_run(
        pipeline_name="gold_to_neo4j",
        orchestration_run_id=orchestration_run_id,
        trigger_type=trigger_type,
        triggered_by=triggered_by,
        source_table="gold.customer_*",
        target_table="neo4j.Customer",
        run_config={"mode": "realtime"},
    )
    run_id = pipeline_run["id"]

    try:
        from .neo4j_adapter import Neo4jPipelineAdapter

        adapter = Neo4jPipelineAdapter()
        logger.info("🔴 Starting realtime Neo4j worker (blocking)")
        adapter.start_realtime_worker()

        # If we reach here, the worker stopped cleanly
        db.update_pipeline_run(
            run_id,
            status="completed",
            completed_at=db._utcnow(),
            duration_seconds=_calculate_duration(start),
        )
        return {"status": "stopped"}

    except Exception as exc:
        db.update_pipeline_run(
            run_id,
            status="failed",
            completed_at=db._utcnow(),
            duration_seconds=_calculate_duration(start),
            error_message=str(exc),
            error_details={"traceback": traceback.format_exc()},
        )
        try:
            from .alerts import send_alert
            send_alert(
                title="Neo4j realtime worker crashed",
                message=str(exc),
                severity="critical",
                pipeline_name="gold_to_neo4j",
                run_id=str(run_id),
            )
        except Exception:
            pass
        logger.error("❌ Neo4j realtime worker FAILED: %s", exc)
        raise

