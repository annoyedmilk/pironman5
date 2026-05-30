"""WS2812 RGB strip over SPI (``/dev/spidev0.0``) using Adafruit NeoPixel_SPI.

Implements a handful of lighting effects (solid, breathing, flow, rainbow, hue
cycle). The current per-LED colours are exposed via ``led_colors`` so the web UI
can render a live preview, which also makes the effects testable in mock mode.
"""

from __future__ import annotations

import asyncio

from ..config import RGB_STYLES
from ..logger import get_logger

log = get_logger("rgb")


def _map(x: float, in_min: float, in_max: float, out_min: float, out_max: float) -> float:
    return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.strip().lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def _hsl_to_rgb(hue: float, saturation: float = 1.0, brightness: float = 1.0) -> tuple[int, int, int]:
    hue %= 360
    hi = int((hue / 60) % 6)
    f = hue / 60.0 - hi
    p = brightness * (1 - saturation)
    q = brightness * (1 - f * saturation)
    t = brightness * (1 - (1 - f) * saturation)
    r, g, b = [
        (brightness, t, p),
        (q, brightness, p),
        (p, brightness, t),
        (p, q, brightness),
        (t, p, brightness),
        (brightness, p, q),
    ][hi]
    return int(r * 255), int(g * 255), int(b * 255)


class _MockStrip:
    """Stands in for NeoPixel_SPI off the Pi - just holds pixel state."""

    def __init__(self, count: int) -> None:
        self.pixels = [(0, 0, 0)] * count

    def __setitem__(self, i: int, color) -> None:
        self.pixels[i] = tuple(color)

    def fill(self, color) -> None:
        self.pixels = [tuple(color)] * len(self.pixels)

    def show(self) -> None:
        pass


class RGBDriver:
    def __init__(self, config, mock: bool = False) -> None:
        self.cfg = config              # RGBConfig
        self.mock = mock
        self.running = False
        self.led_colors: list[list[int]] = [[0, 0, 0]] * config.led_count
        self._counter = 0
        self._strip = None
        self._strip = self._open()

    def _open(self):
        if self.mock:
            return _MockStrip(self.cfg.led_count)
        try:
            import board  # type: ignore
            import neopixel_spi as neopixel  # type: ignore

            strip = neopixel.NeoPixel_SPI(
                board.SPI(),
                self.cfg.led_count,
                pixel_order=neopixel.GRB,
                auto_write=False,
            )
            strip.fill(0)
            strip.show()
            log.info("WS2812 strip opened (%d LEDs over SPI)", self.cfg.led_count)
            return strip
        except Exception as exc:
            log.warning("WS2812 unavailable (%s) - running mock", exc)
            self.mock = True
            return _MockStrip(self.cfg.led_count)

    def apply_config(self) -> None:
        # A LED-count change requires re-opening the strip.
        if len(self.led_colors) != self.cfg.led_count:
            self.stop_strip()
            self.led_colors = [[0, 0, 0]] * self.cfg.led_count
            self._strip = self._open()

    # ---- low-level helpers -------------------------------------------------

    def _push(self, colors: list[tuple[int, int, int]]) -> None:
        for i, c in enumerate(colors):
            self._strip[i] = c
            self.led_colors[i] = list(c)
        self._strip.show()

    def _fill(self, color: tuple[int, int, int]) -> None:
        self._strip.fill(color)
        self.led_colors = [list(color)] * self.cfg.led_count
        self._strip.show()

    def _scaled(self) -> tuple[int, int, int]:
        r, g, b = _hex_to_rgb(self.cfg.color)
        k = self.cfg.brightness * 0.01
        return int(r * k), int(g * k), int(b * k)

    def _scaled_list(self) -> list[tuple[int, int, int]]:
        """Per-LED colors scaled by brightness (used when sync is off)."""
        k = self.cfg.brightness * 0.01
        out = []
        for i in range(self.cfg.led_count):
            hexv = self.cfg.colors[i] if i < len(self.cfg.colors) else self.cfg.color
            r, g, b = _hex_to_rgb(hexv)
            out.append((int(r * k), int(g * k), int(b * k)))
        return out

    # ---- effects (return the sleep delay for the next frame) ---------------

    async def _solid(self) -> None:
        self._fill(self._scaled())
        await asyncio.sleep(0.5)

    async def _breathing(self) -> None:
        period = 200
        self._counter %= period
        delay = _map(self.cfg.speed, 0, 100, 0.05, 0.005)
        base = self._scaled()
        i = self._counter if self._counter < 100 else period - self._counter
        self._fill(tuple(int(x * i * 0.01) for x in base))
        self._counter += 1
        await asyncio.sleep(delay)

    async def _flow(self, reverse: bool = False) -> None:
        n = self.cfg.led_count
        self._counter %= n
        delay = _map(self.cfg.speed, 0, 100, 0.5, 0.1)
        colors = [(0, 0, 0)] * n
        idx = (n - 1 - self._counter) if reverse else self._counter
        colors[idx] = self._scaled()
        self._push(colors)
        self._counter += 1
        await asyncio.sleep(delay)

    async def _flow_reverse(self) -> None:
        await self._flow(reverse=True)

    async def _rainbow(self, reverse: bool = False) -> None:
        n = self.cfg.led_count
        self._counter %= 360
        delay = _map(self.cfg.speed, 0, 100, 0.1, 0.005)
        order = list(range(n))[:: -1 if reverse else 1]
        colors = [(0, 0, 0)] * n
        for i, led in enumerate(order):
            hue = i * 360.0 / n + self._counter
            colors[led] = _hsl_to_rgb(hue, 1, self.cfg.brightness * 0.01)
        self._push(colors)
        self._counter += 2
        await asyncio.sleep(delay)

    async def _rainbow_reverse(self) -> None:
        await self._rainbow(reverse=True)

    async def _hue_cycle(self) -> None:
        self._counter %= 360
        delay = _map(self.cfg.speed, 0, 100, 0.1, 0.005)
        self._fill(_hsl_to_rgb(self._counter, 1, self.cfg.brightness * 0.01))
        self._counter += 2
        await asyncio.sleep(delay)

    # ---- main loop ---------------------------------------------------------

    async def run(self) -> None:
        self.running = True
        log.info("rgb driver started (style=%s, mock=%s)", self.cfg.style, self.mock)
        while self.running:
            if not self.cfg.enable:
                self._fill((0, 0, 0))
                await asyncio.sleep(0.5)
                continue
            if not self.cfg.sync:
                # Per-LED static colors; animated styles only apply when synced.
                self._push(self._scaled_list())
                await asyncio.sleep(0.2)
                continue
            style = self.cfg.style if self.cfg.style in RGB_STYLES else "solid"
            try:
                await getattr(self, f"_{style}")()
            except Exception:
                log.exception("rgb effect %s failed", style)
                await asyncio.sleep(1.0)

    def stop_strip(self) -> None:
        try:
            self._fill((0, 0, 0))
        except Exception:  # pragma: no cover
            pass

    def stop(self) -> None:
        self.running = False
        self.stop_strip()
