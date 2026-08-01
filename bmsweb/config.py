"""
Settings, all from the environment so the compose file is the only place anything is configured.

Every one of these has a working default. Nothing here needs to be set to run the service on a
home LAN — set them when you want something other than what it already does.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    #: Everything that survives a container rebuild: the SQLite index and the uploaded originals.
    data_dir: Path

    #: Shared secret the phone sends as `Authorization: Bearer …`.
    #:
    #: Empty disables the check entirely, which is a perfectly reasonable thing to do on a home
    #: LAN. It is not really about attackers: an unauthenticated POST that writes files to disk can
    #: be reached by anything on the network, including a page open in a browser tab, and a token
    #: costs one header to avoid that.
    upload_token: str | None

    #: Rejected above this, so a wrong URL pointed at this service cannot fill the disk.
    max_upload_bytes: int

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def trash_dir(self) -> Path:
        """Deleted recordings land here rather than being unlinked. Disk is cheap; rides are not."""
        return self.data_dir / "trash"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "bms.sqlite"

    @property
    def auth_required(self) -> bool:
        return bool(self.upload_token)

    def prepare(self) -> None:
        for directory in (self.data_dir, self.raw_dir, self.trash_dir):
            directory.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    token = os.environ.get("BMS_UPLOAD_TOKEN", "").strip()
    return Settings(
        data_dir=Path(os.environ.get("BMS_DATA_DIR", "./data")).resolve(),
        upload_token=token or None,
        max_upload_bytes=int(os.environ.get("BMS_MAX_UPLOAD_MB", "256")) * 1024 * 1024,
    )


@lru_cache(maxsize=1)
def settings() -> Settings:
    return load_settings()
