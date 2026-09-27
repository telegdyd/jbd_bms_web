"""Upload, list, read, annotate and delete recordings."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .. import altitude, companions, effort, profile
from ..companions import AttachStatus
from ..config import Settings
from ..db import transaction
from ..gpx import GpxError
from ..ingest import IngestStatus, ingest, delete as delete_session, sha256_of
from ..parse import BmsSample
from ..simplify import located, simplify
from ..splits import splits as compute_splits
from ..terrain import Terrain
from . import deps

router = APIRouter(prefix="/sessions")

#: Columns a client may chart. A whitelist because the name goes into the SELECT.
SERIES_FIELDS = {
    "volts", "amps", "watts", "soc", "remaining_ah",
    "delta_mv", "min_cell_mv", "max_cell_mv",
    "speed_kmh", "alt_m",
}

#: Chartable alongside them, but they come from an attached companion file rather than from the
#: recording, so they are resolved separately and on their own clock.
CHARTABLE = SERIES_FIELDS | set(companions.CHANNELS) | {"rider_w"}

#: What a session row returns in a list. Samples and polyline are not in it — a list of a hundred
#: rides should be one small response.
LIST_COLUMNS = """
    id, sha256, source_name, kind, device_label, started_at_ms, ended_at_ms, tz_offset_min,
    local_date, duration_ms, sample_count, has_location, is_ride, distance_km, moving_seconds,
    max_speed_kmh, discharged_wh, charged_wh, wh_per_km, soc_start, soc_end, avg_hr_bpm, title,
    polyline
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
        with transaction(connection):
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


# ---------------------------------------------------------------------- companions
#
# A GPX exported from Strava, attached to a recording for the channels the pack cannot see. The
# upload is deliberately shaped like the recording upload — multipart, hash-idempotent — because it
# is the same act by a different route: a file arrives, its original is kept, and what the database
# holds is derived from it.


class CompanionPatch(BaseModel):
    #: Milliseconds added to the companion's timestamps. Anything beyond an hour is not two clocks
    #: disagreeing, it is the wrong file.
    offset_ms: int | None = Field(default=None, ge=-3_600_000, le=3_600_000)
    #: Measure the offset again instead of setting one.
    realign: bool = False


@router.post(
    "/{session_id}/companions",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(deps.require_token)],
)
async def attach_companion(
    session_id: int,
    file: UploadFile = File(...),
    offset_ms: int | None = Form(default=None),
    connection: sqlite3.Connection = Depends(deps.connection),
    settings: Settings = Depends(deps.settings),
) -> Response:
    _require(connection, session_id)
    content = await file.read()

    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty upload")
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "That file is too large")

    try:
        result = companions.attach(
            connection, settings, session_id, file.filename or "companion.gpx", content, offset_ms
        )
    except GpxError as error:
        # Everything GpxError says is about the file the user just picked, so it is worth showing
        # them rather than flattening into "bad request".
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error

    body = {
        "status": result.status.value,
        "companion": _companion(connection, session_id, result.companion_id),
    }
    code = (
        status.HTTP_201_CREATED
        if result.status is AttachStatus.CREATED
        else status.HTTP_200_OK
    )
    return Response(json.dumps(body), status_code=code, media_type="application/json")


@router.get("/{session_id}/companions")
def companion_list(
    session_id: int,
    connection: sqlite3.Connection = Depends(deps.connection),
) -> dict:
    _require(connection, session_id)
    return {"id": session_id, "companions": companions.list_for(connection, session_id)}


@router.patch(
    "/{session_id}/companions/{companion_id}", dependencies=[Depends(deps.require_token)]
)
def patch_companion(
    session_id: int,
    companion_id: int,
    patch: CompanionPatch,
    connection: sqlite3.Connection = Depends(deps.connection),
) -> dict:
    _require_companion(connection, session_id, companion_id)

    if patch.realign:
        companions.realign(connection, companion_id)
    elif patch.offset_ms is not None:
        companions.set_offset(connection, companion_id, patch.offset_ms)
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Give an offset_ms, or ask to realign")

    return _companion(connection, session_id, companion_id)


