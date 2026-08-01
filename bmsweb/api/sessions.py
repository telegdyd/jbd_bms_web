"""Upload, list, read, annotate and delete recordings."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from ..config import Settings
from ..ingest import IngestStatus, ingest, delete as delete_session, sha256_of
from . import deps

router = APIRouter(prefix="/sessions")

#: Columns a client may chart. A whitelist because the name goes into the SELECT.
SERIES_FIELDS = {
    "volts", "amps", "watts", "soc", "remaining_ah",
    "delta_mv", "min_cell_mv", "max_cell_mv",
    "speed_kmh", "alt_m",
}

#: What a session row returns in a list. Samples and polyline are not in it — a list of a hundred
#: rides should be one small response.
LIST_COLUMNS = """
    id, sha256, source_name, kind, device_label, started_at_ms, ended_at_ms, tz_offset_min,
    local_date, duration_ms, sample_count, has_location, is_ride, distance_km, moving_seconds,
    max_speed_kmh, discharged_wh, charged_wh, wh_per_km, soc_start, soc_end, title, polyline
"""


class SessionPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=10_000)


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(deps.require_token)])
async def upload(
    file: UploadFile = File(...),
    sha256: str | None = Form(default=None),
    connection: sqlite3.Connection = Depends(deps.connection),
    settings: Settings = Depends(deps.settings),
) -> Response:
    content = await file.read()

    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty upload")
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Recording is larger than the {settings.max_upload_bytes // 1024 // 1024} MB limit",
        )

    digest = sha256_of(content)
    # The client's own hash is the truncated-upload check: a half-transferred file will not match.
    if sha256 and sha256.lower() != digest:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Content hash does not match the one sent with the upload",
        )

    result = ingest(connection, settings, file.filename or "recording.csv", content)
    body = {"status": result.status.value, "id": result.session_id, "sha256": result.sha256}

    # A duplicate is a success, not an error: it is how a retry after a dropped connection ends,
    # and the phone must be able to mark the file done on seeing it.
    code = (
        status.HTTP_201_CREATED
        if result.status is IngestStatus.CREATED
        else status.HTTP_200_OK
    )
    return Response(json.dumps(body), status_code=code, media_type="application/json")


@router.get("")
def list_sessions(
    connection: sqlite3.Connection = Depends(deps.connection),
    kind: str | None = Query(default=None),
    rides_only: bool = Query(default=False),
    since: str | None = Query(default=None, description="Local date, inclusive: YYYY-MM-DD"),
    until: str | None = Query(default=None, description="Local date, inclusive: YYYY-MM-DD"),
    q: str | None = Query(default=None, description="Free text over title, notes and device"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    where: list[str] = []
    params: list = []

    if kind:
        where.append("kind = ?")
        params.append(kind)
    if rides_only:
        where.append("is_ride = 1")
    if since:
        where.append("local_date >= ?")
        params.append(since)
    if until:
        where.append("local_date <= ?")
        params.append(until)
    if q:
        where.append("(COALESCE(title,'') LIKE ? OR COALESCE(notes,'') LIKE ? OR COALESCE(device_label,'') LIKE ?)")
        params += [f"%{q}%"] * 3

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    total = connection.execute(f"SELECT COUNT(*) AS n FROM sessions {clause}", params).fetchone()["n"]
    rows = connection.execute(
        f"SELECT {LIST_COLUMNS} FROM sessions {clause} ORDER BY started_at_ms DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()

    return {"total": total, "limit": limit, "offset": offset, "sessions": [dict(r) for r in rows]}


@router.get("/{session_id}")
def get_session(
    session_id: int,
    connection: sqlite3.Connection = Depends(deps.connection),
) -> dict:
    return dict(_require(connection, session_id))


@router.patch("/{session_id}", dependencies=[Depends(deps.require_token)])
def patch_session(
    session_id: int,
    patch: SessionPatch,
    connection: sqlite3.Connection = Depends(deps.connection),
) -> dict:
    _require(connection, session_id)

    changes = patch.model_dump(exclude_unset=True)
    if changes:
        assignments = ", ".join(f"{name} = ?" for name in changes)
        with connection:
            connection.execute(
                f"UPDATE sessions SET {assignments} WHERE id = ?",
                [*changes.values(), session_id],
            )

    return dict(_require(connection, session_id))


@router.delete("/{session_id}", dependencies=[Depends(deps.require_token)])
def remove_session(
    session_id: int,
    connection: sqlite3.Connection = Depends(deps.connection),
    settings: Settings = Depends(deps.settings),
) -> dict:
    if not delete_session(connection, settings, session_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such session")
    # The original is in the trash directory, not gone.
    return {"status": "deleted", "id": session_id}


@router.get("/{session_id}/raw.csv")
def raw_csv(
    session_id: int,
    connection: sqlite3.Connection = Depends(deps.connection),
    settings: Settings = Depends(deps.settings),
) -> FileResponse:
    row = _require(connection, session_id)

    path = Path(row["raw_path"])
    if not path.is_absolute():
        path = settings.data_dir / path
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "The original file is no longer on disk")

    return FileResponse(path, media_type="text/csv", filename=row["source_name"])


@router.get("/{session_id}/track")
def track(
    session_id: int,
    connection: sqlite3.Connection = Depends(deps.connection),
) -> dict:
    """
    The route, already simplified at ingest. Points carry the values a map colours by, so the
    frontend does not have to join two responses together to shade a line by speed.
    """
    row = _require(connection, session_id)
    if not row["has_location"]:
        return {"id": session_id, "points": [], "bounds": None}

    points = connection.execute(
        """
        SELECT t_ms, lat, lon, alt_m, speed_kmh, watts, soc
        FROM samples
        WHERE session_id = ? AND lat IS NOT NULL AND lon IS NOT NULL
        ORDER BY t_ms
        """,
        (session_id,),
    ).fetchall()

    return {
        "id": session_id,
        "polyline": row["polyline"],
        "bounds": {
            "min_lat": row["min_lat"], "min_lon": row["min_lon"],
            "max_lat": row["max_lat"], "max_lon": row["max_lon"],
        },
        "points": [dict(p) for p in points],
    }


@router.get("/{session_id}/series")
def series(
    session_id: int,
    connection: sqlite3.Connection = Depends(deps.connection),
    fields: str = Query(default="watts,volts,soc"),
    points: int = Query(default=2000, ge=10, le=20_000),
) -> dict:
    """
    Downsampled by min/max bucketing rather than averaging.

    A one-second 900 W spike has to survive being drawn at two thousand points across a three-hour
    ride; an average erases exactly the thing worth looking at. Each bucket contributes two x
    positions, and every field independently decides which of the two carries its minimum, so a
    rising stretch still reads as rising.
    """
    row = _require(connection, session_id)
    requested = [f.strip() for f in fields.split(",") if f.strip()]
    unknown = [f for f in requested if f not in SERIES_FIELDS]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown field(s): {', '.join(unknown)}")
    if not requested:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields requested")

    columns = ", ".join(requested)
    rows = connection.execute(
        f"SELECT t_ms, {columns} FROM samples WHERE session_id = ? ORDER BY t_ms",
        (session_id,),
    ).fetchall()

    if not rows:
        return {"id": session_id, "t": [], "fields": {f: [] for f in requested}, "gaps": []}

    gaps = _gaps([r["t_ms"] for r in rows], row["gap_threshold_ms"])

    if len(rows) <= points:
        return {
            "id": session_id,
            "t": [r["t_ms"] for r in rows],
            "fields": {f: [r[f] for r in rows] for f in requested},
            "gaps": gaps,
            "downsampled": False,
        }

    return {
        "id": session_id,
        **_bucket(rows, requested, points // 2),
        "gaps": gaps,
        "downsampled": True,
    }


def _bucket(rows: list[sqlite3.Row], fields: list[str], bucket_count: int) -> dict:
    first = rows[0]["t_ms"]
    last = rows[-1]["t_ms"]
    span = max(last - first, 1)
    width = span / bucket_count

    buckets: list[list[sqlite3.Row]] = [[] for _ in range(bucket_count)]
    for row in rows:
        index = min(int((row["t_ms"] - first) / width), bucket_count - 1)
        buckets[index].append(row)

    times: list[int] = []
    output: dict[str, list] = {name: [] for name in fields}

    for index, bucket in enumerate(buckets):
        left = int(first + index * width)
        right = int(first + (index + 0.5) * width)

        if not bucket:
            # An empty bucket is a dropout wide enough to see. Nulls make the chart break rather
            # than draw a straight line across missing time.
            times += [left, right]
            for name in fields:
                output[name] += [None, None]
            continue

        times += [left, right]
        for name in fields:
            values = [(row["t_ms"], row[name]) for row in bucket if row[name] is not None]
            if not values:
                output[name] += [None, None]
                continue
            low = min(values, key=lambda pair: pair[1])
            high = max(values, key=lambda pair: pair[1])
            # Emitted in the order they actually occurred, so the slope of the trace is honest
            # even though the two x positions are synthetic.
            pair = (low[1], high[1]) if low[0] <= high[0] else (high[1], low[1])
            output[name] += list(pair)

    return {"t": times, "fields": output}


def _gaps(times: list[int], threshold_ms: int) -> list[list[int]]:
    """Dropouts, so the frontend can shade them rather than infer them from missing points."""
    return [
        [times[i], times[i + 1]]
        for i in range(len(times) - 1)
        if times[i + 1] - times[i] > threshold_ms
    ]


def _require(connection: sqlite3.Connection, session_id: int) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such session")
    return row
