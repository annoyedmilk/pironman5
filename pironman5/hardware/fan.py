"""Case fans: on/off DC fans on one GPIO pin, no tachometer.

The CPU tower fan is not controlled here. The firmware controls it.
"""

from __future__ import annotations

from ..config import FanConfig
from ..logger import get_logger
from .gpio import open_pin

log = get_logger("fan")

HYSTERESIS = 4.0  # degrees below on_temp at which auto mode switches off


def decide(cfg: FanConfig, on: bool, temp: float | None) -> bool:
    """The new fan state. Without a reading, auto mode holds the state."""
    if cfg.mode != "auto":
        return cfg.mode == "on"
    if temp is None:
        return on
    return temp > cfg.on_temp - HYSTERESIS if on else temp >= cfg.on_temp


class FanDriver:
    def __init__(self, cfg: FanConfig, mock: bool = False) -> None:
        self.on = False
        self._mock = mock
        self._pin = open_pin(cfg.gpio_pin, mock)

    def update(self, cfg: FanConfig, temp: float | None) -> None:
        if cfg.gpio_pin != self._pin.number:
            log.info("fan pin changed %d -> %d", self._pin.number, cfg.gpio_pin)
            self.stop()
            self._pin = open_pin(cfg.gpio_pin, self._mock)
        on = decide(cfg, self.on, temp)
        if on != self.on:
            log.info("fan %s", "ON" if on else "OFF")
        self.on = on
        self._pin.set(on)

    def stop(self) -> None:
        self.on = False
        self._pin.set(False)
        self._pin.close()