@router.delete(
    "/{session_id}/companions/{companion_id}", dependencies=[Depends(deps.require_token)]
)
def detach_companion(
    session_id: int,
    companion_id: int,
    connection: sqlite3.Connection = Depends(deps.connection),
    settings: Settings = Depends(deps.settings),
) -> dict:
    _require_companion(connection, session_id, companion_id)
    companions.detach(connection, settings, companion_id)
    # The GPX is in the trash directory, not gone.
    return {"status": "detached", "id": companion_id}


@router.get("/{session_id}/companions/{companion_id}/raw.gpx")
def companion_raw(
    session_id: int,
    companion_id: int,
    connection: sqlite3.Connection = Depends(deps.connection),
    settings: Settings = Depends(deps.settings),
) -> FileResponse:
    row = _require_companion(connection, session_id, companion_id)

    path = Path(row["raw_path"])
    if not path.is_absolute():
        path = settings.data_dir / path
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "The original file is no longer on disk")

    return FileResponse(path, media_type="application/gpx+xml", filename=row["source_name"])


def _require_companion(
    connection: sqlite3.Connection, session_id: int, companion_id: int
) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM companions WHERE id = ? AND session_id = ?", (companion_id, session_id)
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such companion on this session")
    return row


def _companion(connection: sqlite3.Connection, session_id: int, companion_id: int) -> dict:
    """One companion, with the same overlap figures the list gives."""
    for companion in companions.list_for(connection, session_id):
        if companion["id"] == companion_id:
            return companion
    raise HTTPException(status.HTTP_404_NOT_FOUND, "No such companion on this session")


@router.get("/{session_id}/track")
def track(
    session_id: int,
    connection: sqlite3.Connection = Depends(deps.connection),
    detail: str = Query(default="drawn", pattern="^(drawn|full)$"),
) -> dict:
    """
    The route, with the values a map colours by on each point — so shading a line by speed does not
    mean joining two responses together in the browser.

    `drawn` is the cleaned-up track, which is what should actually be drawn: a three-hour ride is
    ten thousand fixes, and one polyline segment per fix makes a map that cannot be panned. `full`
    returns every fix for anything that needs them.
    """
    row = _require(connection, session_id)
    if not row["has_location"]:
        return {"id": session_id, "detail": detail, "points": [], "bounds": None}

    samples = _samples_of(connection, session_id)
    points = located(samples) if detail == "full" else simplify(samples)

    return {
        "id": session_id,
        "detail": detail,
        "polyline": row["polyline"],
        "bounds": {
            "min_lat": row["min_lat"], "min_lon": row["min_lon"],
            "max_lat": row["max_lat"], "max_lon": row["max_lon"],
        },
        "points": [
            {
                "t_ms": p.at_ms,
                "lat": p.latitude,
                "lon": p.longitude,
                "alt_m": p.altitude_m,
                "speed_kmh": p.speed_kmh,
                "watts": p.watts,
                "soc": p.soc,
                "hr": p.heart_rate_bpm,
            }
            for p in points
        ],
    }


@router.get("/{session_id}/splits")
def session_splits(
    session_id: int,
    connection: sqlite3.Connection = Depends(deps.connection),
    terrain: Terrain | None = Depends(deps.terrain),
    km: float = Query(default=1.0, gt=0.05, le=50.0),
) -> dict:
    row = _require(connection, session_id)
    if not row["has_location"]:
        # An EKD01 recording has a distance but no positions, so there is nothing to cut into
        # kilometres. Better an empty list the page can hide than invented rows.
        return {"id": session_id, "km": km, "splits": []}

    samples = _samples_of(connection, session_id)
    rider = _rider_power(connection, row, samples, terrain)
    return {
        "id": session_id,
        "km": km,
        "splits": [
            s.as_dict()
            for s in compute_splits(
                samples, row["gap_threshold_ms"], km, rider.watts if rider else None
            )
        ],
    }


