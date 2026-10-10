"""The service: one tick loop samples metrics and drives the fan, history, display and stream."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from dataclasses import asdict
from typing import Any

from .config import Config
from .hardware import FanDriver, OLEDDriver, RGBDriver
from .history import HistoryStore
from .logger import get_logger, setup_logging
from .metrics import MockBoard, PiBoard, Sampler

log = get_logger("core")


class Hub:
    """Hands the latest frame to the stream clients."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._seq = 0
        self._data = b""
        self._open = True
        self.clients = 0

    def publish(self, data: bytes) -> None:
        with self._cond:
            self._data = data
            self._seq += 1
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._open = False
            self._cond.notify_all()

    def _wait(self, seq: int) -> tuple[int, bytes] | None:
        """Block until a frame newer than seq exists. None: the hub is closed."""
        with self._cond:
            while self._seq == seq and self._open:
                self._cond.wait()
            return (self._seq, self._data) if self._open else None

    def frames(self) -> Iterator[bytes]:
        """Yield each new frame. Stop when the hub closes."""
        with self._cond:
            self.clients += 1
            seq = self._seq
        try:
            while item := self._wait(seq):
                seq, data = item
                yield data
        finally:
            with self._cond:
                self.clients -= 1


class Core:
    def __init__(self, config: Config, mock: bool = False) -> None:
        self.config = config
        self.mock = mock
        self.hub = Hub()
        self.sampler = Sampler(MockBoard() if mock else PiBoard())
        self.history = HistoryStore(config.resolved_db_path) if config.history.enable else None
        self.rgb = RGBDriver(config.rgb, mock)
        self.fan = FanDriver(config.fan, mock)
        self.oled = OLEDDriver(mock)
        self._update_lock = threading.Lock()

    def start(self) -> None:
        log.info("core starting (mock=%s)", self.mock)
        self.rgb.start()

    def run(self, stop: threading.Event) -> None:
        """Tick until stop is set."""
        deadline = time.monotonic()
        while not stop.is_set():
            try:
                self.tick()
            except Exception:  # one bad tick must not stop fan control
                log.exception("tick failed")
            deadline = max(deadline + self.config.system.data_interval, time.monotonic())
            stop.wait(deadline - time.monotonic())

    def tick(self) -> None:
        cfg = self.config
        sample = self.sampler.read()
        self.fan.update(cfg.fan, sample.cpu_temperature)
        if self.history is not None and cfg.history.enable:
            self.history.add(sample, cfg.history.retention_days)
        awake = self.oled.draw(cfg, sample, self.fan.on)
        if self.hub.clients:
            self.hub.publish(json.dumps(self.status_frame()).encode())
        self.oled.flush(cfg, awake)

    def stop(self) -> None:
        log.info("core stopping")
        steps = [self.hub.close, self.rgb.stop, self.fan.stop, self.oled.stop]
        if self.history is not None:
            steps.append(self.history.close)
        for step in steps:
            try:
                step()
            except Exception:  # a failed device must not keep the others on
                log.exception("stop failed")

    def status_frame(self) -> dict[str, Any]:
        """Metrics, hardware state and config in one JSON-ready dict."""
        frame = asdict(self.sampler.sample)
        frame["case_fan_on"] = self.fan.on
        frame["rgb_leds"] = self.rgb.colors
        frame["oled_preview"] = self.oled.preview
        frame["config"] = self.config.to_dict()
        frame["mock"] = self.mock
        return frame

    def update_config(self, patch: dict[str, Any]) -> list[str]:
        """Validate patch, apply it to the running service and save it."""
        with self._update_lock:
            cfg, touched = self.config.merged(patch)
            self.config = cfg
            self.rgb.apply(cfg.rgb)
            if "oled" in touched:
                self.oled.wake()
            setup_logging(cfg.system.log_level)
            try:
                cfg.save()
            except OSError:
                log.exception("failed to persist config")
        return touched
