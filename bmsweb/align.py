"""
Lining a companion file up with a recording.

Both files carry absolute timestamps, so in principle they need no aligning at all — and the plan
says as much, because until now every timestamp in the system came from one device. A GPX brings a
second clock into the picture, and a watch or a phone running Strava can sit several seconds away
from the logger. Ten seconds is invisible in a distance total and glaring the moment a heart rate
is read at the top of a hill.

So the offset is measured rather than assumed. Both sides know how fast the bike was going — the
recording from its own GPS, the GPX from the spacing of its points — and that pair of curves has
one obvious best fit. What comes back is the shift, in milliseconds, to add to the companion's
timestamps, together with the correlation it achieved, which is the only honest way for the page to
say "this looks right" or "check this yourself".
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from . import geo

#: How far apart the two clocks are allowed to be. Both sides are UTC, so this is drift and a
#: sloppily set watch, not timezones; five minutes is already generous.
MAX_LAG_S = 300

#: The search runs twice: once at this resolution across the whole window, then a second time at
#: one second either side of the winner. A single 1 s pass over ±5 minutes is 601 lags across a
#: three-hour ride, which is seconds of pure-Python arithmetic on an upload; this is a fraction of
#: that and lands in the same place, because a speed curve has nothing in it that moves faster.
COARSE_STEP_S = 5

#: Below this much overlapping movement there is not enough shape to match, and the best
#: correlation found is an accident of two short noisy stretches.
MIN_OVERLAP_S = 90

#: A ride that never moved gives two flat lines, which correlate perfectly at every lag.
MIN_STDEV_KMH = 0.5


@dataclass(frozen=True, slots=True)
class Alignment:
    #: Added to the companion's timestamps to put them on the recording's timeline.
    offset_ms: int
    #: Pearson correlation of the two speed curves at that offset, or None when the match could not
    #: be attempted. Roughly: above 0.8 is the same ride, below 0.5 is worth a second look.
    correlation: float | None
    #: Seconds of the two recordings that were compared, at the offset chosen.
    overlap_s: int
    #: `correlation` when it was measured, `none` when there was nothing to measure it from.
    source: str


def unaligned(reason: str = "none") -> Alignment:
    return Alignment(offset_ms=0, correlation=None, overlap_s=0, source=reason)


def align(
    reference: Sequence[tuple[int, float]],
    candidate: Sequence[tuple[int, float]],
    max_lag_s: int = MAX_LAG_S,
) -> Alignment:
    """
    Both arguments are `(epoch_ms, km/h)` in time order. The result shifts `candidate` onto
    `reference`.
    """
    if len(reference) < 2 or len(candidate) < 2:
        return unaligned()

    base = min(reference[0][0], candidate[0][0])
    span_ms = max(reference[-1][0], candidate[-1][0]) - base
    if span_ms <= 0:
        return unaligned()

    coarse = _search(reference, candidate, base, span_ms, COARSE_STEP_S, max_lag_s, 0)
    if coarse is None:
        return unaligned()

    # Second pass at full resolution, but only around the winner. `COARSE_STEP_S` either side is
    # exactly the interval the coarse pass could not distinguish within.
    fine = _search(reference, candidate, base, span_ms, 1, COARSE_STEP_S, coarse[0])
    lag_s, correlation, overlap = fine or coarse

    return Alignment(
        offset_ms=lag_s * 1000,
        correlation=round(correlation, 4),
        overlap_s=overlap,
        source="correlation",
    )


def speeds_from_fixes(fixes: Sequence[tuple[int, float | None, float | None]]) -> list[tuple[int, float]]:
    """
    Speed derived from how far apart consecutive fixes are — `(epoch_ms, lat, lon)` in — for a
    track that carries positions but no speed of its own, which is every GPX, since the format has
    no field for one.

    The value is stamped at the *later* of the two fixes, matching how a receiver reports the speed
    it has just measured. Anything separated by more than a minute is a pause rather than a stretch
    of riding, and joining across it would invent a straight-line sprint.
    """
    speeds: list[tuple[int, float]] = []
    previous: tuple[int, float, float] | None = None

    for at_ms, lat, lon in fixes:
        if lat is None or lon is None:
            continue
        if previous is not None:
            dt_ms = at_ms - previous[0]
            if 0 < dt_ms <= 60_000:
                metres = geo.distance_metres(previous[1], previous[2], lat, lon)
                speeds.append((at_ms, metres / dt_ms * 3600.0))
        previous = (at_ms, lat, lon)

    return speeds


def _search(
    reference: Sequence[tuple[int, float]],
    candidate: Sequence[tuple[int, float]],
    base_ms: int,
    span_ms: int,
    step_s: int,
    reach_s: int,
    centre_s: int,
) -> tuple[int, float, int] | None:
    """Best `(lag_s, correlation, overlap_s)` within `reach_s` of `centre_s`, or None."""
    length = span_ms // (step_s * 1000) + 1
    if length < MIN_OVERLAP_S // step_s:
        return None

    left = _grid(reference, base_ms, step_s, length)
    right = _grid(candidate, base_ms, step_s, length)
    if left is None or right is None:
        return None

    reach_steps = max(1, reach_s // step_s)
    centre_steps = centre_s // step_s
    minimum_points = max(2, MIN_OVERLAP_S // step_s)

    best: tuple[int, float, int] | None = None
    for steps in range(centre_steps - reach_steps, centre_steps + reach_steps + 1):
        lag_s = steps * step_s
        scored = _pearson(left, right, steps)
        if scored is None:
            continue
        correlation, count = scored
        if count < minimum_points:
            continue
        if best is None or correlation > best[1]:
            best = (lag_s, correlation, count * step_s)

    return best


def _grid(
    series: Sequence[tuple[int, float]], base_ms: int, step_s: int, length: int
) -> list[float | None] | None:
    """
    The series on a fixed grid, each cell the mean of what fell in it and None where nothing did.

    Mean rather than nearest, because this is a downsample: at five seconds a nearest-neighbour
    pick would hand the comparison one arbitrary sample out of five and throw away the shape that
    the match depends on.
    """
    totals = [0.0] * length
    counts = [0] * length

    for at_ms, value in series:
        index = (at_ms - base_ms) // (step_s * 1000)
        if 0 <= index < length:
            totals[index] += value
            counts[index] += 1

    values = [totals[i] / counts[i] if counts[i] else None for i in range(length)]
    present = [v for v in values if v is not None]
    if len(present) < 2:
        return None

    mean = sum(present) / len(present)
    variance = sum((v - mean) ** 2 for v in present) / len(present)
    # A stationary recording is a flat line, and a flat line correlates perfectly with anything at
    # every lag. Refusing here is what stops a bench session confidently claiming an offset.
    if math.sqrt(variance) < MIN_STDEV_KMH:
        return None

    return values


def _pearson(
    left: list[float | None], right: list[float | None], shift: int
) -> tuple[float, int] | None:
    """Correlation of `left[i + shift]` against `right[i]`, over the cells where both exist."""
    n = 0
    sum_x = sum_y = sum_xx = sum_yy = sum_xy = 0.0

    start = max(0, -shift)
    stop = min(len(right), len(left) - shift)

    for i in range(start, stop):
        x = left[i + shift]
        y = right[i]
        if x is None or y is None:
            continue
        n += 1
        sum_x += x
        sum_y += y
        sum_xx += x * x
        sum_yy += y * y
        sum_xy += x * y

    if n < 2:
        return None

    covariance = sum_xy - sum_x * sum_y / n
    spread = math.sqrt(max(sum_xx - sum_x * sum_x / n, 0.0)) * math.sqrt(
        max(sum_yy - sum_y * sum_y / n, 0.0)
    )
    if spread <= 0:
        return None

    return covariance / spread, n
