"""System metrics. One Sample holds all values of one point in time."""

from __future__ import annotations

import platform
import socket
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any

import psutil

from .board import Interface, PiBoard

SLOW_INTERVAL = 10.0  # seconds between reads of slow-changing values


@dataclass(frozen=True)
class Sample:
    time: float = 0.0
    hostname: str = ""
    model: str | None = None
    os: str = ""
    kernel: str = ""
    firmware: str | None = None  # build date of the bootloader
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
    cpu_fan_percent: int | None = None
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
    interfaces: list[Interface] = field(default_factory=list)
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


def _os_name() -> str:
    try:
        return platform.freedesktop_os_release()["PRETTY_NAME"]
    except (OSError, KeyError):
        return platform.system()


class Sampler:
    def __init__(self, board: Any) -> None:
        self.board = board
        self.sample = Sample(
            hostname=socket.gethostname(),
            model=board.model,
            os=_os_name(),
            kernel=platform.release(),
            firmware=board.firmware,
            cpu_count=psutil.cpu_count(),
        )
        self._links: list[Interface] = []
        self._counters: tuple[Any, Any] | None = None
        self._counters_at = 0.0
        self._slow_at = -SLOW_INTERVAL

    def read(self) -> Sample:
        now = time.monotonic()
        fields: dict[str, Any] = {}
        if now - self._slow_at >= SLOW_INTERVAL:
            self._slow_at = now
            fields = self._slow()
        self.sample = replace(self.sample, **fields, **self._fast(now))
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
            "cpu_fan_percent": self.board.fan_percent(),
            "load_avg": load_avg,
            "processes": len(psutil.pids()),
            "memory_total": mem.total,
            "memory_used": mem.total - mem.available,
            "memory_percent": mem.percent,
            "swap_used": swap.used,
            "swap_total": swap.total,
            **self._rates(now),
        }

    def _rates(self, now: float) -> dict[str, Any]:
        """Network and disk throughput per second since the last call."""
        counters = (psutil.net_io_counters(pernic=True), psutil.disk_io_counters())
        prev = self._counters or counters
        dt = max(1e-6, now - self._counters_at)
        self._counters, self._counters_at = counters, now

        def rate(cur: int, old: int) -> int:
            return max(0, round((cur - old) / dt))

        def flow(link: Interface) -> Interface:
            cur, old = nets.get(link.name), old_nets.get(link.name)
            if cur is None or old is None:
                return link
            return replace(
                link,
                download=rate(cur.bytes_recv, old.bytes_recv),
                upload=rate(cur.bytes_sent, old.bytes_sent),
            )

        (nets, disk), (old_nets, old_disk) = counters, prev
        out: dict[str, Any] = {"interfaces": [flow(link) for link in self._links]}
        if disk is not None and old_disk is not None:
            out["disk_read"] = rate(disk.read_bytes, old_disk.read_bytes)
            out["disk_write"] = rate(disk.write_bytes, old_disk.write_bytes)
        return out

    def _slow(self) -> dict[str, Any]:
        disk = psutil.disk_usage("/")
        primary = _route_ip()
        self._links = sorted(self.board.interfaces(), key=lambda link: (link.ip != primary, not link.up))
        return {
            "disk_total": disk.total,
            "disk_used": disk.used,
            "disk_percent": disk.percent,
            **self.board.slow(),
        }


def snapshot() -> dict[str, Any]:
    """One metrics frame. Throughput values are zero on a first read."""
    return asdict(Sampler(PiBoard()).read())
