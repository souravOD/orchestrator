# Pipeline Metrics & LLM Token Tracking — Integration Guide

> **Document for:** Pipeline development teams (PreBronze→Bronze, Bronze→Silver, Silver→Gold)
> **Purpose:** Enable the orchestrator to collect tool-level execution metrics and LLM token usage from your pipeline runs
> **Effort:** ~2-4 hours per pipeline
> **Version bump:** MINOR (backward-compatible additions to return dicts)

---

## Table of Contents

1. [Why We Need This](#why-we-need-this)
2. [What Changes](#what-changes)
3. [Tool Metrics: Full Specification](#tool-metrics-full-specification)
4. [LLM Token Tracking: Full Specification](#llm-token-tracking-full-specification)
5. [Per-Pipeline Implementation Guide](#per-pipeline-implementation-guide)
6. [Testing Your Changes](#testing-your-changes)
7. [FAQ](#faq)
8. [Appendix: Full Return Dict Schema](#appendix-full-return-dict-schema)

---

## Why We Need This

### The Current Problem

Today, the orchestrator treats each pipeline as a **black box**. After a pipeline runs, the orchestrator knows:

- ✅ How many records were written/failed
- ✅ Total duration
- ✅ Whether it succeeded or failed

But the orchestrator has **no visibility into**:

- ❌ Which LangGraph tools/nodes ran and how long each took
- ❌ Which tools failed vs succeeded
- ❌ How many LLM calls were made
- ❌ How many tokens were consumed (prompt + completion)
- ❌ What the estimated cost of LLM usage was
- ❌ Which LLM model was used

### Why This Matters

| Metric | Business Value |
|--------|---------------|
| **Tool-level timing** | Identify bottleneck tools (e.g., "classify_table takes 80% of the runtime") |
| **Tool failure tracking** | Know which specific tools failed without reading logs |
| **Token counts** | Forecast monthly OpenAI costs as data volume grows |
| **Cost per run** | Track cost trends, alert if a run exceeds budget thresholds |
| **Model tracking** | Know if a pipeline is using `gpt-4o` vs `gpt-4o-mini` (10x cost difference) |
| **LLM calls per run** | Detect regressions (e.g., adding a retry loop that doubles LLM calls) |

### How It Works

```
┌─────────────────────┐         ┌─────────────────────┐
│   Your Pipeline     │         │   Orchestrator      │
│                     │         │                     │
│  run_sequential()   │────────>│  Reads return dict  │
│  or                 │ returns │                     │
│  transform_all()    │  dict   │  Extracts:          │
│                     │         │  - tool_metrics     │
│  (adds metrics to   │         │  - llm_usage        │
│   the return dict)  │         │                     │
│                     │         │  Stores in DB:      │
│                     │         │  - tool_metrics →   │
│                     │         │    pipeline_tool_    │
│                     │         │    metrics table     │
│                     │         │  - llm_usage →      │
│                     │         │    pipeline_llm_     │
│                     │         │    usage table       │
└─────────────────────┘         └─────────────────────┘
```

**You add data to your return dict → the orchestrator reads and stores it. That's it.**

---

## What Changes

### Summary

You need to add **two new keys** to the dict that your pipeline's entry-point function returns:

| Key | Type | Description |
|-----|------|-------------|
| `tool_metrics` | `list[dict]` | One entry per LangGraph tool/node invocation |
| `llm_usage` | `dict` | Aggregated LLM token usage and cost for the entire run |

### What Does NOT Change

- ❌ Your existing return dict keys (`records_loaded`, `total_processed`, etc.) — **unchanged**
- ❌ Your function signatures — **unchanged**
- ❌ Your LangGraph graph structure — **unchanged**
- ❌ Your dependencies — only uses existing LangChain imports
- ❌ How you run your pipeline standalone — **unchanged**

### Version Bump Required

This is a **MINOR** change (adding new keys without removing or renaming existing ones):

```toml
# pyproject.toml
[project]
version = "1.1.0"  # was 1.0.0
```

---

## Tool Metrics: Full Specification

### Schema

Each tool invocation in your LangGraph pipeline should produce one entry in the `tool_metrics` list:

```python
{
    "tool_name": str,         # REQUIRED — LangGraph node/tool name
    "duration_ms": int,       # REQUIRED — Wall-clock time in milliseconds
    "records_in": int,        # REQUIRED — Number of records this tool received as input
    "records_out": int,       # REQUIRED — Number of records this tool produced as output
    "status": str,            # REQUIRED — "completed" | "failed" | "skipped"
    "error": str | None,      # REQUIRED — Error message if status == "failed", else None
    "metadata": dict          # OPTIONAL — Any tool-specific metadata (default: {})
}
```

### Field Details

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `tool_name` | `str` | ✅ | — | The name of the LangGraph node or tool. Use the same name as the graph node. Example: `"classify_table"`, `"map_columns"`, `"validate_schema"` |
| `duration_ms` | `int` | ✅ | — | Wall-clock execution time in **milliseconds**. Use `int((time.time() - start) * 1000)` |
| `records_in` | `int` | ✅ | `0` | Number of records (rows) available to this tool at the start. For the first tool in a chain, this is the total input records |
| `records_out` | `int` | ✅ | `0` | Number of records produced by this tool. For transforms, this is the output count. For validators, this is the count of records that passed |
| `status` | `str` | ✅ | `"completed"` | Must be one of: `"completed"`, `"failed"`, `"skipped"` |
| `error` | `str \| None` | ✅ | `None` | If `status == "failed"`, include the error message. Otherwise, `None` |
| `metadata` | `dict` | ❌ | `{}` | Any additional tool-specific data. Use this for things like `{"columns_mapped": 12}`, `{"tables_discovered": 5}`, `{"drift_detected": true}` |

### Implementation Pattern

Add timing and metrics collection to each LangGraph node function:

```python
import time

def classify_table(state: dict) -> dict:
    """LangGraph node: classify incoming data by table type."""
    start = time.time()
    records_in = len(state.get("records", []))

    try:
        # ───────────────────────────────
        # Your existing tool logic here
        # ───────────────────────────────
        classified_records = do_classification(state["records"])
        records_out = len(classified_records)

        state["records"] = classified_records

        # ✅ Record success metric
        state.setdefault("tool_metrics", []).append({
            "tool_name": "classify_table",
            "duration_ms": int((time.time() - start) * 1000),
            "records_in": records_in,
            "records_out": records_out,
            "status": "completed",
            "error": None,
            "metadata": {
                "unique_tables": len(set(r["table"] for r in classified_records))
            },
        })

    except Exception as e:
        # ❌ Record failure metric (still raise!)
        state.setdefault("tool_metrics", []).append({
            "tool_name": "classify_table",
            "duration_ms": int((time.time() - start) * 1000),
            "records_in": records_in,
            "records_out": 0,
            "status": "failed",
            "error": str(e),
        })
        raise  # Always re-raise so the pipeline fails properly

    return state
```

### What About Tools That Don't Process Records?

Some tools don't have a clear "records in/out" concept (e.g., setup tools, config loaders). Use `0` for both:

```python
state.setdefault("tool_metrics", []).append({
    "tool_name": "load_config",
    "duration_ms": int((time.time() - start) * 1000),
    "records_in": 0,
    "records_out": 0,
    "status": "completed",
    "error": None,
    "metadata": {"config_keys_loaded": 15},
})
```

### Decorator Pattern (Optional — For Cleaner Code)

If you have many tools, you can use a decorator to avoid repeating the boilerplate:

```python
import time
import functools

def track_metrics(tool_name: str):
    """Decorator that auto-tracks tool metrics in the LangGraph state."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(state: dict) -> dict:
            start = time.time()
            records_in = len(state.get("records", []))

            try:
                result = func(state)
                records_out = len(result.get("records", []))

                result.setdefault("tool_metrics", []).append({
                    "tool_name": tool_name,
                    "duration_ms": int((time.time() - start) * 1000),
                    "records_in": records_in,
                    "records_out": records_out,
                    "status": "completed",
                    "error": None,
                })
                return result

            except Exception as e:
                state.setdefault("tool_metrics", []).append({
                    "tool_name": tool_name,
                    "duration_ms": int((time.time() - start) * 1000),
                    "records_in": records_in,
                    "records_out": 0,
                    "status": "failed",
                    "error": str(e),
                })
                raise
        return wrapper
    return decorator


# Usage:
@track_metrics("classify_table")
def classify_table(state: dict) -> dict:
    # Your existing logic — no metrics boilerplate needed!
    state["records"] = do_classification(state["records"])
    return state

@track_metrics("map_columns")
def map_columns(state: dict) -> dict:
    state["records"] = do_mapping(state["records"])
    return state
```

> **Note:** The decorator assumes `state["records"]` is the record list. Adjust the `records_in`/`records_out` extraction logic if your pipeline uses a different key.

---

## LLM Token Tracking: Full Specification

### Schema

Add an `llm_usage` key to your return dict with aggregated token counts:

```python
{
    "total_prompt_tokens": int,       # REQUIRED — Total input tokens across all LLM calls
    "total_completion_tokens": int,   # REQUIRED — Total output tokens
    "total_tokens": int,              # REQUIRED — Sum of prompt + completion
    "total_cost_usd": float,          # REQUIRED — Estimated cost in USD
    "model": str,                     # REQUIRED — Primary LLM model used
    "llm_calls": int,                 # REQUIRED — Number of LLM invocations

    # OPTIONAL: per-call breakdown (for debugging/optimization)
    "calls": [
        {
            "tool_name": str,             # Which LangGraph tool made this call
            "model": str,                 # Model used for this specific call
            "prompt_tokens": int,         # Input tokens for this call
            "completion_tokens": int,     # Output tokens for this call
            "cost_usd": float,            # Estimated cost for this call
            "duration_ms": int            # Wall-clock time for this LLM call
        },
        # ...
    ]
}
```

### Field Details

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `total_prompt_tokens` | `int` | ✅ | Total input tokens across all LLM calls in the run |
| `total_completion_tokens` | `int` | ✅ | Total output tokens across all LLM calls |
| `total_tokens` | `int` | ✅ | `total_prompt_tokens + total_completion_tokens` |
| `total_cost_usd` | `float` | ✅ | Estimated USD cost. Use `round(value, 6)` for precision |
| `model` | `str` | ✅ | Primary model name (e.g., `"gpt-4o-mini"`, `"gpt-4o"`) |
| `llm_calls` | `int` | ✅ | Total number of LLM invocations during the run |
| `calls` | `list[dict]` | ❌ | Per-call breakdown (optional but highly recommended for debugging) |

### Implementation: Using LangChain's OpenAI Callback

The easiest way to capture token usage is with LangChain's built-in `get_openai_callback()` context manager. **This works automatically with all OpenAI models called through LangChain.**

```python
import os
from langchain_community.callbacks import get_openai_callback


def run_sequential(state: dict) -> dict:
    """Main entry point for the PreBronze→Bronze pipeline."""

    with get_openai_callback() as cb:
        # ═══════════════════════════════════════════
        # ALL your existing pipeline logic goes here
        # The callback automatically counts tokens
        # for any LangChain LLM calls within this block
        # ═══════════════════════════════════════════
        result = graph.invoke(state)

    # After the with block, cb has the totals:
    result["llm_usage"] = {
        "total_prompt_tokens": cb.prompt_tokens,
        "total_completion_tokens": cb.completion_tokens,
        "total_tokens": cb.total_tokens,
        "total_cost_usd": round(cb.total_cost, 6),
        "model": os.environ.get("OPENAI_MODEL_NAME", "gpt-4o-mini"),
        "llm_calls": cb.successful_requests,
    }

    return result
```

### How `get_openai_callback()` Works

The callback hooks into LangChain's LLM call chain and automatically intercepts:

- **Every `ChatOpenAI` / `OpenAI` call** made within the `with` block
- **Token counts** from the OpenAI API response headers
- **Cost estimation** based on model pricing (LangChain maintains a pricing table)

```python
from langchain_openai import ChatOpenAI
from langchain_community.callbacks import get_openai_callback

llm = ChatOpenAI(model="gpt-4o-mini")

with get_openai_callback() as cb:
    # Call 1
    response1 = llm.invoke("Classify this record: ...")
    print(f"After call 1: {cb.total_tokens} tokens, ${cb.total_cost}")

    # Call 2
    response2 = llm.invoke("Map these columns: ...")
    print(f"After call 2: {cb.total_tokens} tokens, ${cb.total_cost}")

# Final totals (accumulated across all calls in the block)
print(f"Total: {cb.total_tokens} tokens, ${cb.total_cost}")
print(f"Calls: {cb.successful_requests}")
```

**Properties available on the callback object:**

| Property | Type | Description |
|----------|------|-------------|
| `cb.prompt_tokens` | `int` | Total input tokens |
| `cb.completion_tokens` | `int` | Total output tokens |
| `cb.total_tokens` | `int` | Sum of prompt + completion |
| `cb.total_cost` | `float` | Estimated USD cost |
| `cb.successful_requests` | `int` | Number of successful LLM API calls |

### Implementation: Per-Call Breakdown (Optional but Recommended)

If you want per-tool LLM cost breakdowns (e.g., "classify_table used 5000 tokens but map_columns used only 200"):

```python
import os
from langchain_community.callbacks import get_openai_callback


def run_sequential(state: dict) -> dict:
    """Main entry point with per-tool LLM tracking."""

    llm_calls_log = []

    with get_openai_callback() as cb_total:
        # ── Tool 1: Classify ──
        tokens_before = cb_total.total_tokens
        cost_before = cb_total.total_cost
        import time; t0 = time.time()

        state = classify_table(state)  # Makes LLM calls internally

        llm_calls_log.append({
            "tool_name": "classify_table",
            "model": os.environ.get("OPENAI_MODEL_NAME", "gpt-4o-mini"),
            "prompt_tokens": cb_total.prompt_tokens - (tokens_before - cb_total.completion_tokens),
            "completion_tokens": cb_total.completion_tokens,
            "cost_usd": round(cb_total.total_cost - cost_before, 6),
            "duration_ms": int((time.time() - t0) * 1000),
        })

        # ── Tool 2: Map Columns ──
        tokens_before = cb_total.total_tokens
        cost_before = cb_total.total_cost
        t0 = time.time()

        state = map_columns(state)

        llm_calls_log.append({
            "tool_name": "map_columns",
            "model": os.environ.get("OPENAI_MODEL_NAME", "gpt-4o-mini"),
            "prompt_tokens": cb_total.prompt_tokens,
            "completion_tokens": cb_total.completion_tokens,
            "cost_usd": round(cb_total.total_cost - cost_before, 6),
            "duration_ms": int((time.time() - t0) * 1000),
        })

    # Assemble final llm_usage
    state["llm_usage"] = {
        "total_prompt_tokens": cb_total.prompt_tokens,
        "total_completion_tokens": cb_total.completion_tokens,
        "total_tokens": cb_total.total_tokens,
        "total_cost_usd": round(cb_total.total_cost, 6),
        "model": os.environ.get("OPENAI_MODEL_NAME", "gpt-4o-mini"),
        "llm_calls": cb_total.successful_requests,
        "calls": llm_calls_log,
    }

    return state
```

### Simpler Alternative: Per-Tool Callbacks

If per-call tracking feels too complex, you can use **nested callbacks** per tool:

```python
from langchain_community.callbacks import get_openai_callback

def classify_table_with_tracking(state: dict) -> dict:
    """Tool with its own LLM tracking."""
    with get_openai_callback() as cb:
        state = classify_table(state)

    # Store this tool's LLM usage in state
    state.setdefault("_llm_calls_log", []).append({
        "tool_name": "classify_table",
        "prompt_tokens": cb.prompt_tokens,
        "completion_tokens": cb.completion_tokens,
        "cost_usd": round(cb.total_cost, 6),
        "llm_calls": cb.successful_requests,
    })
    return state

# Then in your main entry point, aggregate:
def run_sequential(state: dict) -> dict:
    with get_openai_callback() as cb_total:
        state = classify_table_with_tracking(state)
        state = map_columns_with_tracking(state)
        # ... etc ...

    state["llm_usage"] = {
        "total_prompt_tokens": cb_total.prompt_tokens,
        "total_completion_tokens": cb_total.completion_tokens,
        "total_tokens": cb_total.total_tokens,
        "total_cost_usd": round(cb_total.total_cost, 6),
        "model": os.environ.get("OPENAI_MODEL_NAME", "gpt-4o-mini"),
        "llm_calls": cb_total.successful_requests,
        "calls": state.pop("_llm_calls_log", []),
    }
    return state
```

### What If You Don't Use OpenAI?

If any of your LLM calls use a provider other than OpenAI (e.g., Anthropic, Cohere, local models), `get_openai_callback()` won't capture those. Options:

| Provider | How to Track |
|----------|-------------|
| **OpenAI (via LangChain)** | `get_openai_callback()` — automatic |
| **Anthropic (via LangChain)** | Use `AnthropicCallbackHandler` or parse response `usage` field |
| **Any provider (manual)** | Read `response.usage.prompt_tokens` from the raw API response |
| **Local / self-hosted** | Estimate tokens using `tiktoken` library |

Manual token counting example:

```python
import tiktoken

def count_tokens(text: str, model: str = "gpt-4o-mini") -> int:
    """Count tokens using tiktoken (works offline)."""
    encoding = tiktoken.encoding_for_model(model)
    return len(encoding.encode(text))
```

---

## Per-Pipeline Implementation Guide

### PreBronze → Bronze (`prebronze.orchestrator.run_sequential`)

**Current return dict:**

```python
{
    "records_loaded": int,
    "validation_errors_count": int,
    "validation_errors": list,
}
```

**Updated return dict:**

```python
{
    "records_loaded": int,
    "validation_errors_count": int,
    "validation_errors": list,
    "tool_metrics": [...],      # NEW
    "llm_usage": {...},         # NEW
}
```

**Implementation checklist:**

- [ ] Identify all LangGraph nodes in your graph (e.g., from `StateGraph` definition)
- [ ] Add `tool_metrics` tracking to each node function (use inline or decorator pattern)
- [ ] Wrap `graph.invoke()` with `get_openai_callback()` context manager
- [ ] Add `llm_usage` to the return dict after graph execution
- [ ] Test: Run pipeline and check that `tool_metrics` and `llm_usage` keys are present in the return dict
- [ ] Version bump: `pyproject.toml` → `version = "1.1.0"`

**Typical tools for this pipeline:**

| Tool/Node Name | Makes LLM Calls? | Has Records In/Out? |
|----------------|:-----------------:|:-------------------:|
| `classify_table` | ✅ | ✅ |
| `map_columns` | ✅ | ✅ |
| `validate_schema` | ❌ | ✅ |
| `write_to_bronze` | ❌ | ✅ |

---

### Bronze → Silver (`bronze_to_silver.auto_orchestrator.transform_all_tables`)

**Current return dict:**

```python
{
    "total_processed": int,
    "total_written": int,
    "total_failed": int,
    "total_dq_issues": int,
}
```

**Updated return dict:**

```python
{
    "total_processed": int,
    "total_written": int,
    "total_failed": int,
    "total_dq_issues": int,
    "tool_metrics": [...],      # NEW
    "llm_usage": {...},         # NEW
}
```

**Implementation checklist:**

- [ ] Identify all LangGraph nodes in the transformation graph
- [ ] Add `tool_metrics` tracking to each node
- [ ] Wrap the main execution with `get_openai_callback()`
- [ ] Aggregate metrics across all tables (since `transform_all_tables` may process multiple tables)
- [ ] Add `llm_usage` to the return dict
- [ ] Test and version bump

**Important note for multi-table pipelines:** If `transform_all_tables()` processes multiple tables in a loop, you should accumulate metrics across all tables:

```python
def transform_all_tables() -> dict:
    all_tool_metrics = []
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_cost = 0.0
    total_llm_calls = 0

    for table in tables_to_process:
        with get_openai_callback() as cb:
            result = transform_single_table(table)

        # Accumulate tool metrics
        all_tool_metrics.extend(result.get("tool_metrics", []))

        # Accumulate LLM usage
        total_prompt_tokens += cb.prompt_tokens
        total_completion_tokens += cb.completion_tokens
        total_cost += cb.total_cost
        total_llm_calls += cb.successful_requests

    return {
        "total_processed": ...,
        "total_written": ...,
        "total_failed": ...,
        "total_dq_issues": ...,
        "tool_metrics": all_tool_metrics,
        "llm_usage": {
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_prompt_tokens + total_completion_tokens,
            "total_cost_usd": round(total_cost, 6),
            "model": os.environ.get("OPENAI_MODEL_NAME", "gpt-4o-mini"),
            "llm_calls": total_llm_calls,
        },
    }
```

---

### Silver → Gold (`silver_to_gold.auto_orchestrator.transform_all_tables`)

Same pattern as Bronze→Silver. Follow the same checklist and multi-table accumulation pattern.

---

## Testing Your Changes

### 1. Unit Test: Verify Return Dict Shape

```python
import pytest

def test_return_dict_has_metrics():
    """Verify the pipeline return dict includes tool_metrics and llm_usage."""
    # Run with minimal test data
    result = run_sequential({
        "source_name": "test",
        "raw_input": [{"id": 1, "name": "Test"}],
    })

    # Check tool_metrics
    assert "tool_metrics" in result, "Missing 'tool_metrics' key"
    assert isinstance(result["tool_metrics"], list), "tool_metrics must be a list"

    for metric in result["tool_metrics"]:
        assert "tool_name" in metric, "Each metric must have 'tool_name'"
        assert "duration_ms" in metric, "Each metric must have 'duration_ms'"
        assert "status" in metric, "Each metric must have 'status'"
        assert metric["status"] in ("completed", "failed", "skipped")

    # Check llm_usage
    assert "llm_usage" in result, "Missing 'llm_usage' key"
    usage = result["llm_usage"]
    assert "total_tokens" in usage, "llm_usage must have 'total_tokens'"
    assert "total_cost_usd" in usage, "llm_usage must have 'total_cost_usd'"
    assert "model" in usage, "llm_usage must have 'model'"
    assert usage["total_tokens"] >= 0, "total_tokens must be non-negative"
```

### 2. Integration Test: Verify Metrics Are Realistic

```python
def test_metrics_are_realistic():
    """Verify that metrics contain non-zero values for real runs."""
    result = run_sequential({
        "source_name": "test",
        "raw_input": generate_test_data(100),  # 100 test records
    })

    # At least one tool should have run
    assert len(result["tool_metrics"]) > 0, "No tool metrics recorded"

    # At least one tool should have records
    has_records = any(m["records_in"] > 0 for m in result["tool_metrics"])
    assert has_records, "No tool processed any records"

    # LLM should have been called (if pipeline uses LLM)
    usage = result["llm_usage"]
    assert usage["llm_calls"] > 0, "No LLM calls recorded"
    assert usage["total_tokens"] > 0, "Zero tokens recorded"
```

### 3. Backward Compatibility Test

```python
def test_existing_keys_unchanged():
    """Verify that existing return dict keys are still present and correct."""
    result = run_sequential({
        "source_name": "test",
        "raw_input": [{"id": 1, "name": "Test"}],
    })

    # PreBronze→Bronze required keys
    assert "records_loaded" in result
    assert isinstance(result["records_loaded"], int)
    assert "validation_errors_count" in result
    assert "validation_errors" in result
```

### 4. Run Your Existing Test Suite

Make sure nothing is broken:

```bash
cd your-pipeline-repo/
pip install -e ".[dev]"
pytest tests/ -v
```

---

## FAQ

### Q: What if a tool doesn't make any LLM calls?

Still add a `tool_metrics` entry for it. Just the LLM tracking won't include it. This is valuable for seeing the full pipeline execution timeline.

### Q: What if my pipeline crashes mid-run? Will partial metrics be lost?

The metrics are part of the return dict, so if the pipeline crashes before returning, the orchestrator won't receive any metrics. However, the `tool_metrics` entries that were appended **before** the crash are in the state dict. If your pipeline has a top-level try/except that catches and re-raises, you can include the partial metrics:

```python
def run_sequential(state):
    try:
        with get_openai_callback() as cb:
            result = graph.invoke(state)
        result["llm_usage"] = {...}
        return result
    except Exception as e:
        # Include partial metrics in the error state
        state["llm_usage"] = {
            "total_prompt_tokens": cb.prompt_tokens if 'cb' in dir() else 0,
            ...
        }
        state["_error"] = str(e)
        raise  # Re-raise with metrics in state
```

### Q: Is `get_openai_callback()` thread-safe?

**Yes**, as of LangChain v0.1+. Each `with` block creates an isolated context. Multiple threads can have their own callbacks without interference.

### Q: Does `get_openai_callback()` slow down my pipeline?

**No measurable impact.** It hooks into LangChain's existing callback chain and only reads response metadata. No additional API calls or heavy computation.

### Q: What if I use streaming LLM responses?

`get_openai_callback()` works with streaming too. Token counts are accumulated as chunks arrive.

### Q: What model pricing does LangChain use?

LangChain maintains an internal pricing table. As of February 2025:

| Model | Input (per 1M tokens) | Output (per 1M tokens) |
|-------|----------------------|------------------------|
| `gpt-4o` | $2.50 | $10.00 |
| `gpt-4o-mini` | $0.15 | $0.60 |
| `gpt-4.1-mini` | $0.40 | $1.60 |

If the pricing is not up-to-date in your LangChain version, you can manually calculate cost:

```python
# Manual cost calculation
PRICING = {
    "gpt-4o-mini": {"input": 0.15 / 1_000_000, "output": 0.60 / 1_000_000},
    "gpt-4o": {"input": 2.50 / 1_000_000, "output": 10.00 / 1_000_000},
}

model = "gpt-4o-mini"
cost = (
    cb.prompt_tokens * PRICING[model]["input"] +
    cb.completion_tokens * PRICING[model]["output"]
)
```

### Q: Can I run the pipeline without metrics (for debugging)?

Yes. The orchestrator will gracefully handle missing `tool_metrics` and `llm_usage` keys. If the keys are absent, the orchestrator simply skips storing metrics. Your pipeline works either way.

### Q: Do I need to clean up `tool_metrics` from the state before returning?

No. The orchestrator reads the keys from the return dict. If you're using the state dict as the return dict, the `tool_metrics` list will naturally be there. If you're constructing a separate return dict, make sure to copy the `tool_metrics` from state.

---

## Appendix: Full Return Dict Schema

### PreBronze → Bronze

```python
{
    # ── Existing keys (DO NOT CHANGE) ──
    "records_loaded": int,               # Records successfully written to bronze
    "validation_errors_count": int,      # Records that failed validation
    "validation_errors": list,           # Error details

    # ── New keys (ADD THESE) ──
    "tool_metrics": [
        {
            "tool_name": "classify_table",
            "duration_ms": 1234,
            "records_in": 500,
            "records_out": 498,
            "status": "completed",
            "error": None,
            "metadata": {"unique_tables": 3}
        },
        {
            "tool_name": "map_columns",
            "duration_ms": 856,
            "records_in": 498,
            "records_out": 498,
            "status": "completed",
            "error": None,
            "metadata": {"columns_mapped": 12}
        },
        {
            "tool_name": "validate_schema",
            "duration_ms": 234,
            "records_in": 498,
            "records_out": 496,
            "status": "completed",
            "error": None,
            "metadata": {"validation_rules_applied": 8}
        },
        {
            "tool_name": "write_to_bronze",
            "duration_ms": 1567,
            "records_in": 496,
            "records_out": 496,
            "status": "completed",
            "error": None,
            "metadata": {"target_table": "bronze.walmart_products"}
        }
    ],

    "llm_usage": {
        "total_prompt_tokens": 15420,
        "total_completion_tokens": 3280,
        "total_tokens": 18700,
        "total_cost_usd": 0.004245,
        "model": "gpt-4o-mini",
        "llm_calls": 12,
        "calls": [
            {
                "tool_name": "classify_table",
                "model": "gpt-4o-mini",
                "prompt_tokens": 8200,
                "completion_tokens": 1640,
                "cost_usd": 0.002214,
                "duration_ms": 890
            },
            {
                "tool_name": "map_columns",
                "model": "gpt-4o-mini",
                "prompt_tokens": 7220,
                "completion_tokens": 1640,
                "cost_usd": 0.002031,
                "duration_ms": 756
            }
        ]
    }
}
```

### Bronze → Silver / Silver → Gold

```python
{
    # ── Existing keys (DO NOT CHANGE) ──
    "total_processed": int,
    "total_written": int,
    "total_failed": int,
    "total_dq_issues": int,

    # ── New keys (ADD THESE) ──
    "tool_metrics": [
        {
            "tool_name": "discover_tables",
            "duration_ms": 450,
            "records_in": 0,
            "records_out": 0,
            "status": "completed",
            "error": None,
            "metadata": {"tables_found": 5}
        },
        {
            "tool_name": "transform_table:walmart_products",
            "duration_ms": 3456,
            "records_in": 1200,
            "records_out": 1195,
            "status": "completed",
            "error": None,
            "metadata": {"dq_issues": 3}
        },
        {
            "tool_name": "transform_table:usda_nutrition",
            "duration_ms": 2100,
            "records_in": 800,
            "records_out": 798,
            "status": "completed",
            "error": None,
            "metadata": {"dq_issues": 1}
        }
    ],

    "llm_usage": {
        "total_prompt_tokens": 24500,
        "total_completion_tokens": 5100,
        "total_tokens": 29600,
        "total_cost_usd": 0.006735,
        "model": "gpt-4o-mini",
        "llm_calls": 18
    }
}
```

---

## Timeline & Priority

| Step | Task | Owner | Effort |
|------|------|-------|--------|
| 1 | Read this guide | Pipeline teams | 30 min |
| 2 | Add `tool_metrics` to each LangGraph node | Pipeline teams | 1-2 hours |
| 3 | Add `get_openai_callback()` wrapper | Pipeline teams | 30 min |
| 4 | Test return dict shape | Pipeline teams | 30 min |
| 5 | Version bump `pyproject.toml` | Pipeline teams | 5 min |
| 6 | Tag release `v1.1.0` | Pipeline teams | 5 min |

**Total estimated effort: 2-4 hours per pipeline.**
