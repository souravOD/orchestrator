"""
Pipeline Return-Value Contracts
================================

Pydantic models that validate the dict returned by each pipeline.

**Current mode: LENIENT** (``extra = "allow"``).  Pipelines may return
extra keys without causing a ValidationError.  Once pipeline teams have
cleaned up their return dicts, flip to ``extra = "forbid"`` for strict
enforcement.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Shared sub-models ────────────────────────────────

class ToolMetric(BaseModel):
    """One entry per LangGraph tool/node invocation."""
    tool_name: str
    duration_ms: Optional[int] = None
    records_in: int = 0
    records_out: int = 0
    status: str = "completed"
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class LLMUsage(BaseModel):
    """Aggregated LLM token usage for a pipeline run."""
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    model: Optional[str] = None
    llm_calls: int = 0
    calls: List[Dict[str, Any]] = Field(default_factory=list)


# ── Pipeline-specific contracts ──────────────────────

class PreBronzeResult(BaseModel):
    """Contract for PreBronze→Bronze pipeline returns."""
    records_loaded: int
    validation_errors_count: int = 0
    validation_errors: List[Any] = Field(default_factory=list)
    tool_metrics: List[ToolMetric] = Field(default_factory=list)
    llm_usage: Optional[LLMUsage] = None

    class Config:
        extra = "allow"  # LENIENT — switch to "forbid" after pipeline teams update


class TransformResult(BaseModel):
    """Contract for Bronze→Silver and Silver→Gold pipeline returns."""
    total_processed: int
    total_written: int
    total_failed: int = 0
    total_dq_issues: int = 0
    tool_metrics: List[ToolMetric] = Field(default_factory=list)
    llm_usage: Optional[LLMUsage] = None

    class Config:
        extra = "allow"  # LENIENT — switch to "forbid" after pipeline teams update
