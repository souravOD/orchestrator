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

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from prefect import task

from . import db
from .config import settings
from .logging_utils import RunSummariser, StepLogger

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════

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


def _stream_pipe(
    pipe,
    level: str,
    pipeline_name: str,
    accumulator: List[str],
    orch_run_id: Optional[str] = None,
) -> None:
    """
    Read lines from a subprocess pipe, log each one in real-time,
    accumulate for post-run parsing, and push to RunLogBuffer for SSE.
    """
    from .log_buffer import RunLogBuffer

    stream_name = "stdout" if level == "info" else "stderr"
    log_fn = getattr(logger, level)
    for line in pipe:
        accumulator.append(line)
        stripped = line.rstrip("\n\r")
        if stripped:
            log_fn("[%s] %s", pipeline_name, stripped)
            if orch_run_id:
                RunLogBuffer.append(orch_run_id, stripped, stream_name)
    pipe.close()


def _env_overrides_for_active_env() -> Dict[str, str]:
    """Build env var overrides to route pipeline subprocesses to the correct Supabase.

    When the orchestrator is running in 'testing' mode, this returns overrides
    for SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY pointing at the test instance.
    In production mode, returns an empty dict (inherits parent env).
    """
    if db.get_env() == "testing":
        from .config import settings
        if settings.supabase_test_url and settings.supabase_test_service_role_key:
            logger.info("🧪 Routing subprocess data to TEST Supabase: %s",
                        settings.supabase_test_url[:50])
            return {
                "SUPABASE_URL": settings.supabase_test_url,
                "SUPABASE_SERVICE_ROLE_KEY": settings.supabase_test_service_role_key,
                "SUPABASE_KEY": settings.supabase_test_service_role_key,
            }
        logger.warning("Testing env active but SUPABASE_TEST_URL not configured — "
                        "subprocess will use production Supabase!")
    return {}


def _run_pipeline_subprocess(
    module: str,
    cli_args: List[str],
    timeout: int | None = None,
    pipeline_name: str = "unknown",
    env_overrides: Dict[str, str] | None = None,
    orch_run_id: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """
    Run a pipeline module as a subprocess via ``python -m <module>``.

    Output is streamed **line-by-line in real-time** so you can follow
    pipeline progress as it happens, rather than waiting for completion.

    The subprocess inherits all environment variables (including
    ``SUPABASE_URL``, ``OPENAI_API_KEY``, etc.) from the orchestrator
    process, so pipelines configure themselves from env vars.

    Parameters
    ----------
    module : str
        Dotted Python module path, e.g. ``bronze_to_silver.auto_orchestrator``.
    cli_args : list[str]
        CLI arguments to pass to the pipeline's ``main()``.
    timeout : int | None
        Maximum seconds before the subprocess is killed.
    pipeline_name : str
        Human-readable name for logging / error messages.
    env_overrides : dict | None
        Extra env vars to add/override for this subprocess.

    Returns
    -------
    subprocess.CompletedProcess
        The completed process with stdout/stderr captured.

    Raises
    ------
    PipelineTimeoutError
        If the subprocess exceeds *timeout*.
    RuntimeError
        If the subprocess exits with a non-zero return code.
    """
    from threading import Thread

    cmd = [sys.executable, "-m", module] + cli_args

    # Build environment: inherit everything, merge overrides.
    # PYTHONUNBUFFERED=1 prevents the child process from buffering
    # stdout when writing to a pipe (non-TTY destination).
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}

    # ── Ensure all pipeline packages are importable ──────────────
    # Pipeline packages live in sibling directories relative to
    # the orchestrator.  Add their parent dirs so that
    # ``python -m prebronze.orchestrator`` etc. resolve correctly
    # regardless of whether we're in a venv or system Python.
    _orch_root = Path(__file__).resolve().parent.parent  # orchestrator/
    _project_root = _orch_root.parent                    # Orchestration Pipeline/
    _pipeline_roots = [
        _project_root / "prebronze-to-bronze",
        _project_root / "bronze-to-silver",
        _project_root / "silver-to-gold",
        _project_root / "nutrition-usda-fetching",
        # Docker container fallback paths (when running inside Coolify)
        Path("/app/prebronze-to-bronze"),
        Path("/app/bronze-to-silver"),
        Path("/app/silver-to-gold"),
        Path("/app/nutrition-usda-fetching"),
    ]
    _existing_pp = env.get("PYTHONPATH", "")
    _extra_paths = os.pathsep.join(str(p) for p in _pipeline_roots if p.exists())
    env["PYTHONPATH"] = f"{_extra_paths}{os.pathsep}{_existing_pp}" if _existing_pp else _extra_paths

    if env_overrides:
        env.update(env_overrides)

    logger.info(
        "🔧 Subprocess: %s %s (timeout=%s)",
        module, " ".join(cli_args), timeout,
    )

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    # Stream stdout and stderr concurrently via daemon threads.
    # Each thread reads line-by-line, logs immediately, and accumulates
    # text so callers can still parse proc.stdout after completion.
    stdout_lines: List[str] = []
    stderr_lines: List[str] = []

    t_out = Thread(
        target=_stream_pipe,
        args=(proc.stdout, "info", pipeline_name, stdout_lines, orch_run_id),
        daemon=True,
    )
    t_err = Thread(
        target=_stream_pipe,
        args=(proc.stderr, "warning", f"{pipeline_name}:stderr", stderr_lines, orch_run_id),
        daemon=True,
    )
    t_out.start()
    t_err.start()

    # Wait for the process, respecting the timeout
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        t_out.join(timeout=5)
        t_err.join(timeout=5)
        raise PipelineTimeoutError(pipeline_name, timeout)

    # Wait for reader threads to drain remaining output
    t_out.join(timeout=10)
    t_err.join(timeout=10)

    stdout_text = "".join(stdout_lines)
    stderr_text = "".join(stderr_lines)

    if proc.returncode != 0:
        raise RuntimeError(
            f"Pipeline '{pipeline_name}' exited with code {proc.returncode}.\n"
            f"stderr: {stderr_text[-2000:] if stderr_text else '(empty)'}"
        )

    return subprocess.CompletedProcess(cmd, proc.returncode, stdout_text, stderr_text)


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


