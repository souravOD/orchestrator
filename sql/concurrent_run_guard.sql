-- ══════════════════════════════════════════════════════
-- Migration: Atomic Concurrent Run Prevention
-- ══════════════════════════════════════════════════════
-- Purpose: Prevent TOCTOU race conditions in _guard_concurrent()
--          by enforcing single-running-flow at the DB level.
--
-- Run this ONCE in the Supabase SQL Editor against the orchestration schema.
-- Safe to run on a live system — additive change only.
-- ══════════════════════════════════════════════════════

-- Phase 1: Per-flow + per-source single-running constraint
-- Only one row per (flow_name, source_name) can have status='running'.
-- COALESCE ensures single-instance flows (source_name IS NULL) are still
-- protected, while per-source flows (e.g. full_ingestion) can run in
-- parallel for different sources.
-- Clean up any stale 'running' rows left by crashes before creating
-- the unique index (otherwise CREATE UNIQUE INDEX will fail if duplicates exist).
UPDATE orchestration.orchestration_runs
SET status = 'failed', error_message = 'Marked stale by concurrent_run_guard migration'
WHERE status = 'running'
  AND started_at < now() - INTERVAL '6 hours';

DROP INDEX IF EXISTS orchestration.idx_orch_runs_single_running;

CREATE UNIQUE INDEX idx_orch_runs_single_running
    ON orchestration.orchestration_runs (flow_name, COALESCE(source_name, ''))
    WHERE status = 'running';

-- Phase 2: Cross-flow mutual exclusion (GraphSAGE retrain ↔ inference)
-- Add a flow_group column so retrain + inference can share a lock group.
-- Only one running job per flow_group at a time.
ALTER TABLE orchestration.orchestration_runs
    ADD COLUMN IF NOT EXISTS flow_group VARCHAR(50);

COMMENT ON COLUMN orchestration.orchestration_runs.flow_group
    IS 'Optional grouping for mutual exclusion across related flows (e.g. graphsage).';

-- Backfill existing rows
UPDATE orchestration.orchestration_runs
SET flow_group = 'graphsage'
WHERE flow_name IN ('neo4j_graphsage_retrain', 'neo4j_graphsage_inference')
  AND flow_group IS NULL;

-- Atomic cross-flow constraint: only one running job per flow_group
CREATE UNIQUE INDEX IF NOT EXISTS idx_orch_runs_single_running_group
    ON orchestration.orchestration_runs (flow_group)
    WHERE status = 'running' AND flow_group IS NOT NULL;
