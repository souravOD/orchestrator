"""
Logging Utilities
==================

Step-level logger that wraps pipeline tool execution and writes
structured logs to ``orchestration.pipeline_step_logs`` in real-time.
"""

from __future__ import annotations

import logging
import time
import traceback
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from . import db

logger = logging.getLogger(__name__)


class StepLogger:
    """
    Context-manager for logging individual tool/step execution
    within a pipeline run.

    Usage::

        step_logger = StepLogger(pipeline_run_id="...")

        with step_logger.step("map_tables", order=1) as step:
            result = map_tables(state)
            step.set_records(records_in=100, records_out=95)

        # After all steps:
        step_logger.summarise()
    """

    def __init__(self, pipeline_run_id: str):
        self.pipeline_run_id = pipeline_run_id
        self.steps: List[Dict[str, Any]] = []

    @contextmanager
    def step(self, step_name: str, order: int):
        """Context manager for a single tool execution."""
        info = _StepInfo(
            pipeline_run_id=self.pipeline_run_id,
            step_name=step_name,
            step_order=order,
        )
        try:
            # Create DB record
            row = db.create_step_log(
                pipeline_run_id=self.pipeline_run_id,
                step_name=step_name,
                step_order=order,
            )
            info.db_id = row.get("id")
            info.start_time = time.time()
            logger.info("▶ Step %d: %s — started", order, step_name)

            yield info

            # Success
            elapsed_ms = int((time.time() - info.start_time) * 1000)
            db.update_step_log(
                info.db_id,
                status="completed",
                records_in=info.records_in,
                records_out=info.records_out,
                records_error=info.records_error,
                state_delta=info.state_delta,
                duration_ms=elapsed_ms,
            )
            logger.info(
                "✅ Step %d: %s — completed in %dms (in=%d, out=%d, err=%d)",
                order, step_name, elapsed_ms,
                info.records_in, info.records_out, info.records_error,
            )
            info.status = "completed"
            info.duration_ms = elapsed_ms

        except Exception as exc:
            elapsed_ms = int((time.time() - info.start_time) * 1000)
            tb = traceback.format_exc()
            db.update_step_log(
                info.db_id,
                status="failed",
                records_in=info.records_in,
                records_out=info.records_out,
                records_error=info.records_error,
                duration_ms=elapsed_ms,
                error_message=str(exc),
                error_traceback=tb,
            )
            logger.error(
                "❌ Step %d: %s — FAILED in %dms: %s",
                order, step_name, elapsed_ms, exc,
            )
            info.status = "failed"
            info.error = str(exc)
            info.duration_ms = elapsed_ms
            raise  # Re-raise so Prefect/caller can handle

        finally:
            self.steps.append(info.to_dict())

    def summarise(self) -> Dict[str, Any]:
        """Return a summary of all step executions."""
        total_in = sum(s.get("records_in", 0) for s in self.steps)
        total_out = sum(s.get("records_out", 0) for s in self.steps)
        total_err = sum(s.get("records_error", 0) for s in self.steps)
        total_ms = sum(s.get("duration_ms", 0) for s in self.steps)
        failed = [s["step_name"] for s in self.steps if s.get("status") == "failed"]

        return {
            "total_steps": len(self.steps),
            "completed": len(self.steps) - len(failed),
            "failed": len(failed),
            "failed_steps": failed,
            "total_records_in": total_in,
            "total_records_out": total_out,
            "total_records_error": total_err,
            "total_duration_ms": total_ms,
        }


class _StepInfo:
    """Mutable container for step metrics, yielded to callers."""

    def __init__(
        self,
        pipeline_run_id: str,
        step_name: str,
        step_order: int,
    ):
        self.pipeline_run_id = pipeline_run_id
        self.step_name = step_name
        self.step_order = step_order
        self.db_id: Optional[str] = None
        self.start_time: float = 0
        self.status: str = "pending"
        self.records_in: int = 0
        self.records_out: int = 0
        self.records_error: int = 0
        self.state_delta: Dict[str, Any] = {}
        self.duration_ms: int = 0
        self.error: Optional[str] = None

    def set_records(
        self,
        records_in: int = 0,
        records_out: int = 0,
        records_error: int = 0,
    ):
        """Set record counts for this step."""
        self.records_in = records_in
        self.records_out = records_out
        self.records_error = records_error

    def set_state_delta(self, delta: Dict[str, Any]):
        """Store a snapshot of what this tool changed in the state dict."""
        self.state_delta = delta

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_name": self.step_name,
            "step_order": self.step_order,
            "status": self.status,
            "records_in": self.records_in,
            "records_out": self.records_out,
            "records_error": self.records_error,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


class RunSummariser:
    """
    Aggregates results from a pipeline run into DQ summary records.
    """

    @staticmethod
    def write_dq_summary(
        pipeline_run_id: str,
        final_state: Dict[str, Any],
        target_table: str,
    ):
        """
        Extract DQ metrics from a pipeline's final state and write
        a ``run_dq_summary`` record.
        """
        quality_scores = final_state.get("quality_scores", {})
        dq_issues = final_state.get("dq_issues", [])
        total = len(quality_scores)
        pass_count = sum(1 for v in quality_scores.values() if v >= 80)
        fail_count = total - pass_count
        avg_score = (
            sum(quality_scores.values()) / max(total, 1)
        ) if quality_scores else None

        # Group issues by type
        issues_by_type: Dict[str, int] = {}
        for issue in dq_issues:
            issue_type = issue.get("rule_name", issue.get("type", "unknown"))
            issues_by_type[issue_type] = issues_by_type.get(issue_type, 0) + 1

        db.create_dq_summary(
            pipeline_run_id=pipeline_run_id,
            table_name=target_table,
            total_records=total,
            pass_count=pass_count,
            fail_count=fail_count,
            avg_quality_score=avg_score,
            issues_by_type=issues_by_type,
        )
        logger.info(
            "📊 DQ Summary for %s: %d total, %d pass, %d fail, avg=%.1f",
            target_table, total, pass_count, fail_count, avg_score or 0,
        )
