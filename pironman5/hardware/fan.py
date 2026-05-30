"""Fan control and monitoring.

Two distinct fans live on this board:

* The **case fans** are simple on/off DC fans switched on GPIO pin 6. They have
  no tachometer, so only their commanded state is known. Control modes:
  ``off`` (forced off), ``on`` (forced on), ``auto`` (on above ``on_temp``, off
  once a few degrees below it).
* The **CPU tower fan** is a 4-wire PWM fan on the Pi's fan header, managed by
  the firmware thermal governor. We only read its RPM from sysfs.

Publishes ``case_fan_on`` and ``cpu_fan_rpm`` into metrics extras each tick.
"""

from __future__ import annotations

import asyncio
import os

from ..logger import get_logger
from .gpio import OutputPin

log = get_logger("fan")

# sysfs path for the CPU tower fan's tachometer (RPM read-only).
_FAN_HWMON_DIR = "/sys/devices/platform/cooling_fan/hwmon"
# In auto mode the fan switches off a few degrees below on_temp to avoid
# chattering around the threshold.
_HYSTERESIS = 4.0


class FanDriver:
    def __init__(self, config, metrics, mock: bool = False) -> None:
        self.cfg = config              # FanConfig
        self.metrics = metrics
        self.mock = mock
        self.running = False
        self._on = False
        self._pin = OutputPin(self.cfg.gpio_pin, mock=mock)

    def apply_config(self) -> None:
        """Re-read config; handle a pin change live."""
        if self._pin.pin != self.cfg.gpio_pin:
            log.info("fan pin changed %d -> %d", self._pin.pin, self.cfg.gpio_pin)
            self._pin.change_pin(self.cfg.gpio_pin)
        # Force states take effect on the next tick; nothing else to do.

    def _read_rpm(self) -> int | None:
        if self.mock:
            return 3120 if self._on else 0
        try:
            hwmon = os.listdir(_FAN_HWMON_DIR)
            path = os.path.join(_FAN_HWMON_DIR, hwmon[0], "fan1_input")
            with open(path) as f:
                return int(f.read())
        except Exception:
            return None

    def _decide(self, temp: float | None) -> bool:
        mode = self.cfg.mode
        if mode == "on":
            return True
        if mode == "off":
            return False
        # auto: on above on_temp, off once a few degrees below it
        if temp is None:
            return self._on  # no reading - hold state
        if self._on:
            return temp > self.cfg.on_temp - _HYSTERESIS
        return temp >= self.cfg.on_temp

    def _set(self, on: bool) -> None:
        if on != self._on:
            log.info("fan %s", "ON" if on else "OFF")
        self._on = on
        self._pin.set(on)

    async def run(self) -> None:
        self.running = True
        log.info("fan driver started (mode=%s, mock=%s)", self.cfg.mode, self.mock)
        while self.running:
            temp = (self.metrics.latest or {}).get("cpu_temperature")
            self._set(self._decide(temp))
            self.metrics.set_extra("case_fan_on", self._on)        # GPIO on/off fans
            self.metrics.set_extra("cpu_fan_rpm", self._read_rpm())  # PWM tower fan tach
            await asyncio.sleep(1.0)

    def stop(self) -> None:
        self.running = False
        self._set(False)
        self._pin.close()
