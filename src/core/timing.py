"""Lightweight run timing traces."""
from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from src.state import persistence

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def _timings_path(run_id: str):
    run_dir = persistence.RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir / "timings.json"


def record_timing(
    run_id: str,
    category: str,
    name: str,
    elapsed_seconds: float,
    metadata: dict | None = None,
) -> None:
    """Append one timing event for *run_id*.

    Timing failures must never affect orchestration, so this helper is best
    effort and logs only at debug level on write errors.
    """
    event = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "category": category,
        "name": name,
        "elapsed_seconds": round(elapsed_seconds, 6),
        "metadata": metadata or {},
    }
    try:
        path = _timings_path(run_id)
        with _lock:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, list):
                    data = []
            else:
                data = []
            data.append(event)
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("[timing] Failed to record timing event: %s", exc)


@contextmanager
def timed(run_id: str, category: str, name: str, metadata: dict | None = None) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        record_timing(run_id, category, name, time.perf_counter() - start, metadata)
