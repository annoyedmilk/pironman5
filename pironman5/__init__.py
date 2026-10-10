"""Pironman 5 service for the Raspberry Pi 5 base unit.

Drives the OLED, WS2812 RGB strip and case fans, serves live metrics and
hardware control in a web UI, and keeps a metrics history in SQLite.
"""

from .version import __version__

__all__ = ["__version__"]
