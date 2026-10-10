"""Board sensors. PiBoard reads sysfs and vcgencmd, MockBoard invents values."""

from __future__ import annotations

import math
import os
import re
import subprocess
import time
from typing import Any

import psutil

_THERMAL = "/sys/class/thermal/thermal_zone0/temp"
_FAN_HWMON = "/sys/devices/platform/cooling_fan/hwmon"


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


def _gpu_temperature() -> float | None:
    out = _vcgencmd("measure_temp")
    try:
        return round(float(out.split("=")[1].split("'")[0]), 1)
    except (AttributeError, ValueError, IndexError):
        return None


def _core_voltage() -> float | None:
    out = _vcgencmd("measure_volts", "core")
    try:
        return round(float(out.split("=")[1].rstrip("V")), 3)
    except (AttributeError, ValueError, IndexError):
        return None


def _power() -> dict[str, float | None]:
    """Board power and key voltages from the PMIC ADC."""
    cur: dict[str, float] = {}
    volt: dict[str, float] = {}
    for m in re.finditer(r"(\w+)_(A|V)\s+\w+\(\d+\)=([\d.]+)", _vcgencmd("pmic_read_adc") or ""):
        (cur if m.group(2) == "A" else volt)[m.group(1)] = float(m.group(3))
    watts = sum(cur[n] * volt[n] for n in cur if n in volt)
    return {
        "power_watts": round(watts, 2) if cur else None,
        "input_voltage": round(volt["EXT5V"], 2) if "EXT5V" in volt else None,
        "battery_voltage": round(volt["BATT"], 2) if "BATT" in volt else None,
    }


def _throttle() -> dict[str, str] | None:
    """Decode get_throttled into ok / now / past for each condition."""
    out = _vcgencmd("get_throttled")
    try:
        bits = int(out.split("=")[1], 16)
    except (AttributeError, ValueError, IndexError):
        return None

    def state(now_bit: int, past_bit: int) -> str:
        if bits & (1 << now_bit):
            return "now"
        return "past" if bits & (1 << past_bit) else "ok"

    return {
        "undervoltage": state(0, 16),
        "freq_capped": state(1, 17),
        "throttled": state(2, 18),
        "soft_temp": state(3, 19),
    }


def _nvme_temperature() -> float | None:
    try:
        temps = psutil.sensors_temperatures()
    except (AttributeError, OSError):
        return None
    for name, readings in temps.items():
        if "nvme" in name.lower() and readings:
            return round(readings[0].current, 1)
    return None


class PiBoard:
    def __init__(self) -> None:
        self.model = (_read("/proc/device-tree/model") or "").replace("\x00", "").strip() or None
        self._rpm_path: str | None = None

    def temperature(self) -> float | None:
        try:
            return round(int(_read(_THERMAL) or "") / 1000, 1)
        except ValueError:
            return None

    def fan_rpm(self) -> int | None:
        """Tachometer of the CPU tower fan."""
        try:
            if self._rpm_path is None:
                self._rpm_path = os.path.join(_FAN_HWMON, os.listdir(_FAN_HWMON)[0], "fan1_input")
            return int(_read(self._rpm_path) or "")
        except (OSError, IndexError, ValueError):
            return None

    def slow(self) -> dict[str, Any]:
        """Sample fields that start a process. Read these at a low rate."""
        return {
            "gpu_temperature": _gpu_temperature(),
            "cpu_voltage": _core_voltage(),
            "nvme_temperature": _nvme_temperature(),
            "throttle": _throttle(),
            **_power(),
        }


class MockBoard:
    """Plausible values that follow the load of the host."""

    model = "Raspberry Pi 5 Model B (mock)"

    def _load(self) -> float:
        return min(1.0, os.getloadavg()[0] / (os.cpu_count() or 1))

    def temperature(self) -> float | None:
        return round(42 + 25 * self._load() + 1.5 * math.sin(time.monotonic() / 20), 1)

    def fan_rpm(self) -> int | None:
        return round(1200 + 3000 * self._load())

    def slow(self) -> dict[str, Any]:
        return {
            "gpu_temperature": self.temperature(),
            "cpu_voltage": 0.88,
            "nvme_temperature": 33.0,
            "throttle": dict.fromkeys(("undervoltage", "freq_capped", "throttled", "soft_temp"), "ok"),
            "power_watts": round(3.2 + 6 * self._load(), 2),
            "input_voltage": 5.1,
            "battery_voltage": 3.05,
        }
