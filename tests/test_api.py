from __future__ import annotations

import hashlib


def test_health_needs_no_token(client):
    body = client.get("/api/v1/health").json()

    assert body["status"] == "ok"
    assert body["sessions"] == 0
    assert body["auth_required"] is False


def test_upload_then_read_back(upload, client):
    created = upload()
    assert created.status_code == 201
    session_id = created.json()["id"]

    body = client.get(f"/api/v1/sessions/{session_id}").json()
    assert body["kind"] == "bms"
    assert body["sample_count"] == 6
    assert body["gap_count"] == 1
    assert body["max_delta_mv"] == 45


def test_a_retry_after_a_dropped_connection_is_a_success(upload):
    """
    The phone cannot tell a lost response from a lost request, so it will re-send. That has to end
    in something it can mark as done, not in an error it retries forever.
    """
    first = upload()
    second = upload()

    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert second.json()["id"] == first.json()["id"]


def test_a_truncated_upload_is_rejected(client, fixture_bytes):
    content = fixture_bytes("ride_gps.csv")
    honest_hash = hashlib.sha256(content).hexdigest()

    response = client.post(
        "/api/v1/sessions",
        files={"file": ("20260801-100000_bms.csv", content[: len(content) // 2], "text/csv")},
        data={"sha256": honest_hash},
    )

    assert response.status_code == 400
    assert "hash" in response.json()["detail"].lower()


def test_a_matching_hash_is_accepted(client, fixture_bytes):
    content = fixture_bytes("ride_gps.csv")

    response = client.post(
        "/api/v1/sessions",
        files={"file": ("20260801-100000_bms.csv", content, "text/csv")},
        data={"sha256": hashlib.sha256(content).hexdigest()},
    )

    assert response.status_code == 201


def test_empty_upload_is_rejected(client):
    response = client.post(
        "/api/v1/sessions", files={"file": ("empty.csv", b"", "text/csv")}
    )

    assert response.status_code == 400


def test_oversized_upload_is_rejected(client):
    response = client.post(
        "/api/v1/sessions",
        files={"file": ("huge.csv", b"x" * (9 * 1024 * 1024), "text/csv")},
    )

    assert response.status_code == 413


class TestListing:
    def test_newest_first_with_a_total(self, client, fixture_bytes):
        for name, as_name in [
            ("ride_gps.csv", "20260801-100000_bms.csv"),
            ("bench_no_gps.csv", "20260720-090000_bench.csv"),
            ("ekd01_ride.csv", "20260801-162000_EKD01.csv"),
        ]:
            client.post("/api/v1/sessions", files={"file": (as_name, fixture_bytes(name), "text/csv")})

        body = client.get("/api/v1/sessions").json()

        assert body["total"] == 3
        starts = [s["started_at_ms"] for s in body["sessions"]]
        assert starts == sorted(starts, reverse=True)

    def test_filtered_by_kind(self, client, fixture_bytes):
        client.post("/api/v1/sessions", files={"file": ("20260801-100000_bms.csv", fixture_bytes("ride_gps.csv"), "text/csv")})
        client.post("/api/v1/sessions", files={"file": ("20260801-162000_EKD01.csv", fixture_bytes("ekd01_ride.csv"), "text/csv")})

        body = client.get("/api/v1/sessions", params={"kind": "ekd01"}).json()

        assert body["total"] == 1
        assert body["sessions"][0]["kind"] == "ekd01"

    def test_filtered_by_local_date(self, client, fixture_bytes):
        client.post("/api/v1/sessions", files={"file": ("20260801-100000_bms.csv", fixture_bytes("ride_gps.csv"), "text/csv")})
        client.post("/api/v1/sessions", files={"file": ("20260720-090000_bench.csv", fixture_bytes("bench_no_gps.csv"), "text/csv")})

        body = client.get("/api/v1/sessions", params={"since": "2026-08-01"}).json()

        assert body["total"] == 1

    def test_searched_by_title(self, client, upload):
        session_id = upload().json()["id"]
        client.patch(f"/api/v1/sessions/{session_id}", json={"title": "Evening loop"})

        assert client.get("/api/v1/sessions", params={"q": "evening"}).json()["total"] == 1
        assert client.get("/api/v1/sessions", params={"q": "morning"}).json()["total"] == 0

    def test_the_list_carries_a_route_but_not_the_samples(self, client, upload):
        upload()

        row = client.get("/api/v1/sessions").json()["sessions"][0]

        # Enough to draw a thumbnail, without shipping thousands of rows per listed ride.
        assert row["polyline"]
        assert "samples" not in row


class TestTrack:
    def test_returns_the_route(self, client, upload):
        session_id = upload().json()["id"]

        body = client.get(f"/api/v1/sessions/{session_id}/track").json()

        assert len(body["points"]) == 6
        assert body["bounds"]["min_lat"] == 47.5
        assert body["polyline"]

    def test_a_session_without_gps_has_an_empty_track(self, client, fixture_bytes):
        response = client.post(
            "/api/v1/sessions",
            files={"file": ("20260720-090000_bench.csv", fixture_bytes("bench_no_gps.csv"), "text/csv")},
        )

        body = client.get(f"/api/v1/sessions/{response.json()['id']}/track").json()

        assert body["points"] == []
        assert body["bounds"] is None


class TestSeries:
    def test_small_sessions_come_back_whole(self, client, upload):
        session_id = upload().json()["id"]

        body = client.get(f"/api/v1/sessions/{session_id}/series", params={"fields": "watts,volts"}).json()

        assert body["downsampled"] is False
        assert body["fields"]["watts"] == [-360.0, -360.0, -360.0, -720.0, -720.0, -720.0]
        assert len(body["t"]) == 6

    def test_dropouts_are_reported_explicitly(self, client, upload):
        session_id = upload().json()["id"]

        body = client.get(f"/api/v1/sessions/{session_id}/series").json()

        # 2026-08-01T10:00:02+02:00 to 10:00:30+02:00 — the 28 s hole in the fixture.
        assert body["gaps"] == [[1785571202000, 1785571230000]]

    def test_unknown_fields_are_refused(self, client, upload):
        session_id = upload().json()["id"]

        response = client.get(f"/api/v1/sessions/{session_id}/series", params={"fields": "watts,rm -rf"})

        assert response.status_code == 400

    def test_downsampling_keeps_the_extremes(self, client, fixture_bytes):
        """
        The point of min/max bucketing: a one-second spike must survive being drawn at a fraction
        of the resolution. An average would erase exactly the thing worth looking at.
        """
        rows = ["timestamp,elapsed_s,volts,amps,watts,soc_percent,remaining_ah,cell1_mv,delta_mv,min_cell_mv,max_cell_mv"]
        for i in range(3000):
            watts = -2400.0 if i == 1500 else -300.0
            second = f"{i // 60 % 60:02d}"
            minute = f"{i // 3600:02d}"
            rows.append(
                f"2026-08-01T10:{minute}:{second}.{i % 1000:03d}+02:00,{i}.000,48.0,-7.5,{watts},90,12.4,3600,0,3600,3600"
            )
        content = "\n".join(rows).encode()

        response = client.post(
            "/api/v1/sessions", files={"file": ("20260801-100000_bms.csv", content, "text/csv")}
        )
        session_id = response.json()["id"]

        body = client.get(
            f"/api/v1/sessions/{session_id}/series", params={"fields": "watts", "points": 100}
        ).json()

        assert body["downsampled"] is True
        assert len(body["t"]) <= 100
        assert min(v for v in body["fields"]["watts"] if v is not None) == -2400.0


def test_title_and_notes_are_editable(client, upload):
    session_id = upload().json()["id"]

    patched = client.patch(
        f"/api/v1/sessions/{session_id}", json={"title": "Evening loop", "notes": "headwind"}
    ).json()

    assert patched["title"] == "Evening loop"
    assert patched["notes"] == "headwind"
    assert client.get(f"/api/v1/sessions/{session_id}").json()["title"] == "Evening loop"


def test_the_original_can_be_downloaded_back(client, upload, fixture_bytes):
    session_id = upload().json()["id"]

    response = client.get(f"/api/v1/sessions/{session_id}/raw.csv")

    assert response.status_code == 200
    assert response.content == fixture_bytes("ride_gps.csv")


def test_delete(client, upload):
    session_id = upload().json()["id"]

    assert client.delete(f"/api/v1/sessions/{session_id}").status_code == 200
    assert client.get(f"/api/v1/sessions/{session_id}").status_code == 404


def test_unknown_session_is_a_404(client):
    assert client.get("/api/v1/sessions/999").status_code == 404
    assert client.get("/api/v1/sessions/999/track").status_code == 404
    assert client.delete("/api/v1/sessions/999").status_code == 404


class TestStats:
    def test_totals_and_days(self, client, fixture_bytes):
        client.post("/api/v1/sessions", files={"file": ("20260801-100000_bms.csv", fixture_bytes("ride_gps.csv"), "text/csv")})

        body = client.get("/api/v1/stats", params={"rides_only": False}).json()

        assert body["totals"]["sessions"] == 1
        assert body["days"][0]["local_date"] == "2026-08-01"

    def test_rides_only_by_default(self, client, fixture_bytes):
        """The fixture travels 44 m, which is not a ride."""
        client.post("/api/v1/sessions", files={"file": ("20260801-100000_bms.csv", fixture_bytes("ride_gps.csv"), "text/csv")})

        assert client.get("/api/v1/stats").json()["totals"]["sessions"] == 0


class TestAuth:
    def test_uploads_are_refused_without_the_token(self, guarded_client, fixture_bytes):
        response = guarded_client.post(
            "/api/v1/sessions",
            files={"file": ("20260801-100000_bms.csv", fixture_bytes("ride_gps.csv"), "text/csv")},
        )

        assert response.status_code == 401

    def test_accepted_with_it(self, guarded_client, fixture_bytes):
        response = guarded_client.post(
            "/api/v1/sessions",
            files={"file": ("20260801-100000_bms.csv", fixture_bytes("ride_gps.csv"), "text/csv")},
            headers={"Authorization": "Bearer s3cret"},
        )

        assert response.status_code == 201

    def test_health_stays_open_so_the_phone_can_probe_it(self, guarded_client):
        body = guarded_client.get("/api/v1/health").json()

        assert body["status"] == "ok"
        assert body["auth_required"] is True

    def test_a_wrong_token_is_refused(self, guarded_client, fixture_bytes):
        response = guarded_client.post(
            "/api/v1/sessions",
            files={"file": ("20260801-100000_bms.csv", fixture_bytes("ride_gps.csv"), "text/csv")},
            headers={"Authorization": "Bearer wrong"},
        )

        assert response.status_code == 401
