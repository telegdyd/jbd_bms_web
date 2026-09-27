from __future__ import annotations

import gzip
import struct
import urllib.error

import pytest

from bmsweb.terrain import VOID, Terrain, Tile, tile_name


def make_tile(size: int = 11, height=lambda row, col: 100 + 10 * col, voids=()) -> bytes:
    """A small square tile. Real ones are 3601 wide; the format does not care."""
    cells = []
    for row in range(size):
        for col in range(size):
            cells.append(VOID if (row, col) in voids else height(row, col))
    return struct.pack(f">{size * size}h", *cells)


class TestTile:
    def test_named_after_its_south_west_corner(self):
        assert tile_name(47.57, 19.0004) == "N47E019"
        assert tile_name(47.57, 18.99) == "N47E018"
        assert tile_name(-33.9, -70.6) == "S34W071"

    def test_heights_are_interpolated_between_cells(self):
        tile = Tile("N47E019", make_tile())
        # Height rises 10 m per column, one column per tenth of a degree east.
        assert tile.height(47.5, 19.0) == pytest.approx(100)
        assert tile.height(47.5, 19.25) == pytest.approx(125)
        assert tile.height(47.5, 19.99) == pytest.approx(199.0)

    def test_north_is_the_first_row(self):
        tile = Tile("N47E019", make_tile(height=lambda row, col: 1000 - 10 * row))
        assert tile.height(47.999, 19.5) == pytest.approx(1000, abs=0.1)
        assert tile.height(47.0, 19.5) == pytest.approx(900)

    def test_a_void_gives_no_height_rather_than_minus_32768(self):
        tile = Tile("N47E019", make_tile(voids={(5, 5)}))
        assert tile.height(47.5, 19.5) is None
        assert tile.height(47.5, 19.05) is not None

    def test_a_file_that_is_not_a_square_grid_is_refused(self):
        with pytest.raises(ValueError):
            Tile("N47E019", b"\x00" * 10)


class TestTerrain:
    def test_a_tile_on_disk_is_used_without_asking_anyone(self, tmp_path):
        (tmp_path / "N47E019.hgt").write_bytes(make_tile())
        terrain = Terrain(tmp_path, url=None)

        assert terrain.height(47.5, 19.25) == pytest.approx(125)

    def test_gzipped_tiles_are_read_too(self, tmp_path):
        (tmp_path / "N47E019.hgt.gz").write_bytes(gzip.compress(make_tile()))
        assert Terrain(tmp_path, url=None).height(47.5, 19.25) == pytest.approx(125)

    def test_a_missing_tile_is_fetched_once_and_kept(self, tmp_path):
        asked = []

        def fetch(url):
            asked.append(url)
            return gzip.compress(make_tile())

        terrain = Terrain(tmp_path, url="https://tiles.example/skadi", fetch=fetch)
        assert terrain.height(47.5, 19.25) == pytest.approx(125)
        assert terrain.height(47.6, 19.3) is not None

        assert asked == ["https://tiles.example/skadi/N47/N47E019.hgt.gz"]
        assert (tmp_path / "N47E019.hgt.gz").exists()
        # A fresh instance — a restarted service — finds it on disk.
        assert Terrain(tmp_path, url=None).height(47.5, 19.25) == pytest.approx(125)

    def test_without_a_url_nothing_is_fetched(self, tmp_path):
        def fetch(url):
            raise AssertionError("should not have been asked")

        assert Terrain(tmp_path, url=None, fetch=fetch).height(47.5, 19.25) is None

    def test_a_failed_download_is_not_retried_on_every_point(self, tmp_path):
        asked = []

        def fetch(url):
            asked.append(url)
            raise OSError("network is down")

        terrain = Terrain(tmp_path, url="https://tiles.example", fetch=fetch)
        assert terrain.height(47.5, 19.25) is None
        assert terrain.height(47.6, 19.3) is None
        assert len(asked) == 1
        assert not list(tmp_path.glob("*.part"))

    def test_a_square_with_no_tile_is_remembered_as_empty(self, tmp_path):
        asked = []

        def fetch(url):
            asked.append(url)
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        terrain = Terrain(tmp_path, url="https://tiles.example", fetch=fetch)
        assert terrain.height(0.5, -30.5) is None
        assert terrain.height(0.6, -30.4) is None
        assert len(asked) == 1
