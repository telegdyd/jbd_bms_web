"""Totals and per-day buckets for the dashboard."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from . import deps

router = APIRouter()


@router.get("/stats")
def stats(
    connection: sqlite3.Connection = Depends(deps.connection),
    since: str | None = Query(default=None, description="Local date, inclusive: YYYY-MM-DD"),
    until: str | None = Query(default=None, description="Local date, inclusive: YYYY-MM-DD"),
    rides_only: bool = Query(default=True),
) -> dict:
    where = ["1 = 1"]
    params: list = []

    if rides_only:
        where.append("is_ride = 1")
    if since:
        where.append("local_date >= ?")
        params.append(since)
    if until:
        where.append("local_date <= ?")
        params.append(until)

    clause = " AND ".join(where)

    totals = connection.execute(
        f"""
        SELECT
            COUNT(*)                     AS sessions,
            COALESCE(SUM(distance_km), 0)     AS distance_km,
            COALESCE(SUM(discharged_wh), 0)   AS discharged_wh,
            COALESCE(SUM(charged_wh), 0)      AS charged_wh,
            COALESCE(SUM(moving_seconds), 0)  AS moving_seconds,
            COALESCE(SUM(duration_ms), 0)     AS duration_ms,
            MAX(max_speed_kmh)                AS max_speed_kmh
        FROM sessions WHERE {clause}
        """,
        params,
    ).fetchone()

    result = dict(totals)
    # Averaged over the whole period rather than averaging each ride's figure: a two-kilometre
    # errand should not weigh as heavily as a forty-kilometre ride.
    result["wh_per_km"] = (
        result["discharged_wh"] / result["distance_km"] if result["distance_km"] >= 0.1 else None
    )

    days = connection.execute(
        f"""
        SELECT local_date,
               COUNT(*) AS sessions,
               COALESCE(SUM(distance_km), 0)   AS distance_km,
               COALESCE(SUM(discharged_wh), 0) AS discharged_wh,
               COALESCE(SUM(moving_seconds), 0) AS moving_seconds
        FROM sessions WHERE {clause}
        GROUP BY local_date ORDER BY local_date
        """,
        params,
    ).fetchall()

    return {"totals": result, "days": [dict(day) for day in days]}
