"""
The application.

    uv run uvicorn bmsweb.main:create_app --factory --reload

A factory rather than a module-level `app`, so that importing this module has no side effects —
otherwise merely importing it would create a data directory wherever the process happened to start.

The frontend arrives in milestone 3; until then `bmsweb/static/` may not exist, and the API is the
whole service.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import api, db
from .config import Settings, load_settings

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or load_settings()
    config.prepare()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = config
        app.state.db = db.Database(config.database_path)
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
