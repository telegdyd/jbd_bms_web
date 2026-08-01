"""
The application.

    uv run uvicorn bmsweb.main:create_app --factory --reload

A factory rather than a module-level `app`, so that importing this module has no side effects —
otherwise merely importing it would create a data directory wherever the process happened to start.

The frontend arrives in milestone 3; until then `bmsweb/static/` may not exist, and the API is the
whole service.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import api, db
from .config import Settings, load_settings

STATIC_DIR = Path(__file__).parent / "static"

# Uvicorn's own logger, deliberately. It configures handlers for "uvicorn.*" and leaves the root
# logger alone at WARNING, so a logger of our own would have its startup lines silently dropped —
# which is a poor property for the thing whose whole job is to be readable in `docker logs`.
log = logging.getLogger("uvicorn.error")


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or load_settings()
    config.prepare()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = config
        app.state.db = db.Database(config.database_path)

        # Printed on every start so the container log answers the first question anyone asks of a
        # service they cannot reach: is it actually up, and with what settings?
        with app.state.db.session() as connection:
            sessions = connection.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]

        log.info("bms-web ready")
        log.info("  data      %s", config.data_dir)
        log.info("  sessions  %d", sessions)
        log.info("  auth      %s", "on" if config.auth_required else "off (no token set)")

        try:
            yield
        finally:
            app.state.db.close()

    app = FastAPI(
        title="bms-web",
        description="Self-hosted browser for recordings made by the BMS Android app",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(api.router)

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app
