"""WS2812 strip over SPI through Adafruit NeoPixel_SPI.

frame() is the full lighting logic. The driver thread only shows its result.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from functools import partial
from typing import Any

from ..config import RGBConfig
from ..logger import get_logger

log = get_logger("rgb")

Color = tuple[int, int, int]
Frame = tuple[list[Color], float | None]  # colors, seconds to the next frame (None: static)

BLACK: Color = (0, 0, 0)
FRAME_TIME = 1 / 30


def _from_speed(cfg: RGBConfig, slow: float, fast: float) -> float:
    return slow + (fast - slow) * cfg.speed / 100


def _scaled(color: Color, k: float) -> Color:
    r, g, b = color
    return int(r * k), int(g * k), int(b * k)


def _rgb(value: str, cfg: RGBConfig) -> Color:
    """A hex color at the configured brightness."""
    v = value.strip().lstrip("#")
    return _scaled((int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)), cfg.brightness / 100)


def _hue(hue: float, cfg: RGBConfig) -> Color:
    """A fully saturated hue (degrees) at the configured brightness."""
    v = cfg.brightness / 100
    h = hue % 360 / 60
    rise, fall = v * (h % 1), v * (1 - h % 1)
    r, g, b = [(v, rise, 0), (fall, v, 0), (0, v, rise), (0, fall, v), (rise, 0, v), (v, 0, fall)][int(h)]
    return int(r * 255), int(g * 255), int(b * 255)


def _degrees(cfg: RGBConfig, t: float) -> float:
    return t * 2 / _from_speed(cfg, 0.1, 0.005)


def _solid(cfg: RGBConfig, t: float) -> Frame:
    return [_rgb(cfg.color, cfg)] * cfg.led_count, None


def _breathing(cfg: RGBConfig, t: float) -> Frame:
    phase = t / _from_speed(cfg, 10.0, 1.0) % 1
    return [_scaled(_rgb(cfg.color, cfg), 1 - abs(2 * phase - 1))] * cfg.led_count, FRAME_TIME


def _flow(cfg: RGBConfig, t: float, reverse: bool = False) -> Frame:
    step = _from_speed(cfg, 0.5, 0.1)
    lit = int(t / step) % cfg.led_count
    colors = [BLACK] * cfg.led_count
    colors[-1 - lit if reverse else lit] = _rgb(cfg.color, cfg)
    return colors, step - t % step


def _rainbow(cfg: RGBConfig, t: float, reverse: bool = False) -> Frame:
    n = cfg.led_count
    colors = [_hue(i * 360 / n + _degrees(cfg, t), cfg) for i in range(n)]
    return colors[::-1] if reverse else colors, FRAME_TIME


def _hue_cycle(cfg: RGBConfig, t: float) -> Frame:
    return [_hue(_degrees(cfg, t), cfg)] * cfg.led_count, FRAME_TIME


EFFECTS: dict[str, Callable[[RGBConfig, float], Frame]] = {
    "solid": _solid,
    "breathing": _breathing,
    "flow": _flow,
    "flow_reverse": partial(_flow, reverse=True),
    "rainbow": _rainbow,
    "rainbow_reverse": partial(_rainbow, reverse=True),
    "hue_cycle": _hue_cycle,
}


def frame(cfg: RGBConfig, t: float) -> Frame:
    """The LED colors at time t."""
    if not cfg.enable:
        return [BLACK] * cfg.led_count, None
    if not cfg.sync:
        return [_rgb(color, cfg) for color in cfg.colors], None
    return EFFECTS[cfg.style](cfg, t)


class NullStrip:
    def __setitem__(self, index: int, color: Color) -> None:
        pass

    def show(self) -> None:
        pass


def open_strip(count: int, mock: bool) -> Any:
    if mock:
        return NullStrip()
    try:
        import board
        import neopixel_spi as neopixel

        strip = neopixel.NeoPixel_SPI(board.SPI(), count, pixel_order=neopixel.GRB, auto_write=False)
        strip.fill(0)
        strip.show()
        log.info("WS2812 strip opened (%d LEDs over SPI)", count)
        return strip
    except Exception as exc:  # noqa: BLE001, the Adafruit stack raises its own exception types
        log.warning("WS2812 unavailable (%s) - LEDs have no effect", exc)
        return NullStrip()


class RGBDriver:
    def __init__(self, cfg: RGBConfig, mock: bool = False) -> None:
        self.cfg = cfg
        self.colors: list[Color] = [BLACK] * cfg.led_count
        self._mock = mock
        self._strip = open_strip(cfg.led_count, mock)
        self._wake = threading.Event()
        self._running = True
        self._thread = threading.Thread(target=self._run, name="rgb", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def apply(self, cfg: RGBConfig) -> None:
        self.cfg = cfg
        self._wake.set()

    def _show(self, colors: list[Color]) -> None:
        if colors == self.colors:
            return
        if len(colors) != len(self.colors):
            self._show([BLACK] * len(self.colors))
            self._strip = open_strip(len(colors), self._mock)
        for i, color in enumerate(colors):
            self._strip[i] = color
        self._strip.show()
        self.colors = colors

    def _run(self) -> None:
        """Thread body. A static frame waits for the next config change."""
        log.info("rgb driver started (style=%s)", self.cfg.style)
        while self._running:
            try:
                colors, delay = frame(self.cfg, time.monotonic())
                self._show(colors)
            except Exception:  # keep the thread alive, the SPI backend raises its own types
                log.exception("rgb frame failed")
                delay = 1.0
            self._wake.wait(delay)
            # Clear after the wait. A clear before it can lose the wake of stop().
            self._wake.clear()

    def stop(self) -> None:
        self._running = False
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join()
        self._show([BLACK] * len(self.colors))
