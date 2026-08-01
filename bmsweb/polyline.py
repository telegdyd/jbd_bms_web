"""
Google encoded polyline, precision 5.

Used to keep a route thumbnail on the session row without shipping thousands of coordinate pairs:
a simplified ride encodes to a few hundred bytes, which is what makes a list of fifty rides load
as one request. Leaflet draws it after decoding client-side.
"""

from __future__ import annotations

from typing import Iterable, Sequence


def encode(points: Iterable[tuple[float, float]], precision: int = 5) -> str:
    factor = 10**precision
    out: list[str] = []
    previous_lat = 0
    previous_lon = 0

    for lat, lon in points:
        # Rounded to the grid first, then differenced, so rounding error cannot accumulate along
        # the track.
        scaled_lat = round(lat * factor)
        scaled_lon = round(lon * factor)
        out.append(_chunk(scaled_lat - previous_lat))
        out.append(_chunk(scaled_lon - previous_lon))
        previous_lat = scaled_lat
        previous_lon = scaled_lon

    return "".join(out)


def decode(encoded: str, precision: int = 5) -> list[tuple[float, float]]:
    factor = 10**precision
    points: list[tuple[float, float]] = []
    index = 0
    lat = 0
    lon = 0

    while index < len(encoded):
        for axis in range(2):
            shift = 0
            result = 0
            while True:
                if index >= len(encoded):
                    return points
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lon += delta
        points.append((lat / factor, lon / factor))

    return points


def _chunk(value: int) -> str:
    value = ~(value << 1) if value < 0 else value << 1
    out: list[str] = []
    while value >= 0x20:
        out.append(chr((0x20 | (value & 0x1F)) + 63))
        value >>= 5
    out.append(chr(value + 63))
    return "".join(out)


def encode_samples(samples: Sequence, precision: int = 5) -> str:
    """Convenience for the parsed sample type, which carries more than coordinates."""
    return encode(((s.latitude, s.longitude) for s in samples), precision)
