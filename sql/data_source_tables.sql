-- ============================================================
-- Data Source Registry + Cursor Tracking Tables
-- ============================================================
-- Schema: orchestration
-- Run in: Supabase SQL Editor
-- Dependencies: orchestration schema must already exist
-- ============================================================

-- NOTE: Do NOT wrap in BEGIN/COMMIT — Supabase SQL Editor auto-commits.

-- ──────────────────────────────────────────────────────
-- Table 1: orchestration.data_sources
-- Purpose: Registry of all data files in Supabase Storage
-- ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS orchestration.data_sources (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,

    -- Identity
    source_name     TEXT NOT NULL,                              -- Human-readable name (e.g. 'food_com_recipes')
    source_type     TEXT NOT NULL DEFAULT 'file',               -- 'file' | 'folder' | 'api'
    category        TEXT NOT NULL DEFAULT 'recipes',            -- 'recipes' | 'products' | 'ingredients' | 'customers'

    -- Storage Location
    storage_bucket  TEXT NOT NULL,                              -- Supabase Storage bucket (e.g. 'raw-data')
    storage_path    TEXT NOT NULL,                              -- Path within bucket (e.g. 'recipes/food-com/RAW_recipes.csv')

    -- File Metadata
    file_format     TEXT NOT NULL DEFAULT 'csv',                -- 'csv' | 'json' | 'ndjson'
    file_size_bytes BIGINT,                                    -- Size in bytes
    total_records   INTEGER,                                   -- Total records in the file
    file_hash       TEXT,                                      -- SHA256 of file content (for change detection)

    -- Pipeline Mapping
    target_table    TEXT,                                       -- Bronze target table (e.g. 'raw_recipes')
    vendor_id       UUID,                                      -- Optional B2B vendor ID
    default_config  JSONB DEFAULT '{}'::JSONB,                 -- Default pipeline config (batch_size, model, etc.)

    -- Metadata
    description     TEXT,
    tags            TEXT[],                                    -- Labels (e.g. '{kaggle, v2, sample}')
    parent_source_id UUID REFERENCES orchestration.data_sources(id), -- If this is a sample/subset of another source

    -- Status & Lifecycle
    status          TEXT NOT NULL DEFAULT 'available',          -- available | ingesting | completed | failed | archived
    is_active       BOOLEAN DEFAULT TRUE,

    -- Ingestion Stats (Aggregated)
    total_ingested  INTEGER DEFAULT 0,                         -- Running total of rows successfully ingested
    last_ingested_at TIMESTAMPTZ,

    -- Timestamps
    created_at      TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at      TIMESTAMPTZ DEFAULT NOW() NOT NULL,

    -- Constraints
    CONSTRAINT chk_ds_format CHECK (file_format IN ('csv', 'json', 'ndjson')),
    CONSTRAINT chk_ds_category CHECK (category IN ('recipes', 'products', 'ingredients', 'customers')),
    CONSTRAINT chk_ds_status CHECK (status IN ('available', 'ingesting', 'completed', 'failed', 'archived')),
    CONSTRAINT chk_ds_type CHECK (source_type IN ('file', 'folder', 'api')),
    CONSTRAINT uq_data_source_path UNIQUE (storage_bucket, storage_path)
);

COMMENT ON TABLE orchestration.data_sources IS
    'Registry of all data source files available in Supabase Storage for pipeline ingestion.';
COMMENT ON COLUMN orchestration.data_sources.parent_source_id IS
    'If this source is a sample/subset extracted from a larger file, points to the parent source.';
COMMENT ON COLUMN orchestration.data_sources.total_ingested IS
    'Running total of rows successfully ingested across all cursor batches.';

-- Indexes
CREATE INDEX IF NOT EXISTS idx_ds_status ON orchestration.data_sources (status, is_active);
CREATE INDEX IF NOT EXISTS idx_ds_source_name ON orchestration.data_sources (source_name);
CREATE INDEX IF NOT EXISTS idx_ds_category ON orchestration.data_sources (category);
CREATE INDEX IF NOT EXISTS idx_ds_target_table ON orchestration.data_sources (target_table);


-- ──────────────────────────────────────────────────────
-- Table 2: orchestration.data_source_cursors
-- Purpose: Row-level ingestion progress tracking per batch
-- ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS orchestration.data_source_cursors (
    id                      UUID DEFAULT gen_random_uuid() PRIMARY KEY,

    -- References
    data_source_id          UUID NOT NULL REFERENCES orchestration.data_sources(id) ON DELETE CASCADE,
    orchestration_run_id    UUID REFERENCES orchestration.orchestration_runs(id) ON DELETE SET NULL,

    -- Cursor Position
    cursor_type             TEXT NOT NULL DEFAULT 'row_offset', -- 'row_offset' | 'byte_offset' | 'key_based'
    cursor_start            INTEGER NOT NULL DEFAULT 0,        -- Start position for this batch (inclusive)
    cursor_end              INTEGER NOT NULL DEFAULT 0,        -- End position after this batch (exclusive)
    batch_size              INTEGER NOT NULL DEFAULT 100,      -- Batch size used for this chunk

    -- Status
    status                  TEXT NOT NULL DEFAULT 'pending',   -- pending | in_progress | completed | failed
    records_processed       INTEGER DEFAULT 0,
    records_written         INTEGER DEFAULT 0,
    records_skipped         INTEGER DEFAULT 0,                 -- Deduped by data_hash
    records_failed          INTEGER DEFAULT 0,
    error_message           TEXT,
    error_details           JSONB,

    -- Timestamps
    started_at              TIMESTAMPTZ DEFAULT NOW(),
    completed_at            TIMESTAMPTZ,
    created_at              TIMESTAMPTZ DEFAULT NOW() NOT NULL,

    -- Constraints
    CONSTRAINT chk_cursor_status CHECK (status IN ('pending', 'in_progress', 'completed', 'failed')),
    CONSTRAINT chk_cursor_range CHECK (cursor_end >= cursor_start)
);

COMMENT ON TABLE orchestration.data_source_cursors IS
    'Tracks per-batch ingestion progress for each data source. Each row = one batch attempt.';
COMMENT ON COLUMN orchestration.data_source_cursors.cursor_start IS
    'Start row index (0-based, inclusive) for this batch.';
COMMENT ON COLUMN orchestration.data_source_cursors.cursor_end IS
    'End row index (0-based, exclusive) after this batch completed. Next batch starts here.';

-- Indexes for fast resume lookups
CREATE INDEX IF NOT EXISTS idx_cursor_source ON orchestration.data_source_cursors
    (data_source_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cursor_status ON orchestration.data_source_cursors
    (data_source_id, status);
CREATE INDEX IF NOT EXISTS idx_cursor_run ON orchestration.data_source_cursors
    (orchestration_run_id);

-- Done.
