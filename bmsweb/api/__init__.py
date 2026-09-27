"""The v1 API, assembled from its parts."""

from __future__ import annotations

from fastapi import APIRouter

from . import health, pack, profile, sessions, stats

router = APIRouter(prefix="/api/v1")
router.include_router(health.router, tags=["health"])
router.include_router(sessions.router, tags=["sessions"])
router.include_router(stats.router, tags=["stats"])
router.include_router(pack.router, tags=["pack"])
router.include_router(profile.router, tags=["profile"])

__all__ = ["router"]
