"""
Parallel Source Runner
======================

Runs multiple data sources through the ingestion pipeline in parallel,
with a configurable concurrency limit (``asyncio.Semaphore``).

Features
--------
- **Concurrency limiting** — at most *N* sources run simultaneously.
- **Per-source isolation** — unique ``job_id``, private work directory,
  and scoped log context so parallel runs never collide.
- **Instrumentation** — per-layer timing, end-to-end timing, and
  queue/backlog depth tracked and stored in the ``metadata`` JSONB
  column of each ``orchestration_runs`` row.
- **Failure isolation** — one source failing does not cancel others
  (fire-and-settle semantics).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import settings

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
# Data Structures
# ══════════════════════════════════════════════════════

@dataclass
class SourceJob:
    """Represents a single source's ingestion job."""
    source_name: str
    job_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    work_dir: Optional[str] = None
    orch_run_id: Optional[str] = None

    # Timing
    queued_at: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0
    layer_timings: Dict[str, float] = field(default_factory=dict)

    # Result
    status: str = "queued"
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    @property
    def end_to_end_seconds(self) -> float:
        if self.completed_at and self.started_at:
            return round(self.completed_at - self.started_at, 2)
        return 0.0

    @property
    def wait_seconds(self) -> float:
        """Time spent waiting in the queue before execution started."""
        if self.started_at and self.queued_at:
            return round(self.started_at - self.queued_at, 2)
        return 0.0


@dataclass
class BatchRunResult:
    """Aggregate result from a multi-source parallel run."""
    batch_id: str
    total_sources: int
    completed: int = 0
    failed: int = 0
    total_duration_seconds: float = 0.0
    concurrency_limit: int = 2
    source_results: List[Dict[str, Any]] = field(default_factory=list)


# ══════════════════════════════════════════════════════
# Parallel Runner
# ══════════════════════════════════════════════════════

