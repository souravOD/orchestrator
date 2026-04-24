-- =====================================================
-- ORCHESTRATION SCHEMA
-- =====================================================
-- Tracks all pipeline execution state, job logs,
-- and data flow across the medallion architecture.
--
-- To apply: Run this SQL against your Supabase project
-- via the SQL Editor or psql.
-- =====================================================

CREATE SCHEMA IF NOT EXISTS orchestration;

-- ─────────────────────────────────────────────
-- 1. PIPELINE DEFINITIONS
-- Registry of all known pipelines.
-- ─────────────────────────────────────────────

CREATE TABLE orchestration.pipeline_definitions (
    id              uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    pipeline_name   varchar(100) NOT NULL UNIQUE,
    layer_from      varchar(20)  NOT NULL,
    layer_to        varchar(20)  NOT NULL,
    description     text,
    repo_url        varchar(500),
    default_config  jsonb        DEFAULT '{}',
    is_active       boolean      DEFAULT true,
    timeout_seconds int          DEFAULT NULL,
    created_at      timestamptz  DEFAULT now(),
    updated_at      timestamptz  DEFAULT now(),

    CONSTRAINT pipeline_def_layer_from_check CHECK (
        layer_from IN ('prebronze', 'bronze', 'silver', 'gold')
    ),
    CONSTRAINT pipeline_def_layer_to_check CHECK (
        layer_to IN ('bronze', 'silver', 'gold', 'neo4j')
    )
);

COMMENT ON COLUMN orchestration.pipeline_definitions.timeout_seconds
    IS 'Optional per-pipeline timeout in seconds. NULL means no timeout.';

COMMENT ON TABLE orchestration.pipeline_definitions
    IS 'Registry of all known data pipelines in the medallion architecture';

-- Seed the 3 existing pipelines
INSERT INTO orchestration.pipeline_definitions (pipeline_name, layer_from, layer_to, description)
VALUES
    ('prebronze_to_bronze', 'prebronze', 'bronze',
     'Agentic ingestion: CSV/JSON → Bronze tables via LangGraph (classification, mapping, translation, validation)'),
    ('bronze_to_silver', 'bronze', 'silver',
     'Agentic transformation: Bronze → Silver via LangGraph (flattening, tagging, normalization, DQ, nutrition)'),
    ('silver_to_gold', 'silver', 'gold',
     'Agentic enrichment: Silver → Gold via LangGraph (cuisine resolution, ingredients, nutrition facts, lineage)')
ON CONFLICT (pipeline_name) DO NOTHING;


-- ─────────────────────────────────────────────
-- 2. ORCHESTRATION RUNS
-- Top-level record for a full end-to-end flow.
-- One orchestration_run groups multiple pipeline_runs.
-- ─────────────────────────────────────────────

CREATE TABLE orchestration.orchestration_runs (
    id                      uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    flow_name               varchar(100) NOT NULL,
    flow_type               varchar(30)  NOT NULL DEFAULT 'batch',
    status                  varchar(20)  NOT NULL DEFAULT 'pending',
    trigger_type            varchar(30)  NOT NULL,
    triggered_by            varchar(255),

    -- Source / tenant isolation
    source_name             varchar(100),
    vendor_id               uuid,

    -- Which layers to run (ordered)
    layers                  text[]  DEFAULT ARRAY['prebronze_to_bronze',
                                                   'bronze_to_silver',
                                                   'silver_to_gold'],
    current_layer           varchar(50),

    -- Aggregated metrics
    total_records_processed int         DEFAULT 0,
    total_records_written   int         DEFAULT 0,
    total_dq_issues         int         DEFAULT 0,
    total_errors            int         DEFAULT 0,

    -- Progress tracking (B2B frontend)
    progress_pct            int         NOT NULL DEFAULT 0,
    totals                  jsonb       DEFAULT '{}'::jsonb,

    -- Timing
    started_at              timestamptz,
    completed_at            timestamptz,
    duration_seconds        numeric(10,2),

    -- Flexible config & metadata
    config                  jsonb       DEFAULT '{}',
    metadata                jsonb       DEFAULT '{}',

    created_at              timestamptz DEFAULT now(),

    -- Mutual exclusion grouping (e.g. graphsage retrain + inference)
    flow_group              varchar(50),

    CONSTRAINT orch_runs_status_check CHECK (
        status IN ('pending','queued','running','completed','failed',
                   'partially_completed','cancelled','timed_out')
    ),
    CONSTRAINT orch_runs_flow_type_check CHECK (
        flow_type IN ('batch','realtime','hybrid')
    ),
    CONSTRAINT orch_runs_trigger_check CHECK (
        trigger_type IN ('manual','scheduled','webhook','event',
                         'upstream_complete','api','retry')
    )
);

