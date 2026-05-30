"""FastAPI app factory + uvicorn runner, sharing the asyncio loop with Core."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..logger import get_logger
from ..version import __version__
from .api import router

log = get_logger("web")

STATIC_DIR = Path(__file__).parent / "static"


def create_app(core) -> FastAPI:
    app = FastAPI(title="Pironman 5", version=__version__)
    app.state.core = core
    app.include_router(router)

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    # Static assets (app.js, style.css). Mounted last so it doesn't shadow /api.
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


async def run_server(core, host: str, port: int, stop) -> None:
    """Serve the app on the current event loop until ``stop`` is set.

    We drive uvicorn via its ``should_exit`` flag rather than cancelling the
    serve task, so the server shuts its connections down cleanly without raising
    on the way out.
    """
    import asyncio
    import uvicorn

    app = create_app(core)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    log.info("web UI on http://%s:%d", host, port)

    serve_task = asyncio.create_task(server.serve())
    await stop.wait()
    server.should_exit = True
    await serve_task
