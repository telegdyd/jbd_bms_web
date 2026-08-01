"""
Companion files: a GPX exported from Strava, attached to a recording.

The BMS log knows everything about the pack and nothing about the rider. A watch knows the
opposite. Attaching the watch's export to a session puts heart rate — and cadence, when the file
has it — on the same page as the power trace, without either half pretending to be the other: the
CSV stays the source of truth for every figure, and the companion contributes channels only.

Two rules follow from that and shape everything here.

The GPX is kept whole, exactly as the CSVs are, and the rows in the database are derived from it.
That is what makes `bmsctl reparse` safe to run: rebuilding a session drops its samples, and a
heart rate stored among them would go with them, whereas one kept in its own table beside its own
original comes back.

And the two files come from two clocks, so they are lined up by measurement rather than by faith —
see `align.py`. The offset is stored, not baked into the timestamps, so it stays correctable.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Sequence

from . import align
from .align import Alignment
from .config import Settings
from .db import transaction
from .gpx import GpxError, GpxTrack, parse_gpx

#: Channels a companion can contribute to a session's charts. Not columns on `samples`, so the
#: series endpoint resolves them separately.
CHANNELS = ("hr", "cadence")

#: How far a companion point may be from the moment being asked about before that moment reads as
#: having no data. Widened for files recorded at less than one point a second, so that a watch on
#: smart recording does not come back as a dashed line.
BASE_TOLERANCE_MS = 3_000


class AttachStatus(str, Enum):
    CREATED = "created"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class AttachResult:
    status: AttachStatus
    companion_id: int


def attach(
    connection: sqlite3.Connection,
    settings: Settings,
    session_id: int,
    source_name: str,
    content: bytes,
    offset_ms: int | None = None,
) -> AttachResult:
    """
    Store a GPX against a session and work out where it sits on the session's clock.

    Idempotent on the content hash, per session: attaching the same export twice is a no-op that
    reports the companion already holding it. Raises `GpxError` if the file is not a readable GPX,
    which is a message worth showing to whoever uploaded it.
    """
    digest = hashlib.sha256(content).hexdigest()

    existing = connection.execute(
        "SELECT id FROM companions WHERE session_id = ? AND sha256 = ?", (session_id, digest)
    ).fetchone()
    if existing is not None:
        return AttachResult(AttachStatus.DUPLICATE, existing["id"])

    track = parse_gpx(content)
    alignment = (
        _manual(offset_ms) if offset_ms is not None else align_to_session(connection, session_id, track)
    )
    raw_path = _store_raw(settings, digest, source_name, track, content)

    with transaction(connection):
        companion_id = _insert(
            connection, session_id, digest, source_name, raw_path, track, alignment
        )
    return AttachResult(AttachStatus.CREATED, companion_id)


def align_to_session(
    connection: sqlite3.Connection, session_id: int, track: GpxTrack
) -> Alignment:
    """Match the track's speed against the recording's own. See `align.align`."""
    candidate = align.speeds_from_fixes([(p.at_ms, p.latitude, p.longitude) for p in track.points])
    return align.align(_session_speeds(connection, session_id), candidate)


def realign(connection: sqlite3.Connection, companion_id: int) -> Alignment | None:
    """
    Measure the offset again from what is already indexed — no file read, because the positions
    needed are in `companion_samples`. Worth running after a reparse changes the recording's own
    figures, and it is what the page's "Re-align" button calls.
    """
    row = connection.execute(
        "SELECT session_id FROM companions WHERE id = ?", (companion_id,)
    ).fetchone()
    if row is None:
        return None

    fixes = [
        (r["t_ms"], r["lat"], r["lon"])
        for r in connection.execute(
            "SELECT t_ms, lat, lon FROM companion_samples WHERE companion_id = ? ORDER BY t_ms",
            (companion_id,),
        )
    ]
    alignment = align.align(
        _session_speeds(connection, row["session_id"]), align.speeds_from_fixes(fixes)
    )
    _save_alignment(connection, companion_id, alignment)
    return alignment


def set_offset(connection: sqlite3.Connection, companion_id: int, offset_ms: int) -> bool:
    """A hand-set offset. Kept as `manual` so a later reparse does not quietly overrule it."""
    if connection.execute("SELECT 1 FROM companions WHERE id = ?", (companion_id,)).fetchone() is None:
        return False
    _save_alignment(connection, companion_id, _manual(offset_ms))
    return True


def detach(connection: sqlite3.Connection, settings: Settings, companion_id: int) -> bool:
    """The row goes; the uploaded file moves to the trash directory rather than being unlinked."""
    row = connection.execute(
        "SELECT raw_path FROM companions WHERE id = ?", (companion_id,)
    ).fetchone()
    if row is None:
        return False

    _to_trash(settings, row["raw_path"])
    with transaction(connection):
        connection.execute("DELETE FROM companion_samples WHERE companion_id = ?", (companion_id,))
        connection.execute("DELETE FROM companions WHERE id = ?", (companion_id,))
    return True


def trash_files_for(connection: sqlite3.Connection, settings: Settings, session_id: int) -> None:
    """
    Move a session's companion files aside before the session itself is deleted.

    The rows go on their own — they are `ON DELETE CASCADE` — but a cascade cannot move a file, and
    a GPX left behind with nothing pointing at it is litter.
    """
    for row in connection.execute(
        "SELECT raw_path FROM companions WHERE session_id = ?", (session_id,)
    ).fetchall():
        _to_trash(settings, row["raw_path"])


def list_for(connection: sqlite3.Connection, session_id: int) -> list[dict]:
    """
    Every companion of a session, each with the heart rate figures for the part that actually
    overlaps the recording.

    Computed here rather than stored, because they depend on the offset: a companion nudged by
    twenty seconds must not keep reporting the average it had before it was moved. It is one
    aggregate over a few thousand rows, which is nothing.
    """
    session = connection.execute(
        "SELECT started_at_ms, ended_at_ms FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if session is None:
        return []

    companions = []
    for row in connection.execute(
        "SELECT * FROM companions WHERE session_id = ? ORDER BY id", (session_id,)
    ).fetchall():
        companion = dict(row)
        companion.update(
            _overlap_stats(
                connection,
                row["id"],
                row["offset_ms"],
                session["started_at_ms"],
                session["ended_at_ms"],
            )
        )
        companions.append(companion)

    return companions


def channels(
    connection: sqlite3.Connection,
    session_id: int,
    times: Sequence[int],
    fields: Sequence[str],
) -> dict[str, list]:
    """
    Companion channels sampled at the moments a caller already has — the same instants the chart
    endpoint emits for the recording's own columns, so the two line up point for point.

    Nearest value within a tolerance rather than interpolation: heart rate is a measurement once a
    second, not a continuous function, and inventing values between them would draw a smoother
    trace than the strap ever produced. Outside the tolerance the answer is null, which is how the
    stretch before the watch was started reads as absent rather than as a flat line.
    """
    wanted = [field for field in fields if field in CHANNELS]
    if not wanted:
        return {}

    empty = {field: [None] * len(times) for field in wanted}
    if not times:
        return empty

    columns = ", ".join(f"s.{field}" for field in wanted)
    rows = connection.execute(
        f"""
        SELECT s.t_ms + c.offset_ms AS t_ms, {columns}
        FROM companion_samples s
        JOIN companions c ON c.id = s.companion_id
        WHERE c.session_id = ?
        ORDER BY t_ms
        """,
        (session_id,),
    ).fetchall()
    if not rows:
        return empty

    stamps = [row["t_ms"] for row in rows]
    tolerance = _tolerance(stamps)

    output: dict[str, list] = {field: [] for field in wanted}
    for at_ms in times:
        index = _nearest(stamps, at_ms)
        near = index is not None and abs(stamps[index] - at_ms) <= tolerance
        for field in wanted:
            output[field].append(rows[index][field] if near else None)

    return output


def snapshot(connection: sqlite3.Connection, session_id: int) -> list[dict]:
    """What `restore` needs to bring a session's companions back after it is rebuilt."""
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT id, sha256, source_name, raw_path, offset_ms, offset_source
            FROM companions WHERE session_id = ? ORDER BY id
            """,
            (session_id,),
        )
    ]


def restore(
    connection: sqlite3.Connection,
    settings: Settings,
    session_id: int,
    saved: Sequence[dict],
) -> int:
    """
    Re-attach companions from their stored files after their session has been rebuilt.

    Re-derived rather than copied aside and put back, which is the same bargain the recordings
    themselves make: the file on disk is the truth, so a reparse also picks up any improvement to
    how a GPX is read. A measured offset is measured again — the recording's own figures may have
    moved underneath it — while a hand-set one is left exactly where it was put.

    Must be called inside the caller's transaction, alongside the rebuilt session.
    """
    restored = 0

    for row in saved:
        path = _absolute(settings, row["raw_path"])
        if not path.exists():
            continue
        try:
            track = parse_gpx(path.read_bytes())
        except GpxError:
            continue

        alignment = (
            _manual(row["offset_ms"])
            if row["offset_source"] == "manual"
            else align_to_session(connection, session_id, track)
        )
        _insert(
            connection,
            session_id,
            row["sha256"],
            row["source_name"],
            row["raw_path"],
            track,
            alignment,
            companion_id=row["id"],
        )
        restored += 1

    return restored


def _insert(
    connection: sqlite3.Connection,
    session_id: int,
    digest: str,
    source_name: str,
    raw_path: str,
    track: GpxTrack,
    alignment: Alignment,
    companion_id: int | None = None,
) -> int:
    columns = {
        "id": companion_id,
        "session_id": session_id,
        "sha256": digest,
        "source": "gpx",
        "source_name": source_name,
        "raw_path": raw_path,
        "name": track.name,
        "creator": track.creator,
        "started_at_ms": track.started_at_ms or 0,
        "ended_at_ms": track.ended_at_ms or 0,
        "point_count": len(track.points),
        "hr_count": track.heart_rate_count,
        "cadence_count": sum(1 for p in track.points if p.cadence is not None),
        "offset_ms": alignment.offset_ms,
        "offset_source": alignment.source,
        "correlation": alignment.correlation,
        "overlap_s": alignment.overlap_s,
        "attached_at_ms": int(time.time() * 1000),
    }

    names = [name for name, value in columns.items() if not (name == "id" and value is None)]
    cursor = connection.execute(
        f"INSERT INTO companions ({','.join(names)}) VALUES ({','.join('?' for _ in names)})",
        [columns[name] for name in names],
    )
    new_id = companion_id if companion_id is not None else int(cursor.lastrowid)

    connection.executemany(
        """
        INSERT INTO companion_samples (companion_id, t_ms, hr, cadence, lat, lon, alt_m)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            (new_id, p.at_ms, p.heart_rate, p.cadence, p.latitude, p.longitude, p.altitude_m)
            for p in track.points
        ),
    )
    return new_id


