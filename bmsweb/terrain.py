"""
Ground height under a point, from an elevation map.

The rider's share of a ride is mostly a question of climbing, so it needs heights it can trust. A
phone's GPS altitude drifts with the fix, and this phone has no barometer; the ground itself does
not move. SRTM gives it at one arc-second, about 30 m, which is fine along a route once it is
smoothed over a few seconds of riding.

Tiles are the plain `.hgt` format: a square grid of big-endian 16-bit metres, north row first, one
file per whole degree. Read with `struct` rather than numpy — four values per lookup is nothing, and
it keeps the image free of a large dependency for one function.

They are fetched the first time a ride needs them and kept in the data directory, so after that
everything works with the internet down. Nothing about a ride leaves the house: the only request
is for a file named after a whole degree of latitude and longitude, which covers ~8,000 km².
"""

from __future__ import annotations

import gzip
import logging
import math
import struct
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Iterable

log = logging.getLogger("uvicorn.error")

#: Mapzen's "skadi" tiles on AWS Open Data: SRTM, with other sources filling its gaps. Public, no
#: key, and laid out as `N47/N47E019.hgt.gz`.
DEFAULT_URL = "https://s3.amazonaws.com/elevation-tiles-prod/skadi"

#: SRTM marks cells it has no measurement for with this.
VOID = -32768

#: A tile is 26 MB in memory. A ride rarely crosses more than two.
CACHED_TILES = 4

#: After a failed download, how long before the same tile is tried again. Long enough that a page
#: view does not wait on a dead network every time, short enough that it comes back on its own.
RETRY_AFTER_S = 600

Fetch = Callable[[str], bytes]


def tile_name(lat: float, lon: float) -> str:
    """`N47E019` for any point in 47°–48°N, 19°–20°E. Named after the south-west corner."""
    la, lo = math.floor(lat), math.floor(lon)
    return f"{'N' if la >= 0 else 'S'}{abs(la):02d}{'E' if lo >= 0 else 'W'}{abs(lo):03d}"


class Tile:
    def __init__(self, name: str, data: bytes) -> None:
        cells = len(data) // 2
        size = math.isqrt(cells)
        if size * size != cells or size < 2:
            raise ValueError(f"{name}: {len(data)} bytes is not a square grid of heights")
        self.name = name
        self.size = size
        self.data = data
        self.south = math.floor(_lat_of(name))
        self.west = math.floor(_lon_of(name))

    def height(self, lat: float, lon: float) -> float | None:
        """Bilinear between the four cells around the point; None if any of them is a void."""
        last = self.size - 1
        x = (lon - self.west) * last
        y = (self.south + 1 - lat) * last
        x0 = min(max(int(x), 0), last - 1)
        y0 = min(max(int(y), 0), last - 1)
        fx, fy = x - x0, y - y0

        a = self._at(y0, x0)
        b = self._at(y0, x0 + 1)
        c = self._at(y0 + 1, x0)
        d = self._at(y0 + 1, x0 + 1)
        if VOID in (a, b, c, d):
            return None
        return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy

    def _at(self, row: int, column: int) -> int:
        return struct.unpack_from(">h", self.data, (row * self.size + column) * 2)[0]


class Terrain:
    """
    Heights for points, loading and fetching tiles as they are needed.

    One instance for the whole service; safe to use from several requests at once. A tile being
    downloaded holds the lock, so two page views of the same new ride fetch it once.
    """

    def __init__(self, directory: Path, url: str | None = DEFAULT_URL, fetch: Fetch | None = None):
        self.directory = directory
        self.url = url.rstrip("/") if url else None
        self._fetch = fetch or _download
        self._tiles: OrderedDict[str, Tile | None] = OrderedDict()
        self._failed: dict[str, float] = {}
        self._lock = threading.Lock()

    def heights(self, points: Iterable[tuple[float, float]]) -> list[float | None]:
        return [self.height(lat, lon) for lat, lon in points]

    def height(self, lat: float, lon: float) -> float | None:
        tile = self._tile(tile_name(lat, lon))
        return tile.height(lat, lon) if tile is not None else None

    def _tile(self, name: str) -> Tile | None:
        with self._lock:
            if name in self._tiles:
                self._tiles.move_to_end(name)
                return self._tiles[name]

            tile = self._load(name)
            # A tile that is simply not there — open sea — is remembered as such, so it is not
            # looked for on every point. One that failed to download is not: see `_load`.
            if tile is not None or name not in self._failed:
                self._tiles[name] = tile
                while len(self._tiles) > CACHED_TILES:
                    self._tiles.popitem(last=False)
            return tile

    def _load(self, name: str) -> Tile | None:
        path = self.directory / f"{name}.hgt"
        packed = self.directory / f"{name}.hgt.gz"

        if not path.exists() and not packed.exists():
            if not self._download(name, packed):
                return None

        try:
            data = path.read_bytes() if path.exists() else gzip.decompress(packed.read_bytes())
            return Tile(name, data)
        except (OSError, ValueError, EOFError) as error:
            # Remembered as missing: reading it again for every point would not change the answer.
            # Deleting the file and restarting fetches a fresh one.
            log.warning("elevation tile %s is unreadable: %s", name, error)
            return None

    def _download(self, name: str, target: Path) -> bool:
        if self.url is None:
            return False
        failed_at = self._failed.get(name)
        if failed_at is not None and time.monotonic() - failed_at < RETRY_AFTER_S:
            return False

        url = f"{self.url}/{name[:3]}/{name}.hgt.gz"
        try:
            content = self._fetch(url)
        except urllib.error.HTTPError as error:
            if error.code in (403, 404):
                # No tile for this square: the dataset leaves out open water. Not an error, and
                # not worth asking again.
                self._failed.pop(name, None)
                return False
            log.warning("elevation tile %s: %s", name, error)
            self._failed[name] = time.monotonic()
            return False
        except OSError as error:
            log.warning("elevation tile %s could not be fetched: %s", name, error)
            self._failed[name] = time.monotonic()
            return False

        self.directory.mkdir(parents=True, exist_ok=True)
        # Written beside and renamed, so a restart mid-download cannot leave half a tile that
        # then reads as a corrupt one for ever.
        partial = target.with_suffix(".part")
        partial.write_bytes(content)
        partial.replace(target)
        log.info("elevation tile %s fetched (%d kB)", name, len(content) // 1024)
        self._failed.pop(name, None)
        return True


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def _lat_of(name: str) -> int:
    return int(name[1:3]) * (1 if name[0] == "N" else -1)


def _lon_of(name: str) -> int:
    return int(name[4:7]) * (1 if name[3] == "E" else -1)
