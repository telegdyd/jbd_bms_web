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
    row = connection.execute(
        "SELECT COUNT(*) AS n, MAX(uploaded_at_ms) AS last_upload_ms FROM sessions"
    ).fetchone()
    return {
        "status": "ok",
        "service": "bms-web",
        "schema_version": SCHEMA_VERSION,
        "sessions": row["n"],
        # When the phone last delivered something, for the web page's "synced" line.
        "last_upload_ms": row["last_upload_ms"],
        # So the phone can tell whether to bother attaching a token.
        "auth_required": settings.auth_required,
    }
