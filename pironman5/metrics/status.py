"""System metrics. One Sample holds all values of one point in time."""

from __future__ import annotations

import platform
import socket
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any

import psutil

from .board import PiBoard

SLOW_INTERVAL = 10.0  # seconds between reads of slow-changing values


@dataclass(frozen=True)
class Sample:
    time: float = 0.0
    hostname: str = ""
    model: str | None = None
    kernel: str = ""
    uptime: float = 0.0
    # cpu
    cpu_percent: float = 0.0
    cpu_per_core: list[float] = field(default_factory=list)
    cpu_count: int | None = None
    cpu_freq: float | None = None
    cpu_temperature: float | None = None
    gpu_temperature: float | None = None
    cpu_voltage: float | None = None
    cpu_fan_rpm: int | None = None
    load_avg: list[float] | None = None
    processes: int = 0
    # memory
    memory_total: int = 0
    memory_used: int = 0
    memory_percent: float = 0.0
    swap_used: int = 0
    swap_total: int = 0
    # storage
    disk_total: int = 0
    disk_used: int = 0
    disk_percent: float = 0.0
    nvme_temperature: float | None = None
    disk_read: int | None = None
    disk_write: int | None = None
    # network, the interface of the default route is first
    net_upload: int = 0
    net_download: int = 0
    link_speed: int | None = None
    ips: dict[str, str] = field(default_factory=dict)
    macs: dict[str, str] = field(default_factory=dict)
    # power
    power_watts: float | None = None
    input_voltage: float | None = None
    battery_voltage: float | None = None
    throttle: dict[str, str] | None = None


def _route_ip() -> str | None:
    """Source address of the default route. This sends no packet."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return None


def _network() -> dict[str, Any]:
    ips: dict[str, str] = {}
    macs: dict[str, str] = {}
    for name, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                ips[name] = addr.address
            if addr.family == psutil.AF_LINK and addr.address.strip("0:"):
                macs[name] = addr.address
    primary = _route_ip()
    names = sorted(ips.keys() | macs.keys(), key=lambda n: (ips.get(n) != primary, n not in ips, n))
    stats = psutil.net_if_stats()
    speeds = [stats[n].speed for n in names if n in stats and stats[n].isup and stats[n].speed > 0]
    return {
        "ips": {n: ips[n] for n in names if n in ips},
        "macs": {n: macs[n] for n in names if n in macs},
        "link_speed": speeds[0] if speeds else None,
    }


class Sampler:
    def __init__(self, board: Any) -> None:
        self.board = board
        self.sample = Sample(
            hostname=socket.gethostname(),
            model=board.model,
            kernel=platform.release(),
            cpu_count=psutil.cpu_count(),
        )
        self._counters: tuple[Any, Any] | None = None
        self._counters_at = 0.0
        self._slow_at = -SLOW_INTERVAL

    def read(self) -> Sample:
        now = time.monotonic()
        fields = self._fast(now)
        if now - self._slow_at >= SLOW_INTERVAL:
            self._slow_at = now
            fields.update(self._slow())
        self.sample = replace(self.sample, **fields)
        return self.sample

    def _fast(self, now: float) -> dict[str, Any]:
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        freq = psutil.cpu_freq()
        cores = psutil.cpu_percent(percpu=True)
        try:
            load_avg = [round(x, 2) for x in psutil.getloadavg()]
        except (OSError, AttributeError):
            load_avg = None
        return {
            "time": time.time(),
            "uptime": time.time() - psutil.boot_time(),
            "cpu_percent": round(sum(cores) / max(1, len(cores)), 1),
            "cpu_per_core": cores,
            "cpu_freq": round(freq.current, 1) if freq else None,
            "cpu_temperature": self.board.temperature(),
            "cpu_fan_rpm": self.board.fan_rpm(),
            "load_avg": load_avg,
            "processes": len(psutil.pids()),
            "memory_total": mem.total,
            "memory_used": mem.total - mem.available,
            "memory_percent": mem.percent,
            "swap_used": swap.used,
            "swap_total": swap.total,
            **self._rates(now),
        }

    def _rates(self, now: float) -> dict[str, int]:
        """Network and disk throughput per second since the last call."""
        counters = (psutil.net_io_counters(), psutil.disk_io_counters())
        prev = self._counters or counters
        dt = max(1e-6, now - self._counters_at)
        self._counters, self._counters_at = counters, now

        def rate(cur: int, old: int) -> int:
            return max(0, round((cur - old) / dt))

        (net, disk), (old_net, old_disk) = counters, prev
        out = {
            "net_upload": rate(net.bytes_sent, old_net.bytes_sent),
            "net_download": rate(net.bytes_recv, old_net.bytes_recv),
        }
        if disk is not None and old_disk is not None:
            out["disk_read"] = rate(disk.read_bytes, old_disk.read_bytes)
            out["disk_write"] = rate(disk.write_bytes, old_disk.write_bytes)
        return out

    def _slow(self) -> dict[str, Any]:
        disk = psutil.disk_usage("/")
        return {
            "disk_total": disk.total,
            "disk_used": disk.used,
            "disk_percent": disk.percent,
            **_network(),
            **self.board.slow(),
        }


def snapshot() -> dict[str, Any]:
    """One metrics frame. Throughput values are zero on a first read."""
    return asdict(Sampler(PiBoard()).read())
