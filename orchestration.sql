--
-- PostgreSQL database dump
--

-- Dumped from database version 17.6
-- Dumped by pg_dump version 17.5

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: orchestration; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA orchestration;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alert_log; Type: TABLE; Schema: orchestration; Owner: -
--

CREATE TABLE orchestration.alert_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    alert_type character varying(50) NOT NULL,
    severity character varying(20) DEFAULT 'warning'::character varying NOT NULL,
    title character varying(500) NOT NULL,
    message text,
    pipeline_name character varying(100),
    run_id character varying(100),
    dispatch_result jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT alert_log_severity_check CHECK (((severity)::text = ANY ((ARRAY['info'::character varying, 'warning'::character varying, 'critical'::character varying])::text[])))
);


--
-- Name: TABLE alert_log; Type: COMMENT; Schema: orchestration; Owner: -
--

COMMENT ON TABLE orchestration.alert_log IS 'History of all dispatched alerts across email and GitHub Issues channels';


--
-- Name: event_triggers; Type: TABLE; Schema: orchestration; Owner: -
--

CREATE TABLE orchestration.event_triggers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    trigger_name character varying(100) NOT NULL,
    event_type character varying(50) NOT NULL,
    source_schema character varying(50),
    source_table character varying(100),
    flow_name character varying(100) NOT NULL,
    pipeline_id uuid,
    filter_config jsonb DEFAULT '{}'::jsonb,
    debounce_seconds integer DEFAULT 0,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT event_trigger_type_check CHECK (((event_type)::text = ANY ((ARRAY['supabase_insert'::character varying, 'supabase_update'::character varying, 'supabase_delete'::character varying, 'webhook'::character varying, 'api_call'::character varying, 'file_upload'::character varying, 'upstream_complete'::character varying])::text[])))
);


--
-- Name: TABLE event_triggers; Type: COMMENT; Schema: orchestration; Owner: -
--

COMMENT ON TABLE orchestration.event_triggers IS 'Event/webhook trigger definitions that map DB events to orchestration flows';


--
-- Name: orchestration_runs; Type: TABLE; Schema: orchestration; Owner: -
--

CREATE TABLE orchestration.orchestration_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    flow_name character varying(100) NOT NULL,
    flow_type character varying(30) DEFAULT 'batch'::character varying NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    trigger_type character varying(30) NOT NULL,
    triggered_by character varying(255),
    layers text[] DEFAULT ARRAY['prebronze_to_bronze'::text, 'bronze_to_silver'::text, 'silver_to_gold'::text],
    current_layer character varying(50),
    total_records_processed integer DEFAULT 0,
    total_records_written integer DEFAULT 0,
    total_dq_issues integer DEFAULT 0,
    total_errors integer DEFAULT 0,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    duration_seconds numeric(10,2),
    config jsonb DEFAULT '{}'::jsonb,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    vendor_id uuid,
    source_name character varying(100),
    progress_pct integer DEFAULT 0,
    totals jsonb DEFAULT '{}'::jsonb,
    error_message text,
    CONSTRAINT orch_runs_flow_type_check CHECK (((flow_type)::text = ANY ((ARRAY['batch'::character varying, 'realtime'::character varying, 'hybrid'::character varying])::text[]))),
    CONSTRAINT orch_runs_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'queued'::character varying, 'running'::character varying, 'completed'::character varying, 'failed'::character varying, 'partially_completed'::character varying, 'cancelled'::character varying, 'timed_out'::character varying])::text[]))),
    CONSTRAINT orch_runs_trigger_check CHECK (((trigger_type)::text = ANY ((ARRAY['manual'::character varying, 'scheduled'::character varying, 'webhook'::character varying, 'event'::character varying, 'upstream_complete'::character varying, 'api'::character varying, 'retry'::character varying])::text[])))
);


--
-- Name: TABLE orchestration_runs; Type: COMMENT; Schema: orchestration; Owner: -
--

COMMENT ON TABLE orchestration.orchestration_runs IS 'Top-level orchestration flow execution. Groups one or more pipeline_runs.';


--
-- Name: pipeline_definitions; Type: TABLE; Schema: orchestration; Owner: -
--