def _store_dq_summary(
    pipeline_run_id: str,
    result: Dict[str, Any],
    pipeline_name: str = "unknown",
) -> None:
    """
    Persist dq_summary from a pipeline result dict.

    Expects ``result["dq_summary"]`` with keys:
        total_records, pass_count, fail_count
    Silently skips if the key is missing.
    """
    dq = result.get("dq_summary")
    if not dq:
        return
    try:
        db.create_dq_summary(
            pipeline_run_id=pipeline_run_id,
            table_name=pipeline_name,
            total_records=dq.get("total_records", 0),
            pass_count=dq.get("pass_count", 0),
            fail_count=dq.get("fail_count", 0),
        )
        logger.info(
            "📊 DQ summary stored for %s: %d total, %d pass, %d fail",
            pipeline_name,
            dq.get("total_records", 0),
            dq.get("pass_count", 0),
            dq.get("fail_count", 0),
        )
    except Exception as exc:
        logger.warning("Failed to store DQ summary: %s", exc)


def _store_step_logs(pipeline_run_id: str, result: Dict[str, Any]) -> None:
    """
    Create pipeline_step_logs entries from the tool_metrics in a pipeline result.

    Each tool metric is written as a separate step log row, preserving
    execution order.  Silently skips if tool_metrics is missing.
    """
    tool_metrics = result.get("tool_metrics", [])
    if not tool_metrics:
        return
    try:
        for idx, m in enumerate(tool_metrics, start=1):
            tool_name = m.get("tool_name") or m.get("tool", "unknown")
            step = db.create_step_log(pipeline_run_id, tool_name, step_order=idx)
            db.update_step_log(
                step["id"],
                status="completed",
                records_in=m.get("records_in", 0),
                records_out=m.get("records_out", 0),
                duration_ms=round(m.get("duration_ms", 0)),
            )
        logger.info("📝 Stored %d step logs for pipeline_run %s", len(tool_metrics), pipeline_run_id)
    except Exception as exc:
        logger.warning("Failed to store step logs: %s", exc)


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


