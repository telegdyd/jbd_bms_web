"""
SQLite access and schema.

The database is an index, not the archive. Every figure in it is derived from a raw CSV kept on
disk, so losing this file costs a `bmsctl reparse`, not a ride. That is what makes it safe to
change how anything is computed later.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

#: Structure of the database itself, distinct from bmsweb.SCHEMA_VERSION, which tracks the parsing
#: and summary logic. Adding a migration here does not force a reparse; bumping that one does.
SCHEMA = [
    # 1 — initial
    """
    CREATE TABLE sessions (
        id             INTEGER PRIMARY KEY,
        sha256         TEXT    NOT NULL UNIQUE,
        source_name    TEXT    NOT NULL,
        raw_path       TEXT    NOT NULL,
        kind           TEXT    NOT NULL,
        device_label   TEXT,
        started_at_ms  INTEGER NOT NULL,
        ended_at_ms    INTEGER NOT NULL,
        tz_offset_min  INTEGER,
        local_date     TEXT    NOT NULL,
        duration_ms    INTEGER NOT NULL DEFAULT 0,
        sample_count   INTEGER NOT NULL DEFAULT 0,
        cell_count     INTEGER NOT NULL DEFAULT 0,
        temp_count     INTEGER NOT NULL DEFAULT 0,
        gap_threshold_ms INTEGER NOT NULL DEFAULT 5000,
        has_location   INTEGER NOT NULL DEFAULT 0,
        is_ride        INTEGER NOT NULL DEFAULT 0,

        charged_wh     REAL, discharged_wh REAL,
        peak_charge_w  REAL, peak_discharge_w REAL,
        min_volts      REAL, max_volts REAL,
        min_temp_c     REAL, max_temp_c REAL,
        max_delta_mv   INTEGER,
        soc_start      INTEGER, soc_end INTEGER,
        gap_count      INTEGER NOT NULL DEFAULT 0,
        gap_ms         INTEGER NOT NULL DEFAULT 0,
        distance_km    REAL NOT NULL DEFAULT 0,
        moving_seconds INTEGER NOT NULL DEFAULT 0,
        max_speed_kmh  REAL,
        wh_per_km      REAL,

        min_lat REAL, min_lon REAL, max_lat REAL, max_lon REAL,
        polyline TEXT,

        title TEXT, notes TEXT,

        uploaded_at_ms INTEGER NOT NULL,
        parsed_at_ms   INTEGER NOT NULL,
        schema_version INTEGER NOT NULL
    );

    CREATE INDEX sessions_started ON sessions (started_at_ms DESC);
    CREATE INDEX sessions_local_date ON sessions (local_date);

    CREATE TABLE samples (
        session_id   INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        t_ms         INTEGER NOT NULL,
        volts        REAL, amps REAL, watts REAL,
        soc          INTEGER, remaining_ah REAL,
        delta_mv     INTEGER, min_cell_mv INTEGER, max_cell_mv INTEGER,
        lat          REAL, lon REAL, alt_m REAL, speed_kmh REAL, accuracy_m REAL,
        -- Counts vary per pack, so these are JSON arrays rather than columns. A fixed-width table
        -- would break the first time a different pack is logged.
        cells_mv     TEXT, temps_c TEXT,
        balance_bits INTEGER,
        -- Kind-specific fields that do not deserve a column of their own: the EKD01's assist
        -- level, trip and odometer readings.
        extra        TEXT,
        PRIMARY KEY (session_id, t_ms)
    ) WITHOUT ROWID;
    """,
]


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    # WAL so a long upload writing samples does not block the browser reading the list.
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA synchronous = NORMAL")
    migrate(connection)
    return connection


def migrate(connection: sqlite3.Connection) -> None:
    current = connection.execute("PRAGMA user_version").fetchone()[0]
    for version in range(current, len(SCHEMA)):
        connection.executescript(SCHEMA[version])
        connection.execute(f"PRAGMA user_version = {version + 1}")
    connection.commit()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_dicts(rows: Iterator[sqlite3.Row]) -> list[dict]:
    return [dict(row) for row in rows]
