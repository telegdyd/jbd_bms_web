"""
Liveness.

Deliberately unauthenticated and cheap: this is what the phone probes to decide whether it is on
the home network and worth trying an upload, and what the container healthcheck hits.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from .. import SCHEMA_VERSION
from ..config import Settings
from . import deps

router = APIRouter()


@router.get("/health")
def health(
    connection: sqlite3.Connection = Depends(deps.connection),
    settings: Settings = Depends(deps.settings),
) -> dict:
    sessions = connection.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
    return {
        "status": "ok",
        "service": "bms-web",
        "schema_version": SCHEMA_VERSION,
        "sessions": sessions,
        # So the phone can tell whether to bother attaching a token.
        "auth_required": settings.auth_required,
    }
