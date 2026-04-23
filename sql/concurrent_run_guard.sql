-- ══════════════════════════════════════════════════════
-- Migration: Atomic Concurrent Run Prevention
-- ══════════════════════════════════════════════════════
-- Purpose: Prevent TOCTOU race conditions in _guard_concurrent()
--          by enforcing single-running-flow at the DB level.
--
-- Run this ONCE in the Supabase SQL Editor against the orchestration schema.
-- Safe to run on a live system — additive change only.
-- ══════════════════════════════════════════════════════

-- Phase 1: Per-flow single-running constraint
-- Only one row per flow_name can have status='running' at any time.
-- Attempts to INSERT a second 'running' row for the same flow_name
-- will fail with a unique violation, which the Python code catches
-- and converts to ConcurrentRunError.
CREATE UNIQUE INDEX IF NOT EXISTS idx_orch_runs_single_running
    ON orchestration.orchestration_runs (flow_name)
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

-- ══════════════════════════════════════════════════════
-- Verification queries (run after migration)
-- ══════════════════════════════════════════════════════

-- Check the indexes were created:
-- SELECT indexname, indexdef
-- FROM pg_indexes
-- WHERE tablename = 'orchestration_runs'
--   AND schemaname = 'orchestration'
-- ORDER BY indexname;

-- Test: try inserting two running rows for the same flow (should fail):
-- INSERT INTO orchestration.orchestration_runs (flow_name, status, trigger_type)
-- VALUES ('test_flow', 'running', 'manual');
-- INSERT INTO orchestration.orchestration_runs (flow_name, status, trigger_type)
-- VALUES ('test_flow', 'running', 'manual');  -- should ERROR: duplicate key