def _save_alignment(
    connection: sqlite3.Connection, companion_id: int, alignment: Alignment
) -> None:
    with transaction(connection):
        connection.execute(
            """
            UPDATE companions
            SET offset_ms = ?, offset_source = ?, correlation = ?, overlap_s = ?
            WHERE id = ?
            """,
            (
                alignment.offset_ms,
                alignment.source,
                alignment.correlation,
                alignment.overlap_s,
                companion_id,
            ),
        )


def _session_speeds(connection: sqlite3.Connection, session_id: int) -> list[tuple[int, float]]:
    """
    The recording's speed curve, which is what a companion is matched against.

    The logged `speed_kmh` is the receiver's own reading and is preferred; a recording without one —
    an older file, or a display log — falls back to the spacing of its fixes, which is the same
    thing the GPX side is reduced to.
    """
    rows = connection.execute(
        "SELECT t_ms, speed_kmh, lat, lon FROM samples WHERE session_id = ? ORDER BY t_ms",
        (session_id,),
    ).fetchall()

    recorded = [(row["t_ms"], row["speed_kmh"]) for row in rows if row["speed_kmh"] is not None]
    if len(recorded) >= 2:
        return recorded

    return align.speeds_from_fixes([(row["t_ms"], row["lat"], row["lon"]) for row in rows])


