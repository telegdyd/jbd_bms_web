"""The rider-side figures, against rides whose answers can be worked out by hand."""

from __future__ import annotations

import pytest

from bmsweb import altitude, effort
from bmsweb.parse import BmsSample

MASS = 100.0
MID = effort.assumptions("mixed")[1]
GAP = 5_000


def ride(seconds=300, speed_ms=5.0, grade=0.0, pack_w=0.0, hr=None, pressure=None, start_height=200.0):
    """
    A steady ride due north, one sample a second. Heights are handed over separately, as the
    altitude module would; `pressure(i)` fills the barometer column when given.
    """
    samples, heights = [], []
    for i in range(seconds):
        samples.append(
            BmsSample(
                at_ms=i * 1000, volts=50.0, amps=0.0, watts=pack_w, soc=80, remaining_ah=10.0,
                latitude=47.5 + i * speed_ms / 111_000, longitude=19.0, altitude_m=start_height + 40,
                speed_kmh=speed_ms * 3.6, accuracy_m=4.0,
                heart_rate_bpm=hr(i) if hr else None,
                pressure_hpa=pressure(i) if pressure else None,
            )
        )
        heights.append(start_height + grade * speed_ms * i)
    return samples, heights


def middle(values, margin=40):
    """Away from the ends, where the smoothing windows run out of ride."""
    return values[margin:-margin]


class TestRiderPower:
    def test_on_the_flat_the_rider_supplies_rolling_and_air(self):
        samples, heights = ride(speed_ms=5.0)
        result = effort.rider_power(samples, heights, MASS, GAP, MID)

        expected = MASS * 9.81 * MID.crr * 5.0 + 0.5 * 1.2 * MID.cda * 5.0**3
        assert middle(result.watts) == pytest.approx([expected] * len(middle(result.watts)), rel=1e-6)

    def test_climbing_adds_the_weight_lifted(self):
        samples, heights = ride(speed_ms=4.0, grade=0.05)
        flat = effort.rider_power(*ride(speed_ms=4.0), MASS, GAP, MID)
        climb = effort.rider_power(samples, heights, MASS, GAP, MID)

        lifted = MASS * 9.81 * 0.05 * 4.0
        assert middle(climb.watts)[0] - middle(flat.watts)[0] == pytest.approx(lifted, rel=1e-6)

    def test_the_motor_takes_its_share_through_the_drivetrain(self):
        alone = effort.rider_power(*ride(grade=0.04), MASS, GAP, MID)
        helped = effort.rider_power(*ride(grade=0.04, pack_w=-200.0), MASS, GAP, MID)

        assert middle(alone.watts)[0] - middle(helped.watts)[0] == pytest.approx(200 * MID.drivetrain)
        assert helped.motor_wh == pytest.approx(200 * MID.drivetrain * 299 / 3600)

    def test_a_motor_doing_more_than_the_ride_needs_leaves_the_rider_at_zero_not_below(self):
        result = effort.rider_power(*ride(pack_w=-2000.0), MASS, GAP, MID)
        assert min(w for w in result.watts if w is not None) == 0.0

    def test_down_a_slope_that_holds_the_speed_the_rider_is_coasting(self):
        # 8 % down at 8 m/s: gravity gives ~630 W, rolling and air take ~230 W.
        samples, heights = ride(speed_ms=8.0, grade=-0.08)
        result = effort.rider_power(samples, heights, MASS, GAP, MID)

        assert result.rider_wh == 0.0
        # Nobody did any work at all, so there is no share to speak of.
        assert result.share is None

    def test_a_gentle_descent_at_speed_still_needs_pedalling(self):
        # 1 % down at 9 m/s: gravity gives ~88 W, air alone wants ~260 W.
        samples, heights = ride(speed_ms=9.0, grade=-0.01)
        result = effort.rider_power(samples, heights, MASS, GAP, MID)

        assert all(w > 100 for w in middle(result.watts))

    def test_standing_still_is_no_work_and_no_moving_time(self):
        samples, heights = ride(speed_ms=0.0)
        result = effort.rider_power(samples, heights, MASS, GAP, MID)

        assert result.rider_wh == 0.0
        assert result.moving_s == 0
        assert result.average_w is None

    def test_the_range_brackets_the_middle(self):
        samples, heights = ride(grade=0.03, pack_w=-150.0)
        low, mid, high = (
            effort.rider_power(samples, heights, MASS, GAP, a) for a in effort.assumptions("mixed")
        )
        assert low.rider_wh < mid.rider_wh < high.rider_wh

    def test_nothing_is_credited_across_a_dropout(self):
        samples, heights = ride()
        gapped = samples[:100] + [
            BmsSample(**{**_fields(s), "at_ms": s.at_ms + 60_000}) for s in samples[100:]
        ]
        result = effort.rider_power(gapped, heights, MASS, GAP, MID)
        assert result.watts[99] is None