CREATE INDEX idx_orch_runs_status
    ON orchestration.orchestration_runs (status, created_at DESC);
CREATE INDEX idx_orch_runs_flow
    ON orchestration.orchestration_runs (flow_name, created_at DESC);
CREATE INDEX idx_orch_runs_source
    ON orchestration.orchestration_runs (flow_name, source_name, status);
CREATE INDEX idx_orch_runs_vendor
    ON orchestration.orchestration_runs (vendor_id, created_at DESC);

-- Atomic concurrent-run prevention (per flow + per source)
CREATE UNIQUE INDEX idx_orch_runs_single_running
    ON orchestration.orchestration_runs (flow_name, COALESCE(source_name, ''))
    WHERE status = 'running';

-- Cross-flow mutual exclusion (e.g. graphsage retrain ↔ inference)
CREATE UNIQUE INDEX idx_orch_runs_single_running_group
    ON orchestration.orchestration_runs (flow_group)
    WHERE status = 'running' AND flow_group IS NOT NULL;

COMMENT ON TABLE orchestration.orchestration_runs
    IS 'Top-level orchestration flow execution. Groups one or more pipeline_runs.';
COMMENT ON COLUMN orchestration.orchestration_runs.flow_group
    IS 'Optional grouping for mutual exclusion across related flows (e.g. graphsage).';


-- ─────────────────────────────────────────────
-- 3. PIPELINE RUNS
-- One row per individual pipeline execution.
-- ─────────────────────────────────────────────

CREATE TABLE orchestration.pipeline_runs (
    id                      uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    pipeline_id             uuid        NOT NULL REFERENCES orchestration.pipeline_definitions(id),
    orchestration_run_id    uuid        NOT NULL REFERENCES orchestration.orchestration_runs(id)
                                        ON DELETE CASCADE,
    run_number              serial,
    status                  varchar(20) NOT NULL DEFAULT 'pending',
    trigger_type            varchar(30) NOT NULL DEFAULT 'manual',
    triggered_by            varchar(255),

    -- Configuration
    source_table            varchar(100),
    target_table            varchar(100),
    batch_size              int         DEFAULT 100,
    incremental             boolean     DEFAULT true,
    dry_run                 boolean     DEFAULT false,
    run_config              jsonb       DEFAULT '{}',

    -- Execution metrics
    records_input           int         DEFAULT 0,
    records_processed       int         DEFAULT 0,
    records_written         int         DEFAULT 0,
    records_skipped         int         DEFAULT 0,
    records_failed          int         DEFAULT 0,
    dq_issues_found         int         DEFAULT 0,

    -- Timing
    started_at              timestamptz,
    completed_at            timestamptz,
    duration_seconds        numeric(10,2),

    -- Error tracking
    error_message           text,
    error_details           jsonb,
    retry_count             int         DEFAULT 0,
    max_retries             int         DEFAULT 3,

    created_at              timestamptz DEFAULT now(),

    CONSTRAINT pipeline_runs_status_check CHECK (
        status IN ('pending','queued','running','completed','failed',
                   'retrying','cancelled','skipped','timed_out')
    ),
    CONSTRAINT pipeline_runs_trigger_check CHECK (
        trigger_type IN ('manual','scheduled','webhook','event',
                         'upstream_complete','api','retry')
    )
);

CREATE INDEX idx_pipeline_runs_status
    ON orchestration.pipeline_runs (status);
CREATE INDEX idx_pipeline_runs_orch_run
    ON orchestration.pipeline_runs (orchestration_run_id);
CREATE INDEX idx_pipeline_runs_pipeline
    ON orchestration.pipeline_runs (pipeline_id, created_at DESC);

COMMENT ON TABLE orchestration.pipeline_runs
    IS 'Individual pipeline execution record with metrics, timing, and error info';


-- ─────────────────────────────────────────────
-- 4. PIPELINE STEP LOGS
-- Per-tool execution within a pipeline run.
-- ─────────────────────────────────────────────

CREATE TABLE orchestration.pipeline_step_logs (
    id              uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    pipeline_run_id uuid        NOT NULL REFERENCES orchestration.pipeline_runs(id)
                                ON DELETE CASCADE,
    step_name       varchar(100) NOT NULL,
    step_order      int          NOT NULL,
    status          varchar(20)  NOT NULL DEFAULT 'pending',

    -- Metrics
    records_in      int         DEFAULT 0,
    records_out     int         DEFAULT 0,
    records_error   int         DEFAULT 0,

    -- State snapshot (what this tool added/modified in the state dict)
    state_delta     jsonb       DEFAULT '{}',

    -- Timing
    started_at      timestamptz,
    completed_at    timestamptz,
    duration_ms     int,

    -- Error
    error_message   text,
    error_traceback text,

    CONSTRAINT step_logs_status_check CHECK (
        status IN ('pending','running','completed','failed','skipped')
    )
);

