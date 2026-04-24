-- ============================================================
-- Seed Data: Register All Data Sources + Bootstrap Cursors
-- ============================================================
-- Run AFTER: data_source_tables.sql
-- Run in: Supabase SQL Editor
-- ============================================================

-- NOTE: Do NOT wrap in BEGIN/COMMIT -- Supabase SQL Editor auto-commits.

-- ══════════════════════════════════════════════════════
-- 1. Register All 13 Data Sources
-- ══════════════════════════════════════════════════════

-- ── RECIPES: Partially Ingested ──────────────────────

INSERT INTO orchestration.data_sources
    (source_name, category, storage_bucket, storage_path, file_format,
     file_size_bytes, total_records, target_table, description, tags,
     status, total_ingested)
VALUES
    ('food_com_recipes', 'recipes', 'raw-data', 'recipes/food-com/RAW_recipes.csv',
     'csv', 295259485, 267763, 'raw_recipes',
     'Kaggle Food.com full recipe dataset (267K recipes)',
     '{kaggle, food-com, full}', 'ingesting', 1200),

    ('allrecipes_full', 'recipes', 'raw-data', 'recipes/allrecipes/recipes_raw_nosource_ar.json',
     'json', 49784325, 39802, 'raw_recipes',
     'AllRecipes full dataset (39.8K recipes, dict-of-dicts JSON). 1K sample was extracted from this file.',
     '{allrecipes, full}', 'ingesting', 1000),

    ('indb_normalized', 'recipes', 'raw-data', 'recipes/indb/indb_normalized.csv',
     'csv', 2238925, 1014, 'raw_recipes',
     'Indian Nutrient Database — normalized recipes with inline nutrition columns',
     '{indb, indian, nutrition-inline}', 'ingesting', 1000),

    ('usda_fruits', 'recipes', 'raw-data', 'recipes/usda-fruits/usda_fruits_as_recipes.csv',
     'csv', 36032, 136, 'raw_recipes',
     'USDA fruits formatted as recipe records',
     '{usda, fruits, synthetic}', 'ingesting', 100)
ON CONFLICT (storage_bucket, storage_path) DO NOTHING;

-- ── PRODUCTS: Partially Ingested ─────────────────────

INSERT INTO orchestration.data_sources
    (source_name, category, storage_bucket, storage_path, file_format,
     file_size_bytes, total_records, target_table, description, tags,
     status, total_ingested)
VALUES
    ('off_products_batch_1', 'products', 'raw-data', 'products/off-batch-1/off_batch_1_of_2.ndjson',
     'ndjson', 331088876, 12118, 'raw_products',
     'OpenFoodFacts Batch 1 — full product data (12.1K). 3K sample was a subset of this file.',
     '{off, batch-1, full, products}', 'ingesting', 3500)
ON CONFLICT (storage_bucket, storage_path) DO NOTHING;

-- ── RECIPES: Not Yet Ingested ────────────────────────

INSERT INTO orchestration.data_sources
    (source_name, category, storage_bucket, storage_path, file_format,
     file_size_bytes, total_records, target_table, description, tags,
     status, total_ingested)
VALUES
    ('epicurious_full', 'recipes', 'raw-data', 'recipes/epicurious/recipes_raw_nosource_epi.json',
     'json', 61133971, 25323, 'raw_recipes',
     'Epicurious full dataset (25.3K recipes, dict-of-dicts JSON)',
     '{epicurious, full}', 'available', 0),

    ('food_network_full', 'recipes', 'raw-data', 'recipes/food-network/recipes_raw_nosource_fn.json',
     'json', 93702755, 60039, 'raw_recipes',
     'Food Network full dataset (60K recipes, dict-of-dicts JSON)',
     '{food-network, full}', 'available', 0),

    ('food_com_sample_300', 'recipes', 'raw-data', 'recipes/food-com-sample/300recipes.csv',
     'csv', 432129, 342, 'raw_recipes',
     'Food.com 300 recipe sample (same schema as full Food.com CSV) — fully ingested',
     '{food-com, sample}', 'completed', 342),

    ('indian_recipes_200', 'recipes', 'raw-data', 'recipes/indian-200/indian_recipes_201.csv',
     'csv', 253985, 200, 'raw_recipes',
     '200 Indian recipes (CSV, same schema as Food.com)',
     '{indian, sample}', 'available', 0),

    ('indian_recipes_500', 'recipes', 'raw-data', 'recipes/indian-500/indian_recipes_500.csv',
     'csv', 704830, 570, 'raw_recipes',
     '500 Indian recipes (CSV, same schema as Food.com)',
     '{indian, medium}', 'available', 0)
ON CONFLICT (storage_bucket, storage_path) DO NOTHING;

-- ── PRODUCTS: Not Yet Ingested ───────────────────────

INSERT INTO orchestration.data_sources
    (source_name, category, storage_bucket, storage_path, file_format,
     file_size_bytes, total_records, target_table, description, tags,
     status, total_ingested)
VALUES
    ('off_products_batch_2', 'products', 'raw-data', 'products/off-batch-2/off_batch_2_of_2.ndjson',
     'ndjson', 368991144, 12118, 'raw_products',
     'OpenFoodFacts Batch 2 — full product data (12.1K products, 352 MB)',
     '{off, batch-2, full, products}', 'available', 0)
