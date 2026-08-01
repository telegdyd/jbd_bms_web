"""
Distance and projection maths.

A port of the app's `Geo` object. The constants are reproduced exactly rather than replaced with
a more accurate ellipsoid: the point is that a ride's distance reads the same here as it does on
the phone, and a "better" earth radius would quietly put the two out of step.
"""

from __future__ import annotations

import math

EARTH_RADIUS_M = 6_371_000.0

METRES_PER_DEGREE_LAT = 110_540.0
METRES_PER_DEGREE_LON = 111_320.0


def distance_metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine great-circle distance."""
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def to_local_metres(
    lat: float,
    lon: float,
    origin_lat: float,
    origin_lon: float,
) -> tuple[float, float]:
    """
    Flat-earth projection to metres relative to an origin. Accurate enough over the span of a
    ride, and far cheaper than spherical maths inside a simplification loop.
    """
    x = (lon - origin_lon) * METRES_PER_DEGREE_LON * math.cos(math.radians(origin_lat))
    y = (lat - origin_lat) * METRES_PER_DEGREE_LAT
    return x, y
