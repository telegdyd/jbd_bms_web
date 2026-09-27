"""
The pack as a whole, rather than one recording of it: how much a full charge holds, where the
charge stands now, and how far that goes.

Every figure here is read off sessions already in the index, so it costs nothing to log and a
reparse keeps it current.
"""

from __future__ import annotations

import sqlite3
from statistics import median

from fastapi import APIRouter, Depends

from .. import polyline
from . import deps

router = APIRouter()

#: A ride has to use at least this much of the pack before its watt-hours say anything about a full
#: charge. Below it the SOC gauge's one-percent steps are a large share of the answer.
MIN_SOC_DROP = 30

#: Estimates taken into the median. Recent ones only, so the figure follows the pack as it ages.
CAPACITY_WINDOW = 10

#: Days of riding behind the efficiency a range is worked out from.
EFFICIENCY_DAYS = 30

#: Rides whose starting points decide where home is.
HOME_WINDOW = 20


@router.get("/pack")
def pack(connection: sqlite3.Connection = Depends(deps.connection)) -> dict:
    capacity = _capacity(connection)
    charge = _last_charge(connection)
    efficiency = _efficiency(connection)

    usable_wh = capacity["usable_wh"]
    full_range = usable_wh / efficiency if usable_wh and efficiency else None
    range_now = (
        full_range * charge["soc"] / 100 if full_range is not None and charge is not None else None
    )

    return {
        "capacity": capacity,
        "charge": charge,
        "wh_per_km": efficiency,
        "range_km": range_now,
        "full_range_km": full_range,
        "home": _home(connection),
    }


def _capacity(connection: sqlite3.Connection) -> dict:
    """
    Watt-hours per full charge, as the BMS's own gauge accounts for it: the energy a ride took,
    divided by the share of the pack the gauge says it used.

    That makes it exactly as good as the gauge. It is the right figure for a range, because the
    range is read off the same gauge; it is not an independent measure of the cells' health.
    """
    rows = connection.execute(
        """
        SELECT id, local_date, discharged_wh, soc_start, soc_end
        FROM sessions
        WHERE kind = 'bms'
          AND discharged_wh > 0
          AND soc_start - soc_end >= ?
          -- Charged mid-way (or a long descent on regen) and the ratio stops meaning anything.
          AND COALESCE(charged_wh, 0) <= discharged_wh * 0.05
        ORDER BY started_at_ms DESC
        LIMIT ?
        """,
        (MIN_SOC_DROP, CAPACITY_WINDOW),
    ).fetchall()

    estimates = [
        {
            "id": row["id"],
            "local_date": row["local_date"],
            "wh": row["discharged_wh"] * 100 / (row["soc_start"] - row["soc_end"]),
        }
        for row in rows
    ]
    return {
        # A median, because one ride in the cold reads low and should not drag the answer with it.
        "usable_wh": median(e["wh"] for e in estimates) if estimates else None,
        "estimates": estimates,
    }


def _last_charge(connection: sqlite3.Connection) -> dict | None:
    """Where the gauge stood at the end of the most recent recording that has one."""
    row = connection.execute(
        """
        SELECT id, soc_end, ended_at_ms, local_date
        FROM sessions
        WHERE kind = 'bms' AND soc_end IS NOT NULL
        ORDER BY ended_at_ms DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return {
        "soc": row["soc_end"],
        "session_id": row["id"],
        "at_ms": row["ended_at_ms"],
        "local_date": row["local_date"],
    }


def _efficiency(connection: sqlite3.Connection) -> float | None:
    """
    Watt-hours per kilometre over the last month of riding, counted back from the latest ride
    rather than from today, so a quiet fortnight does not leave the range with nothing to go on.
    """
    row = connection.execute(
        """
        SELECT SUM(discharged_wh) AS wh, SUM(distance_km) AS km
        FROM sessions
        WHERE is_ride = 1 AND kind = 'bms'
          AND local_date >= (
              SELECT date(MAX(local_date), ?) FROM sessions WHERE is_ride = 1 AND kind = 'bms'
          )
        """,
        (f"-{EFFICIENCY_DAYS} days",),
    ).fetchone()
    # The same 0.1 km floor the per-ride figure uses.
    if not row["km"] or row["km"] < 0.1:
        return None
    return row["wh"] / row["km"]


def _home(connection: sqlite3.Connection) -> dict | None:
    """
    Where rides usually start: the median of recent starting points, taken per axis. A median
    because one ride begun somewhere else — a train, a holiday — should not move home.
    """
    rows = connection.execute(
        """
        SELECT polyline FROM sessions
        WHERE is_ride = 1 AND polyline IS NOT NULL AND polyline != ''
        ORDER BY started_at_ms DESC
        LIMIT ?
        """,
        (HOME_WINDOW,),
    ).fetchall()

    starts = []
    for row in rows:
        points = polyline.decode(row["polyline"])
        if points:
            starts.append(points[0])
    if not starts:
        return None
    return {"lat": median(p[0] for p in starts), "lon": median(p[1] for p in starts)}
