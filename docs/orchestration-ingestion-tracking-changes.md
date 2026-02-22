# Orchestration Pipeline — Ingestion Tracking Changes

> **Team:** Orchestration (Python)  
> **Depends on:** PreBronze team (new `load_input_from_storage()`)  
> **Estimated effort:** ~30 lines changed across 4 files

---

## Overview

The B2B Node.js backend will start triggering the orchestration pipeline via `POST /api/trigger` for vendor data ingestion. Two trigger paths:

| Trigger | Flow | What happens |
|---------|------|-------------|
| CSV uploaded to Supabase Storage | `full_ingestion` | PreBronze→Bronze→Silver→Gold→Neo4j |
| API data already in bronze tables | `bronze_to_gold` | Bronze→Silver→Gold→Neo4j |

The orchestrator needs to:

1. Accept `vendor_id`, `storage_bucket`, `storage_path` in trigger requests
2. Pass `vendor_id` through to `orchestration_runs` for tenant isolation
3. Route storage params to the `full_ingestion_flow` so PreBronze can download the CSV

**No changes to Bronze→Silver or Silver→Gold pipelines.** Those auto-discover unprocessed data.

---

## Changes Required

### 1. `models.py` — Add Fields to `TriggerRequest`

```diff
 class TriggerRequest(BaseModel):
     flow_name: str
     source_name: Optional[str] = None
     input_path: Optional[str] = None
+    storage_bucket: Optional[str] = None
+    storage_path: Optional[str] = None
+    vendor_id: Optional[str] = None
     layers: List[str] = []
     batch_size: int = 100
     incremental: bool = True
     dry_run: bool = False
```

These fields are **optional** — existing callers (CLI, scheduled triggers) continue to work without them.

---

### 2. `db.py` — Add `vendor_id` to `create_orchestration_run()`

```diff
 def create_orchestration_run(
     flow_name: str,
+    vendor_id: Optional[str] = None,
     trigger_type: str = "manual",
     triggered_by: Optional[str] = None,
     flow_type: str = "batch",
     layers: Optional[List[str]] = None,
     config: Optional[Dict[str, Any]] = None,
 ) -> Dict[str, Any]:
     payload = {
         "flow_name": flow_name,
+        "vendor_id": vendor_id,
         "flow_type": flow_type,
         "status": "running",
         ...
     }
```

> **Prerequisite:** The SQL migration must be applied first to add the `vendor_id` column to `orchestration.orchestration_runs`.

---

### 3. `flows.py` — Update `full_ingestion_flow()` and `_load_input()`

#### Add params to `full_ingestion_flow()`

```diff
 @flow(name="full_ingestion", ...)
 def full_ingestion_flow(
     source_name: str,
+    vendor_id: Optional[str] = None,
     raw_input: Optional[List[Dict[str, Any]]] = None,
     input_path: Optional[str] = None,
+    storage_bucket: Optional[str] = None,
+    storage_path: Optional[str] = None,
     trigger_type: str = "manual",
     triggered_by: Optional[str] = None,
     config: Optional[Dict[str, Any]] = None,
     resume_from_run_id: Optional[str] = None,
 ) -> Dict[str, Any]:
```

#### Build storage URI before input loading

```diff
+    # If storage params provided, build a storage:// URI for _load_input
+    if raw_input is None and storage_bucket and storage_path:
+        input_path = f"storage://{storage_bucket}/{storage_path}"
+
     if validated.raw_input is None and validated.input_path:
         raw_input = _load_input(validated.input_path)
```

#### Pass `vendor_id` to `create_orchestration_run`

```diff
     orch_run = db.create_orchestration_run(
         flow_name="full_ingestion",
+        vendor_id=vendor_id,
         trigger_type=trigger_type,
         triggered_by=triggered_by,
         layers=[...],
         config=cfg,
     )
```

#### Update `_load_input()` to handle `storage://` URIs

```diff
 def _load_input(path: str) -> List[Dict[str, Any]]:
+    # Handle Supabase Storage URIs
+    if path.startswith("storage://"):
+        parts = path.replace("storage://", "").split("/", 1)
+        bucket, file_path = parts[0], parts[1]
+        # Delegate to PreBronze pipeline's storage loader
+        _ensure_pipeline_on_path("prebronze-to-bronze")
+        from prebronze.orchestrator import load_input_from_storage
+        return load_input_from_storage(bucket, file_path)
+
     file_path = Path(path)
     ...  # existing local file logic unchanged
```