def _fields(sample: BmsSample) -> dict:
    return {name: getattr(sample, name) for name in sample.__slots__}


class TestAltitudeSource:
    class FlatGround:
        def height(self, lat, lon):
            return 150.0 + (lat - 47.5) * 111_000 * 0.02  # a 2 % slope north

    class NoMap:
        def height(self, lat, lon):
            return None

    def test_the_map_is_preferred_to_gps(self):
        samples, _ = ride()
        heights, source = altitude.heights(samples, self.FlatGround())

        assert source == "terrain"
        assert heights[0] == pytest.approx(150.0)
        assert heights[100] == pytest.approx(150.0 + 100 * 5.0 * 0.02, rel=1e-3)

    def test_without_the_map_gps_altitude_stands_in(self):
        samples, _ = ride()
        heights, source = altitude.heights(samples, self.NoMap())
        assert source == "gps"
        assert heights[0] == 240.0

    def test_the_barometer_gives_the_shape_and_the_map_the_level(self):
        # Climbing 2 % by the barometer, whose weather has drifted it 30 m high — a pressure
        # 3.6 hPa low. The map says the same slope, at the right level.
        def pressure(i):
            height = 150.0 + 30.0 + i * 5.0 * 0.02
            return 1013.25 * (1 - height / 44_330.0) ** (1 / 0.190263)

        samples, _ = ride(pressure=pressure)
        heights, source = altitude.heights(samples, self.FlatGround())

        assert source == "barometer"
        assert heights[150] == pytest.approx(150.0 + 150 * 5.0 * 0.02, abs=0.5)

    def test_a_barometer_is_used_even_without_a_map(self):
        samples, _ = ride(pressure=lambda i: 1000.0 - i * 0.01)
        heights, source = altitude.heights(samples, None)

        assert source == "barometer"
        # Levelled against GPS instead, which says 240 m throughout.
        assert sum(heights) / len(heights) == pytest.approx(240.0, abs=1.0)
        assert heights[-1] > heights[0]

    def test_a_barometer_that_only_covers_a_little_of_the_ride_is_ignored(self):
        samples, _ = ride(pressure=lambda i: 1000.0 if i < 30 else None)
        _, source = altitude.heights(samples, self.FlatGround())
        assert source == "terrain"

    def test_a_recording_without_a_route_has_no_heights(self):
        samples = [
            BmsSample(at_ms=i * 1000, volts=50, amps=0, watts=0, soc=80, remaining_ah=10)
            for i in range(10)
        ]
        assert altitude.heights(samples, self.FlatGround()) == ([None] * 10, None)


class TestHeart:
    def test_time_is_counted_into_zones(self):
        # 100 s at 100 bpm, 100 s at 150, 100 s at 185 against a maximum of 190.
        samples, _ = ride(hr=lambda i: 100 if i < 100 else 150 if i < 200 else 185)
        seconds = effort.zone_seconds(samples, 190, GAP)

        assert seconds == [100, 0, 100, 0, 99]

    def test_the_zone_boundaries(self):
        assert effort.zone_bounds(200) == [120, 140, 160, 180]

    def test_the_age_estimate(self):
        assert effort.estimated_max_hr(30) == pytest.approx(187)

    def test_calories_follow_keytel(self):
        samples, _ = ride(seconds=61, hr=lambda i: 140)
        kcal = effort.calories(samples, weight_kg=80, age=35, sex="male", gap_threshold_ms=GAP)

        per_minute = (-55.0969 + 0.6309 * 140 + 0.1988 * 80 + 0.2017 * 35) / 4.184
        assert kcal == pytest.approx(per_minute)

    def test_without_a_sex_both_equations_are_averaged(self):
        samples, _ = ride(seconds=61, hr=lambda i: 140)
        male = effort.calories(samples, 80, 35, "male", GAP)
        female = effort.calories(samples, 80, 35, "female", GAP)
        either = effort.calories(samples, 80, 35, None, GAP)
        assert either == pytest.approx((male + female) / 2)

    def test_a_resting_heart_rate_costs_nothing_rather_than_something_negative(self):
        samples, _ = ride(seconds=61, hr=lambda i: 50)
        assert effort.calories(samples, 60, 25, "female", GAP) == 0.0

    def test_no_heart_rate_is_no_answer(self):
        samples, _ = ride(seconds=61)
        assert effort.calories(samples, 80, 35, "male", GAP) is None