CREATE TABLE orchestration.pipeline_definitions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pipeline_name character varying(100) NOT NULL,
    layer_from character varying(20) NOT NULL,
    layer_to character varying(20) NOT NULL,
    description text,
    repo_url character varying(500),
    default_config jsonb DEFAULT '{}'::jsonb,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    timeout_seconds integer,
    CONSTRAINT pipeline_def_layer_from_check CHECK (((layer_from)::text = ANY ((ARRAY['prebronze'::character varying, 'bronze'::character varying, 'silver'::character varying, 'gold'::character varying])::text[]))),
    CONSTRAINT pipeline_def_layer_to_check CHECK (((layer_to)::text = ANY ((ARRAY['bronze'::character varying, 'silver'::character varying, 'gold'::character varying, 'neo4j'::character varying])::text[])))
);


--
-- Name: TABLE pipeline_definitions; Type: COMMENT; Schema: orchestration; Owner: -
--

COMMENT ON TABLE orchestration.pipeline_definitions IS 'Registry of all known data pipelines in the medallion architecture';


--
-- Name: COLUMN pipeline_definitions.timeout_seconds; Type: COMMENT; Schema: orchestration; Owner: -
--

COMMENT ON COLUMN orchestration.pipeline_definitions.timeout_seconds IS 'Optional per-pipeline timeout in seconds. NULL means no timeout.';


--
-- Name: pipeline_llm_usage; Type: TABLE; Schema: orchestration; Owner: -
--

CREATE TABLE orchestration.pipeline_llm_usage (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pipeline_run_id uuid NOT NULL,
    model character varying(100),
    total_prompt_tokens integer DEFAULT 0,
    total_completion_tokens integer DEFAULT 0,
    total_tokens integer DEFAULT 0,
    total_cost_usd numeric(10,6) DEFAULT 0,
    llm_calls integer DEFAULT 0,
    call_details jsonb DEFAULT '[]'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: TABLE pipeline_llm_usage; Type: COMMENT; Schema: orchestration; Owner: -
--

COMMENT ON TABLE orchestration.pipeline_llm_usage IS 'LLM token usage and estimated cost per pipeline run';


--
-- Name: pipeline_runs; Type: TABLE; Schema: orchestration; Owner: -
--

CREATE TABLE orchestration.pipeline_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pipeline_id uuid NOT NULL,
    orchestration_run_id uuid NOT NULL,
    run_number integer NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    trigger_type character varying(30) DEFAULT 'manual'::character varying NOT NULL,
    triggered_by character varying(255),
    source_table character varying(100),
    target_table character varying(100),
    batch_size integer DEFAULT 100,
    incremental boolean DEFAULT true,
    dry_run boolean DEFAULT false,
    run_config jsonb DEFAULT '{}'::jsonb,
    records_input integer DEFAULT 0,
    records_processed integer DEFAULT 0,
    records_written integer DEFAULT 0,
    records_skipped integer DEFAULT 0,
    records_failed integer DEFAULT 0,
    dq_issues_found integer DEFAULT 0,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    duration_seconds numeric(10,2),
    error_message text,
    error_details jsonb,
    retry_count integer DEFAULT 0,
    max_retries integer DEFAULT 3,
    created_at timestamp with time zone DEFAULT now(),
    pipeline_name character varying(100),
    CONSTRAINT pipeline_runs_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'queued'::character varying, 'running'::character varying, 'completed'::character varying, 'failed'::character varying, 'retrying'::character varying, 'cancelled'::character varying, 'skipped'::character varying, 'timed_out'::character varying])::text[]))),
    CONSTRAINT pipeline_runs_trigger_check CHECK (((trigger_type)::text = ANY ((ARRAY['manual'::character varying, 'scheduled'::character varying, 'webhook'::character varying, 'event'::character varying, 'upstream_complete'::character varying, 'api'::character varying, 'retry'::character varying])::text[])))
);


--
-- Name: TABLE pipeline_runs; Type: COMMENT; Schema: orchestration; Owner: -
--

COMMENT ON TABLE orchestration.pipeline_runs IS 'Individual pipeline execution record with metrics, timing, and error info';


--
-- Name: pipeline_runs_run_number_seq; Type: SEQUENCE; Schema: orchestration; Owner: -
--

CREATE SEQUENCE orchestration.pipeline_runs_run_number_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: pipeline_runs_run_number_seq; Type: SEQUENCE OWNED BY; Schema: orchestration; Owner: -
--

ALTER SEQUENCE orchestration.pipeline_runs_run_number_seq OWNED BY orchestration.pipeline_runs.run_number;


--
-- Name: pipeline_step_logs; Type: TABLE; Schema: orchestration; Owner: -
--