CREATE INDEX idx_step_logs_run
    ON orchestration.pipeline_step_logs (pipeline_run_id, step_order);

COMMENT ON TABLE orchestration.pipeline_step_logs
    IS 'Per-tool execution log within a pipeline run (e.g. map_tables, flatten_payloads)';


-- ─────────────────────────────────────────────
-- 5. SCHEDULE DEFINITIONS
-- Cron-like schedules for automated runs.
-- ─────────────────────────────────────────────

CREATE TABLE orchestration.schedule_definitions (
    id              uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_name   varchar(100) NOT NULL UNIQUE,
    cron_expression varchar(100) NOT NULL,
    flow_name       varchar(100) NOT NULL,
    pipeline_id     uuid         REFERENCES orchestration.pipeline_definitions(id),
    run_config      jsonb        DEFAULT '{}',
    is_active       boolean      DEFAULT true,
    last_run_at     timestamptz,
    next_run_at     timestamptz,
    created_at      timestamptz  DEFAULT now(),
    updated_at      timestamptz  DEFAULT now()
);

COMMENT ON TABLE orchestration.schedule_definitions
    IS 'Cron-based schedule definitions for automated pipeline triggers';

-- Seed default schedules
INSERT INTO orchestration.schedule_definitions
    (schedule_name, cron_expression, flow_name, run_config)
VALUES
    ('daily_full_ingestion', '0 2 * * *', 'full_ingestion',
     '{"batch_size": 500, "incremental": true}'),
    ('weekly_usda_nutrition', '0 3 * * 0', 'full_ingestion',
     '{"source_name": "usda", "batch_size": 1000}')
ON CONFLICT (schedule_name) DO NOTHING;


-- ─────────────────────────────────────────────
-- 6. EVENT TRIGGERS
-- Webhook / event-based trigger definitions.
-- ─────────────────────────────────────────────

CREATE TABLE orchestration.event_triggers (
    id                  uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    trigger_name        varchar(100) NOT NULL UNIQUE,
    event_type          varchar(50)  NOT NULL,
    source_schema       varchar(50),
    source_table        varchar(100),
    flow_name           varchar(100) NOT NULL,
    pipeline_id         uuid         REFERENCES orchestration.pipeline_definitions(id),
    filter_config       jsonb        DEFAULT '{}',
    debounce_seconds    int          DEFAULT 0,
    is_active           boolean      DEFAULT true,
    created_at          timestamptz  DEFAULT now(),
    updated_at          timestamptz  DEFAULT now(),

    CONSTRAINT event_trigger_type_check CHECK (
        event_type IN ('supabase_insert','supabase_update','supabase_delete',
                       'webhook','api_call','file_upload','upstream_complete')
    )
);

COMMENT ON TABLE orchestration.event_triggers
    IS 'Event/webhook trigger definitions that map DB events to orchestration flows';


-- ─────────────────────────────────────────────
-- 7. RUN DQ SUMMARY
-- Data quality summary per pipeline run.
-- ─────────────────────────────────────────────

CREATE TABLE orchestration.run_dq_summary (
    id              uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    pipeline_run_id uuid        NOT NULL REFERENCES orchestration.pipeline_runs(id)
                                ON DELETE CASCADE,
    table_name      varchar(100) NOT NULL,
    total_records   int         DEFAULT 0,
    pass_count      int         DEFAULT 0,
    fail_count      int         DEFAULT 0,
    avg_quality_score numeric(5,2),
    issues_by_type  jsonb       DEFAULT '{}',
    created_at      timestamptz DEFAULT now()
);

CREATE INDEX idx_run_dq_summary_run
    ON orchestration.run_dq_summary (pipeline_run_id);

COMMENT ON TABLE orchestration.run_dq_summary
    IS 'Aggregated data quality metrics per pipeline run and table';


-- ─────────────────────────────────────────────
-- 8. ALERT LOG
-- Tracks all dispatched alerts (email, GitHub Issues).
-- ─────────────────────────────────────────────

CREATE TABLE orchestration.alert_log (
    id              uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    alert_type      varchar(50)  NOT NULL,  -- 'email', 'github_issue', or comma-separated
    severity        varchar(20)  NOT NULL DEFAULT 'warning',
    title           varchar(500) NOT NULL,
    message         text,
    pipeline_name   varchar(100),
    run_id          varchar(100),
    dispatch_result jsonb        DEFAULT '{}',
    created_at      timestamptz  DEFAULT now(),

    CONSTRAINT alert_log_severity_check CHECK (
        severity IN ('info', 'warning', 'critical')
    )
);

