from __future__ import annotations

from bmsweb import db
from bmsweb.ingest import reparse


class TestAttaching:
    def test_a_gpx_is_stored_and_lined_up_with_the_recording(self, attached):
        _, companion = attached(skew_s=7)

        assert companion["offset_source"] == "correlation"
        assert companion["offset_ms"] == -7000, "the watch's clock was seven seconds fast"
        assert companion["correlation"] > 0.9
        assert companion["point_count"] == 600
        assert companion["hr_count"] == 600

    def test_the_original_comes_back_unchanged(self, client, synthetic_ride, attached):
        session_id, companion = attached()
        _, gpx = synthetic_ride(7)

        response = client.get(
            f"/api/v1/sessions/{session_id}/companions/{companion['id']}/raw.gpx"
        )

        assert response.status_code == 200
        assert response.content == gpx

    def test_attaching_the_same_export_twice_is_a_no_op(self, client, synthetic_ride):
        csv, gpx = synthetic_ride(7)
        session_id = client.post(
            "/api/v1/sessions", files={"file": ("20260801-100000_bms.csv", csv, "text/csv")}
        ).json()["id"]

        first = client.post(
            f"/api/v1/sessions/{session_id}/companions",
            files={"file": ("ride.gpx", gpx, "application/gpx+xml")},
        )
        second = client.post(
            f"/api/v1/sessions/{session_id}/companions",
            files={"file": ("ride.gpx", gpx, "application/gpx+xml")},
        )

        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json()["status"] == "duplicate"
        assert second.json()["companion"]["id"] == first.json()["companion"]["id"]

    def test_an_offset_may_be_given_instead_of_measured(self, client, synthetic_ride):
        csv, gpx = synthetic_ride(7)
        session_id = client.post(
            "/api/v1/sessions", files={"file": ("20260801-100000_bms.csv", csv, "text/csv")}
        ).json()["id"]

        body = client.post(
            f"/api/v1/sessions/{session_id}/companions",
            files={"file": ("ride.gpx", gpx, "application/gpx+xml")},
            data={"offset_ms": "-4000"},
        ).json()["companion"]

        assert body["offset_source"] == "manual"
        assert body["offset_ms"] == -4000

    def test_a_short_recording_is_attached_without_a_measured_offset(self, client, fixture_bytes):
        """
        Half a minute of riding is not enough shape to match on. The file still attaches and its
        heart rate is still usable — it is only the offset that goes unclaimed.
        """
        session_id = client.post(
            "/api/v1/sessions",
            files={"file": ("20260801-100000_bms.csv", fixture_bytes("ride_gps.csv"), "text/csv")},
        ).json()["id"]

        body = client.post(
            f"/api/v1/sessions/{session_id}/companions",
            files={"file": ("short.gpx", fixture_bytes("strava_hr.gpx"), "application/gpx+xml")},
        ).json()["companion"]

        assert body["offset_source"] == "none"
        assert body["offset_ms"] == 0
        assert body["hr_in_session"] == 6

    def test_a_file_that_is_not_a_gpx_is_refused_with_a_reason(self, client, upload):
        session_id = upload().json()["id"]

        response = client.post(
            f"/api/v1/sessions/{session_id}/companions",
            files={"file": ("notes.txt", b"not xml at all", "text/plain")},
        )

        assert response.status_code == 400
        assert "XML" in response.json()["detail"]

    def test_an_empty_upload_is_refused(self, client, upload):
        session_id = upload().json()["id"]

        response = client.post(
            f"/api/v1/sessions/{session_id}/companions",
            files={"file": ("empty.gpx", b"", "application/gpx+xml")},
        )

        assert response.status_code == 400

    def test_attaching_to_a_session_that_does_not_exist(self, client, fixture_bytes):
        response = client.post(
            "/api/v1/sessions/999/companions",
            files={"file": ("short.gpx", fixture_bytes("strava_hr.gpx"), "application/gpx+xml")},
        )

        assert response.status_code == 404


class TestCharting:
    def test_heart_rate_arrives_on_the_recording_s_own_clock(self, client, attached):
        """
        The generated heart rate is 120 at the first second and climbs by one a second, so reading
        it back in order is what proves the seven-second shift was actually applied.
        """
        session_id, _ = attached(skew_s=7)

        body = client.get(
            f"/api/v1/sessions/{session_id}/series", params={"fields": "watts,hr", "points": 2000}
        ).json()

        assert body["downsampled"] is False
        assert body["fields"]["hr"][:4] == [120, 121, 122, 123]
        assert len(body["fields"]["hr"]) == len(body["t"])

    def test_a_downsampled_chart_still_carries_it(self, client, attached):
        session_id, _ = attached()

        body = client.get(
            f"/api/v1/sessions/{session_id}/series", params={"fields": "watts,hr", "points": 100}
        ).json()

        assert body["downsampled"] is True
        assert len(body["fields"]["hr"]) == len(body["t"])
        assert all(v is not None for v in body["fields"]["hr"])

    def test_heart_rate_alone_is_a_valid_request(self, client, attached):
        session_id, _ = attached()

        body = client.get(f"/api/v1/sessions/{session_id}/series", params={"fields": "hr"}).json()

        assert any(v is not None for v in body["fields"]["hr"])

    def test_a_session_with_nothing_attached_reports_no_heart_rate(self, client, upload):
        session_id = upload().json()["id"]

        body = client.get(
            f"/api/v1/sessions/{session_id}/series", params={"fields": "watts,hr"}
        ).json()

        # Present but empty, so the page can ask for it unconditionally and simply draw nothing.
        assert body["fields"]["hr"] == [None] * len(body["t"])

    def test_the_stretch_the_watch_was_not_running_for_is_null(self, client, synthetic_ride):
        """A GPX covering the second half of a ride must not flatten across the first."""
        csv, _ = synthetic_ride(seconds=600)
        _, gpx = synthetic_ride(seconds=600)
        session_id = client.post(
            "/api/v1/sessions", files={"file": ("20260801-100000_bms.csv", csv, "text/csv")}
        ).json()["id"]

        # Move it an hour later, well past the ride, so nothing overlaps.
        client.post(
            f"/api/v1/sessions/{session_id}/companions",
            files={"file": ("ride.gpx", gpx, "application/gpx+xml")},
            data={"offset_ms": str(3600 * 1000)},
        )

        body = client.get(f"/api/v1/sessions/{session_id}/series", params={"fields": "hr"}).json()

        assert body["fields"]["hr"] == [None] * len(body["t"])


