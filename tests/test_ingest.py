from __future__ import annotations

from pathlib import Path

from bmsweb.ingest import IngestStatus, ingest, delete, reparse, sha256_of


def test_upload_is_indexed_and_archived(connection, settings, fixture_bytes):
    content = fixture_bytes("ride_gps.csv")

    result = ingest(connection, settings, "20260801-100000_bms.csv", content)

    assert result.status is IngestStatus.CREATED
    row = connection.execute("SELECT * FROM sessions WHERE id = ?", (result.session_id,)).fetchone()

    assert row["kind"] == "bms"
    assert row["device_label"] == "bms"
    assert row["sample_count"] == 6
    assert row["has_location"] == 1
    # 44 m of travel is a walk to the shed, not a ride with its own page.
    assert row["is_ride"] == 0
    assert row["gap_count"] == 1
    assert row["polyline"]

    archived = settings.data_dir / row["raw_path"]
    assert archived.read_bytes() == content, "the original must be kept byte for byte"


def test_archive_is_filed_by_the_month_it_was_recorded(connection, settings, fixture_bytes):
    result = ingest(connection, settings, "20260801-100000_bms.csv", fixture_bytes("ride_gps.csv"))

    row = connection.execute("SELECT raw_path FROM sessions WHERE id = ?", (result.session_id,)).fetchone()

    assert Path(row["raw_path"]).parts[:3] == ("raw", "2026", "08")


def test_local_date_follows_the_recording_not_the_server(connection, settings):
    """
    A ride at 00:30 in +02:00 belongs to that day, not to the previous one in UTC. Getting this
    wrong scatters evening rides across two days in the calendar.
    """
    text = (
        "timestamp,elapsed_s,volts,amps,watts,soc_percent,remaining_ah,"
        "cell1_mv,delta_mv,min_cell_mv,max_cell_mv\n"
        "2026-08-02T00:30:00.000+02:00,0.000,48.0,-7.5,-360.0,90,12.4,3600,0,3600,3600\n"
    )

    result = ingest(connection, settings, "20260802-003000_bms.csv", text.encode())

    row = connection.execute("SELECT local_date, tz_offset_min FROM sessions WHERE id = ?", (result.session_id,)).fetchone()
    assert row["local_date"] == "2026-08-02"
    assert row["tz_offset_min"] == 120


def test_the_same_file_twice_is_one_session(connection, settings, fixture_bytes):
    content = fixture_bytes("ride_gps.csv")

    first = ingest(connection, settings, "20260801-100000_bms.csv", content)
    second = ingest(connection, settings, "20260801-100000_bms.csv", content)

    assert second.status is IngestStatus.DUPLICATE
    assert second.session_id == first.session_id
    assert connection.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 1


def test_the_same_name_from_two_devices_is_two_sessions(connection, settings, fixture_bytes):
    """
    Filenames collide — two recordings can start in the same second. The content hash is the key
    precisely so that is not mistaken for a retry.
    """
    ingest(connection, settings, "20260801-100000_bms.csv", fixture_bytes("ride_gps.csv"))
    ingest(connection, settings, "20260801-100000_bms.csv", fixture_bytes("bench_no_gps.csv"))

    assert connection.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 2


def test_ekd01_samples_keep_their_own_fields(connection, settings, fixture_bytes):
    result = ingest(connection, settings, "20260801-162000_EKD01.csv", fixture_bytes("ekd01_ride.csv"))

    row = connection.execute("SELECT * FROM sessions WHERE id = ?", (result.session_id,)).fetchone()
    assert row["kind"] == "ekd01"
    assert row["device_label"] == "EKD01"

    sample = connection.execute(
        "SELECT * FROM samples WHERE session_id = ? ORDER BY t_ms LIMIT 1", (result.session_id,)
    ).fetchone()
    # No electrical data is invented for a display that does not report any.
    assert sample["volts"] is None
    assert sample["watts"] is None
    assert '"odometer_km": 1284.6' in sample["extra"]


def test_a_recording_with_no_readable_samples_is_still_stored(connection, settings):
    """
    Started and stopped by accident. Storing it is mildly untidy; rejecting it would make the
    phone retry the same file forever.
    """
    content = b"timestamp,elapsed_s,volts,amps,watts,soc_percent,remaining_ah,cell1_mv,delta_mv,min_cell_mv,max_cell_mv\n"

    result = ingest(connection, settings, "20260801-100000_bms.csv", content)

    row = connection.execute("SELECT * FROM sessions WHERE id = ?", (result.session_id,)).fetchone()
    assert row["sample_count"] == 0
    assert row["local_date"] == "2026-08-01"


def test_a_hostile_filename_cannot_escape_the_archive(connection, settings, fixture_bytes):
    result = ingest(connection, settings, "../../../etc/passwd.csv", fixture_bytes("ride_gps.csv"))

    row = connection.execute("SELECT raw_path FROM sessions WHERE id = ?", (result.session_id,)).fetchone()
    stored = (settings.data_dir / row["raw_path"]).resolve()

    assert settings.raw_dir.resolve() in stored.parents
    assert ".." not in row["raw_path"]


class TestReparse:
    def test_rebuilds_in_place_keeping_what_the_user_wrote(self, connection, settings, fixture_bytes):
        result = ingest(connection, settings, "20260801-100000_bms.csv", fixture_bytes("ride_gps.csv"))
        connection.execute(
            "UPDATE sessions SET title = ?, notes = ?, distance_km = 999 WHERE id = ?",
            ("Evening loop", "felt slow", result.session_id),
        )
        connection.commit()

        assert reparse(connection, settings, result.session_id)

        row = connection.execute("SELECT * FROM sessions WHERE id = ?", (result.session_id,)).fetchone()
        assert row["title"] == "Evening loop"
        assert row["notes"] == "felt slow"
        assert row["distance_km"] < 1.0, "the figures come back from the raw file"
        assert row["sample_count"] == 6

    def test_does_not_duplicate_samples(self, connection, settings, fixture_bytes):
        result = ingest(connection, settings, "20260801-100000_bms.csv", fixture_bytes("ride_gps.csv"))

        reparse(connection, settings, result.session_id)
        reparse(connection, settings, result.session_id)

        count = connection.execute(
            "SELECT COUNT(*) AS n FROM samples WHERE session_id = ?", (result.session_id,)
        ).fetchone()["n"]
        assert count == 6

    def test_unknown_session(self, connection, settings):
        assert reparse(connection, settings, 999) is False


def test_delete_moves_the_original_to_the_trash(connection, settings, fixture_bytes):
    result = ingest(connection, settings, "20260801-100000_bms.csv", fixture_bytes("ride_gps.csv"))
    row = connection.execute("SELECT raw_path FROM sessions WHERE id = ?", (result.session_id,)).fetchone()
    archived = settings.data_dir / row["raw_path"]

    assert delete(connection, settings, result.session_id)

    assert not archived.exists()
    assert list(settings.trash_dir.iterdir()), "deleted recordings are moved aside, not unlinked"
    assert connection.execute("SELECT COUNT(*) AS n FROM samples").fetchone()["n"] == 0


def test_sha256_is_the_plain_content_hash(fixture_bytes):
    import hashlib

    content = fixture_bytes("ride_gps.csv")
    assert sha256_of(content) == hashlib.sha256(content).hexdigest()
