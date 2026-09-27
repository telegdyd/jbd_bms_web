"""
Which height to believe, sample by sample.

Three candidates, best first:

  barometer  The watch's air pressure. Steady to a few tens of centimetres from one second to the
             next, which nothing else here is — but it only knows *changes*, and weather drifts it
             by metres over an hour. So its fine detail is kept and its level is taken from the
             ground: the slow difference between the two is followed and added back.
  terrain    The elevation map under each fix (`terrain.py`). Consistent, and immune to a GPS
             altitude that jumps when the fix changes; coarser on a narrow gully than on a road.
  gps        The receiver's own altitude, when neither is available. Above the ellipsoid rather
             than sea level — about 40 m high in Hungary — which only matters for display.

A ride uses one source throughout: switching part way would put a step in the profile that the
power model would read as a cliff.
"""

from __future__ import annotations

from bisect import bisect_left
from typing import Sequence

from .parse import BmsSample
from .terrain import Terrain

#: The barometer is used when it covers at least this much of the located ride.
BAROMETER_COVERAGE = 0.8

#: A map with voids along more than this much of the route is not trusted for the whole ride.
TERRAIN_COVERAGE = 0.95

#: Over how long the barometer's drift is followed against the ground. Long enough that the map's
#: own coarseness averages out, short enough that a front passing through mid-ride does not.
DRIFT_WINDOW_MS = 240_000

#: The international barometric formula, for turning pressure into height differences.
_SEA_LEVEL_HPA = 1013.25


def heights(
    samples: Sequence[BmsSample], terrain: Terrain | None
) -> tuple[list[float | None], str | None]:
    """
    One height per sample, None where the sample has no fix, and the name of the source used —
    or all None and no source, when there is nothing to go on.
    """
    located = [i for i, s in enumerate(samples) if s.has_location]
    if not located:
        return [None] * len(samples), None

    ground = _terrain(samples, located, terrain)

    pressures = [i for i in located if samples[i].pressure_hpa is not None]
    if len(pressures) >= BAROMETER_COVERAGE * len(located):
        return _barometric(samples, located, ground), "barometer"

    if ground is not None:
        return ground, "terrain"

    gps = [samples[i].altitude_m if samples[i].has_location else None for i in range(len(samples))]
    if sum(1 for v in gps if v is not None) >= 2:
        return gps, "gps"
    return [None] * len(samples), None


def _terrain(
    samples: Sequence[BmsSample], located: list[int], terrain: Terrain | None
) -> list[float | None] | None:
    if terrain is None:
        return None

    out: list[float | None] = [None] * len(samples)
    found = 0
    for i in located:
        out[i] = terrain.height(samples[i].latitude, samples[i].longitude)
        found += out[i] is not None
    if found < TERRAIN_COVERAGE * len(located):
        return None

    # The odd void — a cell SRTM never measured — is bridged from its neighbours rather than
    # turning the fix into one without a height.
    _fill(out, located, [s.at_ms for s in samples])
    return out


def _barometric(
    samples: Sequence[BmsSample], located: list[int], ground: list[float | None] | None
) -> list[float | None]:
    times = [s.at_ms for s in samples]
    relative: list[float | None] = [None] * len(samples)
    for i in located:
        pressure = samples[i].pressure_hpa
        if pressure is not None and pressure > 0:
            relative[i] = 44_330.0 * (1.0 - (pressure / _SEA_LEVEL_HPA) ** 0.190263)
    _fill(relative, located, times)

    reference = ground or [
        samples[i].altitude_m if samples[i].has_location else None for i in range(len(samples))
    ]
    difference = [
        (reference[i] - relative[i])
        if reference[i] is not None and relative[i] is not None
        else None
        for i in range(len(samples))
    ]
    known = [d for d in difference if d is not None]
    if not known:
        # Nothing to level it against: the shape is right, the level is sea-level-ish.
        return relative

    drift = _moving_mean(times, difference, DRIFT_WINDOW_MS // 2)
    fallback = sorted(known)[len(known) // 2]
    return [
        None if relative[i] is None else relative[i] + (drift[i] if drift[i] is not None else fallback)
        for i in range(len(samples))
    ]


def _fill(values: list[float | None], located: list[int], times: Sequence[int]) -> None:
    """Linear across the located samples that lack a value; held flat past either end."""
    have = [i for i in located if values[i] is not None]
    if not have:
        return
    stamps = [times[i] for i in have]
    for i in located:
        if values[i] is not None:
            continue
        k = bisect_left(stamps, times[i])
        if k == 0:
            values[i] = values[have[0]]
        elif k >= len(have):
            values[i] = values[have[-1]]
        else:
            a, b = have[k - 1], have[k]
            span = times[b] - times[a]
            f = (times[i] - times[a]) / span if span else 0.0
            values[i] = values[a] + (values[b] - values[a]) * f


def _moving_mean(
    times: Sequence[int], values: Sequence[float | None], half_ms: int
) -> list[float | None]:
    """Mean of the values within ±`half_ms`, None where there are none. Linear time."""
    n = len(times)
    out: list[float | None] = [None] * n
    lo = hi = 0
    total = 0.0
    count = 0
    for i in range(n):
        while hi < n and times[hi] <= times[i] + half_ms:
            if values[hi] is not None:
                total += values[hi]
                count += 1
            hi += 1
        while times[lo] < times[i] - half_ms:
            if values[lo] is not None:
                total -= values[lo]
                count -= 1
            lo += 1
        out[i] = total / count if count else None
    return out
