"""0.96" SSD1306 OLED, 128x64, I2C address 0x3C on bus 1.

draw() makes the picture. The driver sends only the columns that changed.
"""

from __future__ import annotations

import base64
import time
from typing import Any

from ..config import Config
from ..logger import get_logger
from ..metrics import Sample
from .font import BOLT, FAN, LARGE, SMALL, WIRED, bars

log = get_logger("oled")

WIDTH = 128
HEIGHT = 64
PAGES = HEIGHT // 8
_ROWS = (1 << HEIGHT) - 1
ADDR = 0x3C

_INIT = (
    0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40, 0x8D, 0x14, 0x20, 0x00,
    0xDA, 0x12, 0x81, 0xCF, 0xD9, 0xF1, 0xDB, 0x40, 0xA4, 0xA6, 0xAF,
)  # fmt: skip
_CONTRAST = 0xCF  # contrast register value at 100 % brightness
# Segment remap and COM scan direction for each rotation.
_ROTATE = {0: (0xA1, 0xC8), 180: (0xA0, 0xC0)}

# Layout. The last column and row stay free for the burn-in shift.
_RIGHT = WIDTH - 2  # right edge of the text
_ICON = 87  # icon column of the header
_BAR = (27, 71)  # left edge and width of the meter bars
_METERS = 23  # top row of the first meter
_FOOTER = 55  # top row of the status line
_CLOCK = _RIGHT - 28  # left edge of the time
_SIGNAL = (-85, -75, -65, -55)  # dBm at which a signal bar goes on
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

    def meter(self, x: int, y: int, w: int, percent: float) -> None:
        """A 5 px bar with round ends on a dotted track."""
        self.blit(x, y + 2, [1, 0] * (w // 2) + [1])
        fill = round(w * min(100.0, max(0.0, percent)) / 100)
        self.rect(x, y + 1, fill, 3)
        self.rect(x + 1, y, fill - 2, 5)

    def invert(self, x: int, y: int, w: int, h: int) -> None:
        for i in range(x, x + w):
            self.cols[i] ^= ((1 << h) - 1) << y

    def pages(self, dx: int = 0, dy: int = 0) -> bytes:
        """The picture in SSD1306 page order, moved right and down by dx, dy."""
        cols = [0] * dx + [column << dy & _ROWS for column in self.cols[: WIDTH - dx]]
        data = b"".join(column.to_bytes(PAGES, "little") for column in cols)
        return b"".join(data[page::PAGES] for page in range(PAGES))


def _temperature(sample: Sample, cfg: Config) -> tuple[str, str]:
    unit = cfg.system.temperature_unit
    temp = sample.cpu_temperature
    if temp is None:
        return "--", "\N{DEGREE SIGN}" + unit
    return f"{temp * 9 / 5 + 32 if unit == 'F' else temp:.0f}", "\N{DEGREE SIGN}" + unit


def _alert(sample: Sample) -> str | None:
    throttle = sample.throttle or {}
    return next((text for key, text in _ALERTS if throttle.get(key) == "now"), None)


def _footer(canvas: Canvas, sample: Sample) -> None:
    """Status line: the alert, or the first interface with an address and the time."""
    alert = _alert(sample)
    if alert:
        canvas.text((WIDTH - 6 * len(alert)) // 2, _FOOTER, alert)
        canvas.invert(0, _FOOTER - 2, WIDTH - 1, 10)
        return
    link = next((link for link in sample.interfaces if link.ip), None)
    if link is None:
        end = canvas.text(0, _FOOTER, "NO NETWORK")
    else:
        level = sum((link.signal or -100) >= dbm for dbm in _SIGNAL)
        canvas.blit(0, _FOOTER, bars(level) if link.wireless else WIRED)
        end = canvas.text(14, _FOOTER, link.ip)
    if end < _CLOCK:  # a long address leaves no room
        canvas.text(_CLOCK, _FOOTER, time.strftime("%H:%M", time.localtime(sample.time)))


def draw(sample: Sample, cfg: Config, fan_on: bool) -> Canvas:
    canvas = Canvas()
    value, unit = _temperature(sample, cfg)
    canvas.text(canvas.text(0, 0, value, LARGE, gap=2), 0, unit)
    if sample.power_watts is not None:
        canvas.blit(_ICON + 1, 0, BOLT)
        canvas.text_right(_RIGHT, 0, f"{sample.power_watts:.1f}W")
    canvas.blit(_ICON, 11, FAN)
    canvas.text_right(_RIGHT, 11, "ON" if fan_on else "OFF")

    meters = (("CPU", sample.cpu_percent), ("MEM", sample.memory_percent), ("DISK", sample.disk_percent))
    for row, (label, percent) in enumerate(meters):
        y = _METERS + 10 * row
        canvas.text(0, y, label)
        canvas.meter(_BAR[0], y + 1, _BAR[1], percent)
        canvas.text_right(_RIGHT, y, f"{percent:.0f}%")

    _footer(canvas, sample)
    return canvas


class Panel:
    def __init__(self) -> None:
        from smbus2 import SMBus

        self._bus = SMBus(1)
        self._cmd(*_INIT)

    def _cmd(self, *commands: int) -> None:
        self._bus.write_i2c_block_data(ADDR, 0x00, list(commands))

    def power(self, on: bool) -> None:
        self._cmd(0xAF if on else 0xAE)

    def rotate(self, rotation: int) -> None:
        self._cmd(*_ROTATE[rotation])

    def contrast(self, level: int) -> None:
        self._cmd(0x81, level)

    def write(self, page: int, column: int, data: bytes) -> None:
        """Send data to one page, from column to the right."""
        self._cmd(0x21, column, column + len(data) - 1, 0x22, page, page)
        for i in range(0, len(data), 32):
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

    def write(self, page: int, column: int, data: bytes) -> None:
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


def _span(old: bytes | None, new: bytes) -> tuple[int, int]:
    """The column range that holds all differences. None: the panel content is not known."""
    if old is None:
        return 0, WIDTH
    diff = [i for i in range(WIDTH) if old[i] != new[i]]
    return diff[0], diff[-1] + 1


def _encode(canvas: Canvas) -> str:
    return base64.b64encode(canvas.pages()).decode()


class OLEDDriver:
    def __init__(self, mock: bool = False) -> None:
        self._canvas = Canvas()
        self.preview: dict[str, Any] = {"awake": True, "fb": _encode(self._canvas)}
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
        self.preview = {"awake": awake, "fb": _encode(self._canvas)}
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
                first, last = _span(self._shown[page], chunk)
                self._panel.write(page, first, chunk[first:last])
                self._shown[page] = chunk

    def stop(self) -> None:
        self._panel.close()
