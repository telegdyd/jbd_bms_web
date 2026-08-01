"""Shared request dependencies: the database handle and the optional bearer check."""

from __future__ import annotations

import secrets
import sqlite3

from fastapi import Depends, Header, HTTPException, Request, status

from ..config import Settings, settings as load


def settings(request: Request) -> Settings:
    return request.app.state.settings


def connection(request: Request) -> sqlite3.Connection:
    return request.app.state.connection


def require_token(
    authorization: str | None = Header(default=None),
    config: Settings = Depends(settings),
) -> None:
    """
    No-op when no token is configured, which is the default. Set BMS_UPLOAD_TOKEN in the compose
    file to turn it on.
    """
    if not config.auth_required:
        return

    scheme, _, presented = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(presented, config.upload_token or ""):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bad or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


__all__ = ["connection", "load", "require_token", "settings"]
