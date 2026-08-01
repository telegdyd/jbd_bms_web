from __future__ import annotations

import math

import pytest

from bmsweb import align

BASE_MS = 1_785_571_200_000  # 2026-08-01T08:00:00Z


def speeds(seconds: int = 600, shift_s: int = 0, start: int = 0) -> list[tuple[int, float]]:
    """
    A speed curve with enough shape in it to have exactly one best fit. Two sine waves rather than
    one, so that a lag of a whole period cannot score as well as the true one.
    """
    return [
        (BASE_MS + (i + shift_s) * 1000, 18 + 12 * math.sin(i / 40.0) + 4 * math.sin(i / 7.0))
        for i in range(start, start + seconds)
    ]


def test_a_watch_running_late_is_pulled_back():
    """The companion's stamps say a moment happened 7 s after it did, so 7 s comes back off."""
    result = align.align(speeds(), speeds(shift_s=7))

    assert result.offset_ms == -7000
    assert result.source == "correlation"
    assert result.correlation > 0.99


def test_a_watch_running_early_is_pushed_forward():
    assert align.align(speeds(), speeds(shift_s=-23)).offset_ms == 23_000


def test_clocks_that_already_agree_are_left_alone():
    assert align.align(speeds(), speeds()).offset_ms == 0


def test_a_lag_beyond_the_search_window_is_not_invented():
    """Half an hour apart is not two clocks disagreeing, and the best fit inside ±5 min is noise."""
    result = align.align(speeds(), speeds(shift_s=1800))

    assert result.correlation is None or abs(result.offset_ms) <= align.MAX_LAG_S * 1000


def test_a_partial_overlap_still_matches():
    """A watch started five minutes into the ride has only the rest to be matched on."""
    result = align.align(speeds(seconds=1200), speeds(seconds=900, shift_s=11, start=300))

    assert result.offset_ms == -11_000
    assert result.overlap_s >= align.MIN_OVERLAP_S


def test_a_stationary_recording_claims_nothing():
    """
    Two flat lines correlate perfectly at every lag, so a bench session would otherwise report a
    confident offset chosen at random.
    """
    still = [(BASE_MS + i * 1000, 0.0) for i in range(600)]

    result = align.align(still, still)

    assert result.source == "none"
    assert result.offset_ms == 0


def test_too_little_to_go_on():
    short = speeds(seconds=20)

    assert align.align(short, short).source == "none"
    assert align.align([], speeds()).source == "none"


def test_speeds_are_derived_from_the_gaps_between_fixes():
    """
    A ten-thousandth of a degree of latitude is 11.12 m on the app's sphere, so covering one a
    second is 40.03 km/h. Read off `geo`, deliberately: the same radius the phone uses.
    """
    fixes = [(BASE_MS + i * 1000, 47.5 + i * 0.0001, 19.0) for i in range(4)]

    derived = align.speeds_from_fixes(fixes)

    assert len(derived) == 3
    assert derived[0][0] == BASE_MS + 1000, "stamped at the later of the two fixes"
    assert derived[0][1] == pytest.approx(40.03, abs=0.01)


def test_a_pause_is_not_a_sprint():
    """Joining across a stop would invent a straight-line dash covering the whole gap."""
    fixes = [
        (BASE_MS, 47.5, 19.0),
        (BASE_MS + 600_000, 47.6, 19.0),  # ten minutes later, 11 km away
        (BASE_MS + 601_000, 47.6001, 19.0),
    ]

    derived = align.speeds_from_fixes(fixes)

    assert len(derived) == 1
    assert derived[0][0] == BASE_MS + 601_000


def test_fixes_without_a_position_are_skipped():
    fixes = [
        (BASE_MS, 47.5, 19.0),
        (BASE_MS + 1000, None, None),
        (BASE_MS + 2000, 47.5002, 19.0),
    ]

    assert len(align.speeds_from_fixes(fixes)) == 1
