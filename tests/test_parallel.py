"""
Tests for the parallel ingestion module.

All tests mock DB and pipeline calls — no live infrastructure needed.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from orchestrator.parallel import ParallelSourceRunner, SourceJob, BatchRunResult


# ── Helpers ────────────────────────────────────────────

class FakeSettings:
    parallel_max_concurrency = 2
    parallel_work_dir = "/tmp/test-orchestrator-work"
    parallel_queue_warn_threshold = 10


def make_sources(n: int) -> List[Dict[str, Any]]:
    return [{"source_name": f"source_{i}"} for i in range(n)]


# ── Test: Semaphore limits concurrency ─────────────────

@pytest.mark.asyncio
async def test_semaphore_limits_concurrency():
    """Only N sources should run at the same time."""
    max_concurrent_seen = 0
    current_count = 0
    lock = asyncio.Lock()

    original_run_single = ParallelSourceRunner._run_single_source

    async def mock_run_single(self, job, src_config, flow_name,
                               trigger_type, triggered_by, config):
        nonlocal max_concurrent_seen, current_count
        async with lock:
            current_count += 1
            if current_count > max_concurrent_seen:
                max_concurrent_seen = current_count

        await asyncio.sleep(0.1)  # Simulate work

        async with lock:
            current_count -= 1

        job.result = {"status": "completed"}
        job.layer_timings = {"prebronze_to_bronze": 0.1}

    with patch.object(ParallelSourceRunner, '_run_single_source', mock_run_single), \
         patch('orchestrator.parallel.settings', FakeSettings()), \
         patch.object(ParallelSourceRunner, '_store_job_metadata'), \
         patch.object(ParallelSourceRunner, '_create_work_dir', return_value="/tmp/test"):

        runner = ParallelSourceRunner(max_concurrency=2)
        result = await runner.run_sources(
            sources=make_sources(5),
            flow_name="full_ingestion",
        )

        assert result.total_sources == 5
        assert result.completed == 5
        assert result.failed == 0
        assert max_concurrent_seen <= 2, (
            f"Expected max 2 concurrent, saw {max_concurrent_seen}"
        )


# ── Test: Single source fallback ──────────────────────

@pytest.mark.asyncio
async def test_single_source_fallback():
    """A batch with one source should work like a normal trigger."""
    async def mock_run_single(self, job, src_config, flow_name,
                               trigger_type, triggered_by, config):
        job.result = {"status": "completed", "_layer_timings": {"prebronze_to_bronze": 1.0}}
        job.layer_timings = {"prebronze_to_bronze": 1.0}

    with patch.object(ParallelSourceRunner, '_run_single_source', mock_run_single), \
         patch('orchestrator.parallel.settings', FakeSettings()), \
         patch.object(ParallelSourceRunner, '_store_job_metadata'), \
         patch.object(ParallelSourceRunner, '_create_work_dir', return_value="/tmp/test"):

        runner = ParallelSourceRunner(max_concurrency=2)
        result = await runner.run_sources(
            sources=make_sources(1),
            flow_name="full_ingestion",
        )

        assert result.total_sources == 1
        assert result.completed == 1
        assert result.failed == 0
        assert len(result.source_results) == 1
        assert result.source_results[0]["source_name"] == "source_0"


# ── Test: Per-source isolation (job IDs) ──────────────

@pytest.mark.asyncio
async def test_per_source_isolation_job_id():
    """Each source should get a unique job_id and work dir."""
    created_dirs = []

    def mock_create_work_dir(self, job_id):
        created_dirs.append(job_id)
        return f"/tmp/test/{job_id}"

    async def mock_run_single(self, job, *args, **kwargs):
        job.result = {"status": "completed"}

    with patch.object(ParallelSourceRunner, '_run_single_source', mock_run_single), \
         patch('orchestrator.parallel.settings', FakeSettings()), \
         patch.object(ParallelSourceRunner, '_store_job_metadata'), \
         patch.object(ParallelSourceRunner, '_create_work_dir', mock_create_work_dir):

        runner = ParallelSourceRunner(max_concurrency=2)
        result = await runner.run_sources(
            sources=make_sources(3),
            flow_name="full_ingestion",
        )

        # All job_ids should be unique
        job_ids = [r["job_id"] for r in result.source_results]
        assert len(set(job_ids)) == 3, "Job IDs should be unique"

        # Each should have gotten a create_work_dir call
        assert len(created_dirs) == 3
        assert len(set(created_dirs)) == 3


# ── Test: Failure isolation ────────────────────────────

@pytest.mark.asyncio
async def test_failure_isolation():
    """One source failing should not cancel others."""
    call_count = 0

    async def mock_run_single(self, job, src_config, flow_name,
                               trigger_type, triggered_by, config):
        nonlocal call_count
        call_count += 1
        if src_config["source_name"] == "source_1":
            raise RuntimeError("Simulated failure for source_1")
        job.result = {"status": "completed"}

    with patch.object(ParallelSourceRunner, '_run_single_source', mock_run_single), \
         patch('orchestrator.parallel.settings', FakeSettings()), \
         patch.object(ParallelSourceRunner, '_store_job_metadata'), \
         patch.object(ParallelSourceRunner, '_create_work_dir', return_value="/tmp/test"):

        runner = ParallelSourceRunner(max_concurrency=2)
        result = await runner.run_sources(
            sources=make_sources(3),
            flow_name="full_ingestion",
        )

        # All 3 should have been attempted
        assert call_count == 3
        assert result.completed == 2
        assert result.failed == 1

        # The failed source's error should be captured
        failed = [r for r in result.source_results if r["status"] == "failed"]
        assert len(failed) == 1
        assert failed[0]["source_name"] == "source_1"
        assert "Simulated failure" in failed[0]["error"]


# ── Test: Layer timing recorded ────────────────────────

@pytest.mark.asyncio
async def test_layer_timing_recorded():
    """layer_timings dict should be populated in the result."""
    async def mock_run_single(self, job, src_config, flow_name,
                               trigger_type, triggered_by, config):
        job.result = {
            "status": "completed",
            "_layer_timings": {
                "prebronze_to_bronze": 5.0,
                "bronze_to_silver": 3.0,
            },
        }
        job.layer_timings = {
            "prebronze_to_bronze": 5.0,
            "bronze_to_silver": 3.0,
        }

    with patch.object(ParallelSourceRunner, '_run_single_source', mock_run_single), \
         patch('orchestrator.parallel.settings', FakeSettings()), \
         patch.object(ParallelSourceRunner, '_store_job_metadata'), \
         patch.object(ParallelSourceRunner, '_create_work_dir', return_value="/tmp/test"):

        runner = ParallelSourceRunner(max_concurrency=2)
        result = await runner.run_sources(
            sources=make_sources(1),
            flow_name="full_ingestion",
        )

        timings = result.source_results[0]["layer_timings"]
        assert timings["prebronze_to_bronze"] == 5.0
        assert timings["bronze_to_silver"] == 3.0


# ── Test: Queue depth tracking ─────────────────────────

@pytest.mark.asyncio
async def test_queue_depth_tracking():
    """Queue depth should be accurately reported during execution."""
    depths_seen = []

    async def mock_run_single(self, job, src_config, flow_name,
                               trigger_type, triggered_by, config):
        # Record queue depth while a source is being processed
        depths_seen.append(self.queue_depth)
        await asyncio.sleep(0.05)
        job.result = {"status": "completed"}

    with patch.object(ParallelSourceRunner, '_run_single_source', mock_run_single), \
         patch('orchestrator.parallel.settings', FakeSettings()), \
         patch.object(ParallelSourceRunner, '_store_job_metadata'), \
         patch.object(ParallelSourceRunner, '_create_work_dir', return_value="/tmp/test"):

        runner = ParallelSourceRunner(max_concurrency=2)
        result = await runner.run_sources(
            sources=make_sources(4),
            flow_name="full_ingestion",
        )

        # At some point, queue depth should have been > 0 since concurrency=2 < 4 sources
        assert any(d > 0 for d in depths_seen), (
            f"Expected some queue depth > 0, saw {depths_seen}"
        )
        assert result.total_sources == 4
        assert result.completed == 4


# ── Test: SourceJob properties ─────────────────────────

def test_source_job_end_to_end_seconds():
    """SourceJob should calculate end-to-end time correctly."""
    job = SourceJob(source_name="test")
    job.started_at = 100.0
    job.completed_at = 112.5
    assert job.end_to_end_seconds == 12.5


def test_source_job_wait_seconds():
    """SourceJob should calculate wait time correctly."""
    job = SourceJob(source_name="test")
    job.queued_at = 100.0
    job.started_at = 103.2
    assert job.wait_seconds == 3.2


def test_source_job_defaults():
    """SourceJob should have sensible defaults."""
    job = SourceJob(source_name="usda")
    assert job.source_name == "usda"
    assert job.status == "queued"
    assert job.job_id  # Should be auto-generated
    assert len(job.job_id) == 12
    assert job.layer_timings == {}
    assert job.end_to_end_seconds == 0.0


# ── Test: Batch trigger API endpoint ──────────────────

def test_trigger_batch_endpoint():
    """POST /api/trigger-batch should return 200 with batch_id and per-source run_ids."""
    from unittest.mock import patch, MagicMock
    from fastapi.testclient import TestClient

    with patch('orchestrator.api.db') as mock_db, \
         patch('orchestrator.flows.multi_source_ingestion_flow') as mock_flow:

        mock_db.create_orchestration_run.side_effect = [
            {"id": "run-aaa"},
            {"id": "run-bbb"},
        ]

        from orchestrator.api import app
        client = TestClient(app)

        response = client.post("/api/trigger-batch", json={
            "flow_name": "full_ingestion",
            "sources": [
                {"source_name": "usda"},
                {"source_name": "vendor_a"},
            ],
            "batch_size": 50,
            "incremental": True,
        })

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert "batch_id" in data
        assert len(data["sources"]) == 2
        assert data["sources"][0]["source_name"] == "usda"
        assert data["sources"][0]["run_id"] == "run-aaa"
        assert data["sources"][1]["source_name"] == "vendor_a"
        assert data["sources"][1]["run_id"] == "run-bbb"
        assert data["concurrency_limit"] == 2
        assert data["queue_depth"] == 2


def test_trigger_batch_concurrency_in_response():
    """Response should include the configured concurrency limit."""
    from fastapi.testclient import TestClient

    with patch('orchestrator.api.db') as mock_db, \
         patch('orchestrator.flows.multi_source_ingestion_flow'), \
         patch('orchestrator.api.settings') as mock_settings:

        mock_settings.parallel_max_concurrency = 5
        mock_db.create_orchestration_run.return_value = {"id": "run-xxx"}

        from orchestrator.api import app
        client = TestClient(app)

        response = client.post("/api/trigger-batch", json={
            "sources": [{"source_name": "test"}],
        })

        assert response.status_code == 200
        assert response.json()["concurrency_limit"] == 5
