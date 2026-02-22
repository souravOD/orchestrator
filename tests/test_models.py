"""
Tests for orchestrator.models
==============================

Validates Pydantic model creation, serialization, enum values,
and default field behavior.
"""

import pytest
from uuid import UUID
from datetime import datetime

from orchestrator.models import (
    RunStatus,
    TriggerType,
    FlowType,
    StepStatus,
    EventType,
    PipelineDefinition,
    OrchestrationRunCreate,
    OrchestrationRun,
    PipelineRunCreate,
    PipelineRun,
    StepLogCreate,
    StepLog,
    ScheduleDefinition,
    EventTrigger,
    RunDQSummary,
    TriggerRequest,
    RunSummaryResponse,
)


# ── Enum Tests ────────────────────────────────────────

class TestEnums:
    def test_run_status_values(self):
        assert RunStatus.PENDING.value == "pending"
        assert RunStatus.COMPLETED.value == "completed"
        assert RunStatus.FAILED.value == "failed"
        assert RunStatus.RETRYING.value == "retrying"
        assert RunStatus.PARTIALLY_COMPLETED.value == "partially_completed"
        assert len(RunStatus) == 10

    def test_trigger_type_values(self):
        assert TriggerType.MANUAL.value == "manual"
        assert TriggerType.WEBHOOK.value == "webhook"
        assert TriggerType.UPSTREAM_COMPLETE.value == "upstream_complete"
        assert len(TriggerType) == 7

    def test_flow_type_values(self):
        assert FlowType.BATCH.value == "batch"
        assert FlowType.REALTIME.value == "realtime"
        assert FlowType.HYBRID.value == "hybrid"
        assert len(FlowType) == 3

    def test_step_status_values(self):
        assert StepStatus.RUNNING.value == "running"
        assert StepStatus.SKIPPED.value == "skipped"
        assert len(StepStatus) == 5

    def test_event_type_values(self):
        assert EventType.SUPABASE_INSERT.value == "supabase_insert"
        assert EventType.FILE_UPLOAD.value == "file_upload"
        assert len(EventType) == 7

    def test_enums_are_string_enums(self):
        """All enums should be usable as plain strings via .value."""
        assert RunStatus.COMPLETED.value == "completed"
        assert TriggerType.WEBHOOK.value == "webhook"
        # StrEnum members compare equal to their string value
        assert RunStatus.COMPLETED == "completed"
        assert TriggerType.WEBHOOK == "webhook"


# ── Pipeline Definition ──────────────────────────────

class TestPipelineDefinition:
    def test_create_with_required_fields(self):
        pd = PipelineDefinition(
            pipeline_name="test_pipeline",
            layer_from="bronze",
            layer_to="silver",
        )
        assert pd.pipeline_name == "test_pipeline"
        assert pd.layer_from == "bronze"
        assert pd.layer_to == "silver"
        assert pd.is_active is True
        assert isinstance(pd.id, UUID)

    def test_defaults(self):
        pd = PipelineDefinition(
            pipeline_name="x", layer_from="bronze", layer_to="silver"
        )
        assert pd.description is None
        assert pd.repo_url is None
        assert pd.default_config == {}
        assert pd.is_active is True
        assert isinstance(pd.created_at, datetime)

    def test_serialization(self):
        pd = PipelineDefinition(
            pipeline_name="test", layer_from="bronze", layer_to="silver"
        )
        data = pd.model_dump()
        assert "pipeline_name" in data
        assert data["pipeline_name"] == "test"
        assert "id" in data


# ── Orchestration Run ─────────────────────────────────

class TestOrchestrationRun:
    def test_create_minimal(self):
        run = OrchestrationRunCreate(flow_name="full_ingestion")
        assert run.flow_name == "full_ingestion"
        assert run.flow_type == FlowType.BATCH
        assert run.trigger_type == TriggerType.MANUAL
        assert run.layers == [
            "prebronze_to_bronze", "bronze_to_silver", "silver_to_gold"
        ]

    def test_create_with_all_fields(self):
        run = OrchestrationRunCreate(
            flow_name="bronze_to_gold",
            flow_type=FlowType.REALTIME,
            trigger_type=TriggerType.WEBHOOK,
            triggered_by="supabase:bronze.raw_products",
            layers=["bronze_to_silver", "silver_to_gold"],
            config={"batch_size": 500},
            metadata={"source": "api"},
        )
        assert run.flow_type == FlowType.REALTIME
        assert run.config == {"batch_size": 500}
        assert len(run.layers) == 2

    def test_full_run_defaults(self):
        run = OrchestrationRun(flow_name="test")
        assert run.status == RunStatus.PENDING
        assert run.current_layer is None
        assert run.total_records_processed == 0
        assert run.total_records_written == 0
        assert run.total_dq_issues == 0
        assert run.total_errors == 0
        assert isinstance(run.id, UUID)


