"""0.96" SSD1306 OLED, 128x64, I2C address 0x3C on bus 1.

draw() makes the picture. The driver sends only the pages that changed.
"""

from __future__ import annotations

import base64
import time
from typing import Any

from ..config import Config
from ..logger import get_logger
from ..metrics import Sample
from .font import LARGE, SMALL

log = get_logger("oled")

WIDTH = 128
HEIGHT = 64
PAGES = HEIGHT // 8
ADDR = 0x3C

_INIT = (
    0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40, 0x8D, 0x14, 0x20, 0x00,
    0xDA, 0x12, 0x81, 0xCF, 0xD9, 0xF1, 0xDB, 0x40, 0xA4, 0xA6, 0xAF,
)  # fmt: skip
_CONTRAST = 0xCF  # contrast register value at 100 % brightness
# Segment remap and COM scan direction for each rotation.
_ROTATE = {0: (0xA1, 0xC8), 180: (0xA0, 0xC0)}

_RIGHT = 64  # left edge of the bar column
_EDGE = WIDTH - 1  # the last column and row stay free for the burn-in shift
_ALERTS = (("undervoltage", "UNDERVOLTAGE"), ("throttled", "THROTTLED"), ("soft_temp", "TEMP LIMIT"))


class Canvas:
    """A 128x64 picture. One int for each column, bit n is row n."""

    def __init__(self) -> None:
        self.cols = [0] * WIDTH

    def blit(self, x: int, y: int, columns: list[int]) -> int:
        for i, column in enumerate(columns[: max(0, WIDTH - x)]):
            self.cols[x + i] |= column << y
        return x + len(columns)

    def text(self, x: int, y: int, text: str, font: dict[str, list[int]] = SMALL, gap: int = 1) -> int:
        """Draw text and return the x position after it."""
        for char in text:
            x = self.blit(x, y, font.get(char, font["-"])) + gap
        return x

    def text_right(self, right: int, y: int, text: str) -> None:
        """Draw text that ends at column right."""
        self.text(right - sum(len(SMALL.get(c, SMALL["-"])) + 1 for c in text) + 2, y, text)

    def rect(self, x: int, y: int, w: int, h: int) -> None:
        self.blit(x, y, [(1 << h) - 1] * w)

    def invert(self, x: int, y: int, w: int, h: int) -> None:
        for i in range(x, x + w):
            self.cols[i] ^= ((1 << h) - 1) << y

    def pages(self, dx: int = 0, dy: int = 0) -> bytes:
        """The picture in SSD1306 page order, moved right and down by dx, dy."""
        cols = [0] * dx + [column << dy for column in self.cols[: WIDTH - dx]]
        return b"".join(bytes(column >> 8 * page & 0xFF for column in cols) for page in range(PAGES))


def _temperature(sample: Sample, cfg: Config) -> tuple[str, str]:
    unit = cfg.system.temperature_unit
    temp = sample.cpu_temperature
    if temp is None:
        return "--", "\N{DEGREE SIGN}" + unit
    return f"{temp * 9 / 5 + 32 if unit == 'F' else temp:.0f}", "\N{DEGREE SIGN}" + unit


def _status(sample: Sample) -> tuple[str, bool]:
    """The text of the status line, and whether it is an alert."""
    throttle = sample.throttle or {}
    for key, text in _ALERTS:
        if throttle.get(key) == "now":
            return text, True
    return next(iter(sample.ips.values()), "NO NETWORK"), False


def draw(sample: Sample, cfg: Config, fan_on: bool) -> Canvas:
    canvas = Canvas()
    value, unit = _temperature(sample, cfg)
    canvas.text(canvas.text(0, 0, value, LARGE, gap=2), 0, unit)
    if sample.power_watts is not None:
        canvas.text(0, 26, f"{sample.power_watts:.1f}W")
    canvas.text(0, 38, "FAN ON" if fan_on else "FAN OFF")

    meters = (("CPU", sample.cpu_percent), ("MEM", sample.memory_percent), ("DISK", sample.disk_percent))
    for row, (label, percent) in enumerate(meters):
        y = row * 16
        canvas.text(_RIGHT, y, label)
        canvas.text_right(_EDGE - 1, y, f"{percent:.0f}%")
        canvas.rect(_RIGHT, y + 10, _EDGE - _RIGHT, 1)
        canvas.rect(_RIGHT, y + 9, round((_EDGE - _RIGHT) * min(100, percent) / 100), 3)

    text, alert = _status(sample)
    canvas.text(1, 54, text.upper())
    if alert:
        canvas.invert(0, 52, _EDGE, 11)
    return canvas