class ParallelSourceRunner:
    """
    Orchestrates parallel execution of multiple sources with
    semaphore-based concurrency control.

    Usage::

        runner = ParallelSourceRunner(max_concurrency=2)
        result = await runner.run_sources(
            sources=[
                {"source_name": "usda", "input_path": "/data/usda.json"},
                {"source_name": "vendor_a", "storage_bucket": "raw", ...},
            ],
            flow_name="full_ingestion",
            config={"batch_size": 100},
        )
    """

    def __init__(self, max_concurrency: Optional[int] = None):
        self.max_concurrency = max_concurrency or settings.parallel_max_concurrency
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._active_count = 0
        self._queued_count = 0
        self._lock = asyncio.Lock()

    @property
    def queue_depth(self) -> int:
        return self._queued_count

    @property
    def active_count(self) -> int:
        return self._active_count

    async def run_sources(
        self,
        sources: List[Dict[str, Any]],
        flow_name: str = "full_ingestion",
        trigger_type: str = "api",
        triggered_by: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        pre_created_run_ids: Optional[Dict[str, str]] = None,
    ) -> BatchRunResult:
        """
        Run multiple sources in parallel through the ingestion pipeline.

        Parameters
        ----------
        sources
            List of source configs, each with at least ``source_name``.
        flow_name
            Which flow to run for each source.
        pre_created_run_ids
            Optional mapping of source_name → pre-created orch_run_id.

        Returns
        -------
        BatchRunResult with per-source outcomes.
        """
        batch_id = str(uuid.uuid4())[:12]
        batch_start = time.time()
        cfg = config or {}
        run_ids = pre_created_run_ids or {}

        logger.info(
            "🚀 Batch %s — launching %d sources (concurrency=%d)",
            batch_id, len(sources), self.max_concurrency,
        )

        # Check queue warning threshold
        if len(sources) > settings.parallel_queue_warn_threshold:
            logger.warning(
                "⚠️ Batch %s — %d sources exceeds queue warning threshold (%d)",
                batch_id, len(sources), settings.parallel_queue_warn_threshold,
            )

        # Create jobs
        jobs = []
        for src in sources:
            job = SourceJob(
                source_name=src["source_name"],
                orch_run_id=run_ids.get(src["source_name"]),
                queued_at=time.time(),
            )
            job.work_dir = self._create_work_dir(job.job_id)
            jobs.append((job, src))

        # Set initial queue depth
        async with self._lock:
            self._queued_count = len(jobs)

        # Dispatch all sources — semaphore limits actual concurrency
        tasks = [
            self._run_guarded(job, src_config, flow_name, trigger_type,
                              triggered_by, cfg, batch_id)
            for job, src_config in jobs
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        # Aggregate results
        batch_duration = round(time.time() - batch_start, 2)
        result = BatchRunResult(
            batch_id=batch_id,
            total_sources=len(jobs),
            concurrency_limit=self.max_concurrency,
            total_duration_seconds=batch_duration,
        )

        for job, _ in jobs:
            source_result = {
                "source_name": job.source_name,
                "job_id": job.job_id,
                "run_id": job.orch_run_id,
                "status": job.status,
                "end_to_end_seconds": job.end_to_end_seconds,
                "wait_seconds": job.wait_seconds,
                "layer_timings": job.layer_timings,
                "error": job.error,
            }
            result.source_results.append(source_result)
            if job.status == "completed":
                result.completed += 1
            else:
                result.failed += 1

        logger.info(
            "🏁 Batch %s — finished in %.1fs: %d/%d completed, %d failed",
            batch_id, batch_duration, result.completed,
            result.total_sources, result.failed,
        )

        return result

    async def _run_guarded(
        self,
        job: SourceJob,
        src_config: Dict[str, Any],
        flow_name: str,
        trigger_type: str,
        triggered_by: Optional[str],
        config: Dict[str, Any],
        batch_id: str,
    ) -> None:
        """Acquire semaphore, run source, release."""
        try:
            async with self._semaphore:
                async with self._lock:
                    self._queued_count -= 1
                    self._active_count += 1

                logger.info(
                    "[batch=%s job=%s source=%s] 🔄 Starting (active=%d, queued=%d)",
                    batch_id, job.job_id, job.source_name,
                    self._active_count, self._queued_count,
                )

                job.started_at = time.time()
                job.status = "running"

                try:
                    await self._run_single_source(
                        job, src_config, flow_name, trigger_type,
                        triggered_by, config,
                    )
                    job.status = "completed"
                except Exception as exc:
                    job.status = "failed"
                    job.error = str(exc)
                    logger.error(
                        "[batch=%s job=%s source=%s] ❌ Failed: %s",
                        batch_id, job.job_id, job.source_name, exc,
                    )
                finally:
                    job.completed_at = time.time()
                    async with self._lock:
                        self._active_count -= 1

                    # Store instrumentation metadata
                    self._store_job_metadata(job, batch_id)

        except Exception as exc:
            job.status = "failed"
            job.error = f"Guard error: {exc}"
            logger.error(
                "[batch=%s job=%s source=%s] ❌ Guard error: %s",
                batch_id, job.job_id, job.source_name, exc,
            )

    async def _run_single_source(
        self,
        job: SourceJob,
        src_config: Dict[str, Any],
        flow_name: str,
        trigger_type: str,
        triggered_by: Optional[str],
        config: Dict[str, Any],
    ) -> None:
        """Execute a single source's flow in a thread (Prefect flows are sync)."""
        from .logging_utils import SourceScopedLogger
        from .flows import FLOW_REGISTRY

        flow_fn = FLOW_REGISTRY.get(flow_name)
        if not flow_fn:
            raise ValueError(f"Unknown flow: {flow_name}")

        # Build kwargs
        kwargs: Dict[str, Any] = {
            "source_name": src_config["source_name"],
            "trigger_type": trigger_type,
            "triggered_by": triggered_by or "api:/api/trigger-batch",
            "config": config,
        }

        if job.orch_run_id:
            kwargs["orch_run_id"] = job.orch_run_id

        if src_config.get("input_path"):
            kwargs["input_path"] = src_config["input_path"]
        elif src_config.get("storage_bucket") and src_config.get("storage_path"):
            kwargs["storage_bucket"] = src_config["storage_bucket"]
            kwargs["storage_path"] = src_config["storage_path"]

        if src_config.get("vendor_id"):
            kwargs["vendor_id"] = src_config["vendor_id"]

        # Run in thread with per-source scoped logging
        loop = asyncio.get_event_loop()

        def _execute():
            with SourceScopedLogger(job.job_id, job.source_name):
                return flow_fn(**kwargs)

        job.result = await loop.run_in_executor(None, _execute)

        # Extract layer_timings from the flow result if available
        if isinstance(job.result, dict):
            job.layer_timings = job.result.get("_layer_timings", {})

    def _create_work_dir(self, job_id: str) -> str:
        """Create an isolated work directory for a source job."""
        base = Path(settings.parallel_work_dir)
        work_dir = base / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        return str(work_dir)

    def _store_job_metadata(self, job: SourceJob, batch_id: str) -> None:
        """Persist instrumentation metadata to the orchestration_run."""
        if not job.orch_run_id:
            return

        try:
            from . import db
            metadata = {
                "batch_id": batch_id,
                "job_id": job.job_id,
                "layer_timings": job.layer_timings,
                "end_to_end_seconds": job.end_to_end_seconds,
                "wait_seconds": job.wait_seconds,
                "work_dir": job.work_dir,
            }
            db.update_orchestration_run_metadata(job.orch_run_id, metadata)
        except Exception as exc:
            logger.debug(
                "Failed to store job metadata (non-fatal): %s", exc,
            )
