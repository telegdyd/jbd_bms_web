"""
Startup checks.

The unwritable-data-directory case earns its own test because of how badly it presents: the
container restarts for ever while the stack reports itself deployed, and nothing in the symptom
points at file ownership.
"""

from __future__ import annotations

import os
import sys

import pytest

from bmsweb.config import Settings, load_settings


def settings_at(path):
    return Settings(data_dir=path, upload_token=None, max_upload_bytes=1024)


def test_prepare_creates_the_directories(tmp_path):
    config = settings_at(tmp_path / "data")

    config.prepare()

    assert config.data_dir.is_dir()
    assert config.raw_dir.is_dir()
    assert config.trash_dir.is_dir()


def test_prepare_leaves_nothing_behind(tmp_path):
    """The writability probe must not litter the volume."""
    config = settings_at(tmp_path / "data")

    config.prepare()

    assert not (config.data_dir / ".writable").exists()


def test_prepare_is_repeatable(tmp_path):
    config = settings_at(tmp_path / "data")

    config.prepare()
    config.prepare()

    assert config.data_dir.is_dir()


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permissions; the case only arises in the container"
)
def test_an_unwritable_data_directory_explains_itself(tmp_path):
    """
    What a root-owned bind mount looks like from inside a container running as uid 10001. The
    message has to name the cause, because the symptom never will.
    """
    data = tmp_path / "data"
    data.mkdir()
    os.chmod(data, 0o555)

    try:
        with pytest.raises(RuntimeError) as raised:
            settings_at(data).prepare()
    finally:
        os.chmod(data, 0o755)

    message = str(raised.value)
    assert "10001" in message
    assert "named volume" in message


def test_defaults_need_no_environment(monkeypatch):
    for name in ("BMS_DATA_DIR", "BMS_UPLOAD_TOKEN", "BMS_MAX_UPLOAD_MB"):
        monkeypatch.delenv(name, raising=False)

    config = load_settings()

    assert config.upload_token is None
    assert config.auth_required is False
    assert config.max_upload_bytes == 256 * 1024 * 1024


def test_a_blank_token_is_no_token(monkeypatch):
    """Portainer hands through an empty string for a variable left blank in its UI."""
    monkeypatch.setenv("BMS_UPLOAD_TOKEN", "   ")

    assert load_settings().auth_required is False