ON CONFLICT (storage_bucket, storage_path) DO NOTHING;

-- ── ARCHIVED: Sample Subsets (kept for audit trail) ──

INSERT INTO orchestration.data_sources
    (source_name, category, storage_bucket, storage_path, file_format,
     file_size_bytes, total_records, target_table, description, tags,
     status, is_active, total_ingested)
VALUES
    ('allrecipes_sample_1k', 'recipes', 'raw-data', 'recipes/allrecipes-sample/1000_recipes_raw_ar.json',
     'json', 1235643, 1000, 'raw_recipes',
     'ARCHIVED — 1K sample extracted from allrecipes_full. Data now tracked under parent source.',
     '{allrecipes, sample, archived}', 'archived', FALSE, 1000),

    ('off_products_sample_3k', 'products', 'raw-data', 'products/off-sample/off_batch_1_of_2_skip500_take3000.ndjson',
     'ndjson', 80049920, 2999, 'raw_products',
     'ARCHIVED — 3K sample subset of off_products_batch_1. Data now tracked under parent source.',
     '{off, sample, archived}', 'archived', FALSE, 2999)
ON CONFLICT (storage_bucket, storage_path) DO NOTHING;

-- ── Set parent_source_id for archived samples ────────

UPDATE orchestration.data_sources
SET parent_source_id = (SELECT id FROM orchestration.data_sources WHERE source_name = 'allrecipes_full')
WHERE source_name = 'allrecipes_sample_1k';

UPDATE orchestration.data_sources
SET parent_source_id = (SELECT id FROM orchestration.data_sources WHERE source_name = 'off_products_batch_1')
WHERE source_name = 'off_products_sample_3k';


-- ══════════════════════════════════════════════════════
-- 2. Seed Cursor Records for Already-Ingested Sources
-- ══════════════════════════════════════════════════════

-- food_com_recipes: 1,200 of 267,763 ingested
INSERT INTO orchestration.data_source_cursors
    (data_source_id, cursor_type, cursor_start, cursor_end, batch_size,
     status, records_processed, records_written, completed_at)
SELECT id, 'row_offset', 0, 1200, 1200,
       'completed', 1200, 1200, NOW()
FROM orchestration.data_sources
WHERE source_name = 'food_com_recipes';

-- allrecipes_full: 1,000 of 39,802 ingested (from 1K sample extraction)
INSERT INTO orchestration.data_source_cursors
    (data_source_id, cursor_type, cursor_start, cursor_end, batch_size,
     status, records_processed, records_written, completed_at)
SELECT id, 'row_offset', 0, 1000, 1000,
       'completed', 1000, 1000, NOW()
FROM orchestration.data_sources
WHERE source_name = 'allrecipes_full';

-- indb_normalized: 1,000 of 1,014 ingested
INSERT INTO orchestration.data_source_cursors
    (data_source_id, cursor_type, cursor_start, cursor_end, batch_size,
     status, records_processed, records_written, completed_at)
SELECT id, 'row_offset', 0, 1000, 1000,
       'completed', 1000, 1000, NOW()
FROM orchestration.data_sources
WHERE source_name = 'indb_normalized';

-- usda_fruits: 100 of 136 ingested
INSERT INTO orchestration.data_source_cursors
    (data_source_id, cursor_type, cursor_start, cursor_end, batch_size,
     status, records_processed, records_written, completed_at)
SELECT id, 'row_offset', 0, 100, 100,
       'completed', 100, 100, NOW()
FROM orchestration.data_sources
WHERE source_name = 'usda_fruits';

-- off_products_batch_1: 3,500 of 12,118 ingested (includes 3K sample overlap)
INSERT INTO orchestration.data_source_cursors
    (data_source_id, cursor_type, cursor_start, cursor_end, batch_size,
     status, records_processed, records_written, completed_at)
SELECT id, 'row_offset', 0, 3500, 3500,
       'completed', 3500, 3500, NOW()
FROM orchestration.data_sources
WHERE source_name = 'off_products_batch_1';

-- food_com_sample_300: all 342 of 342 ingested
INSERT INTO orchestration.data_source_cursors
    (data_source_id, cursor_type, cursor_start, cursor_end, batch_size,
     status, records_processed, records_written, completed_at)
SELECT id, 'row_offset', 0, 342, 342,
       'completed', 342, 342, NOW()
FROM orchestration.data_sources
WHERE source_name = 'food_com_sample_300';


-- ══════════════════════════════════════════════════════
-- 3. Verification Queries (run these after the seed)
-- ══════════════════════════════════════════════════════

-- Uncomment and run to verify:

-- SELECT source_name, status, is_active, total_records, total_ingested,
--        file_format, storage_path
-- FROM orchestration.data_sources
-- ORDER BY category, status DESC, source_name;

-- SELECT ds.source_name, ds.status AS ds_status,
--        c.cursor_start, c.cursor_end, c.records_written,
--        ds.total_records,
--        ds.total_records - c.cursor_end AS remaining_records
-- FROM orchestration.data_source_cursors c
-- JOIN orchestration.data_sources ds ON ds.id = c.data_source_id
-- ORDER BY ds.source_name;

-- Done.
