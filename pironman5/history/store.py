"""Metrics history: the last hour in RAM, one averaged row per minute in SQLite."""

from __future__ import annotations

import sqlite3
import threading
import time
from collections import deque
from itertools import groupby
from pathlib import Path
from typing import Any

from ..logger import get_logger

log = get_logger("history")

SERIES = ("cpu_percent", "cpu_temperature", "memory_percent", "disk_percent")
RING_SIZE = 3600  # samples kept in RAM
ROW_SECONDS = 60  # time span of one row on disk

_COLUMNS = ", ".join(SERIES)
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS samples (ts REAL NOT NULL PRIMARY KEY, "
    f"{', '.join(f'{name} REAL' for name in SERIES)})"
)
_INSERT = f"INSERT OR REPLACE INTO samples (ts, {_COLUMNS}) VALUES ({', '.join('?' * (len(SERIES) + 1))})"
_SELECT = (
    f"SELECT AVG(ts), {', '.join(f'AVG({name})' for name in SERIES)} FROM samples "
    "WHERE ts >= ? AND ts < ? GROUP BY CAST((ts - ?) / ? AS INTEGER) ORDER BY 1"
)

Row = tuple[Any, ...]  # (ts, *SERIES), a value is None when there is no reading


def _mean(rows: list[Row]) -> Row:
    """Column means. Missing readings do not count."""
    out = []
    for column in zip(*rows):
        values = [v for v in column if v is not None]
        out.append(sum(values) / len(values) if values else None)
    return tuple(out)


class HistoryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._ring: deque[Row] = deque(maxlen=RING_SIZE)
        self._pending: list[Row] = []
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # The tick thread writes and the web threads read. The lock serializes them.
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(_SCHEMA)
        self._conn.commit()
        log.info("history store at %s", self.db_path)

    def add(self, sample: Any, retention_days: int = 30) -> None:
        """Record one sample. Write a row to disk when its minute is complete."""
        row = (sample.time, *(getattr(sample, name) for name in SERIES))
        with self._lock:
            if self._pending and row[0] // ROW_SECONDS != self._pending[0][0] // ROW_SECONDS:
                self._flush()
                self._conn.execute("DELETE FROM samples WHERE ts < ?", (row[0] - retention_days * 86400,))
                self._conn.commit()
            self._pending.append(row)
            self._ring.append(row)

    def _flush(self) -> None:
        self._conn.execute(_INSERT, _mean(self._pending))
        self._conn.commit()
        self._pending.clear()

    def query(self, range_seconds: float, max_points: int = 360) -> list[dict[str, Any]]:
        """Samples of the last range_seconds, averaged into at most max_points."""
        since = time.time() - range_seconds
        width = range_seconds / max_points
        with self._lock:
            recent = [row for row in self._ring if row[0] >= since]
            disk_end = self._ring[0][0] if self._ring else float("inf")
            rows = self._conn.execute(_SELECT, (since, disk_end, since, width)).fetchall()

        def bucket(row: Row) -> int:
            return min(max_points - 1, int((row[0] - since) / width))

        points = [_mean(list(group)) for _, group in groupby(rows + recent, key=bucket)]
        return [dict(zip(("time", *SERIES), point)) for point in points]

    def close(self) -> None:
        with self._lock:
            if self._pending:
                self._flush()
            self._conn.close()
