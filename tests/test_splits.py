from __future__ import annotations

import math

import pytest

from bmsweb import geo
from bmsweb.parse import BmsSample
from bmsweb.splits import splits

#: Degrees of latitude that make one kilometre, so a track can be built to land on a boundary.
KM_IN_DEGREES = 1000.0 / (geo.EARTH_RADIUS_M * math.radians(1.0))


def run(count, *, step_deg, seconds=1, watts=-360.0, speed_kmh=20.0, accuracy_m=5.0, alt=None):
    return [
        BmsSample(
            at_ms=i * seconds * 1000,
            volts=48.0,
            amps=watts / 48.0,
            watts=watts,
            soc=90,
            remaining_ah=12.0,
            latitude=47.5 + i * step_deg,
            longitude=19.0,
            altitude_m=None if alt is None else alt + i,
            speed_kmh=speed_kmh,
            accuracy_m=accuracy_m,
        )
        for i in range(count)
    ]


def test_a_three_kilometre_run_makes_three_splits():
    # 100 steps of a hundredth of a kilometre each is 1 km per 100 samples.
    samples = run(301, step_deg=KM_IN_DEGREES / 100)

    result = splits(samples, gap_threshold_ms=5_000, split_km=1.0)

    assert len(result) == 3
    for split in result[:2]:
        assert split.distance_km == pytest.approx(1.0, abs=0.02)


def test_split_size_is_configurable():
    samples = run(201, step_deg=KM_IN_DEGREES / 100)

    assert len(splits(samples, 5_000, split_km=0.5)) == 4
    assert len(splits(samples, 5_000, split_km=2.0)) == 1


def test_energy_is_attributed_to_the_split_it_was_drawn_in():
    """Each split covers 100 s at 360 W, which is 10 Wh."""
    samples = run(201, step_deg=KM_IN_DEGREES / 100)

    result = splits(samples, 5_000, split_km=1.0)

    assert result[0].discharged_wh == pytest.approx(10.0, rel=0.02)
    assert result[0].charged_wh == 0.0


def test_moving_time_and_average_speed():
    samples = run(101, step_deg=KM_IN_DEGREES / 100)

    first = splits(samples, 5_000, split_km=1.0)[0]

    assert first.moving_s == 100
    assert first.avg_speed_kmh == pytest.approx(1.0 / (100 / 3600), rel=0.02)


def test_a_dropout_does_not_inflate_a_split():
    """The interval spanning the gap contributes neither distance, time nor watt-hours."""
    first_half = run(50, step_deg=KM_IN_DEGREES / 100)
    second_half = [
        BmsSample(
            at_ms=600_000 + i * 1000,
            volts=48.0, amps=-7.5, watts=-360.0, soc=90, remaining_ah=12.0,
            latitude=47.5 + (50 + i) * KM_IN_DEGREES / 100,
            longitude=19.0, speed_kmh=20.0, accuracy_m=5.0,
        )
        for i in range(51)
    ]

    result = splits(first_half + second_half, 5_000, split_km=1.0)

    assert len(result) == 1
    assert result[0].duration_s == 99, "the ten-minute hole is not counted as riding time"
    assert result[0].discharged_wh == pytest.approx(9.9, rel=0.02)


def test_altitude_is_reported_as_net_change_not_summed_noise():
    """
    Summing every per-sample rise would report a flat ride as a mountain, because that is what
    GPS altitude noise adds up to. The net change over the split is the only honest figure.
    """
    samples = run(101, step_deg=KM_IN_DEGREES / 100, alt=120.0)

    first = splits(samples, 5_000, split_km=1.0)[0]

    assert first.altitude_change_m == pytest.approx(100.0, abs=1.0)


def test_a_stationary_recording_has_no_splits():
    samples = run(100, step_deg=0.0)

    assert splits(samples, 5_000) == []


def test_a_recording_without_gps_has_no_splits():
    samples = [
        BmsSample(at_ms=i * 1000, volts=48.0, amps=-7.5, watts=-360.0, soc=90, remaining_ah=12.0)
        for i in range(100)
    ]

    assert splits(samples, 5_000) == []


def test_vague_fixes_do_not_advance_the_kilometre_count():
    """The same accuracy cutoff the summary uses, so the splits add up to the ride's distance."""
    samples = run(301, step_deg=KM_IN_DEGREES / 100, accuracy_m=30.0)

    assert splits(samples, 5_000) == []


def test_splits_add_up_to_the_ride_distance():
    from bmsweb.summary import travelled_km

    samples = run(250, step_deg=KM_IN_DEGREES / 100)

    total = sum(s.distance_km for s in splits(samples, 5_000))

    assert total == pytest.approx(travelled_km(tuple(samples), 5_000), rel=1e-9)


def test_endpoint_returns_rows(client, fixture_bytes):
    response = client.post(
        "/api/v1/sessions",
        files={"file": ("20260801-100000_bms.csv", fixture_bytes("ride_gps.csv"), "text/csv")},
    )
    session_id = response.json()["id"]

    body = client.get(f"/api/v1/sessions/{session_id}/splits").json()
    session = client.get(f"/api/v1/sessions/{session_id}").json()

    # The fixture travels 44 m, so it is one partial split that must equal the ride's own distance.
    assert len(body["splits"]) == 1
    assert body["splits"][0]["distance_km"] == pytest.approx(session["distance_km"])


def test_endpoint_rejects_an_absurd_split_size(client, fixture_bytes):
    response = client.post(
        "/api/v1/sessions",
        files={"file": ("20260801-100000_bms.csv", fixture_bytes("ride_gps.csv"), "text/csv")},
    )

    assert client.get(
        f"/api/v1/sessions/{response.json()['id']}/splits", params={"km": 0}
    ).status_code == 422


def test_endpoint_is_empty_for_a_session_without_gps(client, fixture_bytes):
    response = client.post(
        "/api/v1/sessions",
        files={"file": ("20260801-162000_EKD01.csv", fixture_bytes("ekd01_ride.csv"), "text/csv")},
    )

    body = client.get(f"/api/v1/sessions/{response.json()['id']}/splits").json()

    assert body["splits"] == []
