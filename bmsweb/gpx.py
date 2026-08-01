"""
GPX → points.

The file this is written for is the one Strava hands back from "Export GPX": one trackpoint a
second, each with a UTC time, and heart rate hidden inside the Garmin `TrackPointExtension` that
every fitness exporter has settled on. Nothing here is Strava-specific, though — Garmin Connect,
Wahoo and gpsbabel all write the same shapes, so tags are matched by their local name and whatever
namespace prefix the producer chose is ignored.

This is a *companion* to a recording, never a recording in its own right: the BMS CSV remains the
thing a session is made of, and a GPX only adds channels the pack could not know about.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from xml.etree import ElementTree

#: Anything larger is not a ride, and the parser builds a whole tree in memory.
MAX_BYTES = 64 * 1024 * 1024

_DOCTYPE = re.compile(rb"<!DOCTYPE", re.IGNORECASE)

#: Local tag names carrying a heart rate, in the two spellings in circulation: `gpxtpx:hr` from
#: Garmin's TrackPointExtension v1/v2, and the bare `hr` some exporters emit instead.
_HR_TAGS = frozenset({"hr", "heartrate"})
_CADENCE_TAGS = frozenset({"cad", "cadence"})


class GpxError(ValueError):
    """The upload is not a GPX file this can read. Always safe to show to whoever uploaded it."""


@dataclass(frozen=True, slots=True)
class GpxPoint:
    at_ms: int
    latitude: float | None = None
    longitude: float | None = None
    altitude_m: float | None = None
    heart_rate: int | None = None
    cadence: int | None = None


@dataclass(frozen=True, slots=True)
class GpxTrack:
    points: tuple[GpxPoint, ...]
    #: `<trk><name>`, which is the activity title on Strava. Worth keeping: it is how the rider
    #: recognises which of three exports they attached.
    name: str | None
    #: The `creator` attribute — "StravaGPX", "Garmin Connect", and so on.
    creator: str | None

    @property
    def started_at_ms(self) -> int | None:
        return self.points[0].at_ms if self.points else None

    @property
    def ended_at_ms(self) -> int | None:
        return self.points[-1].at_ms if self.points else None

    @property
    def heart_rate_count(self) -> int:
        return sum(1 for p in self.points if p.heart_rate is not None)


def parse_gpx(content: bytes) -> GpxTrack:
    """
    Parse a whole file. Points arrive in time order regardless of how the file was laid out, and a
    point without a usable time is dropped — it cannot be placed against a recording, which is the
    only reason this file is here.
    """
    if len(content) > MAX_BYTES:
        raise GpxError("That file is far larger than any ride's GPX.")

    # ElementTree expands internal entities, so a file declaring a few of them recursively costs
    # gigabytes of memory before anything of ours runs. No GPX exporter emits a DOCTYPE, so
    # refusing the whole class outright is free.
    if _DOCTYPE.search(content[:4096]):
        raise GpxError("A GPX file with a DOCTYPE declaration is not accepted.")

    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise GpxError(f"Not readable as XML: {error}") from error

    if _local(root.tag) != "gpx":
        raise GpxError(f"Not a GPX file — the document element is <{_local(root.tag)}>.")

    points: list[GpxPoint] = []
    for element in root.iter():
        if _local(element.tag) in ("trkpt", "rtept"):
            point = _point(element)
            if point is not None:
                points.append(point)

    if not points:
        # Almost always a route rather than a recording: a planned line has coordinates but no
        # times, and there is nothing to line up against a ride.
        raise GpxError("No timestamped track points in that file.")

    points.sort(key=lambda p: p.at_ms)
    return GpxTrack(points=tuple(points), name=_name(root), creator=root.get("creator"))


def _point(element: ElementTree.Element) -> GpxPoint | None:
    at_ms = altitude = heart_rate = cadence = None

    for child in element.iter():
        tag = _local(child.tag)
        text = (child.text or "").strip()
        if not text:
            continue
        if tag == "time" and at_ms is None:
            at_ms = _epoch_ms(text)
        elif tag == "ele" and altitude is None:
            altitude = _to_float(text)
        elif tag in _HR_TAGS and heart_rate is None:
            heart_rate = _to_int(text)
        elif tag in _CADENCE_TAGS and cadence is None:
            cadence = _to_int(text)

    if at_ms is None:
        return None

    return GpxPoint(
        at_ms=at_ms,
        latitude=_to_float(element.get("lat")),
        longitude=_to_float(element.get("lon")),
        altitude_m=altitude,
        # A chest strap that has not picked up yet reports 0, which is not a heart rate; storing it
        # would put a cliff to the floor at the start of every chart.
        heart_rate=heart_rate if heart_rate and heart_rate > 0 else None,
        cadence=cadence if cadence is not None and cadence >= 0 else None,
    )


def _name(root: ElementTree.Element) -> str | None:
    """The track's name, falling back to the metadata block some exporters use instead."""
    for element in root.iter():
        if _local(element.tag) == "name" and (element.text or "").strip():
            return element.text.strip()[:200]
    return None


def _local(tag: str) -> str:
    """`{http://www.topografix.com/GPX/1/1}trkpt` → `trkpt`. Namespaces vary; local names do not."""
    return tag.rpartition("}")[2] if isinstance(tag, str) else ""


def _epoch_ms(value: str) -> int | None:
    """
    GPX times are UTC with a trailing `Z`, but offsets appear in the wild and are just as valid.
    A time without either is rejected rather than guessed at: assuming the server's zone would
    silently slide the whole track by hours.
    """
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return round(moment.timestamp() * 1000)


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
        # "148.0" appears in files written from float streams, and int() will not take it.
        return round(float(value))
    except ValueError:
        return None
