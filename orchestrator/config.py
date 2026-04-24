"""
Configuration
==============

Pydantic Settings for environment-based configuration.
"""

from __future__ import annotations

import os
from typing import Optional

from pydantic_settings import BaseSettings


class OrchestratorSettings(BaseSettings):
    """Central configuration loaded from environment variables / .env file."""

    # ── Supabase (production) ────────────────────────
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    # ── Supabase (testing – separate project) ─────
    supabase_test_url: str = ""
    supabase_test_service_role_key: str = ""

    # ── OpenAI (for LLM-augmented pipeline steps) ────
    openai_api_key: Optional[str] = None
    openai_model_name: str = "gpt-4o-mini"
    openai_base_url: Optional[str] = None

    # ── Orchestrator defaults ─────────────────────────
    orchestrator_log_level: str = "INFO"
    orchestrator_default_batch_size: int = 100
    orchestrator_max_retries: int = 3
    orchestrator_retry_delay_seconds: int = 60

    # ── Webhook DLQ ───────────────────────────────────
    dlq_max_retries: int = 3
    dlq_retry_delays_minutes: str = "1,5,30"  # conservative exponential backoff
    dlq_poll_interval_seconds: int = 30

    # ── Webhook server ────────────────────────────────
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8100
    webhook_secret: str = ""

    # ── Neo4j (passed through to Gold-to-Neo4j pipeline) ─
    neo4j_uri: str = ""
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"
    neo4j_realtime_poll_interval: int = 5  # seconds
    neo4j_realtime_workers: int = 3         # parallel outbox worker threads
    neo4j_worker_batch_size: int = 50       # events claimed per worker per poll cycle
    neo4j_worker_lock_timeout: int = 300    # stale lock recovery (seconds)

    # ── Agentic schema drift ─────────────────────────
    agent_llm_model: str = "gpt-4.1-mini"
    drift_confidence_threshold: float = 0.85
    drift_agent_name_threshold: float = 0.7

    # ── Alerting: Email (SMTP) ───────────────────────
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    alert_email_from: str = ""
    alert_email_to: str = ""  # comma-separated list

    # ── Alerting: GitHub Issues ──────────────────────
    github_token: str = ""
    github_repo: str = ""  # "owner/repo"

    # ── CrewAI ───────────────────────────────────────
    crewai_enabled: bool = False

    # ── Gold-to-Neo4j pipeline path ──────────────────
    neo4j_pipeline_dir: str = "gold-to-neo4j"

    # ── Parallel Ingestion ───────────────────────────
    parallel_max_concurrency: int = 5               # max sources running simultaneously
    parallel_work_dir: str = "/tmp/orchestrator-work"  # base dir for per-source isolation
    parallel_queue_warn_threshold: int = 10          # alert if backlog exceeds this

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


# Singleton – import this wherever you need config
settings = OrchestratorSettings()