CREATE INDEX idx_alert_log_severity
    ON orchestration.alert_log (severity, created_at DESC);
CREATE INDEX idx_alert_log_pipeline
    ON orchestration.alert_log (pipeline_name, created_at DESC);

COMMENT ON TABLE orchestration.alert_log
    IS 'History of all dispatched alerts across email and GitHub Issues channels';


-- ─────────────────────────────────────────────
-- Seed Gold-to-Neo4j pipeline definition
-- ─────────────────────────────────────────────

INSERT INTO orchestration.pipeline_definitions (pipeline_name, layer_from, layer_to, description)
VALUES
    ('gold_to_neo4j', 'gold', 'neo4j',
     'Config-driven batch sync: Gold tables → Neo4j nodes/relationships via YAML config + agentic schema drift resolution')
ON CONFLICT (pipeline_name) DO NOTHING;


-- ─────────────────────────────────────────────
-- Seed Neo4j sync schedules
-- ─────────────────────────────────────────────

INSERT INTO orchestration.schedule_definitions
    (schedule_name, cron_expression, flow_name, run_config)
VALUES
    ('hourly_neo4j_sync', '0 * * * *', 'neo4j_batch_sync',
     '{"layer": "all"}'),
    ('daily_reconciliation', '30 3 * * *', 'neo4j_reconciliation',
     '{}')
ON CONFLICT (schedule_name) DO NOTHING;


-- ─────────────────────────────────────────────
-- 9. PIPELINE TOOL METRICS
-- Per-tool/node execution metrics from LangGraph pipelines.
-- ─────────────────────────────────────────────

CREATE TABLE orchestration.pipeline_tool_metrics (
    id              uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    pipeline_run_id uuid NOT NULL REFERENCES orchestration.pipeline_runs(id) ON DELETE CASCADE,
    tool_name       varchar(100) NOT NULL,
    duration_ms     int,
    records_in      int DEFAULT 0,
    records_out     int DEFAULT 0,
    status          varchar(20) DEFAULT 'completed',
    error_message   text,
    metadata        jsonb DEFAULT '{}',
    created_at      timestamptz DEFAULT now()
);

CREATE INDEX idx_tool_metrics_run ON orchestration.pipeline_tool_metrics (pipeline_run_id);

COMMENT ON TABLE orchestration.pipeline_tool_metrics
    IS 'Per-tool execution metrics captured from LangGraph pipeline runs';


-- ─────────────────────────────────────────────
-- 10. PIPELINE LLM USAGE
-- LLM token consumption and cost per pipeline run.
-- ─────────────────────────────────────────────

CREATE TABLE orchestration.pipeline_llm_usage (
    id                      uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    pipeline_run_id         uuid NOT NULL REFERENCES orchestration.pipeline_runs(id) ON DELETE CASCADE,
    model                   varchar(100),
    total_prompt_tokens     int DEFAULT 0,
    total_completion_tokens int DEFAULT 0,
    total_tokens            int DEFAULT 0,
    total_cost_usd          numeric(10,6) DEFAULT 0,
    llm_calls               int DEFAULT 0,
    call_details            jsonb DEFAULT '[]',
    created_at              timestamptz DEFAULT now()
);

CREATE INDEX idx_llm_usage_run ON orchestration.pipeline_llm_usage (pipeline_run_id);

COMMENT ON TABLE orchestration.pipeline_llm_usage
    IS 'LLM token usage and estimated cost per pipeline run';


-- ─────────────────────────────────────────────
-- 11. WEBHOOK DEAD-LETTER QUEUE
-- Failed webhook events stored for auto-retry.
-- Retry strategy: Conservative exponential backoff — 1min, 5min, 30min (max 3 retries)
-- ─────────────────────────────────────────────

CREATE TABLE orchestration.webhook_dead_letter (
    id              uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    payload         jsonb NOT NULL,
    error_message   text,
    error_details   jsonb,
    retry_count     int DEFAULT 0,
    max_retries     int DEFAULT 3,
    status          varchar(20) DEFAULT 'pending',
    next_retry_at   timestamptz,
    last_retry_at   timestamptz,
    resolved_at     timestamptz,
    created_at      timestamptz DEFAULT now(),

    CONSTRAINT dlq_status_check CHECK (
        status IN ('pending', 'retrying', 'resolved', 'exhausted', 'discarded')
    )
);

CREATE INDEX idx_dlq_status ON orchestration.webhook_dead_letter (status, next_retry_at);

COMMENT ON TABLE orchestration.webhook_dead_letter
    IS 'Retry strategy: Conservative exponential backoff — 1min, 5min, 30min (max 3 retries)';

