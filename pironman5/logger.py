"""Logging setup - one place, no per-module reconfiguration."""

import logging
import sys

_CONFIGURED = False

# Map our friendly level names to logging constants.
LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def setup_logging(level: str = "info") -> None:
    """Configure the root 'pironman5' logger once.

    Safe to call repeatedly; only the level is updated after the first call.
    """
    global _CONFIGURED
    log_level = LEVELS.get(str(level).lower(), logging.INFO)

    root = logging.getLogger("pironman5")
    root.setLevel(log_level)

    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        root.addHandler(handler)
        root.propagate = False
        _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the 'pironman5' namespace."""
    return logging.getLogger(f"pironman5.{name}")
