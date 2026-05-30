"""0.96" SSD1306 OLED over I2C (addr 0x3C, bus 1).

The panel shows a single fixed layout summarising the system. Lines are built as
plain text and rendered either to the real panel via Pillow or, in mock mode and
for the web UI, exposed through ``preview`` so the dashboard mirrors the panel.

The panel wakes on any config change and can optionally sleep after a timeout
(``sleep_timeout`` of 0 keeps it always on).
"""

from __future__ import annotations

import asyncio
import time

from ..logger import get_logger

log = get_logger("oled")

OLED_WIDTH = 128
OLED_HEIGHT = 64
OLED_ADDR = 0x3C


def render_lines(data: dict, cfg) -> list[str]:
    """Build the single fixed display layout from a metrics frame."""
    temp = data.get("cpu_temperature")
    if temp is None:
        temp_str = "--"
    elif cfg.system.temperature_unit == "F":
        temp_str = f"{temp * 9 / 5 + 32:.0f}F"
    else:
        temp_str = f"{temp:.0f}C"
    power = data.get("power_watts")
    power_str = f"   {power:.1f}W" if power is not None else ""
    ip = next(iter(data.get("ips", {}).values()), "no network")
    return [
        f"CPU {data.get('cpu_percent', 0):.0f}%   {temp_str}",
        f"MEM {data.get('memory_percent', 0):.0f}%{power_str}",
        f"DISK {data.get('disk_percent', 0):.0f}%",
        str(ip),
    ]


# ---- real panel -----------------------------------------------------------

class _Panel:
    """Minimal SSD1306 128x64 driver over smbus2 with Pillow text rendering."""

    def __init__(self, rotation: int = 0) -> None:
        from smbus2 import SMBus  # type: ignore
        from PIL import Image, ImageDraw, ImageFont  # type: ignore

        self._Image = Image
        self._ImageDraw = ImageDraw
        self.rotation = rotation
        self.bus = SMBus(1)
        self.font = ImageFont.load_default()
        self._init_panel()

    def _cmd(self, c: int) -> None:
        self.bus.write_byte_data(OLED_ADDR, 0x00, c)

    def _init_panel(self) -> None:
        for c in (
            0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40, 0x8D, 0x14,
            0x20, 0x00, 0xA1, 0xC8, 0xDA, 0x12, 0x81, 0xCF, 0xD9, 0xF1,
            0xDB, 0x40, 0xA4, 0xA6, 0xAF,
        ):
            self._cmd(c)

    def render(self, lines: list[str]) -> None:
        img = self._Image.new("1", (OLED_WIDTH, OLED_HEIGHT))
        draw = self._ImageDraw.Draw(img)
        y = 0
        for line in lines[:4]:
            draw.text((2, y), line, font=self.font, fill=1)
            y += 16
        if self.rotation == 180:
            img = img.rotate(180)
        self._flush(img)

    def _flush(self, img) -> None:
        self._cmd(0x21)
        self._cmd(0)
        self._cmd(OLED_WIDTH - 1)
        self._cmd(0x22)
        self._cmd(0)
        self._cmd(7)
        pix = img.load()
        buf = []
        for page in range(8):
            for x in range(OLED_WIDTH):
                bits = 0
                for bit in range(8):
                    bits = (bits << 1) | (0 if pix[x, page * 8 + 7 - bit] == 0 else 1)
                buf.append(bits)
        for i in range(0, len(buf), 16):
            self.bus.write_i2c_block_data(OLED_ADDR, 0x40, buf[i:i + 16])

    def blank(self) -> None:
        self.render([])

    def off(self) -> None:
        self._cmd(0xAE)


# ---- driver ---------------------------------------------------------------

class OLEDDriver:
    def __init__(self, config, metrics, mock: bool = False) -> None:
        self.cfg = config              # the full Config (needs system + oled)
        self.metrics = metrics
        self.mock = mock
        self.running = False

        self._panel = None if mock else self._open()
        self._awake = True
        self._wake_at = time.time()
        self.preview: dict = {"lines": [], "awake": True}

    @property
    def _oled(self):
        return self.cfg.oled

    def _open(self):
        try:
            panel = _Panel(rotation=self.cfg.oled.rotation)
            log.info("OLED opened (SSD1306 @0x%02X)", OLED_ADDR)
            return panel
        except Exception as exc:
            log.warning("OLED unavailable (%s) - running mock", exc)
            self.mock = True
            return None

    def apply_config(self) -> None:
        if self._panel is not None and self._panel.rotation != self._oled.rotation:
            self._panel.rotation = self._oled.rotation
        # Wake the panel on any config change so the effect is visible at once.
        if self._oled.enable:
            self._awake = True
            self._wake_at = time.time()

    def _draw(self, lines: list[str]) -> None:
        self.preview = {"lines": lines, "awake": self._awake}
        if not self.mock and self._panel is not None:
            self._panel.render(lines)

    def _blank(self) -> None:
        self.preview = {"lines": [], "awake": False}
        if not self.mock and self._panel is not None:
            self._panel.blank()

    async def run(self) -> None:
        self.running = True
        log.info("oled driver started (mock=%s)", self.mock)
        while self.running:
            if not self._oled.enable:
                if self._awake:
                    self._blank()
                    self._awake = False
                await asyncio.sleep(0.5)
                continue

            if self._awake and self._oled.sleep_timeout > 0:
                if time.time() - self._wake_at > self._oled.sleep_timeout:
                    self._blank()
                    self._awake = False

            if self._awake:
                self._draw(render_lines(self.metrics.latest or {}, self.cfg))
            await asyncio.sleep(0.5)

    def stop(self) -> None:
        self.running = False
        self._blank()
        if not self.mock and self._panel is not None:
            self._panel.off()