CREATE TABLE orchestration.pipeline_step_logs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pipeline_run_id uuid NOT NULL,
    step_name character varying(100) NOT NULL,
    step_order integer NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    records_in integer DEFAULT 0,
    records_out integer DEFAULT 0,
    records_error integer DEFAULT 0,
    state_delta jsonb DEFAULT '{}'::jsonb,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    duration_ms integer,
    error_message text,
    error_traceback text,
    CONSTRAINT step_logs_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'running'::character varying, 'completed'::character varying, 'failed'::character varying, 'skipped'::character varying])::text[])))
);


--
-- Name: TABLE pipeline_step_logs; Type: COMMENT; Schema: orchestration; Owner: -
--

COMMENT ON TABLE orchestration.pipeline_step_logs IS 'Per-tool execution log within a pipeline run (e.g. map_tables, flatten_payloads)';


--
-- Name: pipeline_tool_metrics; Type: TABLE; Schema: orchestration; Owner: -
--

CREATE TABLE orchestration.pipeline_tool_metrics (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pipeline_run_id uuid NOT NULL,
    tool_name character varying(100) NOT NULL,
    duration_ms integer,
    records_in integer DEFAULT 0,
    records_out integer DEFAULT 0,
    status character varying(20) DEFAULT 'completed'::character varying,
    error_message text,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: TABLE pipeline_tool_metrics; Type: COMMENT; Schema: orchestration; Owner: -
--

COMMENT ON TABLE orchestration.pipeline_tool_metrics IS 'Per-tool execution metrics captured from LangGraph pipeline runs';


--
-- Name: run_dq_summary; Type: TABLE; Schema: orchestration; Owner: -
--

CREATE TABLE orchestration.run_dq_summary (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pipeline_run_id uuid NOT NULL,
    table_name character varying(100) NOT NULL,
    total_records integer DEFAULT 0,
    pass_count integer DEFAULT 0,
    fail_count integer DEFAULT 0,
    avg_quality_score numeric(5,2),
    issues_by_type jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: TABLE run_dq_summary; Type: COMMENT; Schema: orchestration; Owner: -
--

COMMENT ON TABLE orchestration.run_dq_summary IS 'Aggregated data quality metrics per pipeline run and table';


--
-- Name: schedule_definitions; Type: TABLE; Schema: orchestration; Owner: -
--

CREATE TABLE orchestration.schedule_definitions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    schedule_name character varying(100) NOT NULL,
    cron_expression character varying(100) NOT NULL,
    flow_name character varying(100) NOT NULL,
    pipeline_id uuid,
    run_config jsonb DEFAULT '{}'::jsonb,
    is_active boolean DEFAULT true,
    last_run_at timestamp with time zone,
    next_run_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: TABLE schedule_definitions; Type: COMMENT; Schema: orchestration; Owner: -
--

COMMENT ON TABLE orchestration.schedule_definitions IS 'Cron-based schedule definitions for automated pipeline triggers';


--
-- Name: webhook_dead_letter; Type: TABLE; Schema: orchestration; Owner: -
--

CREATE TABLE orchestration.webhook_dead_letter (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    payload jsonb NOT NULL,
    error_message text,
    error_details jsonb,
    retry_count integer DEFAULT 0,
    max_retries integer DEFAULT 3,
    status character varying(20) DEFAULT 'pending'::character varying,
    next_retry_at timestamp with time zone,
    last_retry_at timestamp with time zone,
    resolved_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT dlq_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'retrying'::character varying, 'resolved'::character varying, 'exhausted'::character varying, 'discarded'::character varying])::text[])))
);


--
-- Name: TABLE webhook_dead_letter; Type: COMMENT; Schema: orchestration; Owner: -
--

COMMENT ON TABLE orchestration.webhook_dead_letter IS 'Retry strategy: Conservative exponential backoff — 1min, 5min, 30min (max 3 retries)';


--
-- Name: pipeline_runs run_number; Type: DEFAULT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.pipeline_runs ALTER COLUMN run_number SET DEFAULT nextval('orchestration.pipeline_runs_run_number_seq'::regclass);


--
-- Name: alert_log alert_log_pkey; Type: CONSTRAINT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.alert_log
    ADD CONSTRAINT alert_log_pkey PRIMARY KEY (id);


--
-- Name: event_triggers event_triggers_pkey; Type: CONSTRAINT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.event_triggers
    ADD CONSTRAINT event_triggers_pkey PRIMARY KEY (id);


--
-- Name: event_triggers event_triggers_trigger_name_key; Type: CONSTRAINT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.event_triggers
    ADD CONSTRAINT event_triggers_trigger_name_key UNIQUE (trigger_name);


