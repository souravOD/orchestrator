"""
Dead-Letter Queue Worker
=========================

Background worker that auto-retries failed webhook events using
conservative exponential backoff (1min → 5min → 30min, max 3 retries).

Runs as an asyncio background task registered on FastAPI startup.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime, timedelta, timezone

from . import db
from .config import settings
from .triggers import handle_webhook_event

logger = logging.getLogger(__name__)

# ── Non-retryable error patterns ─────────────────────────────
# Errors matching these patterns are deterministic failures that
# will NEVER succeed on retry. They are immediately discarded
# instead of entering the retry loop (fixes DLQ amplification).
_NON_RETRYABLE_PATTERNS = [
    "ConcurrentRunError",
    "already running",
    "concurrent flow",
    "address already in use",       # Prefect port conflict
    "Pipeline import failed",       # Missing module
    "No module named",              # Import error
    "handled_by_outbox",            # Gold events (outbox worker handles)
]


def _is_non_retryable(exc: Exception) -> bool:
    """Return True if the error is deterministic and will never succeed on retry."""
    msg = str(exc)
    return any(p.lower() in msg.lower() for p in _NON_RETRYABLE_PATTERNS)


# Conservative exponential backoff delays (in minutes)
_RETRY_DELAYS: list[int] = []


def _get_retry_delays() -> list[int]:
    """Parse retry delays from settings (cached)."""
    global _RETRY_DELAYS
    if not _RETRY_DELAYS:
        _RETRY_DELAYS = [
            int(d.strip())
            for d in settings.dlq_retry_delays_minutes.split(",")
        ]
    return _RETRY_DELAYS


def _utcnow() -> str:
    """UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def _utcnow_plus(minutes: int) -> str:
    """UTC timestamp + offset in ISO format."""
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def retry_dead_letters() -> int:
    """
    Process DLQ entries that are due for retry.

    Returns the number of entries processed.
    """
    retryable = db.get_retryable_dead_letters()

    if not retryable:
        return 0

    processed = 0
    for entry in retryable:
        entry_id = entry["id"]
        retry_count = entry.get("retry_count", 0)
        max_retries = entry.get("max_retries", settings.dlq_max_retries)

        logger.info(
            "DLQ retry #%d for entry %s",
            retry_count + 1, entry_id,
        )

        # Mark as retrying
        db.update_dead_letter(entry_id, status="retrying", last_retry_at=_utcnow())

        try:
            handle_webhook_event(entry["payload"])
            # Success — mark as resolved
            db.update_dead_letter(
                entry_id,
                status="resolved",
                resolved_at=_utcnow(),
                retry_count=retry_count + 1,
            )
            logger.info("DLQ entry %s resolved on retry #%d", entry_id, retry_count + 1)

        except Exception as exc:
            # ── Non-retryable errors: discard immediately ──
            if _is_non_retryable(exc):
                db.update_dead_letter(
                    entry_id,
                    status="discarded",
                    retry_count=retry_count + 1,
                    error_message=f"Non-retryable: {exc}",
                )
                logger.info(
                    "DLQ entry %s discarded (non-retryable): %s",
                    entry_id, exc,
                )
                processed += 1
                continue

            # ── Retryable errors: apply backoff or exhaust ──
            new_count = retry_count + 1
            if new_count >= max_retries:
                # Exhausted all retries
                db.update_dead_letter(
                    entry_id,
                    status="exhausted",
                    retry_count=new_count,
                    error_message=str(exc),
                )
                logger.warning(
                    "DLQ entry %s exhausted after %d retries: %s",
                    entry_id, new_count, exc,
                )
                # Send alert for exhausted entries
                try:
                    from .alerts import send_alert
                    send_alert(
                        title="⚠️ Webhook DLQ exhausted",
                        message=(
                            f"Entry {entry_id} failed after {new_count} retries.\n"
                            f"Error: {exc}"
                        ),
                        severity="warning",
                    )
                except Exception:
                    logger.error("Failed to send DLQ exhaustion alert", exc_info=True)
            else:
                # Schedule next retry with backoff
                delays = _get_retry_delays()
                delay_idx = min(new_count - 1, len(delays) - 1)
                delay_minutes = delays[delay_idx]

                db.update_dead_letter(
                    entry_id,
                    status="pending",
                    retry_count=new_count,
                    next_retry_at=_utcnow_plus(delay_minutes),
                    error_message=str(exc),
                )
                logger.info(
                    "DLQ entry %s rescheduled: retry #%d in %d min",
                    entry_id, new_count, delay_minutes,
                )

        processed += 1

    return processed


async def dlq_background_loop():
    """
    Long-running background loop that polls for retryable DLQ entries.

    Should be started as an asyncio task on FastAPI startup.
    """
    poll_interval = settings.dlq_poll_interval_seconds
    logger.info(
        "DLQ worker started (poll every %ds, delays=%s min, max_retries=%d)",
        poll_interval,
        settings.dlq_retry_delays_minutes,
        settings.dlq_max_retries,
    )

    while True:
        try:
            processed = retry_dead_letters()
            if processed > 0:
                logger.info("DLQ worker processed %d entries", processed)
        except Exception:
            logger.error("DLQ worker error", exc_info=True)

        await asyncio.sleep(poll_interval)
