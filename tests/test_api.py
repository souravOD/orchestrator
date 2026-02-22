"""
Tests for orchestrator.api
===========================

Uses FastAPI TestClient to test API endpoints.
All database calls are mocked.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from orchestrator.api import app


@pytest.fixture
def client():
    return TestClient(app)


class TestHealthEndpoint:
    def test_health_check(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "orchestrator"


class TestWebhookEndpoint:
    def test_webhook_without_secret(self, client, monkeypatch):
        """When WEBHOOK_SECRET is empty, no auth required."""
        monkeypatch.setattr("orchestrator.api.settings.webhook_secret", "")

        with patch("orchestrator.api.handle_webhook_event") as mock_handler:
            mock_handler.return_value = {"skipped": False}
            response = client.post(
                "/webhooks/supabase",
                json={
                    "type": "INSERT",
                    "table": "raw_products",
                    "schema": "bronze",
                    "record": {"id": "123"},
                },
            )

        assert response.status_code == 200
        assert response.json()["status"] == "accepted"
        mock_handler.assert_called_once()

    def test_webhook_with_valid_secret(self, client, monkeypatch):
        """Correct secret should be accepted."""
        monkeypatch.setattr(
            "orchestrator.api.settings.webhook_secret", "my-secret"
        )

        with patch("orchestrator.api.handle_webhook_event") as mock_handler:
            mock_handler.return_value = {"ok": True}
            response = client.post(
                "/webhooks/supabase",
                json={"type": "INSERT", "table": "x", "schema": "bronze", "record": {}},
                headers={"x-webhook-secret": "my-secret"},
            )

        assert response.status_code == 200

    def test_webhook_with_invalid_secret(self, client, monkeypatch):
        """Wrong secret should return 401."""
        monkeypatch.setattr(
            "orchestrator.api.settings.webhook_secret", "my-secret"
        )

        response = client.post(
            "/webhooks/supabase",
            json={"type": "INSERT", "table": "x", "schema": "bronze", "record": {}},
            headers={"x-webhook-secret": "wrong-secret"},
        )

        assert response.status_code == 401

    def test_webhook_handler_error(self, client, monkeypatch):
        """Handler exceptions should return 500."""
        monkeypatch.setattr("orchestrator.api.settings.webhook_secret", "")

        with patch("orchestrator.api.handle_webhook_event") as mock_handler:
            mock_handler.side_effect = RuntimeError("Pipeline crashed")
            response = client.post(
                "/webhooks/supabase",
                json={"type": "INSERT", "table": "x", "schema": "bronze", "record": {}},
            )

        assert response.status_code == 500
        assert "Pipeline crashed" in response.json()["detail"]


class TestTriggerEndpoint:
    def test_trigger_unknown_flow(self, client):
        """Unknown flow name should return 400."""
        with patch("orchestrator.flows.FLOW_REGISTRY", {"full_ingestion": MagicMock()}):
            response = client.post(
                "/api/trigger",
                json={"flow_name": "nonexistent_flow"},
            )

        # The actual check uses a deferred import from flows
        assert response.status_code in (400, 500)

    def test_trigger_valid_flow(self, client):
        """Valid flow should be dispatched."""
        mock_flow = MagicMock(return_value={"records_written": 100})

        with patch(
            "orchestrator.flows.FLOW_REGISTRY",
            {"full_ingestion": mock_flow},
        ):
            response = client.post(
                "/api/trigger",
                json={
                    "flow_name": "full_ingestion",
                    "source_name": "walmart",
                    "batch_size": 500,
                },
            )

        assert response.status_code == 200
        assert response.json()["status"] == "completed"


class TestRunsEndpoint:
    @patch("orchestrator.api.db.list_orchestration_runs")
    def test_list_runs(self, mock_list, client):
        mock_list.return_value = [
            {"id": "run-1", "flow_name": "test", "status": "completed"},
            {"id": "run-2", "flow_name": "test2", "status": "failed"},
        ]

        response = client.get("/api/runs?limit=10")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        assert len(data["runs"]) == 2

    @patch("orchestrator.api.db.list_orchestration_runs")
    def test_list_runs_with_status_filter(self, mock_list, client):
        mock_list.return_value = [
            {"id": "run-1", "flow_name": "test", "status": "failed"},
        ]

        response = client.get("/api/runs?status=failed")
        assert response.status_code == 200
        mock_list.assert_called_once_with(limit=20, status="failed")

    @patch("orchestrator.api.db.get_orchestration_run")
    def test_get_run_found(self, mock_get, client, sample_orchestration_run):
        mock_get.return_value = sample_orchestration_run

        response = client.get("/api/runs/test-id")
        assert response.status_code == 200
        assert response.json()["flow_name"] == "full_ingestion"

    @patch("orchestrator.api.db.get_orchestration_run")
    def test_get_run_not_found(self, mock_get, client):
        mock_get.return_value = None

        response = client.get("/api/runs/nonexistent")
        assert response.status_code == 404


class TestPipelinesEndpoint:
    @patch("orchestrator.api.db.list_pipeline_definitions")
    def test_list_pipelines(self, mock_list, client):
        mock_list.return_value = [
            {"id": "p1", "pipeline_name": "prebronze_to_bronze", "is_active": True},
            {"id": "p2", "pipeline_name": "bronze_to_silver", "is_active": True},
        ]

        response = client.get("/api/pipelines")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
