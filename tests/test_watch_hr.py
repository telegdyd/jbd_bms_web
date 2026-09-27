"""
Heart rate from the watch, which the phone folds into the recording as a last `hr_bpm` column once
the ride ends. See `HeartRateLog.merge` in the app.
"""

from __future__ import annotations

import pytest

from bmsweb.parse import parse_csv
from bmsweb.summary import summarise


def with_heart_rate(csv: bytes, bpm) -> bytes:
    """
    The synthetic ride as the phone now writes it: `balance_bits` and then `hr_bpm` on the end,
    with `bpm(i)` for row i — None leaves the cell blank, as the merge does where the watch had
    nothing recent.
    """
    lines = csv.decode().split("\n")
    out = [lines[0] + ",balance_bits,hr_bpm"]
    for i, line in enumerate(lines[1:]):
        value = bpm(i)
        out.append(f"{line},0,{'' if value is None else value}")
    return "\n".join(out).encode()


def watch(i: int) -> int | None:
    # Nothing for the first ten seconds — the watch was still finding the pulse.
    return None if i < 10 else 100 + i % 50


@pytest.fixture
def watched(client, synthetic_ride):
    def _upload(bpm=watch, seconds: int = 600):
        csv, _ = synthetic_ride(0, seconds)
        return client.post(
            "/api/v1/sessions",
            files={"file": ("20260801-100000_bms.csv", with_heart_rate(csv, bpm), "text/csv")},
        ).json()["id"]

    return _upload


class TestParsing:
    def test_the_column_is_read_by_name(self, synthetic_ride):
        csv, _ = synthetic_ride(0, 30)
        session = parse_csv(with_heart_rate(csv, watch).decode())

        assert [s.heart_rate_bpm for s in session.samples[8:12]] == [None, None, 110, 111]
        # Appended after balance_bits, which must still be read as itself.
        assert all(s.balance_bits == 0 for s in session.samples)

    def test_a_recording_from_before_the_watch_has_none(self, synthetic_ride):
        csv, _ = synthetic_ride(0, 30)
        session = parse_csv(csv.decode())

        assert all(s.heart_rate_bpm is None for s in session.samples)

    def test_a_row_cut_short_by_a_kill_is_still_dropped(self, synthetic_ride):
        csv, _ = synthetic_ride(0, 30)
        text = with_heart_rate(csv, watch).decode()
        truncated = text + "\n" + text.split("\n")[-1][:40]

        assert len(parse_csv(truncated).samples) == 30


class TestSummary:
    def test_average_and_maximum_over_the_rows_that_have_one(self, synthetic_ride):
        csv, _ = synthetic_ride(0, 30)
        summary = summarise(parse_csv(with_heart_rate(csv, watch).decode()))

        # Rows 10..29 read 110..129.
        assert summary.avg_heart_rate_bpm == 120
        assert summary.max_heart_rate_bpm == 129

    def test_a_half_rounds_up_as_it_does_on_the_phone(self, synthetic_ride):
        csv, _ = synthetic_ride(0, 30)
        summary = summarise(
            parse_csv(with_heart_rate(csv, lambda i: 132 if i % 2 else 133).decode())
        )

        # 132.5: Kotlin's roundToInt gives 133, Python's round would give 132.
        assert summary.avg_heart_rate_bpm == 133

    def test_nothing_measured_is_none_not_zero(self, synthetic_ride):
        csv, _ = synthetic_ride(0, 30)
        summary = summarise(parse_csv(with_heart_rate(csv, lambda i: None).decode()))

        assert summary.avg_heart_rate_bpm is None
        assert summary.max_heart_rate_bpm is None


class TestApi:
    def test_the_session_carries_the_figures(self, client, watched):
        session_id = watched()
        body = client.get(f"/api/v1/sessions/{session_id}").json()

        assert body["avg_hr_bpm"] is not None
        assert body["max_hr_bpm"] == 149

        listed = client.get("/api/v1/sessions").json()["sessions"][0]
        assert listed["avg_hr_bpm"] == body["avg_hr_bpm"]

    def test_it_is_charted_from_the_recording(self, client, watched):
        session_id = watched(seconds=60)
        body = client.get(f"/api/v1/sessions/{session_id}/series?fields=watts,hr").json()

        assert body["sources"] == {"hr": "recording"}
        assert body["fields"]["hr"][:10] == [None] * 10
        assert body["fields"]["hr"][10:13] == [110, 111, 112]

    def test_downsampling_keeps_it(self, client, watched):
        session_id = watched()
        body = client.get(f"/api/v1/sessions/{session_id}/series?fields=hr&points=100").json()

        assert body["downsampled"] is True
        assert max(v for v in body["fields"]["hr"] if v is not None) == 149

    def test_it_wins_over_an_attached_gpx(self, client, synthetic_ride):
        """The GPX's heart rate is 120 + i % 40; the watch's is 100 + i % 50. Only one is drawn."""
        csv, gpx = synthetic_ride(0, 600)
        session_id = client.post(
            "/api/v1/sessions",
            files={"file": ("20260801-100000_bms.csv", with_heart_rate(csv, watch), "text/csv")},
        ).json()["id"]
        client.post(
            f"/api/v1/sessions/{session_id}/companions",
            files={"file": ("ride.gpx", gpx, "application/gpx+xml")},
        )

        body = client.get(f"/api/v1/sessions/{session_id}/series?fields=hr&points=20000").json()

        assert body["sources"] == {"hr": "recording"}
        assert body["fields"]["hr"][10:13] == [110, 111, 112]

    def test_without_the_watch_the_gpx_still_supplies_it(self, client, attached):
        session_id, _ = attached()
        body = client.get(f"/api/v1/sessions/{session_id}/series?fields=hr").json()

        assert body["sources"] == {"hr": "companion"}
        assert any(v is not None for v in body["fields"]["hr"])

    def test_the_track_carries_it_for_colouring_the_route(self, client, watched):
        session_id = watched()
        points = client.get(f"/api/v1/sessions/{session_id}/track?detail=full").json()["points"]

        assert points[0]["hr"] is None
        assert points[10]["hr"] == 110

    def test_splits_carry_an_average(self, client, watched):
        session_id = watched()
        splits = client.get(f"/api/v1/sessions/{session_id}/splits").json()["splits"]

        assert splits
        assert all(100 <= s["avg_hr_bpm"] <= 149 for s in splits)

    def test_it_survives_a_reparse(self, client, connection, settings, watched):
        from bmsweb.ingest import reparse

        session_id = watched()
        before = client.get(f"/api/v1/sessions/{session_id}").json()

        assert reparse(connection, settings, session_id)
        after = client.get(f"/api/v1/sessions/{session_id}").json()

        assert after["avg_hr_bpm"] == before["avg_hr_bpm"]
        assert after["max_hr_bpm"] == before["max_hr_bpm"]