# ---------------------------------------------------------------------- the rider's side


@router.get("/{session_id}/effort")
def session_effort(
    session_id: int,
    connection: sqlite3.Connection = Depends(deps.connection),
    terrain: Terrain | None = Depends(deps.terrain),
) -> dict:
    """
    The rider's share of the work, time in heart rate zones, and calories. Computed on each request
    from the samples, the profile and the elevation map, so a corrected weight applies to every ride
    at once. Each part is null with a reason beside it when what it needs is missing.
    """
    row = _require(connection, session_id)
    rider_profile = profile.load(connection)
    year = _year_of(row)
    samples = _samples_of(connection, session_id)
    gap = row["gap_threshold_ms"]
    body: dict = {
        "id": session_id,
        "profile_missing": [
            name
            for name, value in (
                ("weight_kg", rider_profile.weight_kg),
                ("birth_year", rider_profile.birth_year),
            )
            if value is None
        ],
    }

    # -- the work
    routed = bool(row["has_location"]) and row["kind"] == "bms"
    heights, source = altitude.heights(samples, terrain) if routed else ([], None)
    body["altitude_source"] = source
    body["rider"] = None
    if not routed:
        body["rider_unavailable"] = "no_route"
    elif source is None:
        body["rider_unavailable"] = "no_heights"
    elif rider_profile.total_mass_kg is None:
        body["rider_unavailable"] = "profile"
    else:
        mass = rider_profile.total_mass_kg
        low, mid, high = (
            effort.rider_power(samples, heights, mass, gap, a)
            for a in effort.assumptions(rider_profile.tyres)
        )
        body["rider"] = {
            "mass_kg": mass,
            "rider_wh": round(mid.rider_wh, 1),
            "rider_wh_low": round(low.rider_wh, 1),
            "rider_wh_high": round(high.rider_wh, 1),
            "motor_wh": round(mid.motor_wh, 1),
            "share": mid.share,
            "share_low": low.share,
            "share_high": high.share,
            "average_w": mid.average_w,
            "moving_s": mid.moving_s,
        }

    # -- the heart
    measured = any(s.heart_rate_bpm is not None for s in samples)
    resolved = profile.max_hr(connection, rider_profile, year)
    body["zones"] = None
    if not measured:
        body["zones_unavailable"] = "no_heart_rate"
    elif resolved is None:
        body["zones_unavailable"] = "profile"
    else:
        body["zones"] = {
            "max_hr": round(resolved[0]),
            "max_hr_source": resolved[1],
            "bounds": effort.zone_bounds(resolved[0]),
            "seconds": effort.zone_seconds(samples, resolved[0], gap),
        }

    age = rider_profile.age_in(year)
    body["calories"] = None
    if not measured:
        body["calories_unavailable"] = "no_heart_rate"
    elif rider_profile.weight_kg is None or age is None:
        body["calories_unavailable"] = "profile"
    else:
        kcal = effort.calories(samples, rider_profile.weight_kg, age, rider_profile.sex, gap)
        body["calories"] = {
            "kcal": round(kcal) if kcal is not None else None,
            "sex_given": rider_profile.sex is not None,
        }

    return body


def _rider_power(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    samples: list[BmsSample],
    terrain: Terrain | None,
) -> effort.RiderPower | None:
    """The middle estimate, for charts and splits; None when it cannot be made."""
    if not row["has_location"] or row["kind"] != "bms":
        return None
    rider_profile = profile.load(connection)
    if rider_profile.total_mass_kg is None:
        return None
    heights, source = altitude.heights(samples, terrain)
    if source is None:
        return None
    _, mid, _ = effort.assumptions(rider_profile.tyres)
    return effort.rider_power(
        samples, heights, rider_profile.total_mass_kg, row["gap_threshold_ms"], mid
    )


