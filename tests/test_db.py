"""
Tests for orchestrator.db
==========================

All Supabase interactions are mocked. Tests validate that:
- CRUD functions build correct queries
- Return values are extracted properly
- Edge cases (empty results, None fields) are handled
"""

import pytest
from unittest.mock import MagicMock, patch

from orchestrator import db


class TestGetSupabaseClient:
    def test_raises_without_credentials(self, monkeypatch):
        """Should raise RuntimeError if credentials are missing."""
        monkeypatch.setenv("SUPABASE_URL", "")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")

        # Reset cached client
        db._client = None
        with pytest.raises(RuntimeError, match="SUPABASE_URL"):
            db.get_supabase_client()

    def test_caches_client(self, monkeypatch):
        """Should return the same client on repeated calls."""
        mock_client = MagicMock()
        db._client = mock_client
        assert db.get_supabase_client() is mock_client
        db._client = None  # cleanup


class TestOrchestrationRunsCRUD:
    def test_create_orchestration_run(self, mock_supabase):
        mock_client, mock_query = mock_supabase
        expected_row = {
            "id": "test-id-123",
            "flow_name": "full_ingestion",
            "status": "running",
        }
        mock_query.execute.return_value = MagicMock(data=[expected_row])
        db._client = mock_client

        result = db.create_orchestration_run(
            flow_name="full_ingestion",
            trigger_type="manual",
            triggered_by="cli",
        )

        assert result["id"] == "test-id-123"
        assert result["flow_name"] == "full_ingestion"
        mock_query.insert.assert_called_once()
        db._client = None

    def test_update_orchestration_run(self, mock_supabase):
        mock_client, mock_query = mock_supabase
        mock_query.execute.return_value = MagicMock(
            data=[{"id": "test-id", "status": "completed"}]
        )
        db._client = mock_client

        result = db.update_orchestration_run(
            "test-id",
            status="completed",
            total_records_written=500,
        )

        assert result["status"] == "completed"
        mock_query.update.assert_called_once()
        db._client = None

    def test_update_orchestration_run_empty_patch(self, mock_supabase):
        """Calling update with no fields should return empty dict."""
        mock_client, _ = mock_supabase
        db._client = mock_client
        result = db.update_orchestration_run("test-id")
        assert result == {}
        db._client = None

    def test_get_orchestration_run(self, mock_supabase, sample_orchestration_run):
        mock_client, mock_query = mock_supabase
        mock_query.execute.return_value = MagicMock(
            data=[sample_orchestration_run]
        )
        db._client = mock_client

        result = db.get_orchestration_run("test-id")
        assert result is not None
        assert result["flow_name"] == "full_ingestion"
        db._client = None

    def test_get_orchestration_run_not_found(self, mock_supabase):
        mock_client, mock_query = mock_supabase
        mock_query.execute.return_value = MagicMock(data=[])
        db._client = mock_client

        result = db.get_orchestration_run("nonexistent")
        assert result is None
        db._client = None

    def test_list_orchestration_runs(self, mock_supabase, sample_orchestration_run):
        mock_client, mock_query = mock_supabase
        mock_query.execute.return_value = MagicMock(
            data=[sample_orchestration_run, sample_orchestration_run]
        )
        db._client = mock_client

        result = db.list_orchestration_runs(limit=10)
        assert len(result) == 2
        db._client = None


class TestPipelineRunsCRUD:
    def test_create_pipeline_run(self, mock_supabase, sample_pipeline_run):
        mock_client, mock_query = mock_supabase
        # get_pipeline_definition returns None (no matching def)
        mock_query.execute.side_effect = [
            MagicMock(data=[]),               # get_pipeline_definition
            MagicMock(data=[sample_pipeline_run]),  # insert
        ]
        db._client = mock_client

        result = db.create_pipeline_run(
            pipeline_name="prebronze_to_bronze",
            orchestration_run_id="orch-123",
        )

        assert result["id"] == sample_pipeline_run["id"]
        db._client = None

    def test_update_pipeline_run(self, mock_supabase):
        mock_client, mock_query = mock_supabase
        mock_query.execute.return_value = MagicMock(
            data=[{"id": "run-1", "status": "completed", "records_written": 100}]
        )
        db._client = mock_client

        result = db.update_pipeline_run(
            "run-1",
            status="completed",
            records_written=100,
        )

        assert result["records_written"] == 100
        db._client = None


class TestStepLogsCRUD:
    def test_create_step_log(self, mock_supabase):
        mock_client, mock_query = mock_supabase
        mock_query.execute.return_value = MagicMock(
            data=[{"id": "step-1", "step_name": "map_tables", "status": "running"}]
        )
        db._client = mock_client

        result = db.create_step_log(
            pipeline_run_id="run-1",
            step_name="map_tables",
            step_order=1,
        )

        assert result["step_name"] == "map_tables"
        db._client = None

    def test_update_step_log_completed(self, mock_supabase):
        mock_client, mock_query = mock_supabase
        mock_query.execute.return_value = MagicMock(
            data=[{"id": "step-1", "status": "completed"}]
        )
        db._client = mock_client

        result = db.update_step_log(
            "step-1",
            status="completed",
            records_in=100,
            records_out=95,
            duration_ms=1200,
        )

        assert result["status"] == "completed"
        # Verify completed_at was set automatically
        call_args = mock_query.update.call_args[0][0]
        assert "completed_at" in call_args
        db._client = None

    def test_update_step_log_empty(self, mock_supabase):
        mock_client, _ = mock_supabase
        db._client = mock_client
        result = db.update_step_log("step-1")
        assert result == {}
        db._client = None


class TestDQSummaryCRUD:
    def test_create_dq_summary(self, mock_supabase):
        mock_client, mock_query = mock_supabase
        mock_query.execute.return_value = MagicMock(
            data=[{"id": "dq-1", "table_name": "silver.products"}]
        )
        db._client = mock_client

        result = db.create_dq_summary(
            pipeline_run_id="run-1",
            table_name="silver.products",
            total_records=100,
            pass_count=90,
            fail_count=10,
            avg_quality_score=88.5,
            issues_by_type={"null_field": 5, "invalid_type": 5},
        )

        assert result["table_name"] == "silver.products"
        db._client = None


class TestSchedulesAndTriggers:
    def test_list_active_schedules(self, mock_supabase):
        mock_client, mock_query = mock_supabase
        mock_query.execute.return_value = MagicMock(
            data=[{"schedule_name": "daily", "cron_expression": "0 2 * * *"}]
        )
        db._client = mock_client

        result = db.list_active_schedules()
        assert len(result) == 1
        assert result[0]["schedule_name"] == "daily"
        db._client = None

    def test_list_active_event_triggers(self, mock_supabase):
        mock_client, mock_query = mock_supabase
        mock_query.execute.return_value = MagicMock(
            data=[{"trigger_name": "bronze_insert", "event_type": "supabase_insert"}]
        )
        db._client = mock_client

        result = db.list_active_event_triggers()
        assert len(result) == 1
        assert result[0]["event_type"] == "supabase_insert"
        db._client = None
