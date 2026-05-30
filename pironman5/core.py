"""The orchestrator: owns config + state, runs every worker on one asyncio loop.

Responsibilities:
* sample metrics every ``system.data_interval``, update latest, write history,
  broadcast a frame to all WebSocket clients;
* run the RGB / OLED / fan async workers;
* apply live config changes from the API.

The web layer reads ``core`` off ``app.state`` and pushes nothing itself - it
just subscribes to the broadcast. One owner of state, no races.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .config import Config
from .hardware import FanDriver, OLEDDriver, RGBDriver
from .history import HistoryStore
from .logger import get_logger
from .metrics import Metrics

log = get_logger("core")


class Core:
    def __init__(self, config: Config, mock: bool = False) -> None:
        self.config = config
        self.mock = mock
        self.metrics = Metrics()

        self.history: HistoryStore | None = None
        if config.history.enable:
            self.history = HistoryStore(config.resolved_db_path, config.history.retention_days)

        # Hardware workers.
        self.rgb = RGBDriver(config.rgb, mock=mock)
        self.fan = FanDriver(config.fan, self.metrics, mock=mock)
        self.oled = OLEDDriver(config, self.metrics, mock=mock)

        # WebSocket subscribers: each is an asyncio.Queue of frames.
        self._subscribers: set[asyncio.Queue] = set()
        self._tasks: list[asyncio.Task] = []
        self._running = False

    # ---- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        self._running = True
        log.info("core starting (mock=%s)", self.mock)
        # Seed metrics so the first frame a client receives already has data.
        self.metrics.collect()
        self._tasks = [
            asyncio.create_task(self._metrics_loop(), name="metrics"),
            asyncio.create_task(self.rgb.run(), name="rgb"),
            asyncio.create_task(self.fan.run(), name="fan"),
            asyncio.create_task(self.oled.run(), name="oled"),
        ]

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        log.info("core stopping")
        self.rgb.running = self.fan.running = self.oled.running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self.rgb.stop()
        self.fan.stop()
        self.oled.stop()
        if self.history is not None:
            self.history.close()

    # ---- metrics + broadcast ----------------------------------------------

    async def _metrics_loop(self) -> None:
        while self._running:
            try:
                # psutil calls can block briefly; keep the loop responsive.
                frame = await asyncio.to_thread(self.metrics.collect)
                if self.history is not None:
                    self.history.record(frame)
                self._broadcast(frame)
            except Exception:
                log.exception("metrics loop error")
            await asyncio.sleep(self.config.system.data_interval)

    def _broadcast(self, frame: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            # Drop the frame for slow consumers rather than unbounded buffering.
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(frame)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=5)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    # ---- snapshots for the API --------------------------------------------

    def status_frame(self) -> dict[str, Any]:
        # Include config so the dashboard controls always track the live state.
        frame = dict(self.metrics.latest)
        frame["rgb_leds"] = self.rgb.led_colors
        frame["oled_preview"] = self.oled.preview
        frame["config"] = self.config.to_dict()
        frame["mock"] = self.mock
        return frame

    # ---- live config -------------------------------------------------------

    def apply_config(self, changed_sections: list[str]) -> None:
        """Push changed config sections to the running workers."""
        if "rgb" in changed_sections:
            self.rgb.apply_config()
        if "fan" in changed_sections:
            self.fan.apply_config()
        if "oled" in changed_sections:
            self.oled.apply_config()
        if "system" in changed_sections:
            from .logger import setup_logging
            setup_logging(self.config.system.log_level)
        # Persist after applying.
        try:
            self.config.save()
        except Exception:
            log.exception("failed to persist config")