def _year_of(row: sqlite3.Row) -> int:
    try:
        return int(row["local_date"][:4])
    except (TypeError, ValueError):
        return date.today().year


def _samples_of(connection: sqlite3.Connection, session_id: int) -> list[BmsSample]:
    """
    Rebuilt from the index rather than re-read from the CSV, so a page view costs no file IO.
    Only the fields the distance and energy rules touch are filled in.
    """
    rows = connection.execute(
        """
        SELECT t_ms, volts, amps, watts, soc, remaining_ah, lat, lon, alt_m, speed_kmh, accuracy_m,
               hr, pressure_hpa
        FROM samples WHERE session_id = ? ORDER BY t_ms
        """,
        (session_id,),
    ).fetchall()

    return [
        BmsSample(
            at_ms=row["t_ms"],
            volts=row["volts"] or 0.0,
            amps=row["amps"] or 0.0,
            watts=row["watts"] if row["watts"] is not None else 0.0,
            soc=row["soc"] or 0,
            remaining_ah=row["remaining_ah"] or 0.0,
            latitude=row["lat"],
            longitude=row["lon"],
            altitude_m=row["alt_m"],
            speed_kmh=row["speed_kmh"],
            accuracy_m=row["accuracy_m"],
            heart_rate_bpm=row["hr"],
            pressure_hpa=row["pressure_hpa"],
        )
        for row in rows
    ]


@router.get("/{session_id}/series")
def series(
    session_id: int,
    connection: sqlite3.Connection = Depends(deps.connection),
    terrain: Terrain | None = Depends(deps.terrain),
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
    unknown = [f for f in requested if f not in CHARTABLE]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown field(s): {', '.join(unknown)}")
    if not requested:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields requested")

    own = [f for f in requested if f in SERIES_FIELDS]
    attached = [f for f in requested if f in companions.CHANNELS]
    derived = "rider_w" in requested

    # Heart rate the watch put in the recording is already on its clock, with nothing to line up,
    # so when there is one it is *the* heart rate and an attached GPX's is not consulted. Two traces
    # of the same heart on one chart would only invite the question of which to believe.
    sources = {}
    if "hr" in requested:
        if row["max_hr_bpm"] is not None:
            attached.remove("hr")
            own.append("hr")
            sources["hr"] = "recording"
        else:
            sources["hr"] = "companion"

    columns = "".join(f", {name}" for name in own)
    rows = connection.execute(
        f"SELECT t_ms{columns} FROM samples WHERE session_id = ? ORDER BY t_ms",
        (session_id,),
    ).fetchall()

    if not rows:
        return {
            "id": session_id, "t": [], "fields": {f: [] for f in requested}, "gaps": [],
            "sources": sources,
        }

    if derived:
        # Worked out from the samples rather than read from a column, then carried through the
        # same bucketing as the rest, so a surge of effort survives being drawn small too.
        samples = _samples_of(connection, session_id)
        rider = _rider_power(connection, row, samples, terrain)
        watts = effort.display_watts(samples, rider.watts) if rider else [None] * len(rows)
        rows = [{**dict(r), "rider_w": w} for r, w in zip(rows, watts)]
        own.append("rider_w")

    gaps = _gaps([r["t_ms"] for r in rows], row["gap_threshold_ms"])

    if len(rows) <= points:
        body = {
            "t": [r["t_ms"] for r in rows],
            "fields": {f: [r[f] for r in rows] for f in own},
            "downsampled": False,
        }
    else:
        body = {**_bucket(rows, own, points // 2), "downsampled": True}

    # Sampled at the instants already chosen for the recording's own columns — including the
    # synthetic ones bucketing emits — so a heart rate is read at the same moment as the watts
    # beside it.
    body["fields"].update(companions.channels(connection, session_id, body["t"], attached))

    return {"id": session_id, **body, "gaps": gaps, "sources": sources}


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