--
-- Name: orchestration_runs orchestration_runs_pkey; Type: CONSTRAINT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.orchestration_runs
    ADD CONSTRAINT orchestration_runs_pkey PRIMARY KEY (id);


--
-- Name: pipeline_definitions pipeline_definitions_pipeline_name_key; Type: CONSTRAINT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.pipeline_definitions
    ADD CONSTRAINT pipeline_definitions_pipeline_name_key UNIQUE (pipeline_name);


--
-- Name: pipeline_definitions pipeline_definitions_pkey; Type: CONSTRAINT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.pipeline_definitions
    ADD CONSTRAINT pipeline_definitions_pkey PRIMARY KEY (id);


--
-- Name: pipeline_llm_usage pipeline_llm_usage_pkey; Type: CONSTRAINT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.pipeline_llm_usage
    ADD CONSTRAINT pipeline_llm_usage_pkey PRIMARY KEY (id);


--
-- Name: pipeline_runs pipeline_runs_pkey; Type: CONSTRAINT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.pipeline_runs
    ADD CONSTRAINT pipeline_runs_pkey PRIMARY KEY (id);


--
-- Name: pipeline_step_logs pipeline_step_logs_pkey; Type: CONSTRAINT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.pipeline_step_logs
    ADD CONSTRAINT pipeline_step_logs_pkey PRIMARY KEY (id);


--
-- Name: pipeline_tool_metrics pipeline_tool_metrics_pkey; Type: CONSTRAINT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.pipeline_tool_metrics
    ADD CONSTRAINT pipeline_tool_metrics_pkey PRIMARY KEY (id);


--
-- Name: run_dq_summary run_dq_summary_pkey; Type: CONSTRAINT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.run_dq_summary
    ADD CONSTRAINT run_dq_summary_pkey PRIMARY KEY (id);


--
-- Name: schedule_definitions schedule_definitions_pkey; Type: CONSTRAINT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.schedule_definitions
    ADD CONSTRAINT schedule_definitions_pkey PRIMARY KEY (id);


--
-- Name: schedule_definitions schedule_definitions_schedule_name_key; Type: CONSTRAINT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.schedule_definitions
    ADD CONSTRAINT schedule_definitions_schedule_name_key UNIQUE (schedule_name);


--
-- Name: webhook_dead_letter webhook_dead_letter_pkey; Type: CONSTRAINT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.webhook_dead_letter
    ADD CONSTRAINT webhook_dead_letter_pkey PRIMARY KEY (id);


--
-- Name: idx_alert_log_pipeline; Type: INDEX; Schema: orchestration; Owner: -
--

CREATE INDEX idx_alert_log_pipeline ON orchestration.alert_log USING btree (pipeline_name, created_at DESC);


--
-- Name: idx_alert_log_severity; Type: INDEX; Schema: orchestration; Owner: -
--

CREATE INDEX idx_alert_log_severity ON orchestration.alert_log USING btree (severity, created_at DESC);


--
-- Name: idx_dlq_status; Type: INDEX; Schema: orchestration; Owner: -
--

CREATE INDEX idx_dlq_status ON orchestration.webhook_dead_letter USING btree (status, next_retry_at);


--
-- Name: idx_llm_usage_run; Type: INDEX; Schema: orchestration; Owner: -
--

CREATE INDEX idx_llm_usage_run ON orchestration.pipeline_llm_usage USING btree (pipeline_run_id);


--
-- Name: idx_orch_runs_created_at; Type: INDEX; Schema: orchestration; Owner: -
--

CREATE INDEX idx_orch_runs_created_at ON orchestration.orchestration_runs USING btree (created_at DESC);


--
-- Name: idx_orch_runs_flow; Type: INDEX; Schema: orchestration; Owner: -
--

CREATE INDEX idx_orch_runs_flow ON orchestration.orchestration_runs USING btree (flow_name, created_at DESC);


--
-- Name: idx_orch_runs_status; Type: INDEX; Schema: orchestration; Owner: -
--

CREATE INDEX idx_orch_runs_status ON orchestration.orchestration_runs USING btree (status, created_at DESC);


--
-- Name: idx_orch_runs_vendor_id; Type: INDEX; Schema: orchestration; Owner: -
--

CREATE INDEX idx_orch_runs_vendor_id ON orchestration.orchestration_runs USING btree (vendor_id) WHERE (vendor_id IS NOT NULL);


--
-- Name: idx_orch_runs_vendor_status; Type: INDEX; Schema: orchestration; Owner: -
--

