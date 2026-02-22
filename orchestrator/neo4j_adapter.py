"""
Neo4j Pipeline Adapter
=======================

Bridge between the orchestrator and the Gold-to-Neo4j pipeline.
Wraps the pipeline's ``catalog_batch`` CLI, ``customer_realtime`` worker,
and ``reconciliation`` service for Prefect task invocation.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import settings

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
# Result Models
# ══════════════════════════════════════════════════════

@dataclass
class Neo4jLayerResult:
    """Result from syncing a single layer (e.g. recipes, products)."""
    layer: str
    status: str = "unknown"
    tables: Dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    error: Optional[str] = None
    checkpoint_before: Optional[str] = None
    checkpoint_after: Optional[str] = None


@dataclass
class Neo4jSyncResult:
    """Aggregated result from a batch sync run."""
    layers_run: List[str] = field(default_factory=list)
    layer_results: List[Neo4jLayerResult] = field(default_factory=list)
    total_rows_fetched: int = 0
    total_duration_ms: int = 0
    status: str = "unknown"
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layers_run": self.layers_run,
            "layer_results": [
                {
                    "layer": r.layer,
                    "status": r.status,
                    "tables": r.tables,
                    "duration_ms": r.duration_ms,
                    "error": r.error,
                }
                for r in self.layer_results
            ],
            "total_rows_fetched": self.total_rows_fetched,
            "total_duration_ms": self.total_duration_ms,
            "status": self.status,
            "error": self.error,
        }


@dataclass
class ReconciliationReport:
    """Result from a reconciliation run."""
    tables_checked: int = 0
    drifts_detected: int = 0
    backfills_proposed: int = 0
    details: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tables_checked": self.tables_checked,
            "drifts_detected": self.drifts_detected,
            "backfills_proposed": self.backfills_proposed,
            "details": self.details,
        }


# ══════════════════════════════════════════════════════
# Pipeline Path Management
# ══════════════════════════════════════════════════════

def _resolve_pipeline_root() -> Path:
    """
    Resolve the Gold-to-Neo4j pipeline root.

    Looks for the pipeline relative to the Orchestration Pipeline
    workspace root (one level above the ``orchestrator/`` package).
    """
    # orchestrator package dir
    pkg_dir = Path(__file__).resolve().parent  # orchestrator/orchestrator/
    workspace_root = pkg_dir.parent.parent     # Orchestration Pipeline/

    pipeline_path = workspace_root / settings.neo4j_pipeline_dir
    if not pipeline_path.exists():
        # Try just the directory name directly
        pipeline_path = workspace_root / "Gold-to-Neo4j_with_agentic_checks" / "Gold-to-Neo4j_services"

    return pipeline_path


def _ensure_pipeline_importable() -> Path:
    """Add the Gold-to-Neo4j pipeline root to sys.path if not present."""
    root = _resolve_pipeline_root()
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
        logger.info("Added Gold-to-Neo4j pipeline to sys.path: %s", root_str)
    return root


# ══════════════════════════════════════════════════════
# Adapter Class
# ══════════════════════════════════════════════════════

class Neo4jPipelineAdapter:
    """
    Wraps Gold-to-Neo4j services for orchestrator invocation.

    Usage::

        adapter = Neo4jPipelineAdapter()
        result = adapter.run_batch_sync("recipes")
        result = adapter.run_all_layers()
        adapter.run_reconciliation()
    """

    LAYER_ORDER = ["recipes", "ingredients", "products", "customers"]

    def __init__(self) -> None:
        self._pipeline_root = _resolve_pipeline_root()

    @property
    def pipeline_root(self) -> Path:
        return self._pipeline_root

    @property
    def state_dir(self) -> Path:
        return self._pipeline_root / "state"

    # ── Batch Sync ────────────────────────────────────

    def run_batch_sync(self, layer: str) -> Neo4jLayerResult:
        """
        Run a single batch sync layer.

        Parameters
        ----------
        layer : str
            One of: recipes, ingredients, products, customers
        """
        if layer not in self.LAYER_ORDER:
            raise ValueError(
                f"Unknown layer: {layer}. Must be one of {self.LAYER_ORDER}"
            )

        _ensure_pipeline_importable()
        result = Neo4jLayerResult(layer=layer)
        start = time.monotonic()

        try:
            from services.catalog_batch.run import _run_layer
            logger.info("Starting Gold→Neo4j batch sync: layer=%s", layer)
            _run_layer(layer)
            result.status = "success"
            logger.info("Gold→Neo4j batch sync completed: layer=%s", layer)

        except ImportError as exc:
            result.status = "failed"
            result.error = f"Pipeline import failed: {exc}"
            logger.error("Could not import Gold-to-Neo4j pipeline: %s", exc)

        except RuntimeError as exc:
            # File lock error means the layer is already running
            if "already running" in str(exc):
                result.status = "skipped"
                result.error = str(exc)
                logger.warning("Layer %s already running, skipped", layer)
            else:
                result.status = "failed"
                result.error = str(exc)
                logger.error("Layer %s failed: %s", layer, exc)

        except Exception as exc:
            result.status = "failed"
            result.error = str(exc)
            logger.error("Layer %s failed: %s", layer, exc, exc_info=True)

        result.duration_ms = int((time.monotonic() - start) * 1000)

        # Try to parse the latest run summary for this layer
        summary = self._parse_latest_summary(layer)
        if summary:
            result.tables = summary.get("tables", {})
            result.checkpoint_before = summary.get("checkpoint_before")
            result.checkpoint_after = summary.get("checkpoint_after")
            rows_fetched = sum(
                t.get("rows_fetched", 0) for t in result.tables.values()
            )
            result.tables["_total_rows_fetched"] = rows_fetched

        return result

    def run_all_layers(self) -> Neo4jSyncResult:
        """Run all batch sync layers in order."""
        sync_result = Neo4jSyncResult()
        start = time.monotonic()

        for layer in self.LAYER_ORDER:
            layer_result = self.run_batch_sync(layer)
            sync_result.layers_run.append(layer)
            sync_result.layer_results.append(layer_result)

            total_fetched = layer_result.tables.get("_total_rows_fetched", 0)
            sync_result.total_rows_fetched += total_fetched

            if layer_result.status == "failed":
                sync_result.status = "failed"
                sync_result.error = f"Layer {layer} failed: {layer_result.error}"
                break

        else:
            sync_result.status = "success"

        sync_result.total_duration_ms = int((time.monotonic() - start) * 1000)
        return sync_result

    # ── Reconciliation ────────────────────────────────

    def run_reconciliation(self) -> ReconciliationReport:
        """
        Run the reconciliation service to detect drift
        between Supabase Gold and Neo4j.
        """
        _ensure_pipeline_importable()
        report = ReconciliationReport()

        try:
            from services.reconciliation.service import main as recon_main
            logger.info("Starting Gold→Neo4j reconciliation")
            recon_main()
            logger.info("Gold→Neo4j reconciliation completed")

            # Parse reconcile_plans.jsonl for results
            plans = self._parse_reconcile_plans()
            report.details = plans
            report.backfills_proposed = len(plans)
            report.drifts_detected = sum(
                1 for p in plans if p.get("agent_response", {}).get("action") == "backfill"
            )

        except ImportError as exc:
            logger.error("Could not import reconciliation service: %s", exc)
            report.details.append({"error": f"Import failed: {exc}"})

        except Exception as exc:
            logger.error("Reconciliation failed: %s", exc, exc_info=True)
            report.details.append({"error": str(exc)})

        return report

    # ── Realtime Worker ───────────────────────────────

    def start_realtime_worker(self) -> None:
        """
        Start the customer_realtime outbox poller.

        This is a blocking call — intended to be run as a
        long-running Prefect task or separate process.
        """
        _ensure_pipeline_importable()

        try:
            from services.customer_realtime.service import main as rt_main
            logger.info("Starting Gold→Neo4j realtime worker")
            rt_main()

        except ImportError as exc:
            logger.error("Could not import customer_realtime: %s", exc)
            raise

    # ── Summary Parsing ───────────────────────────────

    def _parse_latest_summary(self, layer: str) -> Optional[Dict[str, Any]]:
        """Parse the latest run summary for the given layer."""
        summary_file = self.state_dir / "run_summaries.jsonl"
        if not summary_file.exists():
            return None

        latest = None
        try:
            with summary_file.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        if record.get("layer") == layer:
                            latest = record
                    except json.JSONDecodeError:
                        continue
        except Exception as exc:
            logger.warning("Failed to parse run summaries: %s", exc)

        return latest

    def _parse_reconcile_plans(self) -> List[Dict[str, Any]]:
        """Parse the reconcile_plans.jsonl file."""
        plans_file = self.state_dir / "reconcile_plans.jsonl"
        if not plans_file.exists():
            return []

        plans: List[Dict[str, Any]] = []
        try:
            with plans_file.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        plans.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception as exc:
            logger.warning("Failed to parse reconcile plans: %s", exc)

        return plans
