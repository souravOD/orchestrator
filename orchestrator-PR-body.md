# feat: GraphSAGE Inference Flow + Embedding Backfill Orchestration

## Summary

Adds orchestrator integration for the GraphSAGE incremental inference service — a 6-hour cron job that uses the trained model (retained in GDS Catalog) to populate structural embeddings for new nodes between 3-day retraining cycles. Includes cross-flow mutual exclusion to prevent GDS memory contention.

## Problem

New nodes created between 3-day GraphSAGE retraining cycles have no `graphSageEmbedding`, degrading graph-structure-based recommendations. The orchestrator had no mechanism to trigger incremental inference.

## Changes

### `orchestrator/neo4j_adapter.py`

Added `run_graphsage_inference()` method to `Neo4jPipelineAdapter`:

- Lazily imports `GraphSageInferenceService` from G2N
- Follows existing `run_graphsage_retrain()` pattern
- Handles `ImportError` and runtime exceptions with structured logging

### `orchestrator/pipelines.py`

Added `run_gold_to_neo4j_graphsage_inference` task:

- `@task(name="gold_to_neo4j_graphsage_inference", retries=1, retry_delay_seconds=60)`
- Creates `pipeline_run` with `pipeline_name="gold_to_neo4j"`, `run_config={"mode": "graphsage_inference"}`
- Follows exact pattern of the existing `run_gold_to_neo4j_graphsage_retrain` task
- Sends alerts on failure via `send_alert()`

### `orchestrator/flows.py`

**New flow**: `neo4j_graphsage_inference_flow`

- `@flow(name="neo4j_graphsage_inference", retries=0)`
- Dual cross-guard: blocks if retrain OR inference is already running
- Creates orchestration_run record with `layers=["graphsage_inference"]`

**Modified flow**: `neo4j_graphsage_retrain_flow`

- Added `_guard_concurrent("neo4j_graphsage_inference")` — prevents retrain from starting if inference is running

**Registry**: Added `"neo4j_graphsage_inference": neo4j_graphsage_inference_flow` to `FLOW_REGISTRY`

### `orchestrator/api.py`

Added endpoint:

```python
@router.post("/api/neo4j/graphsage/inference")
async def trigger_graphsage_inference()
```

- Returns `200 OK` with result on success
- Returns `500` with error on failure
- Follows existing `/api/neo4j/graphsage/retrain` pattern

### `orchestrator/cli.py`

Added `graphsage-inference` subcommand:

- Handler: `_handle_graphsage_inference()`
- Argparse: `neo4j_sub.add_parser("graphsage-inference")`
- Usage: `python -m orchestrator.cli neo4j graphsage-inference`

## Mutual Exclusion Design

```
┌─────────────────────┐     ┌─────────────────────────┐
│  Retrain Flow       │     │  Inference Flow          │
│  (every 3 days)     │     │  (every 6 hours)         │
├─────────────────────┤     ├─────────────────────────┤
│ _guard_concurrent(  │     │ _guard_concurrent(       │
│   "neo4j_graphsage_ │     │   "neo4j_graphsage_     │
│    retrain")        │     │    inference")           │
│ _guard_concurrent(  │     │ _guard_concurrent(       │
│   "neo4j_graphsage_ │◄───►│   "neo4j_graphsage_     │
│    inference") ←NEW │     │    retrain")             │
└─────────────────────┘     └─────────────────────────┘
         ▲                              ▲
         └──── CANNOT RUN TOGETHER ─────┘
```

Both flows check for the other's active status before proceeding. If either is running, the other raises and waits for the next scheduled invocation.

## Trigger Methods

| Method | Command / URL |
|--------|--------------|
| **API** | `POST /api/neo4j/graphsage/inference` |
| **CLI** | `python -m orchestrator.cli neo4j graphsage-inference` |
| **Cron** | Via `schedule_definitions` SQL table (see below) |

## SQL Required (Post-Deploy)

```sql
INSERT INTO orchestration.schedule_definitions
    (schedule_name, cron_expression, flow_name, run_config)
VALUES
    ('graphsage_inference_6h', '0 */6 * * *', 'neo4j_graphsage_inference', '{}'),
    ('graphsage_retrain_3d', '0 2 */3 * *', 'neo4j_graphsage_retrain', '{}'),
    ('embedding_backfill_15m', '*/15 * * * *', 'neo4j_embedding_backfill', '{}')
ON CONFLICT (schedule_name) DO NOTHING;
```

Then run: `python -m orchestrator.cli schedule --register`

## Depends On

- **`ConferInc/gold-to-neo4j`** PR with `services/graphsage_inference/service.py` (the `GraphSageInferenceService` class that this adapter imports)

## Testing

```bash
# Prerequisite: retrain must run first to create the model
python -m orchestrator.cli neo4j graphsage-retrain

# Then test inference
python -m orchestrator.cli neo4j graphsage-inference

# API trigger
curl -X POST http://localhost:8000/api/neo4j/graphsage/inference

# Verify mutual exclusion (in two terminals simultaneously)
python -m orchestrator.cli neo4j graphsage-retrain &
python -m orchestrator.cli neo4j graphsage-inference
# → Second command should fail with guard error
```
