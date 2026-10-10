"""REST API v1. A handler takes the core, the query and the JSON body, and returns a JSON value."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..config import ConfigError
from ..core import Core

# History ranges in seconds.
RANGES = {
    "10m": 600,
    "1h": 3600,
    "6h": 6 * 3600,
    "24h": 24 * 3600,
    "7d": 7 * 86400,
    "30d": 30 * 86400,
}

# Keys that select hardware pins, file paths or the listen address. Only the CLI can set them.
LOCKED = {"fan": {"gpio_pin"}, "history": {"db_path"}, "web": {"host", "port"}}


class ApiError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def get_status(core: Core, query: dict[str, str], body: Any) -> Any:
    return core.status_frame()


def get_history(core: Core, query: dict[str, str], body: Any) -> Any:
    name = query.get("range", "1h")
    if core.history is None:
        return {"range": name, "samples": []}
    if name not in RANGES:
        raise ApiError(400, f"range must be one of {list(RANGES)}")
    return {"range": name, "samples": core.history.query(RANGES[name])}


def get_config(core: Core, query: dict[str, str], body: Any) -> Any:
    return core.config.to_dict()


def patch_config(core: Core, query: dict[str, str], body: Any) -> Any:
    if not isinstance(body, dict):
        raise ApiError(422, "body must be a JSON object")
    for section, keys in LOCKED.items():
        values = body.get(section)
        for key in keys & (values.keys() if isinstance(values, dict) else set()):
            raise ApiError(422, f"{section}.{key} can be set only with the CLI")
    try:
        changed = core.update_config(body)
    except ConfigError as exc:
        raise ApiError(422, str(exc)) from exc
    return {"changed": changed, "config": core.config.to_dict()}


Handler = Callable[[Core, dict[str, str], Any], Any]

ROUTES: dict[tuple[str, str], Handler] = {
    ("GET", "/api/v1/status"): get_status,
    ("GET", "/api/v1/history"): get_history,
    ("GET", "/api/v1/config"): get_config,
    ("PATCH", "/api/v1/config"): patch_config,
}
