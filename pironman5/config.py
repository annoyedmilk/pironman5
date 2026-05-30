"""Configuration: a flat, validated dataclass schema with JSON persistence.

One config file with hardcoded hardware assumptions for the base unit. Sections:
system, rgb, fan, oled, history, web.

The same schema backs both the on-disk JSON file and the ``/api/v1/config``
endpoints, so the dataclasses double as the validation layer for live PATCHes.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from .logger import get_logger

log = get_logger("config")

# Where the config lives. Honour an explicit override, else XDG, else a
# system path when running as the service user.
ENV_CONFIG_PATH = "PIRONMAN5_CONFIG"

# Valid choices, kept next to the fields they validate.
RGB_STYLES = ("solid", "breathing", "flow", "flow_reverse", "rainbow", "rainbow_reverse", "hue_cycle")
FAN_MODES = ("off", "on", "auto")
OLED_ROTATIONS = (0, 180)
TEMPERATURE_UNITS = ("C", "F")

# Legacy fan mode names accepted on load and mapped onto the current set.
_FAN_MODE_ALIASES = {"always_on": "on", "quiet": "auto"}


class ConfigError(ValueError):
    """Raised when a config value fails validation."""


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class SystemConfig:
    temperature_unit: str = "C"
    # How often metrics are sampled / pushed, in seconds.
    data_interval: float = 1.0
    log_level: str = "info"

    def validate(self) -> None:
        if self.temperature_unit not in TEMPERATURE_UNITS:
            raise ConfigError(f"temperature_unit must be one of {TEMPERATURE_UNITS}")
        self.data_interval = float(_clamp(self.data_interval, 0.2, 60.0))
        if self.log_level not in ("debug", "info", "warning", "error"):
            raise ConfigError("log_level must be debug|info|warning|error")


@dataclass
class RGBConfig:
    enable: bool = True
    led_count: int = 4          # 4x WS2812B on the base unit
    sync: bool = True           # True: one color/effect for all; False: per-LED
    color: str = "#00ffff"      # used when sync is on
    colors: list[str] = field(default_factory=list)  # per-LED when sync is off
    brightness: int = 100       # 0-100
    speed: int = 50             # 0-100
    style: str = "breathing"

    def validate(self) -> None:
        self.led_count = int(_clamp(self.led_count, 1, 64))
        self.brightness = int(_clamp(self.brightness, 0, 100))
        self.speed = int(_clamp(self.speed, 0, 100))
        if self.style not in RGB_STYLES:
            raise ConfigError(f"rgb.style must be one of {RGB_STYLES}")
        if not _is_hex_color(self.color):
            raise ConfigError("rgb.color must be a hex color like #00ffff")
        # Normalise per-LED colors to exactly led_count valid hex entries,
        # padding with the sync color and trimming any excess.
        colors = list(self.colors)[: self.led_count]
        while len(colors) < self.led_count:
            colors.append(self.color)
        for c in colors:
            if not _is_hex_color(c):
                raise ConfigError("rgb.colors must be hex colors like #00ffff")
        self.colors = colors


@dataclass
class FanConfig:
    # off: never run, on: always run, auto: run above on_temp
    mode: str = "auto"
    gpio_pin: int = 6           # case fan on GPIO 6
    on_temp: float = 55.0       # auto: switch on above this temperature

    def validate(self) -> None:
        self.mode = _FAN_MODE_ALIASES.get(self.mode, self.mode)
        if self.mode not in FAN_MODES:
            raise ConfigError(f"fan.mode must be one of {FAN_MODES}")
        self.gpio_pin = int(_clamp(self.gpio_pin, 0, 27))
        self.on_temp = float(_clamp(self.on_temp, 30.0, 90.0))


@dataclass
class OLEDConfig:
    enable: bool = True
    rotation: int = 0           # 0 or 180
    sleep_timeout: int = 0      # seconds; 0 = always on

    def validate(self) -> None:
        if self.rotation not in OLED_ROTATIONS:
            raise ConfigError(f"oled.rotation must be one of {OLED_ROTATIONS}")
        self.sleep_timeout = int(_clamp(self.sleep_timeout, 0, 3600))


@dataclass
class HistoryConfig:
    enable: bool = True
    retention_days: int = 30
    # Path is resolved lazily relative to the config dir if left blank.
    db_path: str = ""

    def validate(self) -> None:
        self.retention_days = int(_clamp(self.retention_days, 1, 365))


@dataclass
class WebConfig:
    enable: bool = True
    host: str = "0.0.0.0"
    port: int = 34001

    def validate(self) -> None:
        self.port = int(_clamp(self.port, 1, 65535))


@dataclass
class Config:
    system: SystemConfig = field(default_factory=SystemConfig)
    rgb: RGBConfig = field(default_factory=RGBConfig)
    fan: FanConfig = field(default_factory=FanConfig)
    oled: OLEDConfig = field(default_factory=OLEDConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    web: WebConfig = field(default_factory=WebConfig)

    # Not serialized - remembered so save()/reload() are path-stable.
    path: Path | None = field(default=None, compare=False, repr=False)

    # ---- (de)serialization -------------------------------------------------

    def validate(self) -> "Config":
        for f in fields(self):
            section = getattr(self, f.name)
            if is_dataclass(section) and hasattr(section, "validate"):
                section.validate()
        return self

    def to_dict(self) -> dict[str, Any]:
        data = {f.name: asdict(getattr(self, f.name)) for f in fields(self) if f.name != "path"}
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        cfg = cls()
        cfg.merge(data)
        return cfg.validate()

    def merge(self, patch: dict[str, Any]) -> list[str]:
        """Deep-merge a partial dict into the config. Returns changed sections.

        Unknown sections/keys are ignored with a warning rather than raising,
        so a slightly stale client PATCH can't break the service.
        """
        changed: list[str] = []
        section_names = {f.name for f in fields(self) if f.name != "path"}
        for section_name, values in patch.items():
            if section_name not in section_names:
                log.warning("ignoring unknown config section %r", section_name)
                continue
            section = getattr(self, section_name)
            valid_keys = {f.name for f in fields(section)}
            touched = False
            for key, value in (values or {}).items():
                if key not in valid_keys:
                    log.warning("ignoring unknown config key %s.%s", section_name, key)
                    continue
                setattr(section, key, value)
                touched = True
            if touched:
                changed.append(section_name)
        return changed

    # ---- file IO -----------------------------------------------------------

    def save(self, path: Path | None = None) -> None:
        target = Path(path or self.path or default_config_path())
        target.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: temp file + rename.
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2) + "\n")
        os.replace(tmp, target)
        self.path = target
        log.debug("config saved to %s", target)

    @property
    def resolved_db_path(self) -> Path:
        if self.history.db_path:
            return Path(self.history.db_path).expanduser()
        base = (self.path or default_config_path()).parent
        return base / "history.db"


def _is_hex_color(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    v = value.strip().lstrip("#")
    if len(v) != 6:
        return False
    try:
        int(v, 16)
        return True
    except ValueError:
        return False


def default_config_path() -> Path:
    """Resolve the config path: env override → XDG → ~/.config fallback."""
    env = os.environ.get(ENV_CONFIG_PATH)
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "pironman5" / "config.json"


def load_config(path: Path | None = None) -> Config:
    """Load config from disk, creating defaults if absent."""
    target = Path(path) if path else default_config_path()
    if target.exists():
        try:
            data = json.loads(target.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise ConfigError(f"failed to read config {target}: {exc}") from exc
        cfg = Config.from_dict(data)
        log.info("loaded config from %s", target)
    else:
        cfg = Config().validate()
        log.info("no config at %s - using defaults", target)
    cfg.path = target
    return cfg
