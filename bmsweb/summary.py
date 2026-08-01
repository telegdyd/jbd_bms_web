"""
Samples → the figures shown for a session.

A port of the app's `LogSummary.of`, constants included. These numbers appear side by side with the
phone's — the same ride opened on the phone and in the browser must not disagree, so the awkward
parts are reproduced rather than improved:

  * energy across a dropout is excluded, never interpolated, because it is unknowable
  * fixes the receiver calls vague contribute no distance
  * steps below the jitter floor are discarded, so a parked phone does not accrue kilometres
  * Wh/km is withheld below 100 m rather than shown as a confident nonsense figure
"""

from __future__ import annotations

from dataclasses import dataclass

from . import geo
from .parse import BmsSample, Ekd01Sample, ParsedSession, Sample, SessionKind

#: Fixes vaguer than this contribute no distance.
MAX_USABLE_ACCURACY_M = 20.0
#: Steps shorter than this are receiver jitter, not travel.
MIN_STEP_M = 1.5
#: At or above this, the rider counts as moving.
MOVING_SPEED_KMH = 1.0
#: Below this distance the Wh/km ratio is dominated by GPS noise.
MIN_DISTANCE_FOR_WH_PER_KM = 0.1


@dataclass(frozen=True, slots=True)
class Summary:
    duration_ms: int = 0
    sample_count: int = 0
    charged_wh: float = 0.0
    discharged_wh: float = 0.0
    peak_charge_w: float = 0.0
    peak_discharge_w: float = 0.0
    min_volts: float = 0.0
    max_volts: float = 0.0
    min_temp_c: float | None = None
    max_temp_c: float | None = None
    max_delta_mv: int | None = None
    #: For an EKD01 recording these carry the display's battery percentage, which is the only
    #: state of charge that device reports.
    soc_start: int | None = None
    soc_end: int | None = None
    gap_count: int = 0
    gap_ms: int = 0
    distance_km: float = 0.0
    moving_seconds: int = 0
    max_speed_kmh: float | None = None
    #: Energy drawn per kilometre — None until enough distance has accumulated to mean anything,
    #: and always None for EKD01, which reports no electrical data at all.
    wh_per_km: float | None = None


def summarise(session: ParsedSession) -> Summary:
    if not session.samples:
        return Summary()
    if session.kind is SessionKind.EKD01:
        return _summarise_ekd01(session)
    return _summarise_bms(session)


def _summarise_bms(session: ParsedSession) -> Summary:
    s: tuple[BmsSample, ...] = session.samples  # type: ignore[assignment]

    charged = 0.0
    discharged = 0.0
    gap_count = 0
    gap_ms = 0

    for i in range(len(s) - 1):
        dt = s[i + 1].at_ms - s[i].at_ms
        if dt > session.gap_threshold_ms:
            # Energy across a dropout is unknowable, so it is excluded rather than interpolated —
            # otherwise a long outage invents watt-hours.
            gap_count += 1
            gap_ms += dt
            continue
        mean_watts = (s[i].watts + s[i + 1].watts) / 2.0
        hours = dt / 3_600_000.0
        if mean_watts > 0:
            charged += mean_watts * hours
        else:
            discharged += -mean_watts * hours

    temps = [t for sample in s for t in sample.temps_c if t is not None]
    deltas = [sample.delta_mv for sample in s if sample.delta_mv is not None]
    speeds = [sample.speed_kmh for sample in s if sample.speed_kmh is not None]

    distance_km = travelled_km(s, session.gap_threshold_ms)
    moving_seconds = _moving_seconds(s, session.gap_threshold_ms)

    return Summary(
        duration_ms=s[-1].at_ms - s[0].at_ms,
        sample_count=len(s),
        charged_wh=charged,
        discharged_wh=discharged,
        peak_charge_w=max(max(sample.watts for sample in s), 0.0),
        # The `+ 0.0` normalises negative zero. A session that only ever charged makes this
        # `-(0.0)`, which is numerically equal to zero but renders as "-0 W" everywhere it is
        # formatted — including on the phone, which has the same quirk.
        peak_discharge_w=-min(min(sample.watts for sample in s), 0.0) + 0.0,
        min_volts=min(sample.volts for sample in s),
        max_volts=max(sample.volts for sample in s),
        min_temp_c=min(temps) if temps else None,
        max_temp_c=max(temps) if temps else None,
        max_delta_mv=max(deltas) if deltas else None,
        soc_start=s[0].soc,
        soc_end=s[-1].soc,
        gap_count=gap_count,
        gap_ms=gap_ms,
        distance_km=distance_km,
        moving_seconds=moving_seconds,
        max_speed_kmh=max(speeds) if speeds else None,
        wh_per_km=(
            discharged / distance_km if distance_km >= MIN_DISTANCE_FOR_WH_PER_KM else None
        ),
    )


def _summarise_ekd01(session: ParsedSession) -> Summary:
    s: tuple[Ekd01Sample, ...] = session.samples  # type: ignore[assignment]

    gap_count = 0
    gap_ms = 0
    for i in range(len(s) - 1):
        dt = s[i + 1].at_ms - s[i].at_ms
        if dt > session.gap_threshold_ms:
            gap_count += 1
            gap_ms += dt

    return Summary(
        duration_ms=s[-1].at_ms - s[0].at_ms,
        sample_count=len(s),
        soc_start=s[0].battery_percent,
        soc_end=s[-1].battery_percent,
        gap_count=gap_count,
        gap_ms=gap_ms,
        # The writer integrates wheel speed into trip_km as it records, deliberately independent
        # of the display's lifetime odometer, so the last row already holds the ride's distance.
        distance_km=s[-1].trip_km,
        moving_seconds=_moving_seconds(s, session.gap_threshold_ms),
        max_speed_kmh=max(sample.speed_kmh for sample in s),
    )


def travelled_km(samples: tuple[Sample, ...], gap_threshold_ms: int) -> float:
    """
    Summed over consecutive fixes, discarding anything that looks like jitter rather than travel:
    a stationary phone wanders by a metre or two per fix, which would otherwise accumulate into
    kilometres over an hour and quietly ruin the Wh/km figure.
    """
    metres = 0.0
    previous: Sample | None = None

    for sample in samples:
        if not sample.has_location:
            continue
        if (sample.accuracy_m or 0.0) > MAX_USABLE_ACCURACY_M:
            continue

        last = previous
        # Advanced before the gap check, matching the app: a fix on the far side of a dropout
        # becomes the new reference point even though the step across the gap is not counted.
        previous = sample
        if last is None:
            continue
        if sample.at_ms - last.at_ms > gap_threshold_ms:
            continue

        step = geo.distance_metres(
            last.latitude, last.longitude, sample.latitude, sample.longitude
        )
        if step >= MIN_STEP_M:
            metres += step

    return metres / 1000.0


def _moving_seconds(samples: tuple[Sample, ...], gap_threshold_ms: int) -> int:
    ms = 0
    for i in range(len(samples) - 1):
        dt = samples[i + 1].at_ms - samples[i].at_ms
        if dt > gap_threshold_ms:
            continue
        if (samples[i].speed_kmh or 0.0) >= MOVING_SPEED_KMH:
            ms += dt
    return ms // 1000
