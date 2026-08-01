from __future__ import annotations

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
def upload(client, fixture_bytes):
    def _upload(name: str = "ride_gps.csv", as_name: str = "20260801-100000_bms.csv", **kwargs):
        return client.post(
            "/api/v1/sessions",
            files={"file": (as_name, fixture_bytes(name), "text/csv")},
            **kwargs,
        )

    return _upload
