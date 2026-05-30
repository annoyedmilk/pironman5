"""System metrics collection.

Built on ``psutil`` plus a few Raspberry Pi sysfs reads and ``vcgencmd`` calls,
so it degrades gracefully on a development machine: Pi-only sensors report
``None`` instead of raising. Each frame is a flat dict the web layer and history
store consume directly.
"""

from __future__ import annotations

import platform
import re
import socket
import subprocess
import time
from typing import Any

import psutil

from ..logger import get_logger

log = get_logger("metrics")

# Cached deltas for per-second throughput, and static values read once.
_last_net = _last_net_time = None
_last_disk = _last_disk_time = None
_model_cache: str | None = None


# ---- helpers --------------------------------------------------------------

def _read(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _vcgencmd(*args: str) -> str | None:
    try:
        return subprocess.check_output(["vcgencmd", *args], text=True, timeout=2).strip()
    except (OSError, subprocess.SubprocessError):
        return None


# ---- CPU ------------------------------------------------------------------

def _cpu_temperature() -> float | None:
    raw = _read("/sys/class/thermal/thermal_zone0/temp")
    if raw is not None:
        try:
            return round(int(raw) / 1000, 1)
        except ValueError:
            pass
    try:
        for readings in psutil.sensors_temperatures().values():
            if readings:
                return round(readings[0].current, 1)
    except (AttributeError, OSError):
        pass
    return None


def _gpu_temperature() -> float | None:
    out = _vcgencmd("measure_temp")
    if out:
        try:
            return round(float(out.split("=")[1].split("'")[0]), 1)
        except (ValueError, IndexError):
            pass
    return None


def _core_voltage() -> float | None:
    out = _vcgencmd("measure_volts", "core")
    if out:
        try:
            return round(float(out.split("=")[1].rstrip("V")), 3)
        except (ValueError, IndexError):
            pass
    return None


# ---- power (Pi 5 PMIC) ----------------------------------------------------

def _power() -> dict[str, Any]:
    """Total board power and key voltages from the PMIC ADC."""
    out = _vcgencmd("pmic_read_adc")
    if not out:
        return {"power_watts": None, "input_voltage": None, "battery_voltage": None}
    cur: dict[str, float] = {}
    volt: dict[str, float] = {}
    for m in re.finditer(r"(\w+)_(A|V)\s+\w+\(\d+\)=([\d.]+)", out):
        (cur if m.group(2) == "A" else volt)[m.group(1)] = float(m.group(3))
    total = sum(cur[n] * volt[n] for n in cur if n in volt)
    return {
        "power_watts": round(total, 2),
        "input_voltage": round(volt["EXT5V"], 2) if "EXT5V" in volt else None,
        "battery_voltage": round(volt["BATT"], 2) if "BATT" in volt else None,
    }


# ---- health / throttling --------------------------------------------------

def _throttle() -> dict[str, str] | None:
    """Decode get_throttled into ok / now / past per condition."""
    out = _vcgencmd("get_throttled")
    if not out or "=" not in out:
        return None
    try:
        bits = int(out.split("=")[1], 16)
    except ValueError:
        return None

    def state(now_bit: int, past_bit: int) -> str:
        if bits & (1 << now_bit):
            return "now"
        if bits & (1 << past_bit):
            return "past"
        return "ok"

    return {
        "undervoltage": state(0, 16),
        "freq_capped": state(1, 17),
        "throttled": state(2, 18),
        "soft_temp": state(3, 19),
    }


# ---- storage --------------------------------------------------------------

def _nvme_temp() -> float | None:
    try:
        temps = psutil.sensors_temperatures()
    except (AttributeError, OSError):
        return None
    for name, readings in temps.items():
        if "nvme" in name.lower() and readings:
            return round(readings[0].current, 1)
    return None


def _disk_io() -> tuple[int | None, int | None]:
    global _last_disk, _last_disk_time
    counters = psutil.disk_io_counters()
    if counters is None:
        return None, None
    now = time.time()
    if _last_disk is None:
        _last_disk, _last_disk_time = counters, now
        return 0, 0
    interval = max(1e-6, now - _last_disk_time)
    read = round((counters.read_bytes - _last_disk.read_bytes) / interval)
    write = round((counters.write_bytes - _last_disk.write_bytes) / interval)
    _last_disk, _last_disk_time = counters, now
    return max(0, read), max(0, write)


# ---- network --------------------------------------------------------------

def _network_speed() -> tuple[int, int]:
    global _last_net, _last_net_time
    now = time.time()
    counters = psutil.net_io_counters()
    if _last_net is None:
        _last_net, _last_net_time = counters, now
        return 0, 0
    interval = max(1e-6, now - _last_net_time)
    up = round((counters.bytes_sent - _last_net.bytes_sent) / interval)
    down = round((counters.bytes_recv - _last_net.bytes_recv) / interval)
    _last_net, _last_net_time = counters, now
    return max(0, up), max(0, down)


def _ips() -> dict[str, str]:
    result: dict[str, str] = {}
    for name, addrs in psutil.net_if_addrs().items():
        if name == "lo":
            continue
        for addr in addrs:
            if addr.family == socket.AF_INET and addr.address:
                result[name] = addr.address
    return result


def _macs() -> dict[str, str]:
    result: dict[str, str] = {}
    for name, addrs in psutil.net_if_addrs().items():
        if name == "lo":
            continue
        for addr in addrs:
            if addr.family == psutil.AF_LINK and addr.address:
                result[name] = addr.address
    return result


def _link_speed() -> int | None:
    """Negotiated link speed (Mbps) of the first up, non-loopback interface."""
    try:
        for name, stats in psutil.net_if_stats().items():
            if name != "lo" and stats.isup and stats.speed > 0:
                return stats.speed
    except OSError:
        pass
    return None


# ---- fan / system ---------------------------------------------------------

def _model() -> str | None:
    global _model_cache
    if _model_cache is None:
        raw = _read("/proc/device-tree/model")
        _model_cache = raw.replace("\x00", "").strip() if raw else ""
    return _model_cache or None


# ---- snapshot -------------------------------------------------------------

def snapshot() -> dict[str, Any]:
    """Collect a single flat metrics frame.

    Byte counts are raw and formatted in the UI. Throughput values are
    per-second and rely on this being called on a regular cadence.
    """
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    freq = psutil.cpu_freq()
    up, down = _network_speed()
    disk_read, disk_write = _disk_io()
    try:
        load_avg = [round(x, 2) for x in psutil.getloadavg()]
    except (OSError, AttributeError):
        load_avg = None

    frame: dict[str, Any] = {
        "time": time.time(),
        # cpu
        "cpu_percent": psutil.cpu_percent(),
        "cpu_per_core": psutil.cpu_percent(percpu=True),
        "cpu_count": psutil.cpu_count(),
        "cpu_freq": round(freq.current, 1) if freq else None,
        "cpu_temperature": _cpu_temperature(),
        "gpu_temperature": _gpu_temperature(),
        "cpu_voltage": _core_voltage(),
        "load_avg": load_avg,
        "processes": len(psutil.pids()),
        # memory
        "memory_total": mem.total,
        "memory_used": mem.total - mem.available,
        "memory_percent": mem.percent,
        "swap_used": swap.used,
        "swap_total": swap.total,
        # storage
        "disk_total": disk.total,
        "disk_used": disk.used,
        "disk_percent": disk.percent,
        "nvme_temperature": _nvme_temp(),
        "disk_read": disk_read,
        "disk_write": disk_write,
        # network
        "net_upload": up,
        "net_download": down,
        "link_speed": _link_speed(),
        "ips": _ips(),
        "macs": _macs(),
        # system
        "uptime": time.time() - psutil.boot_time(),
        "model": _model(),
        "kernel": platform.release(),
    }
    frame.update(_power())
    frame["throttle"] = _throttle()
    return frame


class Metrics:
    """Holds the most recent snapshot and merges in hardware-reported extras.

    Hardware workers publish values such as the case-fan state into ``extras``
    so each web frame can include data that does not come from psutil.
    """

    def __init__(self) -> None:
        self.latest: dict[str, Any] = {}
        self.extras: dict[str, Any] = {}

    def collect(self) -> dict[str, Any]:
        self.latest = snapshot()
        self.latest.update(self.extras)
        return self.latest

    def set_extra(self, key: str, value: Any) -> None:
        self.extras[key] = value
        if self.latest:
            self.latest[key] = value