def lines(sample: Sample, cfg: Config) -> list[str]:
    """The display content as text, for API clients."""
    value, unit = _temperature(sample, cfg)
    power = f"   {sample.power_watts:.1f}W" if sample.power_watts is not None else ""
    return [
        f"CPU {sample.cpu_percent:.0f}%   {value}{unit[1:]}",
        f"MEM {sample.memory_percent:.0f}%{power}",
        f"DISK {sample.disk_percent:.0f}%",
        _status(sample)[0],
    ]


class Panel:
    def __init__(self) -> None:
        from smbus2 import SMBus

        self._bus = SMBus(1)
        self._cmd(*_INIT)

    def _cmd(self, *commands: int) -> None:
        for command in commands:
            self._bus.write_byte_data(ADDR, 0x00, command)

    def power(self, on: bool) -> None:
        self._cmd(0xAF if on else 0xAE)

    def rotate(self, rotation: int) -> None:
        self._cmd(*_ROTATE[rotation])

    def contrast(self, level: int) -> None:
        self._cmd(0x81, level)

    def write(self, page: int, data: bytes) -> None:
        self._cmd(0x21, 0, WIDTH - 1, 0x22, page, page)
        for i in range(0, WIDTH, 32):
            self._bus.write_i2c_block_data(ADDR, 0x40, list(data[i : i + 32]))

    def close(self) -> None:
        self.power(False)
        self._bus.close()


class NullPanel:
    def power(self, on: bool) -> None:
        pass

    def rotate(self, rotation: int) -> None:
        pass

    def contrast(self, level: int) -> None:
        pass

    def write(self, page: int, data: bytes) -> None:
        pass

    def close(self) -> None:
        pass


def open_panel(mock: bool) -> Any:
    if mock:
        return NullPanel()
    try:
        panel = Panel()
        log.info("OLED opened (SSD1306 @0x%02X)", ADDR)
        return panel
    except (ImportError, OSError) as exc:
        log.warning("OLED unavailable (%s) - display has no effect", exc)
        return NullPanel()


def _encode(canvas: Canvas) -> str:
    return base64.b64encode(canvas.pages()).decode()


class OLEDDriver:
    def __init__(self, mock: bool = False) -> None:
        self._canvas = Canvas()
        self.preview: dict[str, Any] = {"lines": [], "awake": True, "fb": _encode(self._canvas)}
        self._panel = open_panel(mock)
        self._wake_at = time.monotonic()
        self._awake: bool | None = None
        self._rotation: int | None = None
        self._brightness: int | None = None
        self._shown: list[bytes | None] = [None] * PAGES

    def wake(self) -> None:
        self._wake_at = time.monotonic()

    def draw(self, cfg: Config, sample: Sample, fan_on: bool) -> bool:
        """Make the next picture. Return False when the panel must sleep."""
        idle = time.monotonic() - self._wake_at
        awake = cfg.oled.enable and (cfg.oled.sleep_timeout == 0 or idle <= cfg.oled.sleep_timeout)
        self._canvas = draw(sample, cfg, fan_on) if awake else Canvas()
        self.preview = {
            "lines": lines(sample, cfg) if awake else [],
            "awake": awake,
            "fb": _encode(self._canvas),
        }
        return awake

    def flush(self, cfg: Config, awake: bool) -> None:
        """Bring the panel to the state of the last draw()."""
        if awake != self._awake:
            self._panel.power(awake)
            self._awake = awake
        if not awake:
            return
        if cfg.oled.brightness != self._brightness:
            self._panel.contrast(round(_CONTRAST * cfg.oled.brightness / 100))
            self._brightness = cfg.oled.brightness
        if cfg.oled.rotation != self._rotation:
            self._panel.rotate(cfg.oled.rotation)
            self._rotation = cfg.oled.rotation
            self._shown = [None] * PAGES  # the remap applies only to new data
        shift = int(time.monotonic() // 60) % 4  # move the picture each minute against burn-in
        data = self._canvas.pages(shift & 1, shift >> 1)
        for page in range(PAGES):
            chunk = data[page * WIDTH : (page + 1) * WIDTH]
            if chunk != self._shown[page]:
                self._panel.write(page, chunk)
                self._shown[page] = chunk

    def stop(self) -> None:
        self._panel.close()
