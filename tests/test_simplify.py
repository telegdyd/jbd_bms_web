from __future__ import annotations

from bmsweb.parse import BmsSample
from bmsweb.simplify import located, simplify


def fix(second, lat, lon=19.0, accuracy_m=5.0):
    return BmsSample(
        at_ms=second * 1000,
        volts=48.0,
        amps=0.0,
        watts=0.0,
        soc=90,
        remaining_ah=12.0,
        latitude=lat,
        longitude=lon,
        accuracy_m=accuracy_m,
    )


def no_fix(second):
    return BmsSample(at_ms=second * 1000, volts=48.0, amps=0.0, watts=0.0, soc=90, remaining_ah=12.0)


def test_located_keeps_only_samples_with_coordinates():
    samples = [fix(0, 47.5), no_fix(1), fix(2, 47.5002)]

    assert len(located(samples)) == 2


def test_vague_fixes_are_dropped():
    samples = [fix(i, 47.5 + i * 0.001, accuracy_m=5.0 if i % 2 == 0 else 60.0) for i in range(9)]

    kept = simplify(samples, min_separation_m=0.0, simplify_epsilon_m=0.0)

    assert all(s.accuracy_m == 5.0 for s in kept)
    assert len(kept) == 5


def test_a_stationary_cluster_collapses():
    """
    Ten minutes stood still produces hundreds of fixes wandering inside a few metres. Without this
    pass every one of them is drawn, and the map shows a scribble where the rider stopped.
    """
    cluster = [fix(i, 47.5 + (i % 3) * 0.000005) for i in range(300)]

    kept = simplify(cluster, simplify_epsilon_m=0.0)

    assert len(kept) <= 3


def test_the_final_point_survives_the_separation_pass():
    """It is where the ride finished, however little it moved from the previous kept fix."""
    samples = [fix(0, 47.5), fix(1, 47.5010), fix(2, 47.50100002)]

    kept = simplify(samples, simplify_epsilon_m=0.0)

    assert kept[-1] is samples[-1]


def test_a_straight_line_thins_to_its_endpoints():
    samples = [fix(i, 47.5 + i * 0.0005) for i in range(20)]

    kept = simplify(samples, min_separation_m=0.0)

    assert kept == [samples[0], samples[-1]]


def test_a_real_corner_is_kept():
    """Douglas-Peucker must thin straight stretches without rounding off the turn between them."""
    north = [fix(i, 47.5 + i * 0.0005) for i in range(10)]
    east = [fix(10 + i, 47.5045, 19.0 + i * 0.0005) for i in range(1, 10)]

    kept = simplify(north + east, min_separation_m=0.0)

    assert len(kept) == 3
    assert kept[1] is north[-1]


def test_too_few_points_pass_through_untouched():
    samples = [fix(0, 47.5), fix(1, 47.5001)]

    assert simplify(samples) == samples


def test_samples_without_location_are_ignored():
    assert simplify([no_fix(0), no_fix(1), no_fix(2)]) == []
