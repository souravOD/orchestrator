"""
Tests for the neo4j_adapter module.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from orchestrator.neo4j_adapter import (
    Neo4jLayerResult,
    Neo4jPipelineAdapter,
    Neo4jSyncResult,
    ReconciliationReport,
    _resolve_pipeline_root,
)


# ── Result Dataclass Tests ────────────────────────────

class TestNeo4jLayerResult:
    def test_defaults(self):
        result = Neo4jLayerResult(layer="recipes")
        assert result.layer == "recipes"
        assert result.status == "unknown"
        assert result.tables == {}
        assert result.duration_ms == 0
        assert result.error is None

    def test_with_values(self):
        result = Neo4jLayerResult(
            layer="products",
            status="success",
            tables={"products": {"rows_fetched": 100}},
            duration_ms=5000,
        )
        assert result.status == "success"
        assert result.tables["products"]["rows_fetched"] == 100


class TestNeo4jSyncResult:
    def test_defaults(self):
        result = Neo4jSyncResult()
        assert result.layers_run == []
        assert result.total_rows_fetched == 0
        assert result.status == "unknown"

    def test_to_dict(self):
        result = Neo4jSyncResult(
            layers_run=["recipes"],
            layer_results=[Neo4jLayerResult(layer="recipes", status="success")],
            total_rows_fetched=50,
            status="success",
        )
        d = result.to_dict()
        assert d["layers_run"] == ["recipes"]
        assert d["status"] == "success"
        assert len(d["layer_results"]) == 1
        assert d["layer_results"][0]["layer"] == "recipes"


class TestReconciliationReport:
    def test_defaults(self):
        report = ReconciliationReport()
        assert report.tables_checked == 0
        assert report.drifts_detected == 0

    def test_to_dict(self):
        report = ReconciliationReport(
            tables_checked=10,
            drifts_detected=2,
            backfills_proposed=1,
            details=[{"table": "products", "action": "backfill"}],
        )
        d = report.to_dict()
        assert d["tables_checked"] == 10
        assert d["drifts_detected"] == 2
        assert len(d["details"]) == 1


# ── Adapter Tests ─────────────────────────────────────

class TestNeo4jPipelineAdapter:
    def test_layer_order(self):
        adapter = Neo4jPipelineAdapter()
        assert adapter.LAYER_ORDER == ["recipes", "ingredients", "products", "customers"]

    def test_invalid_layer_raises(self):
        adapter = Neo4jPipelineAdapter()
        with pytest.raises(ValueError, match="Unknown layer"):
            adapter.run_batch_sync("invalid_layer")

    @patch("orchestrator.neo4j_adapter._ensure_pipeline_importable")
    def test_batch_sync_import_error(self, mock_ensure):
        """When the Gold-to-Neo4j pipeline isn't available, returns failed."""
        adapter = Neo4jPipelineAdapter()
        # Force an ImportError for the pipeline import
        with patch.dict("sys.modules", {"services": None, "services.catalog_batch": None}):
            with patch("builtins.__import__", side_effect=ImportError("no module")):
                result = adapter.run_batch_sync("recipes")
        assert result.status == "failed"
        assert "import" in (result.error or "").lower()

    def test_parse_latest_summary_missing_file(self):
        adapter = Neo4jPipelineAdapter()
        result = adapter._parse_latest_summary("recipes")
        assert result is None

    def test_parse_latest_summary_with_data(self, tmp_path):
        adapter = Neo4jPipelineAdapter()
        # Override state_dir to use tmp_path
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        summary_file = state_dir / "run_summaries.jsonl"
        summary_file.write_text(
            json.dumps({"layer": "recipes", "tables": {"recipes": {"rows_fetched": 50}}, "checkpoint_after": "2026-01-01"}) + "\n"
            + json.dumps({"layer": "products", "tables": {"products": {"rows_fetched": 100}}}) + "\n"
        )

        # Patch state_dir property
        with patch.object(type(adapter), "state_dir", new_callable=PropertyMock, return_value=state_dir):
            result = adapter._parse_latest_summary("recipes")
        assert result is not None
        assert result["layer"] == "recipes"
        assert result["tables"]["recipes"]["rows_fetched"] == 50

    def test_parse_reconcile_plans_empty(self, tmp_path):
        adapter = Neo4jPipelineAdapter()
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        with patch.object(type(adapter), "state_dir", new_callable=PropertyMock, return_value=state_dir):
            plans = adapter._parse_reconcile_plans()
        assert plans == []

    def test_parse_reconcile_plans_with_data(self, tmp_path):
        adapter = Neo4jPipelineAdapter()
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        plans_file = state_dir / "reconcile_plans.jsonl"
        plans_file.write_text(
            json.dumps({"entity": "products", "agent_response": {"action": "backfill"}}) + "\n"
        )

        with patch.object(type(adapter), "state_dir", new_callable=PropertyMock, return_value=state_dir):
            plans = adapter._parse_reconcile_plans()
        assert len(plans) == 1
        assert plans[0]["entity"] == "products"

    def test_run_all_layers_returns_sync_result(self):
        """Verify run_all_layers calls run_batch_sync for each layer."""
        adapter = Neo4jPipelineAdapter()

        def mock_batch_sync(layer):
            return Neo4jLayerResult(
                layer=layer,
                status="success",
                tables={"_total_rows_fetched": 10},
                duration_ms=100,
            )

        with patch.object(adapter, "run_batch_sync", side_effect=mock_batch_sync):
            result = adapter.run_all_layers()

        assert result.status == "success"
        assert len(result.layers_run) == 4
        assert result.total_rows_fetched == 40  # 10 * 4 layers
        assert len(result.layer_results) == 4

    def test_run_all_layers_stops_on_failure(self):
        """Verify run_all_layers stops at first failed layer."""
        adapter = Neo4jPipelineAdapter()

        call_count = 0

        def mock_batch_sync(layer):
            nonlocal call_count
            call_count += 1
            if layer == "ingredients":
                return Neo4jLayerResult(layer=layer, status="failed", error="boom")
            return Neo4jLayerResult(layer=layer, status="success", tables={"_total_rows_fetched": 5})

        with patch.object(adapter, "run_batch_sync", side_effect=mock_batch_sync):
            result = adapter.run_all_layers()

        assert result.status == "failed"
        assert "ingredients" in (result.error or "")
        assert call_count == 2  # recipes + ingredients, stopped before products
