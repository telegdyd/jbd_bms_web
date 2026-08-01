"""
Route cleanup for drawing.

A port of the app's `RouteSimplifier`. The stored recording is never touched — this only affects
what gets drawn on the map, in three passes, each removing a different kind of noise:

  1. Fixes the receiver itself reports as vague are dropped outright.
  2. Points closer together than a few metres collapse into one. This is what removes the scribble
     a stationary phone draws: standing still for ten minutes produces hundreds of fixes wandering
     within a small radius, and without this they are all drawn.
  3. Douglas-Peucker discards points that lie close to the line between their neighbours, which
     thins long straight stretches without rounding off real corners.
"""

from __future__ import annotations

import math
from typing import Sequence

from . import geo
from .parse import Sample

# Defaults matching the app's LoggingSettings. Note these are not the distance constants in
# summary.py: what is accurate enough to draw and what is accurate enough to measure differ.
DEFAULT_MAX_ACCURACY_M = 25.0
DEFAULT_MIN_SEPARATION_M = 6.0
DEFAULT_SIMPLIFY_EPSILON_M = 3.0


def located(samples: Sequence[Sample]) -> list[Sample]:
    """Every fix that has coordinates, exactly as recorded."""
    return [s for s in samples if s.has_location]


def simplify(
    samples: Sequence[Sample],
    max_accuracy_m: float = DEFAULT_MAX_ACCURACY_M,
    min_separation_m: float = DEFAULT_MIN_SEPARATION_M,
    simplify_epsilon_m: float = DEFAULT_SIMPLIFY_EPSILON_M,
) -> list[Sample]:
    usable = [
        s for s in samples if s.has_location and (s.accuracy_m or 0.0) <= max_accuracy_m
    ]
    if len(usable) < 3:
        return usable

    if min_separation_m <= 0.0:
        separated = usable
    else:
        separated = []
        for sample in usable:
            last = separated[-1] if separated else None
            if last is None or geo.distance_metres(
                last.latitude, last.longitude, sample.latitude, sample.longitude
            ) >= min_separation_m:
                separated.append(sample)
        # Keep the true end point: it is where the ride finished, however little it moved.
        if separated[-1] is not usable[-1]:
            separated.append(usable[-1])

    if len(separated) < 3 or simplify_epsilon_m <= 0.0:
        return separated
    return _douglas_peucker(separated, simplify_epsilon_m)


def _douglas_peucker(points: Sequence[Sample], epsilon: float) -> list[Sample]:
    """Iterative rather than recursive, so a long ride cannot blow the stack."""
    origin_lat = points[0].latitude
    origin_lon = points[0].longitude
    projected = [geo.to_local_metres(p.latitude, p.longitude, origin_lat, origin_lon) for p in points]

    keep = [False] * len(points)
    keep[0] = True
    keep[-1] = True

    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue

        farthest = -1
        farthest_distance = 0.0
        for i in range(first + 1, last):
            d = _perpendicular_distance(projected[i], projected[first], projected[last])
            if d > farthest_distance:
                farthest_distance = d
                farthest = i

        if farthest >= 0 and farthest_distance > epsilon:
            keep[farthest] = True
            stack.append((first, farthest))
            stack.append((farthest, last))

    return [p for p, k in zip(points, keep) if k]


def _perpendicular_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    x, y = point
    x1, y1 = start
    x2, y2 = end
    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return math.hypot(x - x1, y - y1)
    return abs(dy * x - dx * y + x2 * y1 - y2 * x1) / length
