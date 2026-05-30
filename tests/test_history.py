import time

from pironman5.history import HistoryStore


def _frame(ts, cpu=10.0):
    return {"time": ts, "cpu_percent": cpu, "cpu_temperature": 40.0, "memory_percent": 30.0}


def test_record_and_query(tmp_path):
    store = HistoryStore(tmp_path / "h.db", retention_days=1)
    now = time.time()
    for i in range(5):
        store.record(_frame(now - i, cpu=float(i)))
    samples = store.query(range_seconds=3600)
    assert len(samples) == 5
    assert {s["cpu_percent"] for s in samples} == {0.0, 1.0, 2.0, 3.0, 4.0}
    store.close()


def test_query_range_excludes_old(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    now = time.time()
    store.record(_frame(now - 10000))
    store.record(_frame(now))
    assert len(store.query(range_seconds=60)) == 1
    store.close()


def test_downsampling_caps_points(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    now = time.time()
    for i in range(1000):
        store.record(_frame(now - i))
    samples = store.query(range_seconds=3600, max_points=100)
    assert len(samples) <= 100
    store.close()


def test_missing_values_become_null(tmp_path):
    store = HistoryStore(tmp_path / "h.db")
    store.record({"time": time.time(), "cpu_percent": 5.0})
    sample = store.query(range_seconds=60)[0]
    assert sample["cpu_percent"] == 5.0
    assert sample["disk_percent"] is None
    store.close()
