"""
The rider's side of a ride: how much of the work was theirs, how hard their heart worked, and
roughly what it cost them.

**The rider's power** is what is left over. Moving a bike takes a known amount of power — rolling
resistance, air, climbing, speeding up — given the mass and a couple of constants for tyres and
posture. The pack says what the motor drew. Whatever the ride needed beyond what the motor put
into the wheel, the rider supplied.

Three things keep that honest, each found by trying the model on real rides:

  * Heights and speeds are smoothed first (±15 s and ±4 s). At one second, GPS jitter turns into
    hundreds of watts of phantom effort once negative values are clipped; past these windows the
    answer stops moving with the window, which is the sign it is measuring the ride and not the
    noise.
  * On a slope steep enough to hold the speed on its own, the rider is coasting and gets nothing.
    Without this, speeding up down a hill — which the map, being smoothed, draws a little too
    shallow — is credited to the rider as hundreds of watts at 50 km/h.
  * The constants are guesses, so the model runs three times, with them set low, middle and high,
    and says so: a figure of 15 Wh reads very differently from "10–21 Wh".

A descent where the rider stands on the pedals holding on through roots is hard work that this
does not see, and cannot: it is not power into the bike. The heart rate sees it, which is why the
calories come from the heart and not from the watts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .altitude import _moving_mean
from .parse import BmsSample

GRAVITY = 9.81
AIR_DENSITY = 1.2

#: Below this the bike is standing, whatever the smoothed speed wobbles to.
MOVING_MS = 0.5

#: Rolling resistance by tyre; the middle of each range.
TYRES = {"road": 0.006, "mixed": 0.010, "offroad": 0.015}
DEFAULT_TYRES = "mixed"

#: Heart rate zones as fractions of the maximum, the common five-zone split. Everything below the
#: first boundary counts as zone 1, so the zones add up to the time the heart rate was known.
ZONE_BOUNDS = (0.6, 0.7, 0.8, 0.9)


@dataclass(frozen=True, slots=True)
class Assumptions:
    crr: float
    #: Drag area, m². An upright rider on a flat-barred bike.
    cda: float
    #: Share of the pack's output that reaches the road: controller, motor and chain losses.
    drivetrain: float
    #: Smoothing, as the reach either side of each sample.
    height_half_ms: int
    speed_half_ms: int


def assumptions(tyres: str | None) -> tuple[Assumptions, Assumptions, Assumptions]:
    """Low, middle and high — for the rider's share, not for any one constant."""
    crr = TYRES.get(tyres or DEFAULT_TYRES, TYRES[DEFAULT_TYRES])
    return (
        Assumptions(crr * 0.8, 0.55, 0.85, 20_000, 5_000),
        Assumptions(crr, 0.60, 0.80, 15_000, 4_000),
        Assumptions(crr * 1.2, 0.65, 0.75, 10_000, 3_000),
    )


@dataclass(frozen=True, slots=True)
class RiderPower:
    #: One per sample: the rider's watts over the interval that starts there. None across a dropout,
    #: where there is no height or speed, and on the last sample.
    watts: list[float | None]
    rider_wh: float
    #: What reached the road from the motor, not what left the pack.
    motor_wh: float
    moving_s: int

    @property
    def share(self) -> float | None:
        total = self.rider_wh + self.motor_wh
        return self.rider_wh / total if total > 0 else None

    @property
    def average_w(self) -> float | None:
        return self.rider_wh * 3600 / self.moving_s if self.moving_s else None


