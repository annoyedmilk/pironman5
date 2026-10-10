"""Hardware drivers. Each one falls back to a device with no effect when its hardware is absent."""

from .fan import FanDriver
from .oled import OLEDDriver
from .rgb import RGBDriver

__all__ = ["FanDriver", "OLEDDriver", "RGBDriver"]
