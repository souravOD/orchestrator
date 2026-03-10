"""
CLI Entry Point
================

Usage::

    # Run the full ingestion pipeline
    python -m orchestrator.cli run --flow full_ingestion --source-name walmart --input data.csv

    # Run Bronze→Gold only
    python -m orchestrator.cli run --flow bronze_to_gold

    # Run a single layer
    python -m orchestrator.cli run --flow single_layer --layer silver_to_gold

    # Run Neo4j batch sync
    python -m orchestrator.cli neo4j sync --layer all

    # Run Neo4j reconciliation
    python -m orchestrator.cli neo4j reconcile

    # List recent alerts
    python -m orchestrator.cli alerts --limit 10

    # Start the webhook API server
    python -m orchestrator.cli serve

    # Register Prefect schedules from DB
    python -m orchestrator.cli schedule --register

    # List recent runs
    python -m orchestrator.cli runs --limit 10
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

# ── Force UTF-8 on Windows to support emoji in piped output ──
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Load .env before anything else
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class _TeeWriter:
    """Write to both the original stream and a log file."""

    def __init__(self, original, log_file_handle):
        self._original = original
        self._log = log_file_handle

    def write(self, data):
        self._original.write(data)
        try:
            self._log.write(data)
            self._log.flush()
        except Exception:
            pass  # never break the pipeline over a log write
        return len(data) if data else 0

    def flush(self):
        self._original.flush()
        try:
            self._log.flush()
        except Exception:
            pass

    # Delegate everything else (fileno, isatty, etc.) to the original
    def __getattr__(self, name):
        return getattr(self._original, name)


def setup_logging(level: str = "INFO"):
    log_level = getattr(logging, level.upper(), logging.INFO)
    log_format = "%(asctime)s │ %(levelname)-8s │ %(name)s │ %(message)s"
    log_datefmt = "%Y-%m-%d %H:%M:%S"

    # Log file path: orchestrator/logs.txt (sibling to orchestrator/ package)
    log_file = Path(__file__).resolve().parent.parent / "logs.txt"

    # ── 1. Tee stdout + stderr to the log file ──────────────
    # This captures EVERYTHING: print(), Prefect output, etc.
    log_fh = open(str(log_file), "w", encoding="utf-8")  # noqa: SIM115
    sys.stdout = _TeeWriter(sys.__stdout__, log_fh)
    sys.stderr = _TeeWriter(sys.__stderr__, log_fh)

    # ── 2. Python logging: console + file handlers ──────────
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    formatter = logging.Formatter(log_format, datefmt=log_datefmt)

    # Console handler (writes to the REAL stdout before tee)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)


def cmd_run(args):
    """Execute an orchestration flow."""
    from .flows import FLOW_REGISTRY

    flow_fn = FLOW_REGISTRY.get(args.flow)
    if not flow_fn:
        print(f"❌ Unknown flow: {args.flow}")
        print(f"   Available flows: {list(FLOW_REGISTRY.keys())}")
        sys.exit(1)

    config = {}
    if args.batch_size:
        config["batch_size"] = args.batch_size
    if args.no_incremental:
        config["incremental"] = False
    if args.skip_translation:
        config["skip_translation"] = True
    if args.vendor_id:
        config["vendor_id"] = args.vendor_id

    kwargs = {
        "trigger_type": "manual",
        "triggered_by": "cli",
        "config": config,
    }

    # Handle Neo4j-specific flow arguments
    if args.flow == "neo4j_batch_sync":
        kwargs["layer"] = args.layer or "all"
    elif args.flow == "full_ingestion":
        if not args.source_name:
            print("❌ --source-name is required for full_ingestion flow")
            sys.exit(1)
        kwargs["source_name"] = args.source_name
        kwargs["input_path"] = args.input
    elif args.flow == "single_layer":
        if not args.layer:
            print("❌ --layer is required for single_layer flow")
            sys.exit(1)
        kwargs["layer"] = args.layer
        kwargs["source_name"] = args.source_name
        kwargs["input_path"] = args.input

    print(f"🚀 Running flow: {args.flow}")
    result = flow_fn(**kwargs)
    print(f"\n✅ Flow completed.")
    if result and isinstance(result, dict):
        for key, value in result.items():
            if isinstance(value, dict):
                written = value.get("total_written", value.get("records_loaded", "?"))
                print(f"   {key}: {written} records written")


def cmd_serve(args):
    """Start the webhook API server."""
    import uvicorn
    from .config import settings

    host = args.host or settings.webhook_host
    port = args.port or settings.webhook_port

    print(f"🌐 Starting orchestrator API on {host}:{port}")
    print(f"   Docs: http://{host}:{port}/docs")
    uvicorn.run(
        "orchestrator.api:app",
        host=host,
        port=port,
        reload=args.reload,
    )


def cmd_schedule(args):
    """Register schedules from the orchestration schema."""
    if args.register:
        from .triggers import register_schedules
        count = register_schedules()
        print(f"✅ Registered {count} schedule(s)")
    else:
        from . import db
        schedules = db.list_active_schedules()
        if not schedules:
            print("No active schedules found.")
            return
        print(f"\n{'Name':<30} {'Cron':<20} {'Flow':<20}")
        print("─" * 70)
        for s in schedules:
            print(f"{s['schedule_name']:<30} {s['cron_expression']:<20} {s['flow_name']:<20}")


def cmd_runs(args):
    """List recent orchestration runs."""
    from . import db

    runs = db.list_orchestration_runs(limit=args.limit, status=args.status)
    if not runs:
        print("No orchestration runs found.")
        return

    print(f"\n{'ID':<38} {'Flow':<25} {'Status':<15} {'Records':<10} {'Duration':<10}")
    print("─" * 98)
    for r in runs:
        run_id = r["id"][:36]
        flow = r.get("flow_name", "?")[:23]
        status = r.get("status", "?")
        records = r.get("total_records_written", 0)
        duration = r.get("duration_seconds", "")
        duration_str = f"{duration}s" if duration else ""
        print(f"{run_id:<38} {flow:<25} {status:<15} {records:<10} {duration_str:<10}")


def cmd_neo4j(args):
    """Neo4j sync commands."""
    if args.neo4j_action == "sync":
        from .flows import neo4j_batch_sync_flow
        layer = args.layer or "all"
        print(f"🚀 Running Neo4j batch sync (layer={layer})")
        result = neo4j_batch_sync_flow(
            layer=layer,
            trigger_type="manual",
            triggered_by="cli",
        )
        print(f"✅ Neo4j sync completed.")
        if isinstance(result, dict):
            print(f"   Status: {result.get('status', '?')}")
            print(f"   Layers: {result.get('layers_run', '?')}")

    elif args.neo4j_action == "reconcile":
        from .flows import neo4j_reconciliation_flow
        print("🚀 Running Neo4j reconciliation")
        result = neo4j_reconciliation_flow(
            trigger_type="manual",
            triggered_by="cli",
        )
        print(f"✅ Reconciliation completed.")
        if isinstance(result, dict):
            print(f"   Tables checked: {result.get('tables_checked', '?')}")
            print(f"   Drifts detected: {result.get('drifts_detected', '?')}")
            print(f"   Backfills proposed: {result.get('backfills_proposed', '?')}")

    elif args.neo4j_action == "realtime":
        from .flows import neo4j_realtime_worker_flow
        print("🔴 Starting Neo4j realtime worker (Ctrl+C to stop)")
        neo4j_realtime_worker_flow(
            trigger_type="manual",
            triggered_by="cli",
        )
    else:
        print(f"❌ Unknown neo4j action: {args.neo4j_action}")
        sys.exit(1)


def cmd_alerts(args):
    """List recent alerts."""
    from . import db

    alerts = db.get_alerts(
        limit=args.limit,
        severity=args.severity,
        pipeline_name=args.pipeline,
    )
    if not alerts:
        print("No alerts found.")
        return

    print(f"\n{'Time':<22} {'Severity':<12} {'Pipeline':<20} {'Title'}")
    print("─" * 90)
    for a in alerts:
        ts = (a.get("created_at") or "")[:19]
        sev = a.get("severity", "?")
        pipe = a.get("pipeline_name") or "—"
        title = (a.get("title") or "")[:40]
        print(f"{ts:<22} {sev:<12} {pipe:<20} {title}")


def main():
    parser = argparse.ArgumentParser(
        prog="orchestrator",
        description="Data Orchestration Tool — Medallion Architecture Pipeline Manager",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ── run ──
    run_parser = subparsers.add_parser("run", help="Execute an orchestration flow")
    run_parser.add_argument(
        "--flow", required=True,
        help="Flow: full_ingestion, bronze_to_gold, single_layer, neo4j_batch_sync, neo4j_reconciliation",
    )
    run_parser.add_argument("--source-name", help="Data source name (e.g. walmart, usda)")
    run_parser.add_argument("--input", help="Path to input file (CSV/JSON/NDJSON)")
    run_parser.add_argument("--layer", help="Layer name (for single_layer or neo4j_batch_sync)")
    run_parser.add_argument("--batch-size", type=int, help="Batch size override")
    run_parser.add_argument("--no-incremental", action="store_true", help="Disable incremental processing")
    run_parser.add_argument("--skip-translation", action="store_true", help="Skip translation step (use for English-only data)")
    run_parser.add_argument("--vendor-id", help="Vendor UUID to associate with this pipeline run")
    run_parser.set_defaults(func=cmd_run)

    # ── serve ──
    serve_parser = subparsers.add_parser("serve", help="Start the webhook API server")
    serve_parser.add_argument("--host", help="Host to bind to")
    serve_parser.add_argument("--port", type=int, help="Port to bind to")
    serve_parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    serve_parser.set_defaults(func=cmd_serve)

    # ── schedule ──
    sched_parser = subparsers.add_parser("schedule", help="Manage cron schedules")
    sched_parser.add_argument("--register", action="store_true", help="Register schedules from DB")
    sched_parser.set_defaults(func=cmd_schedule)

    # ── runs ──
    runs_parser = subparsers.add_parser("runs", help="List recent orchestration runs")
    runs_parser.add_argument("--limit", type=int, default=20, help="Number of runs to show")
    runs_parser.add_argument("--status", help="Filter by status")
    runs_parser.set_defaults(func=cmd_runs)

    # ── neo4j ──
    neo4j_parser = subparsers.add_parser("neo4j", help="Neo4j sync commands")
    neo4j_sub = neo4j_parser.add_subparsers(dest="neo4j_action", help="Neo4j actions")

    sync_parser = neo4j_sub.add_parser("sync", help="Run batch sync to Neo4j")
    sync_parser.add_argument("--layer", default="all", help="Layer: recipes, ingredients, products, customers, or all")

    neo4j_sub.add_parser("reconcile", help="Run reconciliation check")
    neo4j_sub.add_parser("realtime", help="Start realtime outbox poller")
    neo4j_parser.set_defaults(func=cmd_neo4j)

    # ── alerts ──
    alerts_parser = subparsers.add_parser("alerts", help="List recent alerts")
    alerts_parser.add_argument("--limit", type=int, default=20, help="Number of alerts to show")
    alerts_parser.add_argument("--severity", help="Filter by severity: info, warning, critical")
    alerts_parser.add_argument("--pipeline", help="Filter by pipeline name")
    alerts_parser.set_defaults(func=cmd_alerts)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    setup_logging()
    args.func(args)


if __name__ == "__main__":
    main()