def rider_power(
    samples: Sequence[BmsSample],
    heights: Sequence[float | None],
    mass_kg: float,
    gap_threshold_ms: int,
    a: Assumptions,
) -> RiderPower:
    times = [s.at_ms for s in samples]
    speeds = [None if s.speed_kmh is None else s.speed_kmh / 3.6 for s in samples]

    h = _smoothed(times, heights, a.height_half_ms)
    v = _smoothed(times, speeds, a.speed_half_ms)
    grade = _grades(times, h, v, gap_threshold_ms, a.height_half_ms)

    watts: list[float | None] = [None] * len(samples)
    rider_j = motor_j = 0.0
    moving_ms = 0

    for i in range(len(samples) - 1):
        dt_ms = times[i + 1] - times[i]
        if dt_ms <= 0 or dt_ms > gap_threshold_ms:
            continue
        if None in (h[i], h[i + 1], v[i], v[i + 1]):
            continue
        dt = dt_ms / 1000.0

        motor = max(0.0, -(samples[i].watts + samples[i + 1].watts) / 2.0) * a.drivetrain
        motor_j += motor * dt

        vm = (v[i] + v[i + 1]) / 2.0
        if vm < MOVING_MS:
            watts[i] = 0.0
            continue
        moving_ms += dt_ms

        rolling = mass_kg * GRAVITY * a.crr * vm
        air = 0.5 * AIR_DENSITY * a.cda * vm**3
        # The slope alone holds this speed: whatever the rider's legs are doing, it is not work.
        if mass_kg * GRAVITY * grade[i] * vm + rolling + air <= 0:
            watts[i] = 0.0
            continue

        climbing = mass_kg * GRAVITY * (h[i + 1] - h[i]) / dt
        speeding_up = mass_kg * (v[i + 1] ** 2 - v[i] ** 2) / (2.0 * dt)
        rider = max(0.0, rolling + air + climbing + speeding_up - motor)
        watts[i] = rider
        rider_j += rider * dt

    return RiderPower(watts, rider_j / 3600.0, motor_j / 3600.0, moving_ms // 1000)


def display_watts(samples: Sequence[BmsSample], watts: Sequence[float | None]) -> list[float | None]:
    """For a chart: second-by-second clipped power is a comb, and ±5 s shows the effort instead."""
    times = [s.at_ms for s in samples]
    smoothed = _moving_mean(times, watts, 5_000)
    return [None if w is None else round(s, 1) for w, s in zip(watts, smoothed)]


def zone_seconds(
    samples: Sequence[BmsSample], max_hr: float, gap_threshold_ms: int
) -> list[int]:
    """Time in each of the five zones, from the reading at the start of each interval."""
    bounds = [max_hr * f for f in ZONE_BOUNDS]
    ms = [0] * (len(bounds) + 1)
    for i in range(len(samples) - 1):
        bpm = samples[i].heart_rate_bpm
        dt = samples[i + 1].at_ms - samples[i].at_ms
        if bpm is None or dt <= 0 or dt > gap_threshold_ms:
            continue
        ms[sum(1 for b in bounds if bpm >= b)] += dt
    return [m // 1000 for m in ms]


def zone_bounds(max_hr: float) -> list[int]:
    return [round(max_hr * f) for f in ZONE_BOUNDS]


def estimated_max_hr(age: int) -> float:
    """Tanaka, Monahan & Seals (2001): 208 − 0.7 × age. ±10 bpm for any one person."""
    return 208.0 - 0.7 * age


def calories(
    samples: Sequence[BmsSample],
    weight_kg: float,
    age: int,
    sex: str | None,
    gap_threshold_ms: int,
) -> float | None:
    """
    Energy spent, from heart rate: Keytel et al. (2005), the equations most watches use without a
    measured VO₂max. Good to perhaps ±20–30 % for one person, and it counts effort the power model
    cannot see. Without a sex given, the mean of the two equations.

    Clipped at zero per interval: the equations go negative at resting heart rates, which is the
    formula leaving its range, not the rider generating food.
    """
    total_kj = 0.0
    measured = False
    for i in range(len(samples) - 1):
        bpm = samples[i].heart_rate_bpm
        dt = samples[i + 1].at_ms - samples[i].at_ms
        if bpm is None or dt <= 0 or dt > gap_threshold_ms:
            continue
        measured = True
        male = -55.0969 + 0.6309 * bpm + 0.1988 * weight_kg + 0.2017 * age
        female = -20.4022 + 0.4472 * bpm - 0.1263 * weight_kg + 0.074 * age
        per_minute = {"male": male, "female": female}.get(sex or "", (male + female) / 2.0)
        total_kj += max(per_minute, 0.0) * dt / 60_000.0
    return total_kj / 4.184 if measured else None


def _smoothed(times: Sequence[int], values: Sequence[float | None], half_ms: int) -> list[float | None]:
    """A moving mean that leaves a sample without its own value without one."""
    means = _moving_mean(times, values, half_ms)
    return [None if value is None else mean for value, mean in zip(values, means)]


def _grades(
    times: Sequence[int],
    heights: Sequence[float | None],
    speeds: Sequence[float | None],
    gap_threshold_ms: int,
    half_ms: int,
) -> list[float]:
    """
    Slope over ±`half_ms` of riding: rise over the distance covered, which comes from the speed.
    Zero where too little ground was covered to say — standing still has no slope worth the name.
    """
    n = len(times)
    travelled = [0.0] * n
    for i in range(1, n):
        dt = times[i] - times[i - 1]
        step = 0.0
        if 0 < dt <= gap_threshold_ms and speeds[i] is not None and speeds[i - 1] is not None:
            step = (speeds[i] + speeds[i - 1]) / 2.0 * dt / 1000.0
        travelled[i] = travelled[i - 1] + step

    known = [i for i in range(n) if heights[i] is not None]
    out = [0.0] * n
    lo = hi = 0
    for i in range(n):
        while hi < len(known) - 1 and times[known[hi + 1]] <= times[i] + half_ms:
            hi += 1
        while lo < len(known) - 1 and times[known[lo]] < times[i] - half_ms:
            lo += 1
        if not known:
            break
        a, b = known[lo], known[hi]
        distance = travelled[b] - travelled[a]
        if distance > 20.0:
            out[i] = (heights[b] - heights[a]) / distance
    return out
