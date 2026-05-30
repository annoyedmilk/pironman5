"""SQLite ring-buffer time-series store for metrics history.

A single table of numeric samples. Old rows are pruned by retention on insert,
cheaply and throttled. This is plenty for charting a handful of metrics over
days, with no background daemon and no extra network port to manage.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from ..logger import get_logger

log = get_logger("history")

# Columns we persist for charting. Keep this small and numeric.
SERIES = (
    "cpu_percent",
    "cpu_temperature",
    "memory_percent",
    "disk_percent",
)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS samples (
    ts REAL NOT NULL PRIMARY KEY,
    {", ".join(f"{name} REAL" for name in SERIES)}
);
CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);
"""


class HistoryStore:
    def __init__(self, db_path: str | Path, retention_days: int = 30) -> None:
        self.db_path = Path(db_path)
        self.retention_days = retention_days
        self._lock = threading.Lock()
        self._last_prune = 0.0
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the metrics loop and API may touch it from
        # different threads; we serialize all access with our own lock.
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        log.info("history store at %s (retention %dd)", self.db_path, retention_days)

    def record(self, frame: dict[str, Any]) -> None:
        """Insert one sample row from a metrics frame."""
        ts = frame.get("time", time.time())
        values = [ts] + [_as_float(frame.get(name)) for name in SERIES]
        placeholders = ", ".join("?" for _ in values)
        columns = "ts, " + ", ".join(SERIES)
        with self._lock:
            self._conn.execute(
                f"INSERT OR REPLACE INTO samples ({columns}) VALUES ({placeholders})",
                values,
            )
            self._conn.commit()
        self._maybe_prune(ts)

    def query(self, range_seconds: float, max_points: int = 600) -> list[dict[str, Any]]:
        """Return samples newer than ``range_seconds`` ago, downsampled.

        Downsampling is stride-based (every Nth row) so the chart stays light
        without server-side aggregation complexity.
        """
        since = time.time() - range_seconds
        columns = "ts, " + ", ".join(SERIES)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {columns} FROM samples WHERE ts >= ? ORDER BY ts ASC",
                (since,),
            ).fetchall()

        stride = max(1, len(rows) // max_points)
        out: list[dict[str, Any]] = []
        for row in rows[::stride]:
            ts, *vals = row
            point: dict[str, Any] = {"time": ts}
            point.update({name: vals[i] for i, name in enumerate(SERIES)})
            out.append(point)
        return out

    def _maybe_prune(self, now: float) -> None:
        # Prune at most once per minute to keep inserts cheap.
        if now - self._last_prune < 60:
            return
        self._last_prune = now
        cutoff = now - self.retention_days * 86400
        with self._lock:
            self._conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
