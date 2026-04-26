"""
Run Log Buffer
===============

Thread-safe, in-memory buffer for real-time subprocess output lines.

Each orchestration run registers a buffer when it starts.  The SSE endpoint
reads new lines from the buffer via ``get_since()``, providing real-time
subprocess output to the dashboard terminal.

Buffers are automatically cleaned up when the run completes.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any, Dict, List, Optional


class RunLogBuffer:
    """Thread-safe per-run buffer for real-time subprocess log lines.

    Usage::

        RunLogBuffer.register("run-123")
        # ... from _stream_pipe thread:
        RunLogBuffer.append("run-123", "Processing table X...", "stdout")
        # ... from SSE endpoint:
        new_lines = RunLogBuffer.get_since("run-123", cursor=42)
        # ... when run finishes:
        RunLogBuffer.cleanup("run-123")
    """

    _buffers: Dict[str, deque] = {}
    _lock = threading.Lock()

    @classmethod
    def register(cls, orch_run_id: str, max_lines: int = 10_000) -> None:
        """Create a new buffer for a run.  Safe to call multiple times."""
        with cls._lock:
            if orch_run_id not in cls._buffers:
                cls._buffers[orch_run_id] = deque(maxlen=max_lines)

    @classmethod
    def append(
        cls,
        orch_run_id: str,
        message: str,
        stream: str = "stdout",
    ) -> None:
        """Append a line to a run's buffer (called from reader threads)."""
        with cls._lock:
            buf = cls._buffers.get(orch_run_id)
            if buf is not None:
                buf.append({"message": message, "stream": stream})

    @classmethod
    def get_since(cls, orch_run_id: str, cursor: int = 0) -> List[Dict[str, Any]]:
        """Return all lines from *cursor* onward.  Thread-safe snapshot."""
        with cls._lock:
            buf = cls._buffers.get(orch_run_id)
            if not buf:
                return []
            items = list(buf)
            return items[cursor:]

    @classmethod
    def size(cls, orch_run_id: str) -> int:
        """Current number of buffered lines."""
        with cls._lock:
            buf = cls._buffers.get(orch_run_id)
            return len(buf) if buf else 0

    @classmethod
    def cleanup(cls, orch_run_id: str) -> None:
        """Remove the buffer for a completed run to free memory."""
        with cls._lock:
            cls._buffers.pop(orch_run_id, None)

    @classmethod
    def drain(cls, orch_run_id: str) -> List[Dict[str, Any]]:
        """Return all buffered lines and remove the buffer atomically.

        Use this instead of ``cleanup()`` when you need to persist the
        console output before freeing memory.  The buffer is deleted in
        the same lock acquisition, preventing a race between the persist
        read and a concurrent cleanup call.
        """
        with cls._lock:
            buf = cls._buffers.pop(orch_run_id, None)
            return list(buf) if buf else []

    @classmethod
    def is_registered(cls, orch_run_id: str) -> bool:
        """Check if a buffer exists for this run."""
        with cls._lock:
            return orch_run_id in cls._buffers