def _parse_pipeline_stdout(stdout: str) -> Dict[str, Any]:
    """
    Best-effort extraction of structured data from subprocess stdout.

    Pipelines write human-readable logs to stdout.  This function
    tries to extract a JSON object if the last non-empty line is
    valid JSON (future convention), otherwise returns an empty dict.
    """
    if not stdout:
        return {}
    # Try to find a JSON object in the last line (future convention)
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {}


# ══════════════════════════════════════════════════════
# Task 1: PreBronze → Bronze
# ══════════════════════════════════════════════════════

@task(
    name="prebronze_to_bronze",
    retries=0,
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
        # 2. Write raw_input to temp JSON file for subprocess
        input_file = None
        try:
            input_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False,
                prefix=f"prebronze_{run_id}_",
            )
            json.dump(raw_input, input_file)
            input_file.close()  # flush & close so subprocess can read it
            input_path = input_file.name
        except Exception as write_exc:
            if input_file and os.path.exists(input_file.name):
                os.unlink(input_file.name)
            raise RuntimeError(f"Failed to write temp input file: {write_exc}") from write_exc

        # 3. Build CLI args
        cli_args = [
            "--input", input_path,
            "--source-name", source_name,
            "--ingestion-run-id", str(run_id),
        ]
        if cfg.get("output_dir"):
            cli_args += ["--output-dir", cfg["output_dir"]]
        if cfg.get("target_table"):
            cli_args += ["--target-table", cfg["target_table"]]
        if cfg.get("skip_translation") is False:
            cli_args.append("--enable-translation")
        if cfg.get("vendor_id"):
            cli_args.extend(["--vendor-id", str(cfg["vendor_id"])])
        if cfg.get("source_record_id_field"):
            cli_args.extend(["--source-record-id-field", cfg["source_record_id_field"]])

        # 4. Run pipeline as subprocess
        timeout = db.get_pipeline_timeout("prebronze_to_bronze")
        logger.info(
            "🚀 Starting PreBronze→Bronze (source=%s, records=%d, timeout=%s)",
            source_name, len(raw_input), timeout,
        )
        proc = _run_pipeline_subprocess(
            module="prebronze.orchestrator",
            cli_args=cli_args,
            timeout=timeout,
            pipeline_name="prebronze_to_bronze",
            env_overrides={
                **_env_overrides_for_active_env(),
                "OPENAI_MODEL_NAME": cfg.get("prebronze_model_name", os.environ.get("OPENAI_MODEL_NAME", "openai/gpt-5-mini")),
                # Only override API key if explicitly provided; otherwise inherit from parent env
                **({"OPENAI_API_KEY": cfg["prebronze_api_key"]} if cfg.get("prebronze_api_key") else {}),
                # Intra-tool LLM parallelism (ThreadPoolExecutor workers)
                "LLM_PARALLEL_WORKERS": str(cfg.get("llm_parallel_workers", 5)),
            },
            orch_run_id=orchestration_run_id,
        )

        # 5. Parse result from stdout (best-effort)
        result = _parse_pipeline_stdout(proc.stdout)

        # 5b. Extract detected_table from stdout for downstream decisions
        #     PreBronze prints: "Supabase load complete: N rows upserted into <table>."
        if proc.stdout and "upserted into" in proc.stdout:
            for line in proc.stdout.splitlines():
                if "upserted into" in line:
                    table_name = line.split("upserted into")[-1].strip().rstrip(".")
                    if table_name:
                        result["detected_table"] = table_name
                        logger.info("Detected target table from stdout: %s", table_name)
                    break

        # 6. Extract metrics — use parsed result or estimate from input
        records_written = result.get("records_loaded", result.get("supabase_rows_written", 0))
        records_failed = result.get("validation_errors_count", 0)
        dq_issues = len(result.get("validation_errors", []))

        # 7. Update pipeline run
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

        # 7b. Persist detected_table for resume recovery
        if result.get("detected_table"):
            db.update_pipeline_run(
                run_id,
                run_config={"detected_table": result["detected_table"]},
            )

        # 8. Persist tool metrics + LLM usage + DQ summary
        _store_metrics(run_id, result)
        _store_dq_summary(run_id, result, "prebronze_to_bronze")
        _store_step_logs(run_id, result)

        logger.info("✅ PreBronze→Bronze completed. Written=%d, Failed=%d", records_written, records_failed)
        return result

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
    finally:
        # Always clean up temp file
        if input_file and os.path.exists(input_file.name):
            try:
                os.unlink(input_file.name)
            except OSError:
                logger.warning("Could not remove temp file: %s", input_file.name)


