from __future__ import annotations

import math

import pytest

from bmsweb import geo
from bmsweb.parse import BmsSample, ParsedSession, SessionKind, parse_csv
from bmsweb.summary import summarise

#: One 0.0001° step along a meridian, which is how far every sample in these tests moves.
STEP_M = geo.EARTH_RADIUS_M * math.radians(0.0001)


def session(samples, gap_threshold_ms=5_000, kind=SessionKind.BMS):
    return ParsedSession(
        kind=kind,
        samples=tuple(samples),
        cell_count=0,
        temp_count=0,
        gap_threshold_ms=gap_threshold_ms,
        tz_offset_min=120,
    )


def sample(second, watts=0.0, volts=48.0, soc=90, **kwargs):
    return BmsSample(
        at_ms=second * 1000,
        volts=volts,
        amps=watts / volts,
        watts=watts,
        soc=soc,
        remaining_ah=12.0,
        **kwargs,
    )


def track(count, *, start_second=0, accuracy_m=5.0, step_deg=0.0001, speed_kmh=12.0, watts=0.0):
    """A straight run north, one sample per second."""
    return [
        sample(
            start_second + i,
            watts=watts,
            latitude=47.5 + i * step_deg,
            longitude=19.0,
            accuracy_m=accuracy_m,
            speed_kmh=speed_kmh,
        )
        for i in range(count)
    ]


class TestEnergy:
    def test_discharge_accumulates_by_trapezoid(self):
        """Two seconds at 360 W out is 360 × 2/3600 = 0.2 Wh."""
        s = summarise(session([sample(0, watts=-360.0), sample(1, watts=-360.0), sample(2, watts=-360.0)]))

        assert s.discharged_wh == pytest.approx(0.2)
        assert s.charged_wh == 0.0

    def test_charge_and_discharge_are_kept_apart(self):
        s = summarise(session([sample(0, watts=360.0), sample(1, watts=360.0), sample(2, watts=-360.0)]))

        # The middle pair averages to zero and lands in the discharge branch contributing nothing.
        assert s.charged_wh == pytest.approx(0.1)
        assert s.discharged_wh == pytest.approx(0.0)

    def test_energy_across_a_dropout_is_excluded_not_interpolated(self):
        """A ten-minute outage between two samples must not invent watt-hours."""
        s = summarise(session([sample(0, watts=-360.0), sample(600, watts=-360.0), sample(601, watts=-360.0)]))

        assert s.gap_count == 1
        assert s.gap_ms == 600_000
        assert s.discharged_wh == pytest.approx(0.1)

    def test_peaks_are_signed_by_direction(self):
        s = summarise(session([sample(0, watts=-900.0), sample(1, watts=250.0), sample(2, watts=-100.0)]))

        assert s.peak_discharge_w == 900.0
        assert s.peak_charge_w == 250.0

    def test_peaks_floor_at_zero_when_only_one_direction_occurs(self):
        s = summarise(session([sample(0, watts=-900.0), sample(1, watts=-100.0)]))

        assert s.peak_charge_w == 0.0
        assert s.peak_discharge_w == 900.0


class TestDistance:
    def test_straight_run_sums_its_steps(self):
        s = summarise(session(track(5)))

        assert s.distance_km == pytest.approx(4 * STEP_M / 1000)

    def test_vague_fixes_contribute_nothing(self):
        """Above 20 m of claimed accuracy the fix is not evidence of travel."""
        s = summarise(session(track(5, accuracy_m=25.0)))

        assert s.distance_km == 0.0

    def test_jitter_below_the_floor_is_discarded(self):
        """
        A parked phone wanders a metre or so per fix. An hour of that would otherwise read as
        kilometres and ruin the Wh/km figure.
        """
        s = summarise(session(track(600, step_deg=0.00001)))  # ~1.1 m per step

        assert s.distance_km == 0.0

    def test_the_step_across_a_dropout_is_not_counted(self):
        """
        But the fix on the far side still becomes the reference point, so travel resumes cleanly
        rather than being measured from where the signal was lost.
        """
        s = summarise(session(track(3) + track(3, start_second=600)))

        assert s.gap_count == 1
        # Two steps before the gap, two after — the jump across it is excluded.
        assert s.distance_km == pytest.approx(4 * STEP_M / 1000)

    def test_a_recording_with_no_location_has_no_distance(self):
        s = summarise(session([sample(0, watts=-360.0), sample(1, watts=-360.0)]))

        assert s.distance_km == 0.0
        assert s.max_speed_kmh is None


class TestWhPerKm:
    def test_withheld_below_a_hundred_metres(self):
        s = summarise(session(track(5, watts=-360.0)))

        assert s.distance_km < 0.1
        assert s.wh_per_km is None

    def test_reported_once_the_distance_means_something(self):
        s = summarise(session(track(60, watts=-3600.0)))

        assert s.distance_km == pytest.approx(59 * STEP_M / 1000)
        assert s.wh_per_km == pytest.approx(s.discharged_wh / s.distance_km)


class TestMovingTime:
    def test_counts_only_intervals_that_start_above_walking_pace(self):
        samples = [
            sample(0, speed_kmh=0.0),
            sample(1, speed_kmh=12.0),
            sample(2, speed_kmh=12.0),
            sample(3, speed_kmh=0.5),
        ]

        assert summarise(session(samples)).moving_seconds == 2

    def test_intervals_spanning_a_dropout_are_excluded(self):
        samples = [sample(0, speed_kmh=12.0), sample(600, speed_kmh=12.0), sample(601, speed_kmh=12.0)]

        assert summarise(session(samples)).moving_seconds == 1


def test_empty_session_is_all_zeros():
    s = summarise(session([]))

    assert s.sample_count == 0
    assert s.duration_ms == 0
    assert s.distance_km == 0.0
    assert s.wh_per_km is None


def test_ride_fixture_end_to_end(load_fixture):
    """Every figure the app's summary card shows, for a file with a real dropout in it."""
    s = summarise(parse_csv(load_fixture("ride_gps.csv")))

    assert s.sample_count == 6
    assert s.duration_ms == 32_000

    assert s.discharged_wh == pytest.approx(0.6)
    assert s.charged_wh == 0.0
    assert s.peak_discharge_w == 720.0
    assert s.peak_charge_w == 0.0

    assert (s.min_volts, s.max_volts) == (46.8, 48.0)
    assert (s.min_temp_c, s.max_temp_c) == (25.0, 29.1)
    assert s.max_delta_mv == 45
    assert (s.soc_start, s.soc_end) == (90, 79)

    assert s.gap_count == 1
    assert s.gap_ms == 28_000

    # Four one-step intervals counted; the jump across the 28 s dropout is not.
    assert s.distance_km == pytest.approx(4 * STEP_M / 1000)
    assert s.moving_seconds == 4
    assert s.max_speed_kmh == 20.0
    assert s.wh_per_km is None


def test_ekd01_fixture_end_to_end(load_fixture):
    s = summarise(parse_csv(load_fixture("ekd01_ride.csv")))

    assert s.sample_count == 5
    assert s.duration_ms == 4_000
    # Distance comes from the writer's own integration of wheel speed, not the lifetime odometer.
    assert s.distance_km == 0.0159
    assert s.max_speed_kmh == 24.6
    assert s.moving_seconds == 3
    # The display transmits no electrical data, so there is nothing to report and nothing invented.
    assert s.discharged_wh == 0.0
    assert s.charged_wh == 0.0
    assert s.wh_per_km is None
    # Battery percentage stands in for state of charge; it is the only one this device reports.
    assert (s.soc_start, s.soc_end) == (93, 88)
