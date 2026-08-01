from __future__ import annotations

import pytest

from bmsweb.gpx import GpxError, parse_gpx


def test_a_strava_export_parses(fixture_bytes):
    track = parse_gpx(fixture_bytes("strava_hr.gpx"))

    assert track.creator == "StravaGPX"
    assert track.name == "Morning Ride"
    assert len(track.points) == 6
    assert track.heart_rate_count == 6


def test_points_carry_heart_rate_cadence_and_position(fixture_bytes):
    first = parse_gpx(fixture_bytes("strava_hr.gpx")).points[0]

    # 2026-08-01T08:00:00Z, the same instant as the CSV fixture's 10:00:00+02:00.
    assert first.at_ms == 1785571200000
    assert first.heart_rate == 102
    assert first.cadence == 64
    assert (first.latitude, first.longitude) == (47.5, 19.0)
    assert first.altitude_m == 120.0


def test_a_missing_cadence_is_absent_rather_than_zero(fixture_bytes):
    assert parse_gpx(fixture_bytes("strava_hr.gpx")).points[-1].cadence is None


def test_any_namespace_prefix_is_accepted():
    """
    Producers differ on prefixes and on which version of the extension schema they claim. Matching
    local names means a file from Garmin Connect or Wahoo reads the same as Strava's.
    """
    content = b"""<?xml version="1.0"?>
    <gpx xmlns="http://www.topografix.com/GPX/1/1"
         xmlns:x="http://www.garmin.com/xmlschemas/TrackPointExtension/v2">
     <trk><trkseg>
      <trkpt lat="47.5" lon="19.0"><time>2026-08-01T08:00:00Z</time>
       <extensions><x:TrackPointExtension><x:hr>133</x:hr></x:TrackPointExtension></extensions>
      </trkpt>
     </trkseg></trk>
    </gpx>"""

    assert parse_gpx(content).points[0].heart_rate == 133


def test_points_arrive_in_time_order():
    content = b"""<?xml version="1.0"?>
    <gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
     <trkpt lat="47.5" lon="19.0"><time>2026-08-01T08:00:09Z</time></trkpt>
     <trkpt lat="47.5" lon="19.0"><time>2026-08-01T08:00:04Z</time></trkpt>
    </trkseg></trk></gpx>"""

    stamps = [p.at_ms for p in parse_gpx(content).points]

    assert stamps == sorted(stamps)


def test_a_zero_heart_rate_is_not_a_reading():
    """A strap that has not picked up yet reports 0, and charting it draws a cliff to the floor."""
    content = b"""<?xml version="1.0"?>
    <gpx xmlns="http://www.topografix.com/GPX/1/1"
         xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"><trk><trkseg>
     <trkpt lat="47.5" lon="19.0"><time>2026-08-01T08:00:00Z</time>
      <extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>0</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions>
     </trkpt>
    </trkseg></trk></gpx>"""

    assert parse_gpx(content).points[0].heart_rate is None


def test_a_point_without_a_time_is_dropped():
    """A planned route has coordinates and no times, and cannot be placed against a recording."""
    content = b"""<?xml version="1.0"?>
    <gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
     <trkpt lat="47.5" lon="19.0"></trkpt>
     <trkpt lat="47.6" lon="19.0"><time>2026-08-01T08:00:04Z</time></trkpt>
    </trkseg></trk></gpx>"""

    assert len(parse_gpx(content).points) == 1


def test_a_route_with_no_times_at_all_is_refused():
    content = b"""<?xml version="1.0"?>
    <gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
     <trkpt lat="47.5" lon="19.0"></trkpt>
    </trkseg></trk></gpx>"""

    with pytest.raises(GpxError, match="timestamped"):
        parse_gpx(content)


def test_a_time_without_an_offset_is_refused_rather_than_guessed_at():
    content = b"""<?xml version="1.0"?>
    <gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
     <trkpt lat="47.5" lon="19.0"><time>2026-08-01T08:00:00</time></trkpt>
    </trkseg></trk></gpx>"""

    with pytest.raises(GpxError):
        parse_gpx(content)


def test_something_that_is_not_xml():
    with pytest.raises(GpxError, match="XML"):
        parse_gpx(b"this is a CSV, actually")


def test_xml_that_is_not_a_gpx():
    with pytest.raises(GpxError, match="GPX"):
        parse_gpx(b"<?xml version='1.0'?><kml><Placemark/></kml>")


def test_an_entity_bomb_is_refused_before_it_is_expanded():
    """
    ElementTree expands internal entities, so a few recursive definitions cost gigabytes before any
    of our code runs. No exporter emits a DOCTYPE, so the whole class is refused for free.
    """
    content = (
        b"<?xml version='1.0'?><!DOCTYPE gpx ["
        b"<!ENTITY a 'aaaaaaaaaa'><!ENTITY b '&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;'>"
        b"]><gpx><trk><trkseg><trkpt><name>&b;</name></trkpt></trkseg></trk></gpx>"
    )

    with pytest.raises(GpxError, match="DOCTYPE"):
        parse_gpx(content)
