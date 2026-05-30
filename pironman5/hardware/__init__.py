"""Hardware drivers, each behind a small interface with a ``--mock`` fallback.

Every driver imports its Pi-only libraries lazily inside ``_open()``. If the
import fails (e.g. running on a laptop) or ``mock=True`` is passed, the driver
runs in mock mode: it keeps full state and behaviour but does no real IO, so the
service - and the whole web UI - runs and is testable off the Pi.
"""

from .fan import FanDriver
from .oled import OLEDDriver
from .rgb import RGBDriver

__all__ = ["FanDriver", "OLEDDriver", "RGBDriver"]