# ══════════════════════════════════════════════════════
# Task 1.5: USDA Nutrition Fetch (conditional)
# ══════════════════════════════════════════════════════


@task(
    name="usda_nutrition_fetch",
    retries=0,
    log_prints=True,
)
def run_usda_nutrition_fetch(
    orchestration_run_id: str,
    trigger_type: str = "upstream_complete",
    triggered_by: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Fetch USDA nutrition profiles for ingredients found in ``bronze.raw_recipes``.

    This task reads directly from Supabase (no temp-file input needed),
    queries the USDA FoodData Central API for missing ingredients, and
    upserts nutrition profiles into ``bronze.raw_ingredients``.

    Parameters
    ----------
    orchestration_run_id : str
        Parent orchestration run id.
    config : dict, optional
        Optional keys: ``usda_limit`` (int), ``usda_max_workers`` (int).

    Returns
    -------
    dict
        Statistics: extracted, existing, new, successful, failed, inserted.
    """
    cfg = config or {}
    start = time.time()

    _check_cancellation(orchestration_run_id)

    # 1. Create pipeline run record
    pipeline_run = db.create_pipeline_run(
        pipeline_name="usda_nutrition_fetch",
        orchestration_run_id=orchestration_run_id,
        trigger_type=trigger_type,
        triggered_by=triggered_by,
        run_config=cfg,
    )
    run_id = pipeline_run["id"]
    db.update_orchestration_run(orchestration_run_id, current_layer="usda_nutrition_fetch")

    try:
        # 2. Build CLI args
        cli_args: List[str] = []
        limit = cfg.get("usda_limit")
        if limit:
            cli_args += ["--limit", str(limit)]
        max_workers = cfg.get("usda_max_workers", 5)
        cli_args += ["--max-workers", str(max_workers)]

        # NOTE: Do NOT pass --ingestion-run-id to USDA fetcher.
        # Recipes in raw_recipes have the PreBronze run's ingestion_run_id,
        # not the USDA pipeline's run_id. The USDA fetcher will process
        # ALL recipes with status 'INGESTED' and skip already-fetched ones.

        # Pass source table if specified (for F3 product extension)
        source_table = cfg.get("source_table", "raw_recipes")
        cli_args += ["--source-table", source_table]

        # 3. Run USDA Fetcher as subprocess
        timeout = cfg.get("usda_timeout", 3600)  # Default 1 hour — USDA API is slow
        usda_model = cfg.get("usda_model_name", os.environ.get("OPENAI_MODEL_NAME", "openai/gpt-5-mini"))
        logger.info(
            "🔬 Starting USDA Nutrition Fetch (limit=%s, workers=%s, timeout=%s, model=%s)",
            limit, max_workers, timeout, usda_model,
        )
        proc = _run_pipeline_subprocess(
            module="main_enhanced",
            cli_args=cli_args,
            timeout=timeout,
            pipeline_name="usda_nutrition_fetch",
            env_overrides={
                **_env_overrides_for_active_env(),
                "OPENAI_MODEL_NAME": usda_model,
                # Only override API key if explicitly provided; otherwise inherit from parent env
                **({"OPENAI_API_KEY": cfg["usda_api_key"]} if cfg.get("usda_api_key") else {}),
            },
            orch_run_id=orchestration_run_id,
        )

        # 4. Parse result from stdout
        result = _parse_pipeline_stdout(proc.stdout)

        # 5. Extract stats from stdout lines (best-effort)
        #    USDA Fetcher prints: "[OK] Extracted N unique ingredients from recipes"
        #    and final summary stats
        extracted = result.get("extracted", 0)
        inserted = result.get("inserted", 0)
        failed = result.get("failed", 0)

        # 6. Update pipeline run
        db.update_pipeline_run(
            run_id,
            status="completed",
            records_input=extracted,
            records_processed=extracted,
            records_written=inserted,
            records_failed=failed,
            completed_at=db._utcnow(),
            duration_seconds=_calculate_duration(start),
        )

        _store_metrics(run_id, result)
        _store_dq_summary(run_id, result, "usda_nutrition_fetch")
        _store_step_logs(run_id, result)

        logger.info(
            "✅ USDA Nutrition Fetch completed. Extracted=%d, Inserted=%d, Failed=%d",
            extracted, inserted, failed,
        )
        return result

    except PipelineTimeoutError:
        db.update_pipeline_run(
            run_id,
            status="timed_out",
            completed_at=db._utcnow(),
            duration_seconds=_calculate_duration(start),
            error_message=f"USDA fetch timed out after {cfg.get('usda_timeout', 3600)}s",
        )
        logger.error("⏰ USDA Nutrition Fetch TIMED OUT")
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
        logger.error("❌ USDA Nutrition Fetch FAILED: %s", exc)
        raise


# ══════════════════════════════════════════════════════
# Task 2: Bronze → Silver
# ══════════════════════════════════════════════════════

@task(
    name="bronze_to_silver",
    retries=0,
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
        # Build CLI args from config
        batch_size = cfg.get("batch_size", settings.orchestrator_default_batch_size)
        cli_args = ["--batch-size", str(batch_size)]
        if cfg.get("incremental", True):
            cli_args.append("--incremental")
        if cfg.get("dry_run"):
            cli_args.append("--dry-run")
        if cfg.get("enable_schema_diff"):
            cli_args.append("--enable-schema-diff")
        if cfg.get("enable_dq_generation"):
            cli_args.append("--enable-dq-generation")
        if cfg.get("vendor_id"):
            cli_args.extend(["--vendor-id", str(cfg["vendor_id"])])
        if cfg.get("tables"):
            cli_args.extend(["--tables", cfg["tables"]])

        timeout = db.get_pipeline_timeout("bronze_to_silver")
        logger.info("🚀 Starting Bronze→Silver (auto-discovery mode, timeout=%s)", timeout)
        proc = _run_pipeline_subprocess(
            module="bronze_to_silver.auto_orchestrator",
            cli_args=cli_args,
            timeout=timeout,
            pipeline_name="bronze_to_silver",
            env_overrides={
                **_env_overrides_for_active_env(),
                # Intra-tool LLM parallelism (ThreadPoolExecutor workers)
                "LLM_PARALLEL_WORKERS": str(cfg.get("llm_parallel_workers", 5)),
            },
            orch_run_id=orchestration_run_id,
        )

        # Parse structured result from stdout (best-effort)
        result = _parse_pipeline_stdout(proc.stdout)

        total_processed = result.get("total_records_fetched", 0)
        total_written = result.get("total_records_written", 0)
        total_failed = len(result.get("errors", []))
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
        _store_dq_summary(run_id, result, "bronze_to_silver")
        _store_step_logs(run_id, result)

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
    retries=0,
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
        # Build CLI args from config
        batch_size = cfg.get("batch_size", settings.orchestrator_default_batch_size)
        cli_args = ["--batch-size", str(batch_size)]
        if cfg.get("incremental", True):
            cli_args.append("--incremental")
        if cfg.get("reprocess_all"):
            cli_args.append("--reprocess-all")
        if cfg.get("dry_run"):
            cli_args.append("--dry-run")
        if cfg.get("vendor_id"):
            cli_args.extend(["--vendor-id", str(cfg["vendor_id"])])
        if cfg.get("tables"):
            cli_args.extend(["--tables", cfg["tables"]])

        timeout = db.get_pipeline_timeout("silver_to_gold")
        logger.info("🚀 Starting Silver→Gold (auto-discovery mode, timeout=%s)", timeout)
        proc = _run_pipeline_subprocess(
            module="silver_to_gold.auto_orchestrator",
            cli_args=cli_args,
            timeout=timeout,
            pipeline_name="silver_to_gold",
            env_overrides=_env_overrides_for_active_env(),
            orch_run_id=orchestration_run_id,
        )

        # Parse structured result from stdout (best-effort)
        result = _parse_pipeline_stdout(proc.stdout)

        total_processed = result.get("total_records_fetched", 0)
        total_written = result.get("total_records_written", 0)
        total_failed = len(result.get("errors", []))

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
        _store_dq_summary(run_id, result, "silver_to_gold")
        _store_step_logs(run_id, result)

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
        _store_dq_summary(run_id, result_dict, "gold_to_neo4j")
        _store_step_logs(run_id, result_dict)

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


# ══════════════════════════════════════════════════════
# Task 7: Embedding Backfill
# ══════════════════════════════════════════════════════

@task(name="gold_to_neo4j_embedding_backfill", retries=1, retry_delay_seconds=60)
def run_gold_to_neo4j_embedding_backfill(
    orchestration_run_id: str,
    trigger_type: str = "scheduled",
    triggered_by: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run the embedding backfill service to generate missing semantic embeddings.
    """
    cfg = config or {}
    start = time.time()

    _check_cancellation(orchestration_run_id)

    pipeline_run = db.create_pipeline_run(
        pipeline_name="gold_to_neo4j",
        orchestration_run_id=orchestration_run_id,
        trigger_type=trigger_type,
        triggered_by=triggered_by,
        source_table="neo4j.*",
        target_table="neo4j.*.semanticEmbedding",
        run_config={"mode": "embedding_backfill"},
    )
    run_id = pipeline_run["id"]
    db.update_orchestration_run(orchestration_run_id, current_layer="embedding_backfill")

    try:
        from .neo4j_adapter import Neo4jPipelineAdapter

        adapter = Neo4jPipelineAdapter()
        result = adapter.run_embedding_backfill()

        status = "completed" if result.get("status") != "failed" else "failed"
        db.update_pipeline_run(
            run_id,
            status=status,
            records_written=result.get("total_backfilled", 0),
            completed_at=db._utcnow(),
            duration_seconds=_calculate_duration(start),
            error_message=result.get("error"),
        )

        if status == "failed":
            try:
                from .alerts import send_alert
                send_alert(
                    title="Embedding backfill failed",
                    message=str(result.get("error", "Unknown")),
                    severity="warning",
                    pipeline_name="gold_to_neo4j",
                    run_id=str(run_id),
                )
            except Exception:
                pass
            logger.error("❌ Embedding backfill FAILED: %s", result.get("error"))
        else:
            logger.info(
                "✅ Embedding backfill completed: %d nodes",
                result.get("total_backfilled", 0),
            )

        return result

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
                title="Embedding backfill exception",
                message=str(exc),
                severity="critical",
                pipeline_name="gold_to_neo4j",
                run_id=str(run_id),
            )
        except Exception:
            pass
        logger.error("❌ Embedding backfill FAILED: %s", exc)
        raise


