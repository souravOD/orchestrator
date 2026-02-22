"""
Shared test fixtures for the orchestrator test suite.
"""

import os
import pytest
from unittest.mock import MagicMock, patch


# ── Ensure no real Supabase calls during tests ────────

@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """Set safe defaults for all env vars so config loads without a real .env."""
    monkeypatch.setenv("SUPABASE_URL", "https://test-project.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-1234567890")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    monkeypatch.setenv("ORCHESTRATOR_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("ORCHESTRATOR_DEFAULT_BATCH_SIZE", "50")
    monkeypatch.setenv("ORCHESTRATOR_MAX_RETRIES", "2")
    monkeypatch.setenv("ORCHESTRATOR_RETRY_DELAY_SECONDS", "5")
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")


@pytest.fixture
def mock_supabase():
    """
    Return a mock Supabase client with chainable query builders.
    Patches db._client so no real connections are made.
    """
    mock_client = MagicMock()

    # Build a chainable mock for table queries
    mock_query = MagicMock()
    mock_query.select.return_value = mock_query
    mock_query.insert.return_value = mock_query
    mock_query.update.return_value = mock_query
    mock_query.eq.return_value = mock_query
    mock_query.order.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.execute.return_value = MagicMock(data=[])

    # schema("orchestration").table("xxx") → mock_query
    mock_schema = MagicMock()
    mock_schema.table.return_value = mock_query
    mock_client.schema.return_value = mock_schema

    return mock_client, mock_query


@pytest.fixture
def sample_orchestration_run():
    """Sample orchestration run record as returned by Supabase."""
    return {
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "flow_name": "full_ingestion",
        "flow_type": "batch",
        "status": "running",
        "trigger_type": "manual",
        "triggered_by": "cli",
        "layers": ["prebronze_to_bronze", "bronze_to_silver", "silver_to_gold"],
        "current_layer": None,
        "total_records_processed": 0,
        "total_records_written": 0,
        "total_dq_issues": 0,
        "total_errors": 0,
        "started_at": "2026-02-21T06:00:00+00:00",
        "completed_at": None,
        "duration_seconds": None,
        "config": {},
        "metadata": {},
        "created_at": "2026-02-21T06:00:00+00:00",
    }


@pytest.fixture
def sample_pipeline_run():
    """Sample pipeline run record."""
    return {
        "id": "11111111-2222-3333-4444-555555555555",
        "pipeline_id": "66666666-7777-8888-9999-000000000000",
        "orchestration_run_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "run_number": 1,
        "status": "running",
        "trigger_type": "manual",
        "triggered_by": "cli",
        "source_table": None,
        "target_table": None,
        "batch_size": 100,
        "incremental": True,
        "dry_run": False,
        "run_config": {},
        "records_input": 0,
        "records_processed": 0,
        "records_written": 0,
        "records_skipped": 0,
        "records_failed": 0,
        "dq_issues_found": 0,
        "started_at": "2026-02-21T06:00:00+00:00",
        "completed_at": None,
        "duration_seconds": None,
        "error_message": None,
        "error_details": None,
        "retry_count": 0,
        "max_retries": 3,
        "created_at": "2026-02-21T06:00:00+00:00",
    }
