"""
Tests for orchestrator.flows
==============================

Tests flow definitions with all pipeline tasks and DB calls mocked.
"""

import pytest
from unittest.mock import patch, MagicMock

from orchestrator.flows import (
    FLOW_REGISTRY,
    _load_input,
)


class TestFlowRegistry:
    def test_registry_contains_all_flows(self):
        assert "full_ingestion" in FLOW_REGISTRY
        assert "bronze_to_gold" in FLOW_REGISTRY
        assert "single_layer" in FLOW_REGISTRY
        assert "realtime_event" in FLOW_REGISTRY
        assert "multi_source_ingestion" in FLOW_REGISTRY
        assert len(FLOW_REGISTRY) == 8

    def test_all_entries_are_callable(self):
        for name, flow_fn in FLOW_REGISTRY.items():
            assert callable(flow_fn), f"{name} is not callable"


class TestLoadInput:
    def test_load_json_array(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text('[{"name": "a"}, {"name": "b"}]')
        result = _load_input(str(f))
        assert len(result) == 2
        assert result[0]["name"] == "a"

    def test_load_json_single_object(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text('{"name": "single"}')
        result = _load_input(str(f))
        assert len(result) == 1
        assert result[0]["name"] == "single"

    def test_load_ndjson(self, tmp_path):
        f = tmp_path / "data.ndjson"
        f.write_text('{"id": 1}\n{"id": 2}\n{"id": 3}\n')
        result = _load_input(str(f))
        assert len(result) == 3
        assert result[2]["id"] == 3

    def test_load_jsonl(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"x": 1}\n{"x": 2}\n')
        result = _load_input(str(f))
        assert len(result) == 2

    def test_load_csv(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("name,age\nAlice,30\nBob,25\n")
        try:
            result = _load_input(str(f))
            assert len(result) == 2
            assert result[0]["name"] == "Alice"
        except ImportError:
            pytest.skip("pandas not installed")

    def test_load_nonexistent_file(self):
        with pytest.raises(FileNotFoundError):
            _load_input("/tmp/does_not_exist.json")

    def test_load_unsupported_format(self, tmp_path):
        f = tmp_path / "data.xml"
        f.write_text("<data/>")
        with pytest.raises(ValueError, match="Unsupported"):
            _load_input(str(f))

    def test_load_ndjson_with_blank_lines(self, tmp_path):
        f = tmp_path / "data.ndjson"
        f.write_text('{"id": 1}\n\n{"id": 2}\n\n')
        result = _load_input(str(f))
        assert len(result) == 2


class TestSingleLayerFlow:
    @patch("orchestrator.flows.db")
    @patch("orchestrator.flows.run_bronze_to_silver")
    def test_single_layer_valid(self, mock_task, mock_db):
        """single_layer_flow should dispatch to the correct task."""
        mock_db.create_orchestration_run.return_value = {"id": "orch-1"}
        mock_db.update_orchestration_run.return_value = {}
        mock_db._utcnow.return_value = "2026-01-01T00:00:00"
        mock_task.return_value = {"total_written": 50}

        from orchestrator.flows import single_layer_flow
        # Call the underlying function (not Prefect-wrapped)
        result = single_layer_flow.fn(
            layer="bronze_to_silver",
            trigger_type="manual",
            triggered_by="test",
        )

        mock_task.assert_called_once()
        assert result == {"total_written": 50}

    @patch("orchestrator.flows.db")
    def test_single_layer_invalid(self, mock_db):
        """Invalid layer name should raise ValueError."""
        mock_db.create_orchestration_run.return_value = {"id": "orch-1"}

        from orchestrator.flows import single_layer_flow
        with pytest.raises(ValueError, match="Unknown layer"):
            single_layer_flow.fn(
                layer="nonexistent_layer",
                trigger_type="manual",
            )


class TestRealtimeEventFlow:
    @patch("orchestrator.flows.single_layer_flow")
    def test_bronze_schema_routes_correctly(self, mock_single):
        """Events on bronze schema should route to bronze_to_silver."""
        mock_single.return_value = {"ok": True}

        from orchestrator.flows import realtime_event_flow
        result = realtime_event_flow.fn(
            event_type="supabase_insert",
            source_schema="bronze",
            source_table="raw_products",
            record={"id": "123"},
        )

        mock_single.assert_called_once()
        call_kwargs = mock_single.call_args[1]
        assert call_kwargs["layer"] == "bronze_to_silver"

    def test_unknown_schema_skips(self):
        """Events on unknown schema should be skipped."""
        from orchestrator.flows import realtime_event_flow
        result = realtime_event_flow.fn(
            event_type="supabase_insert",
            source_schema="unknown_schema",
            source_table="some_table",
            record={},
        )

        assert result.get("skipped") is True
