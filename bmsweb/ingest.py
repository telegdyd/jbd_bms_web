"""
Upload → raw file on disk → parsed rows in SQLite.

The raw CSV is written first and never touched again. Everything else in the database is derived
from it, so a change to how a figure is computed is a `reparse` away rather than a lost ride.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

from . import SCHEMA_VERSION, polyline
from .config import Settings
from .db import transaction
from .parse import BmsSample, Ekd01Sample, ParsedSession, SessionKind, parse_csv
from .simplify import simplify
from .summary import Summary, summarise

#: A geo-tagged session shorter than this is a walk to the shed, not a ride worth its own page.
RIDE_DISTANCE_KM = 0.2

#: `yyyyMMdd-HHmmss_<device-label>.csv`, as both writers name their files.
FILE_NAME = re.compile(r"^(\d{8}-\d{6})_(.+)\.csv$", re.IGNORECASE)


class IngestStatus(str, Enum):
    CREATED = "created"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class IngestResult:
    status: IngestStatus
    session_id: int
    sha256: str


def sha256_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def ingest(
    connection: sqlite3.Connection,
    settings: Settings,
    source_name: str,
    content: bytes,
) -> IngestResult:
    """
    Idempotent on the content hash. Re-uploading a file the server already has is a no-op that
    reports which session it was, which is what lets the phone retry blindly after a dropped wifi
    connection without worrying about duplicates.
    """
    digest = sha256_of(content)

    existing = connection.execute(
        "SELECT id FROM sessions WHERE sha256 = ?", (digest,)
    ).fetchone()
    if existing is not None:
        return IngestResult(IngestStatus.DUPLICATE, existing["id"], digest)

    text = content.decode("utf-8", errors="replace")
    session = parse_csv(text)
    started_at_ms, tz_offset_min = _start_of(session, source_name)

    raw_path = _store_raw(settings, digest, source_name, started_at_ms, tz_offset_min, content)
    with transaction(connection):
        session_id = _insert(
            connection, session, source_name, digest, raw_path, settings, started_at_ms, tz_offset_min
        )
    return IngestResult(IngestStatus.CREATED, session_id, digest)


def reparse(connection: sqlite3.Connection, settings: Settings, session_id: int) -> bool:
    """
    Rebuild one session from its raw CSV, keeping its id, title and notes.

    This is the whole reason the originals are kept: when a summary rule changes, the history
    changes with it instead of being frozen at whatever the code did the day it was uploaded.
    """
    row = connection.execute(
        "SELECT sha256, source_name, raw_path, title, notes FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        return False

    path = Path(row["raw_path"])
    if not path.is_absolute():
        path = settings.data_dir / path
    if not path.exists():
        return False

    session = parse_csv(path.read_text(encoding="utf-8", errors="replace"))
    started_at_ms, tz_offset_min = _start_of(session, row["source_name"])

    with transaction(connection):
        connection.execute("DELETE FROM samples WHERE session_id = ?", (session_id,))
        connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        _insert(
            connection,
            session,
            row["source_name"],
            row["sha256"],
            row["raw_path"],
            settings,
            started_at_ms,
            tz_offset_min,
            session_id=session_id,
            title=row["title"],
            notes=row["notes"],
        )
    return True


def delete(connection: sqlite3.Connection, settings: Settings, session_id: int) -> bool:
    """The index row goes; the original is moved aside rather than unlinked."""
    row = connection.execute(
        "SELECT raw_path FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return False

    path = Path(row["raw_path"])
    if not path.is_absolute():
        path = settings.data_dir / path
    if path.exists():
        settings.trash_dir.mkdir(parents=True, exist_ok=True)
        target = settings.trash_dir / path.name
        if target.exists():
            target = settings.trash_dir / f"{int(time.time())}_{path.name}"
        path.rename(target)

    with transaction(connection):
        connection.execute("DELETE FROM samples WHERE session_id = ?", (session_id,))
        connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    return True


def _store_raw(
    settings: Settings,
    digest: str,
    source_name: str,
    started_at_ms: int,
    tz_offset_min: int | None,
    content: bytes,
) -> str:
    """
    Filed by the month the recording was made, so the archive stays browsable by hand. The hash
    prefix keeps two same-second recordings from different devices apart.
    """
    day = _local_datetime(started_at_ms, tz_offset_min)
    directory = settings.raw_dir / f"{day.year:04d}" / f"{day.month:02d}"
    directory.mkdir(parents=True, exist_ok=True)

    path = directory / f"{digest[:8]}_{_safe_name(source_name)}"
    path.write_bytes(content)
    # Stored relative to the data directory so the volume can be moved or remounted elsewhere.
    return str(path.relative_to(settings.data_dir))


def _insert(
    connection: sqlite3.Connection,
    session: ParsedSession,
    source_name: str,
    digest: str,
    raw_path: str,
    settings: Settings,
    started_at_ms: int,
    tz_offset_min: int | None,
    session_id: int | None = None,
    title: str | None = None,
    notes: str | None = None,
) -> int:
    s: Summary = summarise(session)
    now_ms = int(time.time() * 1000)

    track = simplify(session.samples) if session.has_location else []
    located = [x for x in session.samples if x.has_location]

    columns = {
        "id": session_id,
        "sha256": digest,
        "source_name": source_name,
        "raw_path": raw_path,
        "kind": session.kind.value,
        "device_label": _device_label(source_name),
        "started_at_ms": started_at_ms,
        "ended_at_ms": session.ended_at_ms or started_at_ms,
        "tz_offset_min": tz_offset_min,
        "local_date": _local_datetime(started_at_ms, tz_offset_min).date().isoformat(),
        "duration_ms": s.duration_ms,
        "sample_count": s.sample_count,
        "cell_count": session.cell_count,
        "temp_count": session.temp_count,
        "gap_threshold_ms": session.gap_threshold_ms,
        "has_location": int(session.has_location),
        "is_ride": int(session.has_location and s.distance_km >= RIDE_DISTANCE_KM),
        "charged_wh": s.charged_wh,
        "discharged_wh": s.discharged_wh,
        "peak_charge_w": s.peak_charge_w,
        "peak_discharge_w": s.peak_discharge_w,
        "min_volts": s.min_volts,
        "max_volts": s.max_volts,
        "min_temp_c": s.min_temp_c,
        "max_temp_c": s.max_temp_c,
        "max_delta_mv": s.max_delta_mv,
        "soc_start": s.soc_start,
        "soc_end": s.soc_end,
        "gap_count": s.gap_count,
        "gap_ms": s.gap_ms,
        "distance_km": s.distance_km,
        "moving_seconds": s.moving_seconds,
        "max_speed_kmh": s.max_speed_kmh,
        "wh_per_km": s.wh_per_km,
        "min_lat": min((x.latitude for x in located), default=None),
        "min_lon": min((x.longitude for x in located), default=None),
        "max_lat": max((x.latitude for x in located), default=None),
        "max_lon": max((x.longitude for x in located), default=None),
        "polyline": polyline.encode_samples(track) if track else None,
        "title": title,
        "notes": notes,
        "uploaded_at_ms": now_ms,
        "parsed_at_ms": now_ms,
        "schema_version": SCHEMA_VERSION,
    }

    names = [name for name, value in columns.items() if not (name == "id" and value is None)]
    placeholders = ",".join("?" for _ in names)
    cursor = connection.execute(
        f"INSERT INTO sessions ({','.join(names)}) VALUES ({placeholders})",
        [columns[name] for name in names],
    )
    new_id = session_id if session_id is not None else int(cursor.lastrowid)

    connection.executemany(
        """
        INSERT INTO samples (
            session_id, t_ms, volts, amps, watts, soc, remaining_ah,
            delta_mv, min_cell_mv, max_cell_mv,
            lat, lon, alt_m, speed_kmh, accuracy_m,
            cells_mv, temps_c, balance_bits, extra
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (_sample_row(new_id, sample) for sample in session.samples),
    )
    # No commit here: every caller wraps this in `transaction()`, so that a reparse replaces a
    # session atomically instead of leaving it deleted if the rebuild fails.
    return new_id


