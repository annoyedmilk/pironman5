import sqlite3
import time
from contextlib import closing

from pironman5.history import HistoryStore
from pironman5.metrics import Sample


def _sample(ts, cpu=10.0, temp=40.0):
    return Sample(time=ts, cpu_percent=cpu, cpu_temperature=temp, memory_percent=30.0, disk_percent=5.0)


def _rows(path):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT ts, cpu_percent FROM samples ORDER BY ts").fetchall()


def test_recent_samples_come_from_memory(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    now = time.time()
    for i in range(5):
        store.add(_sample(now - 4 + i, cpu=float(i)))
    samples = store.query(range_seconds=60)
    assert [s["cpu_percent"] for s in samples] == [0.0, 1.0, 2.0, 3.0, 4.0]
    store.close()


def test_one_averaged_row_per_minute_on_disk(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    start = time.time() // 60 * 60 - 180
    for i in range(180):
        store.add(_sample(start + i, cpu=float(i // 60)))
    assert [cpu for _, cpu in _rows(tmp_path / "h.db")] == [0.0, 1.0]
    store.close()
    assert [cpu for _, cpu in _rows(tmp_path / "h.db")] == [0.0, 1.0, 2.0]


def test_query_reads_disk_rows_after_restart(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    now = time.time()
    store.add(_sample(now - 10000))
    store.add(_sample(now - 300, cpu=50.0))
    store.close()
    store = HistoryStore(tmp_path / "h.db")
    store.add(_sample(now, cpu=70.0))
    assert [s["cpu_percent"] for s in store.query(range_seconds=3600)] == [50.0, 70.0]
    store.close()


def test_query_caps_points(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    now = time.time()
    for i in range(1000):
        store.add(_sample(now - 999 + i))
    assert len(store.query(range_seconds=3600, max_points=100)) <= 100
    store.close()


def test_retention_removes_old_rows(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    now = time.time()
    store.add(_sample(now - 3 * 86400))
    store.add(_sample(now - 120), retention_days=1)
    store.add(_sample(now), retention_days=1)
    assert len(_rows(tmp_path / "h.db")) == 1
    store.close()


def test_missing_values_become_null(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    store.add(_sample(time.time(), cpu=5.0, temp=None))
    sample = store.query(range_seconds=60)[0]
    assert sample["cpu_percent"] == 5.0
    assert sample["cpu_temperature"] is None
    store.close()
