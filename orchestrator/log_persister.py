"""
Log Persister
==============

Best-effort upload of pipeline run logs to the ``orchestration-logs``
Supabase Storage bucket for long-term auditing.

Each completed (or failed) orchestration run produces a JSON bundle::

    orchestration-logs/
        runs/
            {run_id}/
                summary.json        — run metadata + final status
                step_logs.json      — all pipeline step logs
                pipeline_runs.json  — pipeline-level records

Upload is "fire-and-forget": failures are logged but never block the
pipeline flow.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import db
from .config import settings

logger = logging.getLogger(__name__)

BUCKET = "orchestration-logs"
TEST_BUCKET = "testing-orchestration-logs"
RUN_PREFIX = "runs"


def _get_log_bucket() -> str:
    """Return the correct log bucket for the active environment."""
    return TEST_BUCKET if db.get_env() == "testing" else BUCKET


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_json(obj: Any) -> str:
    """JSON-serialise with datetime/date handling."""
    def _default(o: Any) -> Any:
        if isinstance(o, datetime):
            return o.isoformat()
        if hasattr(o, "isoformat"):
            return o.isoformat()
        return str(o)
    return json.dumps(obj, indent=2, default=_default, ensure_ascii=False)


def _upload_json(path: str, data: Any) -> bool:
    """Upload a JSON blob to the orchestration-logs bucket.

    Returns True on success, False on failure (never raises).
    """
    try:
        client = db.get_storage_client()
        bucket = _get_log_bucket()
        content = _to_json(data).encode("utf-8")

        # Use upsert to overwrite if exists (re-persist on retry)
        client.storage.from_(bucket).upload(
            path,
            content,
            file_options={
                "content-type": "application/json",
                "upsert": "true",
            },
        )
        logger.debug("📦 Uploaded to %s/%s (%d bytes)", bucket, path, len(content))
        return True
    except Exception as exc:
        logger.warning("⚠️ Log persist failed for %s: %s", path, exc)
        return False


def _upload_jsonl(path: str, lines: list) -> bool:
    """Upload a list of dicts as newline-delimited JSON (JSONL).

    Returns True on success, False on failure (never raises).
    """
    try:
        client = db.get_storage_client()
        bucket = _get_log_bucket()
        content = "\n".join(
            json.dumps(line, default=str, ensure_ascii=False) for line in lines
        ).encode("utf-8")

        client.storage.from_(bucket).upload(
            path,
            content,
            file_options={
                "content-type": "application/x-ndjson",
                "upsert": "true",
            },
        )
        logger.debug("📦 Uploaded JSONL to %s/%s (%d lines, %d bytes)",
                     bucket, path, len(lines), len(content))
        return True
    except Exception as exc:
        logger.warning("⚠️ JSONL persist failed for %s: %s", path, exc)
        return False


def persist_run_logs(
    orchestration_run_id: str,
    *,
    extra_metadata: Optional[Dict[str, Any]] = None,
    console_lines: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """Persist complete log bundle for an orchestration run.

    Collects:
    - orchestration_runs row (summary)
    - pipeline_runs linked to this orchestration run
    - pipeline_step_logs for each pipeline run
    - console output lines (real-time subprocess stdout/stderr)

    All uploaded to: orchestration-logs/runs/{run_id}/

    Parameters
    ----------
    orchestration_run_id : str
        The UUID of the orchestration run to persist.
    extra_metadata : dict, optional
        Additional metadata to embed in summary.json (e.g. trigger info).
    console_lines : list of dict, optional
        Raw subprocess output lines captured from RunLogBuffer.drain().

    Returns
    -------
    bool : True if all uploads succeeded.
    """
    try:
        logger.info("📦 Persisting logs for run %s ...", orchestration_run_id)

        # Fetch orchestration run
        orch_run = db.get_orchestration_run(orchestration_run_id)
        if not orch_run:
            logger.warning("Run %s not found — skipping log persist", orchestration_run_id)
            return False

        prefix = f"{RUN_PREFIX}/{orchestration_run_id}"
        all_ok = True

        # ── 1. Summary ──────────────────────────────────
        summary = {
            "run_id": orchestration_run_id,
            "flow_name": orch_run.get("flow_name"),
            "status": orch_run.get("status"),
            "trigger_type": orch_run.get("trigger_type"),
            "triggered_by": orch_run.get("triggered_by"),
            "source_name": orch_run.get("source_name"),
            "started_at": orch_run.get("started_at"),
            "completed_at": orch_run.get("completed_at"),
            "duration_seconds": orch_run.get("duration_seconds"),
            "total_records_written": orch_run.get("total_records_written", 0),
            "total_errors": orch_run.get("total_errors", 0),
            "total_dq_issues": orch_run.get("total_dq_issues", 0),
            "config": orch_run.get("config"),
            "persisted_at": _utcnow_iso(),
        }
        if extra_metadata:
            summary["extra"] = extra_metadata

        if not _upload_json(f"{prefix}/summary.json", summary):
            all_ok = False

        # ── 2. Pipeline Runs ────────────────────────────
        pipeline_runs = db.list_pipeline_runs(orchestration_run_id=orchestration_run_id)
        if not _upload_json(f"{prefix}/pipeline_runs.json", pipeline_runs):
            all_ok = False

        # ── 3. Step Logs (aggregated across all pipeline runs) ──
        all_steps: List[Dict[str, Any]] = []
        for pr in pipeline_runs:
            steps = db.list_step_logs(pipeline_run_id=pr["id"], limit=1000)
            for step in steps:
                step["pipeline_name"] = pr.get("pipeline_name", "")
                step["pipeline_run_id"] = pr["id"]
            all_steps.extend(steps)

        # Sort chronologically
        all_steps.sort(
            key=lambda x: x.get("started_at") or x.get("created_at") or ""
        )

        if not _upload_json(f"{prefix}/step_logs.json", all_steps):
            all_ok = False

        # ── 4. Console output (real-time subprocess logs) ──
        if console_lines:
            if not _upload_jsonl(f"{prefix}/console_output.jsonl", console_lines):
                all_ok = False

        if all_ok:
            n_console = len(console_lines) if console_lines else 0
            logger.info(
                "✅ Logs persisted: %s (%d pipeline runs, %d steps, %d console lines)",
                orchestration_run_id, len(pipeline_runs), len(all_steps), n_console,
            )
        else:
            logger.warning(
                "⚠️ Partial log persist for %s (some uploads failed)",
                orchestration_run_id,
            )

        return all_ok

    except Exception as exc:
        logger.error(
            "❌ Log persist completely failed for %s: %s",
            orchestration_run_id, exc,
        )
        return False


def persist_trigger_event(
    trigger_name: str,
    event_type: str,
    *,
    payload: Optional[Dict[str, Any]] = None,
    result: Optional[Dict[str, Any]] = None,
    orchestration_run_id: Optional[str] = None,
) -> bool:
    """Persist a trigger/cron execution event.

    Uploaded to: orchestration-logs/triggers/{trigger_name}/{timestamp}.json
    """
    ts = datetime.now(timezone.utc)
    ts_slug = ts.strftime("%Y%m%d_%H%M%S")
    path = f"triggers/{trigger_name}/{ts_slug}.json"

    data = {
        "trigger_name": trigger_name,
        "event_type": event_type,
        "timestamp": ts.isoformat(),
        "orchestration_run_id": orchestration_run_id,
        "payload": payload,
        "result": result,
    }

    return _upload_json(path, data)
