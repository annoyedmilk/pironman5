"""Pironman 5 service for the Raspberry Pi 5 base unit.

Drives the OLED, WS2812 RGB strip and tower fan, exposes live metrics and
hardware control over a small FastAPI web UI, and keeps a SQLite history ring
buffer. One device, one config file, no hidden state.
"""

from .version import __version__

__all__ = ["__version__"]
