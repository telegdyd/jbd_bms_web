from __future__ import annotations

import pytest

from bmsweb.parse import DEFAULT_GAP_MS, SessionKind, gap_threshold_ms, parse_csv


def test_ride_shape(load_fixture):
    session = parse_csv(load_fixture("ride_gps.csv"))

    assert session.kind is SessionKind.BMS
    assert len(session.samples) == 6
    assert session.cell_count == 4
    assert session.temp_count == 2
    assert session.has_location
    # +02:00 in the timestamp, kept so the ride groups under the day the rider actually rode it.
    assert session.tz_offset_min == 120


def test_ride_columns_land_in_the_right_fields(load_fixture):
    first = parse_csv(load_fixture("ride_gps.csv")).samples[0]

    assert first.volts == 48.0
    assert first.amps == -7.5
    assert first.watts == -360.0
    assert first.soc == 90
    assert first.remaining_ah == 12.4
    assert first.temps_c == (25.0, 26.0)
    assert first.cells_mv == (3600, 3610, 3620, 3630)
    assert (first.delta_mv, first.min_cell_mv, first.max_cell_mv) == (30, 3600, 3630)
    assert (first.latitude, first.longitude) == (47.5, 19.0)
    assert first.altitude_m == 120.0
    assert first.speed_kmh == 12.0
    assert first.accuracy_m == 5.0
    assert first.balance_bits == 0


def test_recording_without_location_columns_still_loads(load_fixture):
    """Location columns were appended later; every earlier recording has to stay readable."""
    session = parse_csv(load_fixture("bench_no_gps.csv"))

    assert len(session.samples) == 4
    assert session.cell_count == 3
    assert session.temp_count == 1
    assert not session.has_location


def test_absent_location_columns_do_not_wrap_around(load_fixture):
    """
    The bug this exists to catch: the app reads these with `getOrNull(-1)`, which is null, whereas
    a naive Python port would index backwards from the end and report the balance bitfield as a
    latitude. The fixture's balance_bits is 6, which would pass for a plausible coordinate.
    """
    sample = parse_csv(load_fixture("bench_no_gps.csv")).samples[0]

    assert sample.latitude is None
    assert sample.longitude is None
    assert sample.altitude_m is None
    assert sample.speed_kmh is None
    assert sample.accuracy_m is None
    assert sample.has_location is False
    assert sample.balance_bits == 6


def test_row_truncated_by_a_kill_is_dropped(load_fixture):
    """A session killed mid-write leaves a short final row. Lose the row, not the session."""
    session = parse_csv(load_fixture("truncated.csv"))

    assert len(session.samples) == 2
    assert session.samples[-1].volts == 12.81


def test_ekd01_is_detected_from_its_columns(load_fixture):
    session = parse_csv(load_fixture("ekd01_ride.csv"))

    assert session.kind is SessionKind.EKD01
    assert len(session.samples) == 5
    assert session.cell_count == 0
    assert not session.has_location

    first = session.samples[0]
    assert first.speed_kmh == 0.0
    assert first.odometer_km == 1284.60
    assert first.battery_percent == 93
    assert session.samples[-1].trip_km == 0.0159


def test_crlf_line_endings(load_fixture):
    """Uploads can pick up Windows line endings in transit; a stray \\r must not poison the last column."""
    text = load_fixture("bench_no_gps.csv").replace("\n", "\r\n")
    session = parse_csv(text)

    assert len(session.samples) == 4
    assert session.samples[0].balance_bits == 6


def test_blank_and_unparseable_rows_are_skipped():
    header = (
        "timestamp,elapsed_s,volts,amps,watts,soc_percent,remaining_ah,"
        "cell1_mv,delta_mv,min_cell_mv,max_cell_mv"
    )
    text = "\n".join(
        [
            header,
            "2026-08-01T10:00:00.000+02:00,0.000,48.0,-7.5,-360.0,90,12.4,3600,0,3600,3600",
            "",
            "not-a-timestamp,1.000,48.0,-7.5,-360.0,90,12.4,3600,0,3600,3600",
            "2026-08-01T10:00:02.000+02:00,2.000,,-7.5,-360.0,90,12.4,3600,0,3600,3600",
            "2026-08-01T10:00:03.000+02:00,3.000,47.0,-7.5,-360.0,90,12.4,3600,0,3600,3600",
        ]
    )

    assert len(parse_csv(text).samples) == 2


def test_timestamp_without_an_offset_is_rejected():
    """
    Matching the app, which parses with ISO_OFFSET_DATE_TIME. Assuming the server's own zone
    would silently move the sample by hours.
    """
    header = (
        "timestamp,elapsed_s,volts,amps,watts,soc_percent,remaining_ah,"
        "cell1_mv,delta_mv,min_cell_mv,max_cell_mv"
    )
    text = header + "\n2026-08-01T10:00:00.000,0.000,48.0,-7.5,-360.0,90,12.4,3600,0,3600,3600"

    assert parse_csv(text).samples == ()


def test_empty_file():
    session = parse_csv("")
    assert session.samples == ()
    assert session.gap_threshold_ms == DEFAULT_GAP_MS


class TestGapThreshold:
    def test_too_few_samples_falls_back(self):
        assert gap_threshold_ms([0, 1000]) == DEFAULT_GAP_MS

    def test_one_hertz_logging_keeps_the_floor(self):
        """3 × 1 s is under the floor, so a brief stutter is not called a dropout."""
        assert gap_threshold_ms([0, 1000, 2000, 3000, 4000]) == DEFAULT_GAP_MS

    def test_slow_logging_scales_the_threshold(self):
        """At one sample per 10 s, a 30 s gap is normal spacing, not an outage."""
        assert gap_threshold_ms([0, 10_000, 20_000, 30_000]) == 30_000

    def test_median_ignores_the_outlier(self):
        """One long dropout must not drag the threshold up and hide the next one."""
        assert gap_threshold_ms([0, 1000, 2000, 600_000, 601_000]) == DEFAULT_GAP_MS


def test_gap_threshold_from_the_ride_fixture(load_fixture):
    session = parse_csv(load_fixture("ride_gps.csv"))
    assert session.gap_threshold_ms == DEFAULT_GAP_MS


@pytest.mark.parametrize("name", ["ride_gps.csv", "bench_no_gps.csv", "ekd01_ride.csv"])
def test_samples_are_in_order(load_fixture, name):
    times = [s.at_ms for s in parse_csv(load_fixture(name)).samples]
    assert times == sorted(times)
