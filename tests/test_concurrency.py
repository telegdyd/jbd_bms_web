"""
The ride page asks for its track, series and splits at once, so the database is reached from
several threadpool threads simultaneously. A single shared SQLite connection fails that with
`InterfaceError: bad parameter or other API misuse`, and it fails it only under real concurrency —
which is why this file exists rather than being folded into the endpoint tests.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest


@pytest.fixture
def busy_session(client, fixture_bytes):
    """A recording with enough samples that a request holds the connection for a moment."""
    header = (
        "timestamp,elapsed_s,volts,amps,watts,soc_percent,remaining_ah,"
        "cell1_mv,delta_mv,min_cell_mv,max_cell_mv,latitude,longitude,altitude_m,"
        "speed_kmh,gps_accuracy_m,balance_bits"
    )
    rows = [header]
    for i in range(4000):
        rows.append(
            f"2026-08-01T10:{i // 3600:02d}:{i // 60 % 60:02d}.{i % 1000:03d}+02:00,{i}.000,"
            f"48.0,-7.5,-360.0,90,12.4,3600,0,3600,3600,"
            f"{47.5 + i * 0.00002:.6f},19.0,120.0,20.00,5.0,0"
        )

    response = client.post(
        "/api/v1/sessions",
        files={"file": ("20260801-100000_bms.csv", "\n".join(rows).encode(), "text/csv")},
    )
    return response.json()["id"]


def test_the_ride_page_requests_do_not_collide(client, busy_session):
    """Exactly what the detail view fires: three endpoints, at once, all reading samples."""
    paths = [
        f"/api/v1/sessions/{busy_session}/track",
        f"/api/v1/sessions/{busy_session}/series?fields=watts,volts,speed_kmh&points=3000",
        f"/api/v1/sessions/{busy_session}/splits",
    ] * 4

    with ThreadPoolExecutor(max_workers=12) as pool:
        responses = list(pool.map(client.get, paths))

    assert [r.status_code for r in responses] == [200] * len(paths)


def test_reads_stay_correct_under_load(client, busy_session):
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(client.get, [f"/api/v1/sessions/{busy_session}"] * 16))

    assert {r.json()["sample_count"] for r in responses} == {4000}


def test_uploads_and_reads_can_overlap(client, fixture_bytes, busy_session):
    """A ride arriving from the phone while the browser is reading must not fail either one."""

    def upload(index):
        rows = fixture_bytes("ride_gps.csv").decode().rstrip().split("\n")
        # A distinct body per upload, so these are genuine inserts rather than deduplicated retries.
        rows[-1] = rows[-1].replace("46.800", f"46.8{index:02d}")
        return client.post(
            "/api/v1/sessions",
            files={"file": (f"20260801-1000{index:02d}_bms.csv", "\n".join(rows).encode(), "text/csv")},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        writes = pool.map(upload, range(6))
        reads = pool.map(client.get, [f"/api/v1/sessions/{busy_session}/splits"] * 6)
        write_codes = [r.status_code for r in writes]
        read_codes = [r.status_code for r in reads]

    assert write_codes == [201] * 6
    assert read_codes == [200] * 6