def _sample_row(session_id: int, sample: BmsSample | Ekd01Sample) -> tuple:
    if isinstance(sample, Ekd01Sample):
        # The display reports no electrical data, so those columns stay null rather than being
        # filled with plausible-looking zeros. Battery percentage is the only state of charge it has.
        return (
            session_id, sample.at_ms,
            None, None, None,
            sample.battery_percent, None,
            None, None, None,
            None, None, None, sample.speed_kmh, None,
            None, None, None,
            json.dumps(
                {
                    "assist_level": sample.assist_level,
                    "trip_km": sample.trip_km,
                    "odometer_km": sample.odometer_km,
                    "battery_bars": sample.battery_bars,
                }
            ),
        )

    return (
        session_id, sample.at_ms,
        sample.volts, sample.amps, sample.watts,
        sample.soc, sample.remaining_ah,
        sample.delta_mv, sample.min_cell_mv, sample.max_cell_mv,
        sample.latitude, sample.longitude, sample.altitude_m, sample.speed_kmh, sample.accuracy_m,
        json.dumps(sample.cells_mv) if sample.cells_mv else None,
        json.dumps(sample.temps_c) if sample.temps_c else None,
        sample.balance_bits,
        None,
    )


def _start_of(session: ParsedSession, source_name: str) -> tuple[int, int | None]:
    """
    The first sample's timestamp, which is authoritative because it carries its own offset.

    A recording with no readable samples still gets stored — a session started and stopped by
    accident is junk, but making the phone retry it forever is worse — so it falls back to the
    stamp in the filename, and finally to now.
    """
    if session.started_at_ms is not None:
        return session.started_at_ms, session.tz_offset_min

    match = FILE_NAME.match(source_name)
    if match:
        try:
            naive = datetime.strptime(match.group(1), "%Y%m%d-%H%M%S")
            return int(naive.replace(tzinfo=timezone.utc).timestamp() * 1000), None
        except ValueError:
            pass

    return int(time.time() * 1000), None


def _local_datetime(at_ms: int, tz_offset_min: int | None) -> datetime:
    """
    The moment as the rider saw it. Without this a ride that finished after midnight UTC, or one
    recorded in another timezone, lands under the wrong day in the calendar.
    """
    zone = timezone(timedelta(minutes=tz_offset_min)) if tz_offset_min is not None else timezone.utc
    return datetime.fromtimestamp(at_ms / 1000, tz=zone)


def _device_label(source_name: str) -> str | None:
    match = FILE_NAME.match(source_name)
    return match.group(2) if match else None


def _safe_name(source_name: str) -> str:
    """The name arrives from a client, so it never gets to escape the raw directory."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", Path(source_name).name)
    return cleaned[:80] or "recording.csv"