def _overlap_stats(
    connection: sqlite3.Connection,
    companion_id: int,
    offset_ms: int,
    started_at_ms: int,
    ended_at_ms: int,
) -> dict:
    row = connection.execute(
        """
        SELECT COUNT(hr) AS n, MIN(hr) AS lo, AVG(hr) AS mean, MAX(hr) AS hi,
               MIN(t_ms) AS first_ms, MAX(t_ms) AS last_ms
        FROM companion_samples
        WHERE companion_id = ? AND t_ms + ? BETWEEN ? AND ?
        """,
        (companion_id, offset_ms, started_at_ms, ended_at_ms),
    ).fetchone()

    covered = (
        (row["last_ms"] - row["first_ms"]) // 1000
        if row["first_ms"] is not None and row["last_ms"] is not None
        else 0
    )
    return {
        "hr_in_session": row["n"] or 0,
        "hr_min": row["lo"],
        "hr_avg": round(row["mean"], 1) if row["mean"] is not None else None,
        "hr_max": row["hi"],
        # Seconds of the session the companion actually has something to say about. A GPX started
        # halfway through covers half a ride, and the page should be able to say so.
        "covered_s": covered,
    }


def _tolerance(stamps: Sequence[int]) -> int:
    """Two point-spacings, so a file recorded every five seconds is still a continuous trace."""
    if len(stamps) < 3:
        return BASE_TOLERANCE_MS
    deltas = sorted(stamps[i + 1] - stamps[i] for i in range(len(stamps) - 1))
    return max(BASE_TOLERANCE_MS, deltas[len(deltas) // 2] * 2)


def _nearest(stamps: Sequence[int], at_ms: int) -> int | None:
    if not stamps:
        return None
    index = bisect_left(stamps, at_ms)
    if index == 0:
        return 0
    if index >= len(stamps):
        return len(stamps) - 1
    before = at_ms - stamps[index - 1]
    after = stamps[index] - at_ms
    return index - 1 if before <= after else index


def _manual(offset_ms: int) -> Alignment:
    return Alignment(offset_ms=offset_ms, correlation=None, overlap_s=0, source="manual")


def _store_raw(
    settings: Settings, digest: str, source_name: str, track: GpxTrack, content: bytes
) -> str:
    """
    Filed by the month the *track* was recorded, so the directory stays browsable by hand next to
    the recordings. In UTC, unlike the CSVs: a GPX carries no offset of its own to place it in.
    """
    started = track.started_at_ms or int(time.time() * 1000)
    day = datetime.fromtimestamp(started / 1000, tz=timezone.utc)

    directory = settings.companions_dir / f"{day.year:04d}" / f"{day.month:02d}"
    directory.mkdir(parents=True, exist_ok=True)

    path = directory / f"{digest[:8]}_{_safe_name(source_name)}"
    path.write_bytes(content)
    # Relative to the data directory, so the volume can be moved or remounted elsewhere.
    return str(path.relative_to(settings.data_dir))


def _absolute(settings: Settings, raw_path: str) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else settings.data_dir / path


def _to_trash(settings: Settings, raw_path: str) -> None:
    path = _absolute(settings, raw_path)
    if not path.exists():
        return

    settings.trash_dir.mkdir(parents=True, exist_ok=True)
    target = settings.trash_dir / path.name
    if target.exists():
        target = settings.trash_dir / f"{int(time.time())}_{path.name}"
    path.rename(target)


def _safe_name(source_name: str) -> str:
    """The name arrives from a client, so it never gets to escape the companions directory."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", Path(source_name).name)
    return cleaned[:80] or "companion.gpx"
