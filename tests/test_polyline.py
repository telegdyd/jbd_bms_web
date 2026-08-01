from __future__ import annotations

import pytest

from bmsweb import polyline

#: The example from Google's own encoded-polyline specification, so this checks the format rather
#: than merely checking the encoder against its own decoder.
REFERENCE_POINTS = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
REFERENCE_ENCODED = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"


def test_matches_the_reference_encoding():
    assert polyline.encode(REFERENCE_POINTS) == REFERENCE_ENCODED


def test_matches_the_reference_decoding():
    decoded = polyline.decode(REFERENCE_ENCODED)

    assert len(decoded) == 3
    for got, expected in zip(decoded, REFERENCE_POINTS):
        assert got == pytest.approx(expected, abs=1e-5)


def test_round_trip_holds_to_the_grid():
    points = [(47.5 + i * 0.0001, 19.0 - i * 0.00007) for i in range(200)]

    decoded = polyline.decode(polyline.encode(points))

    assert len(decoded) == len(points)
    for got, expected in zip(decoded, points):
        assert got == pytest.approx(expected, abs=1e-5)


def test_rounding_error_does_not_accumulate():
    """Deltas are taken between rounded values, so a long track cannot drift off its true line."""
    points = [(47.5 + i * 0.000015, 19.0) for i in range(5000)]

    decoded = polyline.decode(polyline.encode(points))

    assert decoded[-1][0] == pytest.approx(points[-1][0], abs=1e-5)


def test_empty_track():
    assert polyline.encode([]) == ""
    assert polyline.decode("") == []
