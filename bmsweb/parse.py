"""
CSV → samples.

A port of the app's `LogRepository.parse`, and it has to stay one: the phone and this service must
read the same file the same way, or a ride reads differently depending on where you look at it.

Everything the app tolerates is tolerated here — a session killed mid-write leaves a short final
row, older recordings have no location columns at all, and the cell and temperature counts are
properties of the individual file rather than of the format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Iterable, Sequence

DEFAULT_GAP_MS = 5_000

_CELL_COLUMN = re.compile(r"^cell\d+_mv$")

# The header the EKD01 writer emits. Kind is decided from the columns, not the filename: both
# writers use the same `yyyyMMdd-HHmmss_label.csv` naming and the same directory.
_EKD01_MARKERS = frozenset({"speed_kmh", "assist_level", "odometer_km", "battery_percent"})


class SessionKind(str, Enum):
    BMS = "bms"
    EKD01 = "ekd01"


@dataclass(frozen=True, slots=True)
class BmsSample:
    at_ms: int
    volts: float
    amps: float
    watts: float
    soc: int
    remaining_ah: float
    temps_c: tuple[float | None, ...] = ()
    cells_mv: tuple[int | None, ...] = ()
    delta_mv: int | None = None
    min_cell_mv: int | None = None
    max_cell_mv: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    altitude_m: float | None = None
    speed_kmh: float | None = None
    accuracy_m: float | None = None
    balance_bits: int | None = None

    @property
    def has_location(self) -> bool:
        return self.latitude is not None and self.longitude is not None


@dataclass(frozen=True, slots=True)
class Ekd01Sample:
    at_ms: int
    speed_kmh: float
    assist_level: int
    trip_km: float
    odometer_km: float
    battery_bars: int
    battery_percent: int

    # The display puts no GPS on the BLE link, so an EKD01 recording never has a route of its own.
    @property
    def has_location(self) -> bool:
        return False


Sample = BmsSample | Ekd01Sample


@dataclass(frozen=True, slots=True)
class ParsedSession:
    kind: SessionKind
    samples: tuple[Sample, ...]
    cell_count: int
    temp_count: int
    #: Spacing above which two samples are treated as separated by a dropout.
    gap_threshold_ms: int
    #: From the first row's timestamp. Kept so local-day grouping matches what the rider saw,
    #: including rides recorded in another timezone or across a DST change.
    tz_offset_min: int | None

    @property
    def started_at_ms(self) -> int | None:
        return self.samples[0].at_ms if self.samples else None

    @property
    def ended_at_ms(self) -> int | None:
        return self.samples[-1].at_ms if self.samples else None

    @property
    def has_location(self) -> bool:
        return any(s.has_location for s in self.samples)


def parse_csv(text: str) -> ParsedSession:
    """Parse a whole recording. `text` is the file as written by the phone, header included."""
    lines = text.splitlines()
    if not lines:
        return ParsedSession(SessionKind.BMS, (), 0, 0, DEFAULT_GAP_MS, None)

    header = lines[0].split(",")
    if detect_kind(header) is SessionKind.EKD01:
        return _parse_ekd01(header, lines[1:])
    return _parse_bms(header, lines[1:])


def detect_kind(header: Sequence[str]) -> SessionKind:
    """
    A BMS recording always has `volts`; an EKD01 recording has the display's own columns and no
    electrical data at all, because the display does not transmit any.
    """
    columns = {c.strip() for c in header}
    if "volts" not in columns and _EKD01_MARKERS.issubset(columns):
        return SessionKind.EKD01
    return SessionKind.BMS


def _parse_bms(header: list[str], rows: Iterable[str]) -> ParsedSession:
    temp_count = sum(1 for c in header if c.startswith("temp"))
    cell_count = sum(1 for c in header if _CELL_COLUMN.match(c))

    # The leading seven columns are fixed by the writer; everything after them is positional
    # relative to the counts above.
    temp_start = 7
    cell_start = temp_start + temp_count
    delta_index = cell_start + cell_count

    # Located by name, so a recording made before location logging existed simply has none.
    lat_index = _index_of(header, "latitude")
    lon_index = _index_of(header, "longitude")
    alt_index = _index_of(header, "altitude_m")
    speed_index = _index_of(header, "speed_kmh")
    accuracy_index = _index_of(header, "gps_accuracy_m")
    balance_index = _index_of(header, "balance_bits")

    samples: list[BmsSample] = []
    tz_offset_min: int | None = None

    for raw in rows:
        if not raw.strip():
            continue
        c = raw.split(",")
        # A session killed mid-write leaves a short final row; drop it rather than fail.
        if len(c) < delta_index + 3:
            continue

        moment = _parse_timestamp(c[0])
        if moment is None:
            continue
        if tz_offset_min is None:
            tz_offset_min = _offset_minutes(moment)

        volts = _to_float(c[2])
        amps = _to_float(c[3])
        watts = _to_float(c[4])
        if volts is None or amps is None or watts is None:
            continue

        samples.append(
            BmsSample(
                at_ms=_epoch_ms(moment),
                volts=volts,
                amps=amps,
                watts=watts,
                soc=_to_int(c[5]) or 0,
                remaining_ah=_to_float(c[6]) or 0.0,
                temps_c=tuple(_to_float(c[temp_start + i]) for i in range(temp_count)),
                cells_mv=tuple(_to_int(c[cell_start + i]) for i in range(cell_count)),
                delta_mv=_to_int(c[delta_index]),
                min_cell_mv=_to_int(c[delta_index + 1]),
                max_cell_mv=_to_int(c[delta_index + 2]),
                latitude=_to_float(_at(c, lat_index)),
                longitude=_to_float(_at(c, lon_index)),
                altitude_m=_to_float(_at(c, alt_index)),
                speed_kmh=_to_float(_at(c, speed_index)),
                accuracy_m=_to_float(_at(c, accuracy_index)),
                balance_bits=_to_int(_at(c, balance_index)),
            )
        )

    return ParsedSession(
        kind=SessionKind.BMS,
        samples=tuple(samples),
        cell_count=cell_count,
        temp_count=temp_count,
        gap_threshold_ms=gap_threshold_ms([s.at_ms for s in samples]),
        tz_offset_min=tz_offset_min,
    )


def _parse_ekd01(header: list[str], rows: Iterable[str]) -> ParsedSession:
    speed_index = _index_of(header, "speed_kmh")
    assist_index = _index_of(header, "assist_level")
    trip_index = _index_of(header, "trip_km")
    odometer_index = _index_of(header, "odometer_km")
    bars_index = _index_of(header, "battery_bars")
    percent_index = _index_of(header, "battery_percent")

    samples: list[Ekd01Sample] = []
    tz_offset_min: int | None = None

    for raw in rows:
        if not raw.strip():
            continue
        c = raw.split(",")

        moment = _parse_timestamp(c[0])
        if moment is None:
            continue
        if tz_offset_min is None:
            tz_offset_min = _offset_minutes(moment)

        speed = _to_float(_at(c, speed_index))
        if speed is None:
            continue

        samples.append(
            Ekd01Sample(
                at_ms=_epoch_ms(moment),
                speed_kmh=speed,
                assist_level=_to_int(_at(c, assist_index)) or 0,
                trip_km=_to_float(_at(c, trip_index)) or 0.0,
                odometer_km=_to_float(_at(c, odometer_index)) or 0.0,
                battery_bars=_to_int(_at(c, bars_index)) or 0,
                battery_percent=_to_int(_at(c, percent_index)) or 0,
            )
        )

    return ParsedSession(
        kind=SessionKind.EKD01,
        samples=tuple(samples),
        cell_count=0,
        temp_count=0,
        gap_threshold_ms=gap_threshold_ms([s.at_ms for s in samples]),
        tz_offset_min=tz_offset_min,
    )


def gap_threshold_ms(times: Sequence[int]) -> int:
    """
    Derived from the median spacing rather than assumed, because the log interval is
    configurable — a 30 s session must not read as one continuous dropout.
    """
    if len(times) < 3:
        return DEFAULT_GAP_MS
    deltas = sorted(times[i + 1] - times[i] for i in range(len(times) - 1))
    median = deltas[len(deltas) // 2]
    return max(median * 3, DEFAULT_GAP_MS)


def _index_of(header: Sequence[str], name: str) -> int:
    try:
        return header.index(name)
    except ValueError:
        return -1


def _at(cells: Sequence[str], index: int) -> str | None:
    """
    Guards the absent-column case. A bare `cells[index]` would be a real bug here: the app's
    `getOrNull(-1)` yields null, while Python's negative indexing would happily return the *last*
    column, so a recording without GPS would report its balance bitfield as a latitude.
    """
    if index < 0 or index >= len(cells):
        return None
    return cells[index]


def _parse_timestamp(value: str) -> datetime | None:
    """
    Lenient ISO parsing, matching the app's use of ISO_OFFSET_DATE_TIME rather than the writer's
    fixed-width pattern, so files written before the format was pinned still load.

    A timestamp without an offset is rejected, as it is on the phone: without one there is no way
    to place the sample on a real timeline, and guessing the server's zone would silently shift it.
    """
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _epoch_ms(moment: datetime) -> int:
    return round(moment.timestamp() * 1000)


def _offset_minutes(moment: datetime) -> int | None:
    offset = moment.utcoffset()
    return None if offset is None else round(offset.total_seconds() / 60)


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
