"""The profile, and the rider-side figures the ride page asks for."""

from __future__ import annotations

import struct

import pytest


@pytest.fixture
def ground(settings):
    """
    An elevation tile under the synthetic ride: rising 1000 m per degree north, which over the
    ride's 3 km is a steady 0.9 % climb.
    """
    size = 11
    cells = [round(1000 - 100 * row) for row in range(size) for _ in range(size)]
    settings.dem_dir.mkdir(parents=True, exist_ok=True)
    (settings.dem_dir / "N47E019.hgt").write_bytes(struct.pack(f">{size * size}h", *cells))


@pytest.fixture
def ride(client, synthetic_ride):
    """The synthetic ride, with the watch's heart rate added as the phone writes it."""
    csv, _ = synthetic_ride(0, 600)
    lines = csv.decode().split("\n")
    lines = [lines[0] + ",balance_bits,hr_bpm"] + [
        f"{line},0,{110 + i % 60}" for i, line in enumerate(lines[1:])
    ]
    return client.post(
        "/api/v1/sessions",
        files={"file": ("20260801-100000_bms.csv", "\n".join(lines).encode(), "text/csv")},
    ).json()["id"]


def put(client, **fields):
    return client.put("/api/v1/profile", json=fields)


class TestProfile:
    def test_empty_to_begin_with(self, client):
        body = client.get("/api/v1/profile").json()

        assert body["weight_kg"] is None
        assert body["max_hr_used"] is None
        assert body["defaults"] == {"bike_kg": 25.0, "tyres": "mixed"}

    def test_saved_and_read_back(self, client):
        put(client, weight_kg=78.5, birth_year=1990, sex="male", bike_kg=27, tyres="offroad")
        body = client.get("/api/v1/profile").json()

        assert (body["weight_kg"], body["birth_year"], body["sex"]) == (78.5, 1990, "male")
        assert (body["bike_kg"], body["tyres"]) == (27, "offroad")

    def test_nonsense_is_refused(self, client):
        assert put(client, weight_kg=5).status_code == 422
        assert put(client, sex="robot").status_code == 422
        assert put(client, tyres="square").status_code == 422

    def test_the_maximum_heart_rate_is_estimated_from_age(self, client):
        from datetime import date

        age = 40
        body = put(client, birth_year=date.today().year - age).json()

        assert body["max_hr_used"] == round(208 - 0.7 * age)
        assert body["max_hr_source"] == "age"
        assert len(body["zone_bounds"]) == 4

    def test_a_higher_recorded_heart_rate_raises_it(self, client, ride):
        # The ride peaks at 169; the estimate for an 80-year-old is 152.
        body = put(client, birth_year=1946).json()

        assert body["max_hr_recorded"] == 169
        assert body["max_hr_used"] == 169
        assert body["max_hr_source"] == "recorded"

    def test_one_set_by_hand_wins(self, client, ride):
        body = put(client, birth_year=1946, max_hr=190).json()
        assert (body["max_hr_used"], body["max_hr_source"]) == (190, "set")


class TestEffort:
    def test_without_a_profile_every_part_says_what_it_needs(self, client, ride, ground):
        body = client.get(f"/api/v1/sessions/{ride}/effort").json()

        assert body["profile_missing"] == ["weight_kg", "birth_year"]
        assert body["rider"] is None and body["rider_unavailable"] == "profile"
        assert body["zones"] is None and body["zones_unavailable"] == "profile"
        assert body["calories"] is None and body["calories_unavailable"] == "profile"

    def test_with_one_the_rider_s_share_zones_and_calories(self, client, ride, ground):
        put(client, weight_kg=80, birth_year=1990, sex="male")
        body = client.get(f"/api/v1/sessions/{ride}/effort").json()

        assert body["altitude_source"] == "terrain"
        rider = body["rider"]
        assert rider["mass_kg"] == 105
        assert rider["rider_wh_low"] < rider["rider_wh"] < rider["rider_wh_high"]
        assert 0 < rider["share"] < 1
        # 360 W from the pack for ten minutes, through an 80 % drivetrain.
        assert rider["motor_wh"] == pytest.approx(360 * 0.8 * 599 / 3600, rel=0.01)

        assert sum(body["zones"]["seconds"]) == 599
        assert body["calories"]["kcal"] > 0

    def test_a_heavier_rider_did_more_of_the_climbing(self, client, ride, ground):
        put(client, weight_kg=60)
        light = client.get(f"/api/v1/sessions/{ride}/effort").json()["rider"]["rider_wh"]
        put(client, weight_kg=100)
        heavy = client.get(f"/api/v1/sessions/{ride}/effort").json()["rider"]["rider_wh"]

        assert heavy > light

    def test_without_the_map_gps_heights_are_used(self, client, ride):
        put(client, weight_kg=80)
        body = client.get(f"/api/v1/sessions/{ride}/effort").json()

        assert body["altitude_source"] == "gps"
        assert body["rider"] is not None

    def test_a_session_with_no_route_still_has_zones(self, client, upload):
        put(client, weight_kg=80, birth_year=1990)
        session_id = upload("bench_no_gps.csv").json()["id"]
        body = client.get(f"/api/v1/sessions/{session_id}/effort").json()

        assert body["rider_unavailable"] == "no_route"
        assert body["zones_unavailable"] == "no_heart_rate"

    def test_the_rider_s_power_can_be_charted(self, client, ride, ground):
        url = f"/api/v1/sessions/{ride}/series?fields=watts,rider_w"
        assert all(v is None for v in client.get(url).json()["fields"]["rider_w"])

        put(client, weight_kg=80)
        values = client.get(url).json()["fields"]["rider_w"]
        assert any(v and v > 0 for v in values)

    def test_the_map_can_be_shaded_by_the_rider_s_power(self, client, ride, ground):
        url = f"/api/v1/sessions/{ride}/track"
        assert all(p["rider_w"] is None for p in client.get(url).json()["points"])

        put(client, weight_kg=80)
        drawn = client.get(url).json()["points"]
        full = client.get(url, params={"detail": "full"}).json()["points"]
        # Each drawn segment carries the hardest moment of the samples it stands for.
        assert max(p["rider_w"] or 0 for p in drawn) == max(p["rider_w"] or 0 for p in full)
        assert max(p["rider_w"] or 0 for p in drawn) > 0

    def test_splits_carry_the_rider_s_work(self, client, ride, ground):
        put(client, weight_kg=80)
        splits = client.get(f"/api/v1/sessions/{ride}/splits").json()["splits"]
        effort = client.get(f"/api/v1/sessions/{ride}/effort").json()["rider"]

        assert sum(s["rider_wh"] for s in splits) == pytest.approx(effort["rider_wh"], abs=0.1)
