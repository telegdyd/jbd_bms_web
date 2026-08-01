from __future__ import annotations

import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bmsweb import db
from bmsweb.config import Settings
from bmsweb.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture():
    def _load(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    return _load


@pytest.fixture
def fixture_bytes():
    def _load(name: str) -> bytes:
        return (FIXTURES / name).read_bytes()

    return _load


@pytest.fixture
def settings(tmp_path) -> Settings:
    config = Settings(data_dir=tmp_path / "data", upload_token=None, max_upload_bytes=8 * 1024 * 1024)
    config.prepare()
    return config


@pytest.fixture
def connection(settings):
    handle = db.connect(settings.database_path)
    yield handle
    handle.close()


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def guarded_client(tmp_path):
    """A service with a token configured, for checking the check."""
    config = Settings(
        data_dir=tmp_path / "guarded", upload_token="s3cret", max_upload_bytes=8 * 1024 * 1024
    )
    config.prepare()
    with TestClient(create_app(config)) as test_client:
        yield test_client


@pytest.fixture
def synthetic_ride():
    """
    One made-up ride recorded twice: the pack's CSV, and a watch's GPX of the same minutes with a
    heart rate and a clock `skew_s` seconds fast.

    Generated rather than checked in because the checked-in fixtures are deliberately tiny, and
    lining two recordings up needs minutes of movement to have anything to line up.
    """

    def _make(skew_s: int = 0, seconds: int = 600):
        csv = [
            "timestamp,elapsed_s,volts,amps,watts,soc_percent,remaining_ah,cell1_mv,delta_mv,"
            "min_cell_mv,max_cell_mv,latitude,longitude,altitude_m,speed_kmh,gps_accuracy_m"
        ]
        gpx = [
            '<?xml version="1.0"?>',
            '<gpx creator="StravaGPX" xmlns="http://www.topografix.com/GPX/1/1" '
            'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">',
            "<trk><name>Test ride</name><trkseg>",
        ]

        latitude = 47.5
        for i in range(seconds):
            # Two sine waves, so that no lag but the true one can fit as well.
            speed = 18 + 12 * math.sin(i / 40.0) + 4 * math.sin(i / 7.0)
            latitude += speed / 3.6 / 110540.0
            # 10:00+02:00 and 08:00Z are the same instant; the skew is the watch being wrong.
            csv.append(
                f"2026-08-01T10:{i // 60:02d}:{i % 60:02d}.000+02:00,{i}.000,48.0,-7.5,-360.00,"
                f"90,12.4,3600,30,3600,3630,{latitude:.6f},19.000000,120.0,{speed:.2f},5.0"
            )
            at = i + skew_s
            gpx.append(
                f'<trkpt lat="{latitude:.6f}" lon="19.000000"><ele>120</ele>'
                f"<time>2026-08-01T08:{at // 60:02d}:{at % 60:02d}Z</time><extensions>"
                f"<gpxtpx:TrackPointExtension><gpxtpx:hr>{120 + i % 40}</gpxtpx:hr>"
                f"</gpxtpx:TrackPointExtension></extensions></trkpt>"
            )

        gpx.append("</trkseg></trk></gpx>")
        return "\n".join(csv).encode(), "\n".join(gpx).encode()

    return _make


@pytest.fixture
def attached(client, synthetic_ride):
    """A session with a companion GPX on it, seven seconds out. Returns both ids."""

    def _attach(skew_s: int = 7, seconds: int = 600):
        csv, gpx = synthetic_ride(skew_s, seconds)
        session_id = client.post(
            "/api/v1/sessions", files={"file": ("20260801-100000_bms.csv", csv, "text/csv")}
        ).json()["id"]
        companion = client.post(
            f"/api/v1/sessions/{session_id}/companions",
            files={"file": ("ride.gpx", gpx, "application/gpx+xml")},
        ).json()["companion"]
        return session_id, companion

    return _attach


@pytest.fixture
def upload(client, fixture_bytes):
    def _upload(name: str = "ride_gps.csv", as_name: str = "20260801-100000_bms.csv", **kwargs):
        return client.post(
            "/api/v1/sessions",
            files={"file": (as_name, fixture_bytes(name), "text/csv")},
            **kwargs,
        )

    return _upload
