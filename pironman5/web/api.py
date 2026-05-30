"""REST + WebSocket API (v1).

All handlers reach the running service via ``request.app.state.core``. The API
is intentionally small: a status snapshot, a live stream, history for charts,
and get/patch config.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from ..config import ConfigError
from ..logger import get_logger

log = get_logger("api")

router = APIRouter(prefix="/api/v1")

# Human range strings → seconds, for the history endpoint.
_RANGES = {
    "10m": 600,
    "1h": 3600,
    "6h": 6 * 3600,
    "24h": 24 * 3600,
    "7d": 7 * 86400,
    "30d": 30 * 86400,
}


def _core(request: Request):
    return request.app.state.core


@router.get("/status")
async def get_status(request: Request):
    """One-shot metrics + hardware snapshot."""
    return _core(request).status_frame()


@router.get("/history")
async def get_history(request: Request, range: str = "1h"):
    core = _core(request)
    if core.history is None:
        return {"range": range, "samples": []}
    seconds = _RANGES.get(range)
    if seconds is None:
        raise HTTPException(400, f"range must be one of {list(_RANGES)}")
    samples = await asyncio.to_thread(core.history.query, seconds)
    return {"range": range, "samples": samples}


@router.get("/config")
async def get_config(request: Request):
    return _core(request).config.to_dict()


@router.patch("/config")
async def patch_config(request: Request, patch: dict):
    """Deep-merge a partial config, validate, apply live, persist."""
    core = _core(request)
    config = core.config
    # Merge into a copy-by-reapply: mutate then validate; on failure, reload.
    changed = config.merge(patch)
    try:
        config.validate()
    except ConfigError as exc:
        # Re-read from disk to discard the bad partial mutation.
        from ..config import load_config
        core.config = load_config(config.path)
        raise HTTPException(422, str(exc)) from exc
    core.apply_config(changed)
    return {"changed": changed, "config": config.to_dict()}


@router.websocket("/stream")
async def stream(websocket: WebSocket):
    """Push a metrics frame to the client whenever one is broadcast."""
    await websocket.accept()
    core = websocket.app.state.core
    queue = core.subscribe()
    try:
        # Send an immediate snapshot so the UI paints without waiting a tick.
        await websocket.send_json(core.status_frame())
        while True:
            await queue.get()
            await websocket.send_json(core.status_frame())
    except WebSocketDisconnect:
        pass
    except Exception:
        log.debug("websocket closed", exc_info=True)
    finally:
        core.unsubscribe(queue)