# ── Pipeline Run ──────────────────────────────────────

class TestPipelineRun:
    def test_create_minimal(self):
        run = PipelineRunCreate(
            pipeline_name="prebronze_to_bronze",
            orchestration_run_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )
        assert run.pipeline_name == "prebronze_to_bronze"
        assert run.batch_size == 100
        assert run.incremental is True
        assert run.dry_run is False

    def test_full_run_defaults(self):
        run = PipelineRun(
            pipeline_name="test",
            orchestration_run_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )
        assert run.status == RunStatus.PENDING
        assert run.records_written == 0
        assert run.retry_count == 0
        assert run.max_retries == 3

    def test_serialization_roundtrip(self):
        run = PipelineRunCreate(
            pipeline_name="bronze_to_silver",
            orchestration_run_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            batch_size=500,
        )
        data = run.model_dump()
        restored = PipelineRunCreate(**data)
        assert restored.pipeline_name == run.pipeline_name
        assert restored.batch_size == 500


# ── Step Log ──────────────────────────────────────────

class TestStepLog:
    def test_create(self):
        log = StepLogCreate(
            pipeline_run_id="11111111-2222-3333-4444-555555555555",
            step_name="map_tables",
            step_order=1,
        )
        assert log.step_name == "map_tables"
        assert log.step_order == 1

    def test_full_step_defaults(self):
        log = StepLog(
            pipeline_run_id="11111111-2222-3333-4444-555555555555",
            step_name="test",
            step_order=1,
        )
        assert log.status == StepStatus.PENDING
        assert log.records_in == 0
        assert log.records_out == 0
        assert log.state_delta == {}


# ── Schedule Definition ───────────────────────────────

class TestScheduleDefinition:
    def test_create(self):
        sched = ScheduleDefinition(
            schedule_name="daily_ingestion",
            cron_expression="0 2 * * *",
            flow_name="full_ingestion",
        )
        assert sched.cron_expression == "0 2 * * *"
        assert sched.is_active is True
        assert sched.run_config == {}


# ── Event Trigger ─────────────────────────────────────

class TestEventTrigger:
    def test_create(self):
        trigger = EventTrigger(
            trigger_name="bronze_insert",
            event_type=EventType.SUPABASE_INSERT,
            source_schema="bronze",
            source_table="raw_products",
            flow_name="single_layer",
        )
        assert trigger.event_type == EventType.SUPABASE_INSERT
        assert trigger.debounce_seconds == 0
        assert trigger.is_active is True


# ── DQ Summary ────────────────────────────────────────

class TestRunDQSummary:
    def test_create(self):
        dq = RunDQSummary(
            pipeline_run_id="11111111-2222-3333-4444-555555555555",
            table_name="silver.products",
            total_records=100,
            pass_count=95,
            fail_count=5,
            avg_quality_score=92.5,
            issues_by_type={"missing_field": 3, "invalid_format": 2},
        )
        assert dq.pass_count == 95
        assert dq.issues_by_type["missing_field"] == 3


# ── API Request / Response ────────────────────────────

class TestTriggerRequest:
    def test_defaults(self):
        req = TriggerRequest()
        assert req.flow_name == "full_ingestion"
        assert req.batch_size == 100
        assert req.incremental is True
        assert req.dry_run is False

    def test_custom(self):
        req = TriggerRequest(
            flow_name="single_layer",
            source_name="walmart",
            layers=["bronze_to_silver"],
            batch_size=500,
        )
        assert req.source_name == "walmart"
        assert req.layers == ["bronze_to_silver"]


class TestRunSummaryResponse:
    def test_create(self):
        resp = RunSummaryResponse(
            id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            flow_name="full_ingestion",
            status=RunStatus.COMPLETED,
            trigger_type=TriggerType.MANUAL,
            total_records_written=500,
        )
        assert resp.status == RunStatus.COMPLETED
        assert resp.total_records_written == 500
