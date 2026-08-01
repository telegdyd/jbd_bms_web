from __future__ import annotations

import math

import pytest

from bmsweb import geo


def test_one_degree_of_latitude():
    """
    Along a meridian the haversine reduces to R·Δφ exactly, so this pins the earth radius the app
    uses rather than merely checking the formula against itself.
    """
    assert geo.distance_metres(47.0, 19.0, 48.0, 19.0) == pytest.approx(111_194.93, abs=0.01)


def test_a_tenth_of_a_milliarcdegree_step():
    """The step size the route fixtures move by, spelled out so the distance tests have a literal."""
    assert geo.distance_metres(47.5, 19.0, 47.5001, 19.0) == pytest.approx(11.1195, abs=1e-4)


def test_longitude_shrinks_with_latitude():
    at_equator = geo.distance_metres(0.0, 0.0, 0.0, 1.0)
    at_home = geo.distance_metres(47.5, 19.0, 47.5, 20.0)
    assert at_home < at_equator
    assert at_home == pytest.approx(at_equator * math.cos(math.radians(47.5)), rel=1e-3)


def test_identical_points_are_zero_not_nan():
    """`asin(min(1, sqrt(a)))` exists to keep rounding from pushing the argument past 1."""
    assert geo.distance_metres(47.5, 19.0, 47.5, 19.0) == 0.0


def test_distance_is_symmetric():
    there = geo.distance_metres(47.5, 19.0, 47.6, 19.1)
    back = geo.distance_metres(47.6, 19.1, 47.5, 19.0)
    assert there == pytest.approx(back)


def test_local_projection_is_origin_relative():
    x, y = geo.to_local_metres(47.5001, 19.0001, 47.5, 19.0)
    assert y == pytest.approx(11.054, abs=1e-3)
    assert x == pytest.approx(0.0001 * geo.METRES_PER_DEGREE_LON * math.cos(math.radians(47.5)))

    at_origin = geo.to_local_metres(47.5, 19.0, 47.5, 19.0)
    assert at_origin == (0.0, 0.0)