class TestOffset:
    def test_it_can_be_set_by_hand(self, client, attached):
        session_id, companion = attached()

        body = client.patch(
            f"/api/v1/sessions/{session_id}/companions/{companion['id']}",
            json={"offset_ms": 12_000},
        ).json()

        assert body["offset_ms"] == 12_000
        assert body["offset_source"] == "manual"

    def test_and_measured_again_afterwards(self, client, attached):
        session_id, companion = attached(skew_s=7)
        client.patch(
            f"/api/v1/sessions/{session_id}/companions/{companion['id']}", json={"offset_ms": 12_000}
        )

        body = client.patch(
            f"/api/v1/sessions/{session_id}/companions/{companion['id']}", json={"realign": True}
        ).json()

        assert body["offset_ms"] == -7000
        assert body["offset_source"] == "correlation"

    def test_a_patch_that_asks_for_nothing_is_refused(self, client, attached):
        session_id, companion = attached()

        response = client.patch(
            f"/api/v1/sessions/{session_id}/companions/{companion['id']}", json={}
        )

        assert response.status_code == 400

    def test_an_absurd_offset_is_refused(self, client, attached):
        session_id, companion = attached()

        response = client.patch(
            f"/api/v1/sessions/{session_id}/companions/{companion['id']}",
            json={"offset_ms": 9_000_000},
        )

        assert response.status_code == 422

    def test_the_heart_rate_figures_follow_the_offset(self, client, attached):
        """
        Shown figures are computed over the part that overlaps the ride, so a companion pushed
        clear of the session must stop reporting an average for it.
        """
        session_id, companion = attached()
        assert companion["hr_in_session"] > 0

        moved = client.patch(
            f"/api/v1/sessions/{session_id}/companions/{companion['id']}",
            json={"offset_ms": 3600 * 1000},
        ).json()

        assert moved["hr_in_session"] == 0
        assert moved["hr_avg"] is None


class TestLifecycle:
    def test_a_companion_survives_a_reparse(self, client, settings, attached):
        """
        A reparse rebuilds a session from its CSV, and the sample rows go with it. A heart rate
        stored among them would have gone too — which is the whole reason it is kept apart.
        """
        session_id, companion = attached(skew_s=7)

        connection = db.connect(settings.database_path)
        assert reparse(connection, settings, session_id)
        connection.close()

        body = client.get(f"/api/v1/sessions/{session_id}/companions").json()["companions"]
        assert len(body) == 1
        assert body[0]["id"] == companion["id"]
        assert body[0]["offset_ms"] == -7000
        assert body[0]["hr_in_session"] == 600

    def test_a_hand_set_offset_is_not_overruled_by_a_reparse(self, client, settings, attached):
        session_id, companion = attached()
        client.patch(
            f"/api/v1/sessions/{session_id}/companions/{companion['id']}", json={"offset_ms": 5000}
        )

        connection = db.connect(settings.database_path)
        reparse(connection, settings, session_id)
        connection.close()

        body = client.get(f"/api/v1/sessions/{session_id}/companions").json()["companions"][0]
        assert body["offset_ms"] == 5000
        assert body["offset_source"] == "manual"

    def test_detaching_keeps_the_file(self, client, settings, attached):
        session_id, companion = attached()

        assert client.delete(
            f"/api/v1/sessions/{session_id}/companions/{companion['id']}"
        ).status_code == 200
        assert client.get(f"/api/v1/sessions/{session_id}/companions").json()["companions"] == []
        assert list(settings.trash_dir.glob("*.gpx")), "the original is in the trash, not gone"

    def test_deleting_the_session_takes_its_companions_with_it(self, client, settings, attached):
        session_id, _ = attached()

        client.delete(f"/api/v1/sessions/{session_id}")

        assert client.get(f"/api/v1/sessions/{session_id}/companions").status_code == 404
        assert list(settings.trash_dir.glob("*.gpx")), "and does not leave the file behind"

    def test_a_companion_belongs_to_one_session(self, client, upload, attached):
        session_id, companion = attached()
        other = upload(as_name="20260901-100000_bms.csv").json()["id"]

        assert client.get(
            f"/api/v1/sessions/{other}/companions/{companion['id']}/raw.gpx"
        ).status_code == 404
        assert client.patch(
            f"/api/v1/sessions/{other}/companions/{companion['id']}", json={"offset_ms": 0}
        ).status_code == 404


def test_attaching_needs_the_token_when_one_is_set(guarded_client, fixture_bytes):
    session_id = guarded_client.post(
        "/api/v1/sessions",
        files={"file": ("20260801-100000_bms.csv", fixture_bytes("ride_gps.csv"), "text/csv")},
        headers={"Authorization": "Bearer s3cret"},
    ).json()["id"]

    response = guarded_client.post(
        f"/api/v1/sessions/{session_id}/companions",
        files={"file": ("short.gpx", fixture_bytes("strava_hr.gpx"), "application/gpx+xml")},
    )

    assert response.status_code == 401