#### Also update `bronze_to_gold_flow()` to accept `vendor_id`

```diff
 @flow(name="bronze_to_gold", ...)
 def bronze_to_gold_flow(
+    vendor_id: Optional[str] = None,
     trigger_type: str = "manual",
     ...
 ):
     orch_run = db.create_orchestration_run(
         flow_name="bronze_to_gold",
+        vendor_id=vendor_id,
         trigger_type=trigger_type,
         ...
     )
```

---

### 4. `api.py` — Forward New Fields in `trigger_flow()`

```diff
 @app.post("/api/trigger")
 async def trigger_flow(request: TriggerRequest):
     ...
     kwargs: Dict[str, Any] = {
         "trigger_type": "api",
         "triggered_by": "api:/api/trigger",
         "config": config,
+        "vendor_id": request.vendor_id,
     }

     if request.flow_name == "full_ingestion":
         kwargs["source_name"] = request.source_name or "api"
-        kwargs["input_path"] = request.input_path
+        if request.storage_bucket and request.storage_path:
+            kwargs["storage_bucket"] = request.storage_bucket
+            kwargs["storage_path"] = request.storage_path
+        else:
+            kwargs["input_path"] = request.input_path

     elif request.flow_name == "single_layer":
         ...
+
+    elif request.flow_name == "bronze_to_gold":
+        kwargs["vendor_id"] = request.vendor_id
```

---

## What Does NOT Change

| Component | Status |
|-----------|--------|
| `run_prebronze_to_bronze()` in `pipelines.py` | ❌ No change — receives `raw_input: List[Dict]` as before |
| `run_bronze_to_silver()` in `pipelines.py` | ❌ No change — auto-discovers unprocessed bronze rows |
| `run_silver_to_gold()` in `pipelines.py` | ❌ No change — auto-discovers unprocessed silver rows |
| `run_gold_to_neo4j_batch()` in `pipelines.py` | ❌ No change |
| All LangGraph node functions | ❌ No change |
| `contracts.py` | ❌ No change |
| `logging_utils.py`, `alerts.py`, `dlq_worker.py` | ❌ No change |

---

## SQL Migration (Apply First)

```sql
ALTER TABLE orchestration.orchestration_runs
  ADD COLUMN IF NOT EXISTS vendor_id uuid,
  ADD COLUMN IF NOT EXISTS progress_pct integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS totals jsonb DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_orch_runs_vendor
  ON orchestration.orchestration_runs (vendor_id, created_at DESC);

ALTER PUBLICATION supabase_realtime ADD TABLE orchestration.orchestration_runs;
ALTER PUBLICATION supabase_realtime ADD TABLE orchestration.pipeline_runs;
```

---

## Dependencies on Other Teams

| Dependency | Team | What you need |
|-----------|------|---------------|
| `load_input_from_storage(bucket, path)` function | PreBronze | Returns `List[Dict]` from Supabase Storage file |

---

## Example Trigger Requests from B2B

### CSV Upload (full pipeline)

```json
{
  "flow_name": "full_ingestion",
  "source_name": "products",
  "vendor_id": "550e8400-e29b-41d4-a716-446655440000",
  "storage_bucket": "csv-uploads",
  "storage_path": "vendors/550e8400/products_20260222.csv"
}
```

### API Ingestion (skip PreBronze, data already in bronze)

```json
{
  "flow_name": "bronze_to_gold",
  "source_name": "products",
  "vendor_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

---

## Testing Checklist

- [ ] `POST /api/trigger` with `storage_bucket` + `storage_path` → `full_ingestion_flow` starts, downloads CSV
- [ ] `POST /api/trigger` with `flow_name=bronze_to_gold` + `vendor_id` → flow starts, `vendor_id` stored in DB
- [ ] `GET /api/runs?limit=1` → returned run has `vendor_id` field populated
- [ ] Existing CLI triggers (no vendor_id) still work — field is optional
- [ ] Existing webhook triggers still work — no regression
