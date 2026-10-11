"""Logging: one stderr handler on the 'pironman5' logger."""

import logging
import sys

LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def setup_logging(level: str = "info") -> None:
    """Set the level. The first call adds the handler."""
    root = logging.getLogger("pironman5")
    root.setLevel(LEVELS.get(str(level).lower(), logging.INFO))
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    root.addHandler(handler)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"pironman5.{name}")
