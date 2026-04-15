"""
Trigger System
===============

Schedule registration and webhook event dispatching.
"""

from __future__ import annotations

import logging
import time as _time
from typing import Any, Dict, List, Optional

from . import db

logger = logging.getLogger(__name__)

# ── Debounce logic for Gold schema batch syncs ──
_last_batch_trigger: Dict[str, float] = {}
_DEBOUNCE_SECONDS = 30


def _should_debounce(source_table: str) -> bool:
    """Prevent redundant batch syncs within the debounce window."""
    now = _time.time()
    last = _last_batch_trigger.get(source_table, 0)
    if now - last < _DEBOUNCE_SECONDS:
        logger.info(
            "⏳ Debouncing batch sync for %s (%.0fs since last)",
            source_table, now - last,
        )
        return True
    _last_batch_trigger[source_table] = now
    return False


# ══════════════════════════════════════════════════════
# Schedule Registration
# ══════════════════════════════════════════════════════

def register_schedules():
    """
    Read active schedule definitions from the orchestration schema
    and register them as Prefect deployments with cron triggers.

    This should be called once at startup or via ``orchestrator schedule --register``.
    """
    try:
        from prefect.client.schemas.schedules import CronSchedule
    except ImportError:
        logger.error("Prefect is not installed. Cannot register schedules.")
        return

    schedules = db.list_active_schedules()
    if not schedules:
        logger.info("No active schedules found in orchestration.schedule_definitions")
        return

    from .flows import FLOW_REGISTRY

    registered = 0
    for sched in schedules:
        flow_name = sched["flow_name"]
        cron_expr = sched["cron_expression"]
        sched_name = sched["schedule_name"]
        run_config = sched.get("run_config", {})

        if flow_name not in FLOW_REGISTRY:
            logger.warning(
                "Schedule '%s' references unknown flow '%s'. Skipping.",
                sched_name, flow_name,
            )
            continue

        flow_fn = FLOW_REGISTRY[flow_name]

        try:
            flow_fn.serve(
                name=sched_name,
                cron=cron_expr,
                parameters=run_config,
            )
            registered += 1
            logger.info(
                "📅 Registered schedule: %s → %s (cron: %s)",
                sched_name, flow_name, cron_expr,
            )
        except Exception as exc:
            logger.error(
                "Failed to register schedule '%s': %s", sched_name, exc,
            )

    logger.info("Registered %d / %d schedules", registered, len(schedules))
    return registered


# ══════════════════════════════════════════════════════
# Webhook Event Handler
# ══════════════════════════════════════════════════════

def handle_webhook_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process a Supabase database webhook payload and dispatch
    to the appropriate flow.

    Expected Supabase webhook payload format::

        {
            "type": "INSERT",        # INSERT, UPDATE, DELETE
            "table": "raw_recipes",
            "schema": "bronze",
            "record": { ... },       # The new/updated row
            "old_record": { ... },   # Previous row (for UPDATE/DELETE)
        }

    Returns
    -------
    dict
        Dispatch result or skip reason.
    """
    event_type = payload.get("type", "").lower()
    source_table = payload.get("table", "")
    source_schema = payload.get("schema", "")
    record = payload.get("record", {})

    if not event_type or not source_table:
        logger.warning("Malformed webhook payload: missing type or table")
        return {"error": "Malformed payload", "skipped": True}

    # Map Supabase event type to our enum
    event_type_map = {
        "insert": "supabase_insert",
        "update": "supabase_update",
        "delete": "supabase_delete",
    }
    mapped_type = event_type_map.get(event_type, event_type)

    # Check if there's a matching event trigger in the DB
    triggers = db.list_active_event_triggers()
    matching = [
        t for t in triggers
        if t["event_type"] == mapped_type
        and (t.get("source_schema") is None or t["source_schema"] == source_schema)
        and (t.get("source_table") is None or t["source_table"] == source_table)
    ]

    if not matching:
        # No explicit trigger defined — use default routing
        logger.info(
            "No explicit trigger for %s.%s (%s). Using default routing.",
            source_schema, source_table, mapped_type,
        )

        # Route Gold schema events to Neo4j batch sync
        if source_schema == "gold":
            if _should_debounce(source_table):
                return {"status": "debounced", "table": source_table}
            logger.info(
                "📡 Gold schema event detected — routing to neo4j_batch_sync"
            )
            from .flows import neo4j_batch_sync_flow
            return neo4j_batch_sync_flow(
                layer="all",
                trigger_type="webhook",
                triggered_by=f"gold.{source_table}",
            )

        from .flows import realtime_event_flow
        return realtime_event_flow(
            event_type=mapped_type,
            source_schema=source_schema,
            source_table=source_table,
            record=record,
        )

    # Dispatch to the first matching trigger's flow
    trigger = matching[0]
    flow_name = trigger["flow_name"]
    filter_config = trigger.get("filter_config", {})

    logger.info(
        "📡 Trigger '%s' matched: %s.%s → flow=%s",
        trigger["trigger_name"], source_schema, source_table, flow_name,
    )

    from .flows import FLOW_REGISTRY

    flow_fn = FLOW_REGISTRY.get(flow_name)
    if not flow_fn:
        logger.error("Trigger references unknown flow: %s", flow_name)
        return {"error": f"Unknown flow: {flow_name}", "skipped": True}

    # Build flow parameters
    flow_params: Dict[str, Any] = {
        "trigger_type": "webhook",
        "triggered_by": f"{source_schema}.{source_table}",
        "config": filter_config,
    }

    # For flows that need source_name / raw_input
    if flow_name in ("full_ingestion", "single_layer"):
        flow_params["source_name"] = source_table
        if record:
            flow_params["raw_input"] = [record]

    return flow_fn(**flow_params)
