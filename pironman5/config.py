"""Config schema, validation and JSON persistence.

A Config is not changed after validation. A change makes a new Config.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .logger import LEVELS, get_logger

log = get_logger("config")

ENV_CONFIG_PATH = "PIRONMAN5_CONFIG"

RGB_STYLES = ("solid", "breathing", "flow", "flow_reverse", "rainbow", "rainbow_reverse", "hue_cycle")
FAN_MODES = ("off", "on", "auto")
OLED_ROTATIONS = (0, 180)
TEMPERATURE_UNITS = ("C", "F")


class ConfigError(ValueError):
    """A config value failed validation."""


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _is_hex_color(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"#?[0-9a-fA-F]{6}", value.strip()) is not None


def _typed(name: str, default: Any, value: Any) -> Any:
    """Check value against the type of the field default."""
    kind = type(default)
    if kind is float and type(value) is int:
        return float(value)
    if kind is int and type(value) is float and value.is_integer():
        return int(value)
    if type(value) is not kind:
        raise ConfigError(f"{name} must be {kind.__name__}")
    return value


@dataclass
class SystemConfig:
    temperature_unit: str = "C"
    data_interval: float = 1.0  # seconds between samples
    log_level: str = "info"

    def validate(self) -> None:
        if self.temperature_unit not in TEMPERATURE_UNITS:
            raise ConfigError(f"temperature_unit must be one of {TEMPERATURE_UNITS}")
        self.data_interval = float(_clamp(self.data_interval, 0.2, 60.0))
        if self.log_level not in LEVELS:
            raise ConfigError("log_level must be debug|info|warning|error")


@dataclass
class RGBConfig:
    enable: bool = True
    led_count: int = 4  # 4x WS2812B on the base unit
    sync: bool = True  # one color and effect for all LEDs
    color: str = "#00ffff"
    colors: list[str] = field(default_factory=list)  # per LED, used when sync is off
    brightness: int = 100  # 0-100
    speed: int = 50  # 0-100
    style: str = "breathing"

    def validate(self) -> None:
        self.led_count = int(_clamp(self.led_count, 1, 64))
        self.brightness = int(_clamp(self.brightness, 0, 100))
        self.speed = int(_clamp(self.speed, 0, 100))
        if self.style not in RGB_STYLES:
            raise ConfigError(f"rgb.style must be one of {RGB_STYLES}")
        if not _is_hex_color(self.color):
            raise ConfigError("rgb.color must be a hex color like #00ffff")
        # Exactly led_count entries: trim the excess, pad with the sync color.
        colors = self.colors[: self.led_count]
        colors += [self.color] * (self.led_count - len(colors))
        if not all(_is_hex_color(c) for c in colors):
            raise ConfigError("rgb.colors must be hex colors like #00ffff")
        self.colors = colors


@dataclass
class FanConfig:
    mode: str = "auto"  # off, on, or auto (on above on_temp)
    gpio_pin: int = 6
    on_temp: float = 55.0

    def validate(self) -> None:
        if self.mode not in FAN_MODES:
            raise ConfigError(f"fan.mode must be one of {FAN_MODES}")
        self.gpio_pin = int(_clamp(self.gpio_pin, 0, 27))
        self.on_temp = float(_clamp(self.on_temp, 30.0, 90.0))


@dataclass
class OLEDConfig:
    enable: bool = True
    rotation: int = 0  # 0 or 180
    sleep_timeout: int = 0  # seconds, 0 keeps the panel on
    brightness: int = 100  # 0-100

    def validate(self) -> None:
        if self.rotation not in OLED_ROTATIONS:
            raise ConfigError(f"oled.rotation must be one of {OLED_ROTATIONS}")
        self.brightness = int(_clamp(self.brightness, 0, 100))
        self.sleep_timeout = int(_clamp(self.sleep_timeout, 0, 3600))


@dataclass
class HistoryConfig:
    enable: bool = True
    retention_days: int = 30
    db_path: str = ""  # empty: history.db in the systemd state directory, or next to the config file

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

    # Not serialized. Keeps save() on the file the config came from.
    path: Path | None = field(default=None, compare=False, repr=False)

    def validate(self) -> Config:
        for section in self._sections().values():
            section.validate()
        return self

    def _sections(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in _SECTIONS}

    def to_dict(self) -> dict[str, Any]:
        return {name: asdict(section) for name, section in self._sections().items()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        return cls().merged(data)[0]

    def merged(self, patch: dict[str, Any]) -> tuple[Config, list[str]]:
        """Return a validated copy with patch applied, and the touched sections."""
        if not isinstance(patch, dict):
            raise ConfigError("config must be an object")
        cfg = Config(path=self.path)
        touched: list[str] = []
        for name, section in cfg._sections().items():
            known = _known(name, section, patch.get(name, {}))
            for key, value in {**asdict(getattr(self, name)), **known}.items():
                setattr(section, key, _typed(f"{name}.{key}", getattr(section, key), value))
            if known:
                touched.append(name)
        for name in patch.keys() - _SECTIONS:
            log.warning("ignoring unknown config section %r", name)
        return cfg.validate(), touched

    def save(self, path: Path | None = None) -> None:
        target = Path(path or self.path or default_config_path())
        target.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: temp file, then rename.
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2) + "\n")
        os.replace(tmp, target)
        self.path = target
        log.debug("config saved to %s", target)

    @property
    def resolved_db_path(self) -> Path:
        if self.history.db_path:
            return Path(self.history.db_path).expanduser()
        # systemd sets STATE_DIRECTORY for a unit with StateDirectory=.
        state = os.environ.get("STATE_DIRECTORY", "").split(":")[0]
        return Path(state or (self.path or default_config_path()).parent) / "history.db"


_SECTIONS = ("system", "rgb", "fan", "oled", "history", "web")


def _known(name: str, section: Any, values: Any) -> dict[str, Any]:
    """The entries of values that are fields of section."""
    if not isinstance(values, dict):
        raise ConfigError(f"{name} must be an object")
    # A stale client must not break the service: unknown keys are only logged.
    for key in values.keys() - vars(section).keys():
        log.warning("ignoring unknown config key %s.%s", name, key)
    return {key: value for key, value in values.items() if key in vars(section)}


def default_config_path() -> Path:
    """Env override, then XDG, then ~/.config."""
    env = os.environ.get(ENV_CONFIG_PATH)
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "pironman5" / "config.json"


def load_config(path: Path | None = None) -> Config:
    """Load config from disk. Use defaults if the file is absent."""
    target = Path(path) if path else default_config_path()
    data: dict[str, Any] = {}
    if target.exists():
        try:
            data = json.loads(target.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise ConfigError(f"failed to read config {target}: {exc}") from exc
        log.info("loaded config from %s", target)
    else:
        log.info("no config at %s - using defaults", target)
    cfg = Config.from_dict(data)
    cfg.path = target
    return cfg
