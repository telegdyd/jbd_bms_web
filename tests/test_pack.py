from __future__ import annotations

import itertools

import pytest

from bmsweb import polyline

_ids = itertools.count(1)


@pytest.fixture
def add_session(connection):
    """
    A session row written straight into the index. The pack figures are all read off summaries, so
    a summary is all a test needs — no CSV has to be built that happens to drain 40 % of a pack.
    """

    def _add(
        local_date: str,
        *,
        discharged_wh: float = 400.0,
        charged_wh: float = 0.0,
        soc_start: int | None = 90,
        soc_end: int | None = 50,
        distance_km: float = 40.0,
        is_ride: bool = True,
        kind: str = "bms",
        start: tuple[float, float] | None = (47.5, 19.05),
        uploaded_at_ms: int = 0,
    ) -> int:
        n = next(_ids)
        started = 1_780_000_000_000 + n * 3_600_000
        connection.execute(
            """
            INSERT INTO sessions (
                sha256, source_name, raw_path, kind, started_at_ms, ended_at_ms, local_date,
                is_ride, has_location, discharged_wh, charged_wh, soc_start, soc_end, distance_km,
                polyline, uploaded_at_ms, parsed_at_ms, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1)
            """,
            (
                f"sha{n}", f"{n}.csv", f"raw/{n}.csv", kind, started, started + 3_000_000,
                local_date, int(is_ride), int(start is not None), discharged_wh, charged_wh,
                soc_start, soc_end, distance_km,
                polyline.encode([start, (start[0] + 0.01, start[1])]) if start else None,
                uploaded_at_ms,
            ),
        )
        connection.commit()
        return n

    return _add


def test_an_empty_index_has_nothing_to_say(client):
    body = client.get("/api/v1/pack").json()

    assert body["capacity"]["usable_wh"] is None
    assert body["charge"] is None
    assert body["range_km"] is None
    assert body["home"] is None


def test_capacity_is_energy_over_the_share_of_the_pack_used(client, add_session):
    add_session("2026-09-01", discharged_wh=400, soc_start=90, soc_end=50)

    assert client.get("/api/v1/pack").json()["capacity"]["usable_wh"] == pytest.approx(1000)


def test_short_rides_do_not_count_towards_capacity(client, add_session):
    """Ten percent of the gauge is too coarse to scale up to a full charge."""
    add_session("2026-09-01", discharged_wh=100, soc_start=90, soc_end=80)

    assert client.get("/api/v1/pack").json()["capacity"]["usable_wh"] is None


def test_a_ride_charged_on_the_way_does_not_count(client, add_session):
    add_session("2026-09-01", discharged_wh=400, charged_wh=100, soc_start=90, soc_end=50)

    assert client.get("/api/v1/pack").json()["capacity"]["usable_wh"] is None


def test_capacity_is_the_median_so_one_cold_ride_does_not_drag_it(client, add_session):
    add_session("2026-09-01", discharged_wh=400, soc_start=90, soc_end=50)  # 1000
    add_session("2026-09-02", discharged_wh=404, soc_start=90, soc_end=50)  # 1010
    add_session("2026-09-03", discharged_wh=280, soc_start=90, soc_end=50)  # 700, cold

    assert client.get("/api/v1/pack").json()["capacity"]["usable_wh"] == pytest.approx(1000)


def test_range_follows_the_latest_charge_and_recent_efficiency(client, add_session):
    add_session("2026-09-01", discharged_wh=400, soc_start=90, soc_end=50, distance_km=40)

    body = client.get("/api/v1/pack").json()

    assert body["charge"]["soc"] == 50
    assert body["wh_per_km"] == pytest.approx(10)
    assert body["full_range_km"] == pytest.approx(100)
    assert body["range_km"] == pytest.approx(50)


def test_efficiency_counts_back_a_month_from_the_latest_ride(client, add_session):
    add_session("2026-06-01", discharged_wh=2000, distance_km=40, soc_start=None, soc_end=None)
    add_session("2026-09-01", discharged_wh=400, distance_km=40)

    assert client.get("/api/v1/pack").json()["wh_per_km"] == pytest.approx(10)


def test_the_last_charge_can_come_from_a_session_that_went_nowhere(client, add_session):
    """A charge on the balcony after the ride is where the pack now stands."""
    add_session("2026-09-01", soc_start=90, soc_end=50)
    add_session("2026-09-02", soc_start=50, soc_end=95, is_ride=False, start=None, distance_km=0)

    assert client.get("/api/v1/pack").json()["charge"]["soc"] == 95


def test_home_is_where_rides_usually_start(client, add_session):
    add_session("2026-09-01", start=(47.50, 19.05))
    add_session("2026-09-02", start=(47.50, 19.05))
    add_session("2026-09-03", start=(46.00, 18.00))  # a ride begun after a train

    home = client.get("/api/v1/pack").json()["home"]

    assert home == {"lat": pytest.approx(47.50), "lon": pytest.approx(19.05)}


def test_health_reports_the_last_upload(client, add_session):
    add_session("2026-09-01", uploaded_at_ms=1_000)
    add_session("2026-09-02", uploaded_at_ms=5_000)

    assert client.get("/api/v1/health").json()["last_upload_ms"] == 5_000


def test_stats_group_days_into_monday_weeks(client, add_session):
    add_session("2026-09-21", distance_km=10)  # Monday
    add_session("2026-09-27", distance_km=5)   # the Sunday of that week
    add_session("2026-09-28", distance_km=7)   # the next Monday

    weeks = client.get("/api/v1/stats").json()["weeks"]

    assert [(w["week_start"], w["distance_km"]) for w in weeks] == [
        ("2026-09-21", 15),
        ("2026-09-28", 7),
    ]
