"""The v1 API, assembled from its three parts."""

from __future__ import annotations

from fastapi import APIRouter

from . import health, sessions, stats

router = APIRouter(prefix="/api/v1")
router.include_router(health.router, tags=["health"])
router.include_router(sessions.router, tags=["sessions"])
router.include_router(stats.router, tags=["stats"])

__all__ = ["router"]