CREATE INDEX idx_orch_runs_vendor_status ON orchestration.orchestration_runs USING btree (vendor_id, status) WHERE (vendor_id IS NOT NULL);


--
-- Name: idx_pipeline_runs_orch_run; Type: INDEX; Schema: orchestration; Owner: -
--

CREATE INDEX idx_pipeline_runs_orch_run ON orchestration.pipeline_runs USING btree (orchestration_run_id);


--
-- Name: idx_pipeline_runs_pipeline; Type: INDEX; Schema: orchestration; Owner: -
--

CREATE INDEX idx_pipeline_runs_pipeline ON orchestration.pipeline_runs USING btree (pipeline_id, created_at DESC);


--
-- Name: idx_pipeline_runs_status; Type: INDEX; Schema: orchestration; Owner: -
--

CREATE INDEX idx_pipeline_runs_status ON orchestration.pipeline_runs USING btree (status);


--
-- Name: idx_run_dq_summary_run; Type: INDEX; Schema: orchestration; Owner: -
--

CREATE INDEX idx_run_dq_summary_run ON orchestration.run_dq_summary USING btree (pipeline_run_id);


--
-- Name: idx_step_logs_run; Type: INDEX; Schema: orchestration; Owner: -
--

CREATE INDEX idx_step_logs_run ON orchestration.pipeline_step_logs USING btree (pipeline_run_id, step_order);


--
-- Name: idx_tool_metrics_run; Type: INDEX; Schema: orchestration; Owner: -
--

CREATE INDEX idx_tool_metrics_run ON orchestration.pipeline_tool_metrics USING btree (pipeline_run_id);


--
-- Name: event_triggers event_triggers_pipeline_id_fkey; Type: FK CONSTRAINT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.event_triggers
    ADD CONSTRAINT event_triggers_pipeline_id_fkey FOREIGN KEY (pipeline_id) REFERENCES orchestration.pipeline_definitions(id);


--
-- Name: pipeline_llm_usage pipeline_llm_usage_pipeline_run_id_fkey; Type: FK CONSTRAINT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.pipeline_llm_usage
    ADD CONSTRAINT pipeline_llm_usage_pipeline_run_id_fkey FOREIGN KEY (pipeline_run_id) REFERENCES orchestration.pipeline_runs(id) ON DELETE CASCADE;


--
-- Name: pipeline_runs pipeline_runs_orchestration_run_id_fkey; Type: FK CONSTRAINT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.pipeline_runs
    ADD CONSTRAINT pipeline_runs_orchestration_run_id_fkey FOREIGN KEY (orchestration_run_id) REFERENCES orchestration.orchestration_runs(id) ON DELETE CASCADE;


--
-- Name: pipeline_runs pipeline_runs_pipeline_id_fkey; Type: FK CONSTRAINT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.pipeline_runs
    ADD CONSTRAINT pipeline_runs_pipeline_id_fkey FOREIGN KEY (pipeline_id) REFERENCES orchestration.pipeline_definitions(id);


--
-- Name: pipeline_step_logs pipeline_step_logs_pipeline_run_id_fkey; Type: FK CONSTRAINT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.pipeline_step_logs
    ADD CONSTRAINT pipeline_step_logs_pipeline_run_id_fkey FOREIGN KEY (pipeline_run_id) REFERENCES orchestration.pipeline_runs(id) ON DELETE CASCADE;


--
-- Name: pipeline_tool_metrics pipeline_tool_metrics_pipeline_run_id_fkey; Type: FK CONSTRAINT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.pipeline_tool_metrics
    ADD CONSTRAINT pipeline_tool_metrics_pipeline_run_id_fkey FOREIGN KEY (pipeline_run_id) REFERENCES orchestration.pipeline_runs(id) ON DELETE CASCADE;


--
-- Name: run_dq_summary run_dq_summary_pipeline_run_id_fkey; Type: FK CONSTRAINT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.run_dq_summary
    ADD CONSTRAINT run_dq_summary_pipeline_run_id_fkey FOREIGN KEY (pipeline_run_id) REFERENCES orchestration.pipeline_runs(id) ON DELETE CASCADE;


--
-- Name: schedule_definitions schedule_definitions_pipeline_id_fkey; Type: FK CONSTRAINT; Schema: orchestration; Owner: -
--

ALTER TABLE ONLY orchestration.schedule_definitions
    ADD CONSTRAINT schedule_definitions_pipeline_id_fkey FOREIGN KEY (pipeline_id) REFERENCES orchestration.pipeline_definitions(id);


--
-- PostgreSQL database dump complete
--

