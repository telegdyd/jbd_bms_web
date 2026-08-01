"""
Per-kilometre breakdown of a ride.

Deliberately on the server rather than in the browser. The rules that decide what counts as travel
— the accuracy cutoff, the jitter floor, the excluded dropouts — live in `summary.py`, and a second
copy of them in JavaScript would drift from the first the moment either changed. The frontend gets
finished rows.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Sequence

from . import geo
from .summary import MAX_USABLE_ACCURACY_M, MIN_STEP_M, MOVING_SPEED_KMH
from .parse import Sample

DEFAULT_SPLIT_KM = 1.0


@dataclass(frozen=True, slots=True)
class Split:
    index: int
    distance_km: float
    started_at_ms: int
    ended_at_ms: int
    duration_s: int
    moving_s: int
    discharged_wh: float
    charged_wh: float
    avg_speed_kmh: float | None
    max_speed_kmh: float | None
    #: Net change over the split, which is the only altitude figure worth showing: summing raw
    #: per-sample rises would report hundreds of metres of climb on a flat ride, because that is
    #: what GPS altitude noise looks like when you add it up.
    altitude_change_m: float | None

    def as_dict(self) -> dict:
        return asdict(self)


def splits(
    samples: Sequence[Sample],
    gap_threshold_ms: int,
    split_km: float = DEFAULT_SPLIT_KM,
) -> list[Split]:
    if split_km <= 0 or len(samples) < 2:
        return []

    cumulative = _cumulative_km(samples, gap_threshold_ms)
    if cumulative[-1] <= 0:
        return []

    count = int(cumulative[-1] / split_km) + 1
    distance = [0.0] * count
    duration_ms = [0] * count
    moving_ms = [0] * count
    discharged = [0.0] * count
    charged = [0.0] * count
    max_speed: list[float | None] = [None] * count
    first_ms: list[int | None] = [None] * count
    last_ms: list[int | None] = [None] * count
    first_alt: list[float | None] = [None] * count
    last_alt: list[float | None] = [None] * count

    for i in range(len(samples) - 1):
        # The interval belongs to the split its *start* falls in. A boundary crossed mid-interval
        # is not divided: at one sample a second the error is a fraction of a second either way.
        bucket = min(int(cumulative[i] / split_km), count - 1)
        current, following = samples[i], samples[i + 1]
        dt = following.at_ms - current.at_ms

        _record(bucket, current, first_ms, last_ms, first_alt, last_alt, max_speed)

        if dt > gap_threshold_ms:
            continue

        distance[bucket] += cumulative[i + 1] - cumulative[i]
        duration_ms[bucket] += dt
        if (current.speed_kmh or 0.0) >= MOVING_SPEED_KMH:
            moving_ms[bucket] += dt

        watts = _mean_watts(current, following)
        if watts is not None:
            hours = dt / 3_600_000.0
            if watts > 0:
                charged[bucket] += watts * hours
            else:
                discharged[bucket] += -watts * hours

    last = samples[-1]
    final = min(int(cumulative[-1] / split_km), count - 1)
    _record(final, last, first_ms, last_ms, first_alt, last_alt, max_speed)

    return [
        Split(
            index=index,
            distance_km=distance[index],
            started_at_ms=first_ms[index] or 0,
            ended_at_ms=last_ms[index] or 0,
            duration_s=duration_ms[index] // 1000,
            moving_s=moving_ms[index] // 1000,
            discharged_wh=discharged[index],
            charged_wh=charged[index],
            avg_speed_kmh=(
                distance[index] / (moving_ms[index] / 3_600_000.0) if moving_ms[index] else None
            ),
            max_speed_kmh=max_speed[index],
            altitude_change_m=(
                last_alt[index] - first_alt[index]
                if first_alt[index] is not None and last_alt[index] is not None
                else None
            ),
        )
        for index in range(count)
        # A trailing sliver from the last few metres is noise, not a split worth a row.
        if distance[index] > 0.0
    ]


def _cumulative_km(samples: Sequence[Sample], gap_threshold_ms: int) -> list[float]:
    """
    Distance travelled by each sample, under exactly the rules `summary.travelled_km` uses — same
    accuracy cutoff, same jitter floor, same reference point advancing across a dropout.
    """
    cumulative = [0.0] * len(samples)
    metres = 0.0
    previous: Sample | None = None

    for index, sample in enumerate(samples):
        if sample.has_location and (sample.accuracy_m or 0.0) <= MAX_USABLE_ACCURACY_M:
            last = previous
            previous = sample
            if last is not None and sample.at_ms - last.at_ms <= gap_threshold_ms:
                step = geo.distance_metres(
                    last.latitude, last.longitude, sample.latitude, sample.longitude
                )
                if step >= MIN_STEP_M:
                    metres += step
        cumulative[index] = metres / 1000.0

    return cumulative


def _record(
    bucket: int,
    sample: Sample,
    first_ms: list[int | None],
    last_ms: list[int | None],
    first_alt: list[float | None],
    last_alt: list[float | None],
    max_speed: list[float | None],
) -> None:
    if first_ms[bucket] is None:
        first_ms[bucket] = sample.at_ms
    last_ms[bucket] = sample.at_ms

    altitude = getattr(sample, "altitude_m", None)
    if altitude is not None:
        if first_alt[bucket] is None:
            first_alt[bucket] = altitude
        last_alt[bucket] = altitude

    speed = sample.speed_kmh
    if speed is not None and (max_speed[bucket] is None or speed > max_speed[bucket]):
        max_speed[bucket] = speed


def _mean_watts(current: Sample, following: Sample) -> float | None:
    a = getattr(current, "watts", None)
    b = getattr(following, "watts", None)
    if a is None or b is None:
        return None
    return (a + b) / 2.0
