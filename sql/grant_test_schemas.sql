-- ══════════════════════════════════════════════════════
-- Migration: Grant Access to Bronze / Silver / Gold Schemas
-- ══════════════════════════════════════════════════════
-- Purpose: Allow service_role (used by PostgREST / pipeline subprocesses)
--          to read and write data in bronze, silver, and gold schemas.
--          Also grants read access for authenticated/anon roles.
--
-- Run this ONCE in the TEST Supabase SQL Editor.
-- Safe to run on a live system — additive change only (GRANT is idempotent).
--
-- After running, also expose the schemas via PostgREST:
--   Supabase Dashboard → Settings → API → Exposed schemas
--   Add: bronze, silver, gold (comma-separated alongside existing ones)
-- ══════════════════════════════════════════════════════

-- ── Schema USAGE grants ────────────────────────────────
GRANT USAGE ON SCHEMA bronze TO service_role, authenticated, anon;
GRANT USAGE ON SCHEMA silver TO service_role, authenticated, anon;
GRANT USAGE ON SCHEMA gold   TO service_role, authenticated, anon;

-- ── Table privileges ───────────────────────────────────
-- service_role gets full CRUD (used by pipeline subprocesses)
GRANT ALL ON ALL TABLES IN SCHEMA bronze TO service_role;
GRANT ALL ON ALL TABLES IN SCHEMA silver TO service_role;
GRANT ALL ON ALL TABLES IN SCHEMA gold   TO service_role;

-- authenticated / anon get read-only (dashboard queries)
GRANT SELECT ON ALL TABLES IN SCHEMA bronze TO authenticated, anon;
GRANT SELECT ON ALL TABLES IN SCHEMA silver TO authenticated, anon;
GRANT SELECT ON ALL TABLES IN SCHEMA gold   TO authenticated, anon;

-- ── Sequence privileges ────────────────────────────────
-- Prevents "permission denied for sequence" errors on serial/identity columns
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA bronze TO service_role, authenticated;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA silver TO service_role, authenticated;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA gold   TO service_role, authenticated;

-- ── Default privileges for FUTURE tables & sequences ───
-- Ensures any tables/sequences created later inherit the same grants
ALTER DEFAULT PRIVILEGES IN SCHEMA bronze GRANT ALL ON TABLES TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA silver GRANT ALL ON TABLES TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA gold   GRANT ALL ON TABLES TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA bronze GRANT SELECT ON TABLES TO authenticated, anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA silver GRANT SELECT ON TABLES TO authenticated, anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA gold   GRANT SELECT ON TABLES TO authenticated, anon;

ALTER DEFAULT PRIVILEGES IN SCHEMA bronze GRANT USAGE, SELECT ON SEQUENCES TO service_role, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA silver GRANT USAGE, SELECT ON SEQUENCES TO service_role, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA gold   GRANT USAGE, SELECT ON SEQUENCES TO service_role, authenticated;
