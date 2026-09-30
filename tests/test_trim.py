from __future__ import annotations

import pytest

from bmsweb.ingest import reparse
from bmsweb.parse import parse_csv
from bmsweb.summary import running_km, travelled_km

#: 10:00:00+02:00 on the synthetic ride's day, which is where its first sample sits.
T0 = 1_785_571_200_000


@pytest.fixture
def ride(client, synthetic_ride):
    """A ten-minute ride, uploaded. Returns its id and the CSV as sent."""
    csv, _ = synthetic_ride(seconds=600)
    session_id = client.post(
        "/api/v1/sessions", files={"file": ("20260801-100000_bms.csv", csv, "text/csv")}
    ).json()["id"]
    return session_id, csv


def put_trim(client, session_id, start_ms=None, end_ms=None):
    return client.put(
        f"/api/v1/sessions/{session_id}/trim", json={"start_ms": start_ms, "end_ms": end_ms}
    )


def test_the_first_sample_is_where_the_tests_think(client, ride):
    session_id, _ = ride
    assert client.get(f"/api/v1/sessions/{session_id}").json()["started_at_ms"] == T0


def test_trimming_the_end_rebuilds_every_figure_from_what_is_kept(client, ride):
    session_id, _ = ride
    before = client.get(f"/api/v1/sessions/{session_id}").json()

    response = put_trim(client, session_id, end_ms=T0 + 299_000)
    assert response.status_code == 200
    after = response.json()

    assert after["sample_count"] == 300
    assert after["ended_at_ms"] == T0 + 299_000
    assert after["duration_ms"] == 299_000
    assert after["trim_start_ms"] is None
    assert after["trim_end_ms"] == T0 + 299_000
    assert 0 < after["distance_km"] < before["distance_km"]
    assert after["discharged_wh"] < before["discharged_wh"]

    # The charts and the map only ever see the kept stretch.
    series = client.get(f"/api/v1/sessions/{session_id}/series?fields=watts").json()
    assert max(series["t"]) <= T0 + 299_000
    track = client.get(f"/api/v1/sessions/{session_id}/track?detail=full").json()
    assert len(track["points"]) == 300


def test_trimming_the_start_moves_the_ride_start(client, ride):
    session_id, _ = ride
    after = put_trim(client, session_id, start_ms=T0 + 60_000).json()

    assert after["started_at_ms"] == T0 + 60_000
    assert after["sample_count"] == 540


def test_the_original_is_left_whole(client, ride):
    session_id, csv = ride
    put_trim(client, session_id, T0 + 60_000, T0 + 120_000)

    assert client.get(f"/api/v1/sessions/{session_id}/raw.csv").content == csv


def test_a_trim_survives_a_reparse(client, ride, connection, settings):
    session_id, _ = ride
    put_trim(client, session_id, end_ms=T0 + 299_000)

    assert reparse(connection, settings, session_id)

    after = client.get(f"/api/v1/sessions/{session_id}").json()
    assert after["sample_count"] == 300
    assert after["trim_end_ms"] == T0 + 299_000


def test_untrimming_gives_back_the_recording_as_uploaded(client, ride):
    session_id, _ = ride
    before = client.get(f"/api/v1/sessions/{session_id}").json()

    put_trim(client, session_id, T0 + 60_000, T0 + 120_000)
    after = put_trim(client, session_id).json()

    assert after["trim_start_ms"] is None and after["trim_end_ms"] is None
    for name in ("sample_count", "started_at_ms", "ended_at_ms", "distance_km", "discharged_wh"):
        assert after[name] == before[name]


def test_a_trim_can_be_widened_again(client, ride):
    """Measured against the original, not the trim before it."""
    session_id, _ = ride
    put_trim(client, session_id, end_ms=T0 + 100_000)
    after = put_trim(client, session_id, end_ms=T0 + 400_000).json()

    assert after["sample_count"] == 401


def test_a_bound_beyond_the_recording_is_no_bound(client, ride):
    session_id, _ = ride
    after = put_trim(client, session_id, T0 - 3_600_000, T0 + 3_600_000).json()

    assert after["trim_start_ms"] is None and after["trim_end_ms"] is None
    assert after["sample_count"] == 600


def test_a_trim_keeps_the_upload_time(client, ride):
    """The sidebar's "last upload" is about the phone; a trim is not the phone delivering anything."""
    session_id, _ = ride
    before = client.get(f"/api/v1/sessions/{session_id}").json()["uploaded_at_ms"]
    after = put_trim(client, session_id, end_ms=T0 + 299_000).json()["uploaded_at_ms"]

    assert after == before


@pytest.mark.parametrize(
    ("start", "end", "says"),
    [
        (T0 + 200_000, T0 + 100_000, "before the end"),
        (T0 + 100_000, T0 + 100_000, "before the end"),
        (T0 + 100_200, T0 + 100_800, "less than two"),
    ],
)
def test_a_trim_that_leaves_nothing_is_refused(client, ride, start, end, says):
    session_id, _ = ride
    response = put_trim(client, session_id, start, end)

    assert response.status_code == 400
    assert says in response.json()["detail"]
    # And nothing was changed by trying.
    assert client.get(f"/api/v1/sessions/{session_id}").json()["sample_count"] == 600


def test_an_unknown_session(client):
    assert put_trim(client, 999, end_ms=T0).status_code == 404
    assert client.get("/api/v1/sessions/999/trim").status_code == 404


def test_the_editor_is_shown_the_whole_recording_whatever_the_trim(client, ride):
    session_id, _ = ride
    before = client.get(f"/api/v1/sessions/{session_id}").json()
    put_trim(client, session_id, T0 + 60_000, T0 + 120_000)

    body = client.get(f"/api/v1/sessions/{session_id}/trim").json()

    assert body["started_at_ms"] == T0
    assert body["ended_at_ms"] == T0 + 599_000
    assert body["trim_start_ms"] == T0 + 60_000
    assert body["trim_end_ms"] == T0 + 120_000
    assert len(body["points"]) == 600
    assert body["points"][-1]["km"] == pytest.approx(before["distance_km"], abs=1e-4)
    assert all(p["lat"] is not None for p in body["points"])


def test_the_editor_is_thinned_on_a_long_recording_but_reaches_the_end(
    client, synthetic_ride, monkeypatch
):
    # The fixture's clock stops at 10:59, so the budget comes down rather than the ride going up.
    monkeypatch.setattr("bmsweb.api.sessions.TRIM_POINTS", 500)
    csv, _ = synthetic_ride(seconds=3_000)
    session_id = client.post(
        "/api/v1/sessions", files={"file": ("20260801-100000_bms.csv", csv, "text/csv")}
    ).json()["id"]

    points = client.get(f"/api/v1/sessions/{session_id}/trim").json()["points"]

    assert len(points) == 501
    assert points[0]["t_ms"] == T0
    assert points[-1]["t_ms"] == T0 + 2_999_000


def test_a_companion_survives_a_trim(client, attached):
    session_id, companion = attached()
    put_trim(client, session_id, end_ms=T0 + 400_000)

    listed = client.get(f"/api/v1/sessions/{session_id}/companions").json()["companions"]
    assert [c["id"] for c in listed] == [companion["id"]]


def test_the_running_distance_ends_where_the_summary_does(load_fixture):
    session = parse_csv(load_fixture("ride_gps.csv"))
    running = running_km(session.samples, session.gap_threshold_ms)

    assert len(running) == len(session.samples)
    assert running == sorted(running)
    assert running[-1] == travelled_km(session.samples, session.gap_threshold_ms)
