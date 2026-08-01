"""
Command-line access to the ingest core.

`summarise` exists for one job: pull a real recording off the phone, run it through here, and check
the figures against what the app's own summary card shows for the same file. Synthetic fixtures
prove the logic is self-consistent; only a real recording proves the port agrees with the phone.

    uv run python -m bmsweb.cli summarise tests/fixtures/real/20260801-101500_bms.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import SCHEMA_VERSION, db
from .config import load_settings
from .ingest import ingest, reparse
from .parse import SessionKind, parse_csv
from .simplify import simplify
from .summary import summarise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bmsctl", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    summarise_cmd = sub.add_parser("summarise", help="print the summary for a recording")
    summarise_cmd.add_argument("path", type=Path, nargs="+")

    import_cmd = sub.add_parser(
        "import", help="load recordings straight off disk, without going through the phone"
    )
    import_cmd.add_argument("path", type=Path, nargs="+", help="CSV files or directories of them")

    reparse_cmd = sub.add_parser(
        "reparse", help="rebuild stored figures from the raw CSVs after a logic change"
    )
    reparse_cmd.add_argument("ids", type=int, nargs="*", help="default: everything out of date")
    reparse_cmd.add_argument(
        "--all", action="store_true", help="rebuild every session, not just stale ones"
    )

    args = parser.parse_args(argv)

    if args.command == "summarise":
        for i, path in enumerate(args.path):
            if i:
                print()
            _print_summary(path)
        return 0

    if args.command == "import":
        return _import(args.path)

    if args.command == "reparse":
        return _reparse(args.ids, args.all)

    return 0


def _import(paths: list[Path]) -> int:
    settings = load_settings()
    settings.prepare()
    connection = db.connect(settings.database_path)

    files: list[Path] = []
    for path in paths:
        files.extend(sorted(path.glob("*.csv")) if path.is_dir() else [path])

    created = duplicates = 0
    for file in files:
        result = ingest(connection, settings, file.name, file.read_bytes())
        if result.status.value == "created":
            created += 1
        else:
            duplicates += 1
        print(f"{result.status.value:9} {file.name} → id {result.session_id}")

    print(f"\n{created} imported, {duplicates} already present")
    connection.close()
    return 0


def _reparse(ids: list[int], everything: bool) -> int:
    settings = load_settings()
    settings.prepare()
    connection = db.connect(settings.database_path)

    if ids:
        targets = ids
    else:
        query = "SELECT id FROM sessions"
        params: tuple = ()
        if not everything:
            query += " WHERE schema_version < ?"
            params = (SCHEMA_VERSION,)
        targets = [row["id"] for row in connection.execute(query + " ORDER BY id", params)]

    if not targets:
        print(f"Nothing to do — every session is at schema version {SCHEMA_VERSION}.")
        return 0

    done = 0
    for session_id in targets:
        if reparse(connection, settings, session_id):
            done += 1
        else:
            print(f"skipped {session_id}: no session, or its raw file is missing")

    print(f"{done} of {len(targets)} rebuilt at schema version {SCHEMA_VERSION}")
    connection.close()
    return 0


def _print_summary(path: Path) -> None:
    session = parse_csv(path.read_text(encoding="utf-8", errors="replace"))
    s = summarise(session)

    print(f"{path.name}")
    print(f"  kind          {session.kind.value}")
    print(f"  samples       {s.sample_count}")
    print(f"  duration      {_duration(s.duration_ms // 1000)}")

    if session.kind is SessionKind.BMS:
        print(f"  cells/temps   {session.cell_count} / {session.temp_count}")
        print(f"  discharged    {s.discharged_wh:.1f} Wh")
        print(f"  charged       {s.charged_wh:.1f} Wh")
        print(f"  peak out/in   {s.peak_discharge_w:.0f} W / {s.peak_charge_w:.0f} W")
        print(f"  voltage       {s.min_volts:.2f} – {s.max_volts:.2f} V")
        if s.max_delta_mv is not None:
            print(f"  worst spread  {s.max_delta_mv} mV")
        if s.min_temp_c is not None and s.max_temp_c is not None:
            print(f"  temperature   {s.min_temp_c:.1f} – {s.max_temp_c:.1f} °C")

    print(f"  SOC           {s.soc_start}% → {s.soc_end}%")

    if s.distance_km > 0:
        print(f"  distance      {s.distance_km:.2f} km")
        if s.wh_per_km is not None:
            print(f"  efficiency    {s.wh_per_km:.1f} Wh/km")
        if s.max_speed_kmh is not None:
            print(f"  top speed     {s.max_speed_kmh:.1f} km/h")
        if s.moving_seconds > 0:
            average = s.distance_km / (s.moving_seconds / 3600.0)
            print(f"  moving        {_duration(s.moving_seconds)} · {average:.1f} km/h average")

    if session.has_location:
        print(f"  route points  {len(simplify(session.samples))} drawn of {s.sample_count}")

    if s.gap_count:
        plural = "" if s.gap_count == 1 else "s"
        print(
            f"  dropouts      {s.gap_count} gap{plural} totalling {_duration(s.gap_ms // 1000)}"
            " — excluded from the watt-hour totals"
        )


def _duration(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


if __name__ == "__main__":
    sys.exit(main())