# ══════════════════════════════════════════════════════
# Task 8: GraphSAGE Retraining
# ══════════════════════════════════════════════════════

@task(name="gold_to_neo4j_graphsage_retrain", retries=0)
def run_gold_to_neo4j_graphsage_retrain(
    orchestration_run_id: str,
    trigger_type: str = "scheduled",
    triggered_by: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run the GraphSAGE retraining service (every 3 days).
    """
    cfg = config or {}
    start = time.time()

    _check_cancellation(orchestration_run_id)

    pipeline_run = db.create_pipeline_run(
        pipeline_name="gold_to_neo4j",
        orchestration_run_id=orchestration_run_id,
        trigger_type=trigger_type,
        triggered_by=triggered_by,
        source_table="neo4j.*",
        target_table="neo4j.*.graphSageEmbedding",
        run_config={"mode": "graphsage_retrain"},
    )
    run_id = pipeline_run["id"]
    db.update_orchestration_run(orchestration_run_id, current_layer="graphsage_retrain")

    try:
        from .neo4j_adapter import Neo4jPipelineAdapter

        adapter = Neo4jPipelineAdapter()
        result = adapter.run_graphsage_retrain()

        # Treat any non-"failed" status as completed (matches inference task pattern)
        is_failure = result.get("status") == "failed"
        status = "failed" if is_failure else "completed"
        db.update_pipeline_run(
            run_id,
            status=status,
            completed_at=db._utcnow(),
            duration_seconds=_calculate_duration(start),
            error_message=result.get("error") if is_failure else None,
        )

        if is_failure:
            errors = result.get("errors", [])
            error_msg = (
                result.get("error")
                or (errors[0].get("error", "Unknown") if errors else "Unknown")
            )
            try:
                from .alerts import send_alert
                send_alert(
                    title="GraphSAGE retrain failed",
                    message=error_msg,
                    severity="warning",
                    pipeline_name="gold_to_neo4j",
                    run_id=str(run_id),
                )
            except Exception:
                pass
            logger.error("❌ GraphSAGE retrain FAILED: %s", error_msg)
        else:
            logger.info(
                "✅ GraphSAGE retrain completed in %dms",
                result.get("duration_ms", 0),
            )

        return result

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
                title="GraphSAGE retrain exception",
                message=str(exc),
                severity="critical",
                pipeline_name="gold_to_neo4j",
                run_id=str(run_id),
            )
        except Exception:
            pass
        logger.error("❌ GraphSAGE retrain FAILED: %s", exc)
        raise


# ══════════════════════════════════════════════════════
# Task 9: GraphSAGE Inference (Incremental)
# ══════════════════════════════════════════════════════

@task(name="gold_to_neo4j_graphsage_inference", retries=1, retry_delay_seconds=60)
def run_gold_to_neo4j_graphsage_inference(
    orchestration_run_id: str,
    trigger_type: str = "scheduled",
    triggered_by: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run GraphSAGE inference for new nodes using the trained model (every 6 hours).
    """
    cfg = config or {}
    start = time.time()

    _check_cancellation(orchestration_run_id)

    pipeline_run = db.create_pipeline_run(
        pipeline_name="gold_to_neo4j",
        orchestration_run_id=orchestration_run_id,
        trigger_type=trigger_type,
        triggered_by=triggered_by,
        source_table="neo4j.*",
        target_table="neo4j.*.graphSageEmbedding",
        run_config={"mode": "graphsage_inference"},
    )
    run_id = pipeline_run["id"]
    db.update_orchestration_run(orchestration_run_id, current_layer="graphsage_inference")

    try:
        from .neo4j_adapter import Neo4jPipelineAdapter

        adapter = Neo4jPipelineAdapter()
        result = adapter.run_graphsage_inference()

        # "skipped" is treated as success (model not loaded yet)
        is_failure = result.get("status") == "failed"
        status = "failed" if is_failure else "completed"

        db.update_pipeline_run(
            run_id,
            status=status,
            records_written=result.get("nodes_inferred", 0),
            completed_at=db._utcnow(),
            duration_seconds=_calculate_duration(start),
            error_message=result.get("error") if is_failure else None,
        )

        if is_failure:
            try:
                from .alerts import send_alert
                send_alert(
                    title="GraphSAGE inference failed",
                    message=str(result.get("error", "Unknown")),
                    severity="warning",
                    pipeline_name="gold_to_neo4j",
                    run_id=str(run_id),
                )
            except Exception:
                pass
            logger.error("❌ GraphSAGE inference FAILED: %s", result.get("error"))
        else:
            logger.info(
                "✅ GraphSAGE inference completed: status=%s, nodes=%d",
                result.get("status", "?"),
                result.get("nodes_inferred", 0),
            )

        return result

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
                title="GraphSAGE inference exception",
                message=str(exc),
                severity="critical",
                pipeline_name="gold_to_neo4j",
                run_id=str(run_id),
            )
        except Exception:
            pass
        logger.error("❌ GraphSAGE inference FAILED: %s", exc)
        raise
