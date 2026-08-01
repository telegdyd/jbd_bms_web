"""
SQLite access and schema.

The database is an index, not the archive. Every figure in it is derived from a raw CSV kept on
disk, so losing this file costs a `bmsctl reparse`, not a ride. That is what makes it safe to
change how anything is computed later.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
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


def open_connection(path: Path) -> sqlite3.Connection:
    """A configured connection with no migration check. Cheap — a local file open."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # A generous busy timeout rather than an immediate "database is locked": WAL allows one writer
    # at a time, and an upload indexing thousands of samples can hold that briefly.
    connection = sqlite3.connect(
        path,
        check_same_thread=False,
        timeout=30.0,
        # Autocommit, so that writes go through `transaction()` and its explicit BEGIN IMMEDIATE.
        # See the note there for why the default deferred transactions cannot be made to work.
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    # WAL so a long upload writing samples does not block the browser reading the list.
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def connect(path: Path) -> sqlite3.Connection:
    """Open and bring the schema up to date. For the CLI and tests, which are single-threaded."""
    connection = open_connection(path)
    migrate(connection)
    return connection


class Database:
    """
    One SQLite connection per *request*.

    Not one shared connection: that is unsafe for concurrent use whatever `check_same_thread` says,
    and the ride page asks for its track, series and splits at once, which fails with
    `InterfaceError: bad parameter or other API misuse`.

    Not one per thread either, which is the tempting fix and is subtly wrong here. FastAPI resolves
    a synchronous dependency in the threadpool and may then run the endpoint on a *different*
    thread, so a connection handed out by thread affinity can still be used from somewhere else —
    which shows up as the same error, intermittently, under load.

    Binding the connection to the request instead removes the question. Opening a SQLite file is
    cheap enough that a handful of opens per page view does not register, and separate connections
    against one WAL database are exactly what it is built for.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        # Migrated once, up front, so no request ever races another to create the schema.
        connect(path).close()

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        connection = open_connection(self.path)
        try:
            yield connection
        finally:
            connection.close()

    def close(self) -> None:
        """Nothing is held open between requests, so shutdown has nothing to release."""


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """
    A write transaction that takes its lock up front.

    SQLite's default deferred transaction starts as a reader and asks to become a writer at the
    first INSERT. When two connections do that at once, neither can be granted the upgrade and one
    fails with "database is locked" *immediately* — the busy timeout cannot wait its way out of a
    deadlock. BEGIN IMMEDIATE claims the write lock at the start instead, which is a plain wait
    that the timeout does resolve. It is also what makes `bmsctl import` safe to run while the
    service is up.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")


def migrate(connection: sqlite3.Connection) -> None:
    current = connection.execute("PRAGMA user_version").fetchone()[0]
    for version in range(current, len(SCHEMA)):
        connection.executescript(SCHEMA[version])
        connection.execute(f"PRAGMA user_version = {version + 1}")


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_dicts(rows: Iterator[sqlite3.Row]) -> list[dict]:
    return [dict(row) for row in rows]
