"""
Data Models
============

Pydantic models matching the ``orchestration`` schema.
Used for creating / reading records and for API request/response bodies.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enums ─────────────────────────────────────────────

class RunStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    TIMED_OUT = "timed_out"
    PARTIALLY_COMPLETED = "partially_completed"


class TriggerType(str, Enum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    WEBHOOK = "webhook"
    EVENT = "event"
    UPSTREAM_COMPLETE = "upstream_complete"
    API = "api"
    RETRY = "retry"


class FlowType(str, Enum):
    BATCH = "batch"
    REALTIME = "realtime"
    HYBRID = "hybrid"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class EventType(str, Enum):
    SUPABASE_INSERT = "supabase_insert"
    SUPABASE_UPDATE = "supabase_update"
    SUPABASE_DELETE = "supabase_delete"
    WEBHOOK = "webhook"
    API_CALL = "api_call"
    FILE_UPLOAD = "file_upload"
    UPSTREAM_COMPLETE = "upstream_complete"


# ── Pipeline Definition ──────────────────────────────

class PipelineDefinition(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    pipeline_name: str
    layer_from: str
    layer_to: str
    description: Optional[str] = None
    repo_url: Optional[str] = None
    default_config: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    timeout_seconds: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ── Orchestration Run ─────────────────────────────────

class OrchestrationRunCreate(BaseModel):
    """Payload for creating a new orchestration run."""
    flow_name: str
    flow_type: FlowType = FlowType.BATCH
    trigger_type: TriggerType = TriggerType.MANUAL
    triggered_by: Optional[str] = None
    layers: List[str] = Field(
        default=["prebronze_to_bronze", "bronze_to_silver", "silver_to_gold"]
    )
    config: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class OrchestrationRun(OrchestrationRunCreate):
    id: UUID = Field(default_factory=uuid4)
    status: RunStatus = RunStatus.PENDING
    current_layer: Optional[str] = None
    total_records_processed: int = 0
    total_records_written: int = 0
    total_dq_issues: int = 0
    total_errors: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── Pipeline Run ──────────────────────────────────────

class PipelineRunCreate(BaseModel):
    """Payload for creating a new pipeline run."""
    pipeline_name: str
    orchestration_run_id: UUID
    trigger_type: TriggerType = TriggerType.MANUAL
    triggered_by: Optional[str] = None
    source_table: Optional[str] = None
    target_table: Optional[str] = None
    batch_size: int = 100
    incremental: bool = True
    dry_run: bool = False
    run_config: Dict[str, Any] = Field(default_factory=dict)


class PipelineRun(PipelineRunCreate):
    id: UUID = Field(default_factory=uuid4)
    pipeline_id: Optional[UUID] = None
    run_number: Optional[int] = None
    status: RunStatus = RunStatus.PENDING
    records_input: int = 0
    records_processed: int = 0
    records_written: int = 0
    records_skipped: int = 0
    records_failed: int = 0
    dq_issues_found: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    error_message: Optional[str] = None
    error_details: Optional[Dict[str, Any]] = None
    retry_count: int = 0
    max_retries: int = 3
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── Step Log ──────────────────────────────────────────

class StepLogCreate(BaseModel):
    """Payload for creating a step log entry."""
    pipeline_run_id: UUID
    step_name: str
    step_order: int


class StepLog(StepLogCreate):
    id: UUID = Field(default_factory=uuid4)
    status: StepStatus = StepStatus.PENDING
    records_in: int = 0
    records_out: int = 0
    records_error: int = 0
    state_delta: Dict[str, Any] = Field(default_factory=dict)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    error_traceback: Optional[str] = None


# ── Schedule Definition ───────────────────────────────

class ScheduleDefinition(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    schedule_name: str
    cron_expression: str
    flow_name: str
    pipeline_id: Optional[UUID] = None
    run_config: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None


# ── Event Trigger ─────────────────────────────────────

class EventTrigger(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    trigger_name: str
    event_type: EventType
    source_schema: Optional[str] = None
    source_table: Optional[str] = None
    flow_name: str
    pipeline_id: Optional[UUID] = None
    filter_config: Dict[str, Any] = Field(default_factory=dict)
    debounce_seconds: int = 0
    is_active: bool = True


# ── DQ Summary ────────────────────────────────────────

class RunDQSummary(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    pipeline_run_id: UUID
    table_name: str
    total_records: int = 0
    pass_count: int = 0
    fail_count: int = 0
    avg_quality_score: Optional[float] = None
    issues_by_type: Dict[str, int] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── API Request/Response ──────────────────────────────

class TriggerRequest(BaseModel):
    """Request body for manual API trigger."""
    flow_name: str = "full_ingestion"
    source_name: Optional[str] = None
    input_path: Optional[str] = None
    storage_bucket: Optional[str] = None
    storage_path: Optional[str] = None
    vendor_id: Optional[str] = None
    layers: List[str] = Field(
        default=["prebronze_to_bronze", "bronze_to_silver", "silver_to_gold"]
    )
    batch_size: int = 100
    incremental: bool = True
    dry_run: bool = False


class IngestionInput(BaseModel):
    """Validated input for full_ingestion and single_layer flows."""
    source_name: str = Field(..., min_length=1, description="Vendor/data source name")
    raw_input: Optional[List[Dict[str, Any]]] = None
    input_path: Optional[str] = None

    @model_validator(mode="after")
    def at_least_one_input(self):
        if not self.raw_input and not self.input_path:
            raise ValueError("Either 'raw_input' or 'input_path' must be provided")
        return self

    @field_validator("raw_input")
    @classmethod
    def non_empty_if_provided(cls, v):
        if v is not None and len(v) == 0:
            raise ValueError("raw_input cannot be an empty list")
        return v

class RunSummaryResponse(BaseModel):
    """Lightweight response for listing runs."""
    id: UUID
    flow_name: str
    status: RunStatus
    trigger_type: TriggerType
    total_records_written: int = 0
    total_dq_issues: int = 0
    started_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None


# ── Alert Models ──────────────────────────────────────

class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(str, Enum):
    EMAIL = "email"
    GITHUB_ISSUE = "github_issue"


class AlertLog(BaseModel):
    """Record of a dispatched alert."""
    id: UUID = Field(default_factory=uuid4)
    alert_type: str
    severity: str = "warning"
    title: str
    message: Optional[str] = None
    pipeline_name: Optional[str] = None
    run_id: Optional[str] = None
    dispatch_result: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── Neo4j Sync API Models ────────────────────────────

class Neo4jSyncTriggerRequest(BaseModel):
    """Request body for triggering Neo4j sync via API."""
    layer: str = "all"  # recipes, ingredients, products, customers, or all
    trigger_type: str = "api"

