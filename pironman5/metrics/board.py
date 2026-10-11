"""Board sensors. PiBoard reads sysfs and vcgencmd, MockBoard invents values."""

from __future__ import annotations

import fcntl
import math
import os
import re
import socket
import struct
import subprocess
import time
from dataclasses import dataclass, replace
from typing import Any

import psutil

_THERMAL = "/sys/class/thermal/thermal_zone0/temp"
_FAN_HWMON = "/sys/devices/platform/cooling_fan/hwmon"
_NET = "/sys/class/net"
_BOOTLOADER = "/proc/device-tree/chosen/bootloader/build-timestamp"
_SIOCGIWRATE = 0x8B21  # wireless extensions ioctl: bit rate


@dataclass(frozen=True)
class Interface:
    name: str
    wireless: bool = False
    up: bool = False  # the link has a carrier
    ip: str | None = None
    mac: str | None = None
    speed: int | None = None  # Mbit/s
    signal: int | None = None  # dBm
    download: int = 0  # bytes per second
    upload: int = 0


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


def _bootloader_date() -> str | None:
    """Build date of the bootloader in the EEPROM."""
    try:
        with open(_BOOTLOADER, "rb") as f:
            return time.strftime("%Y-%m-%d", time.gmtime(int.from_bytes(f.read(4), "big")))
    except OSError:
        return None


def _signal(name: str) -> int | None:
    """Signal level in dBm. A line of /proc/net/wireless is: name, status, link quality, level."""
    for line in (_read("/proc/net/wireless") or "").splitlines():
        fields = line.split()
        if fields[:1] == [name + ":"]:
            return int(float(fields[3]))
    return None


def _wifi(name: str) -> dict[str, Any]:
    """Bit rate and signal level of a wireless interface."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            reply = fcntl.ioctl(s, _SIOCGIWRATE, struct.pack("16s16x", name.encode()))
    except OSError:
        return {}
    # The reply is the interface name, then the rate in bit/s.
    return {"speed": round(struct.unpack_from("i", reply, 16)[0] / 1e6) or None, "signal": _signal(name)}


def _interface(name: str, addrs: dict[str, Any]) -> Interface:
    path = os.path.join(_NET, name)
    up = _read(os.path.join(path, "carrier")) == "1"
    wireless = os.path.isdir(os.path.join(path, "wireless"))
    ips = [a.address for a in addrs.get(name, ()) if a.family == socket.AF_INET]
    link: dict[str, Any] = {}
    if up and wireless:
        link = _wifi(name)
    elif up:
        speed = int(_read(os.path.join(path, "speed")) or 0)
        link = {"speed": speed if speed > 0 else None}
    return Interface(
        name, wireless, up, ips[0] if ips else None, _read(os.path.join(path, "address")), **link
    )


class PiBoard:
    def __init__(self) -> None:
        self.model = (_read("/proc/device-tree/model") or "").replace("\x00", "").strip() or None
        self.firmware = _bootloader_date()
        self._fan_dir: str | None = None

    def temperature(self) -> float | None:
        try:
            return round(int(_read(_THERMAL) or "") / 1000, 1)
        except ValueError:
            return None

    def _fan(self, name: str) -> int | None:
        """One value of the CPU tower fan."""
        try:
            if self._fan_dir is None:
                self._fan_dir = os.path.join(_FAN_HWMON, os.listdir(_FAN_HWMON)[0])
            return int(_read(os.path.join(self._fan_dir, name)) or "")
        except (OSError, IndexError, ValueError):
            return None

    def fan_rpm(self) -> int | None:
        return self._fan("fan1_input")

    def fan_percent(self) -> int | None:
        """PWM duty that the firmware set."""
        pwm = self._fan("pwm1")
        return None if pwm is None else round(pwm * 100 / 255)

    def interfaces(self) -> list[Interface]:
        """The network interfaces that have a device, without throughput."""
        try:
            names = sorted(n for n in os.listdir(_NET) if os.path.exists(os.path.join(_NET, n, "device")))
        except OSError:
            return []
        addrs = psutil.net_if_addrs()
        return [_interface(name, addrs) for name in names]

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
    firmware = "2025-01-01"

    def _load(self) -> float:
        return min(1.0, os.getloadavg()[0] / (os.cpu_count() or 1))

    def temperature(self) -> float | None:
        return round(42 + 25 * self._load() + 1.5 * math.sin(time.monotonic() / 20), 1)

    def fan_rpm(self) -> int | None:
        return round(1200 + 3000 * self._load())

    def fan_percent(self) -> int | None:
        return round(30 + 70 * self._load())

    def interfaces(self) -> list[Interface]:
        wifi = Interface("wlan0", True, True, "192.168.1.20", "d8:3a:dd:00:00:02", 433, -57)
        flow = round(2e6 * self._load())
        return [Interface("eth0", mac="d8:3a:dd:00:00:01"), replace(wifi, download=flow, upload=flow // 8)]

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
