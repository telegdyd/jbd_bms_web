"""
The rider: the handful of facts about them that the rider-side figures need.

Nothing here is required. Each figure asks for what it uses and says what is missing, so a profile
with only a weight in it already gives the rider's share of the work.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import asdict, dataclass

from . import effort
from .db import transaction

SEXES = ("male", "female")

#: A bike with a conversion kit and its battery. Only a starting point; it is on the form.
DEFAULT_BIKE_KG = 25.0


@dataclass(frozen=True, slots=True)
class Profile:
    weight_kg: float | None = None
    birth_year: int | None = None
    #: "male", "female", or None for either — the calorie equations then take the mean of the two.
    sex: str | None = None
    bike_kg: float | None = None
    tyres: str | None = None
    #: Measured, if the rider knows it. Otherwise estimated; see `max_hr`.
    max_hr: int | None = None

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def total_mass_kg(self) -> float | None:
        if self.weight_kg is None:
            return None
        return self.weight_kg + (self.bike_kg if self.bike_kg is not None else DEFAULT_BIKE_KG)

    def age_in(self, year: int) -> int | None:
        """Age during a given year. Without the birthday itself, off by one for part of it."""
        return None if self.birth_year is None else year - self.birth_year


def load(connection: sqlite3.Connection) -> Profile:
    row = connection.execute("SELECT * FROM profile WHERE id = 1").fetchone()
    if row is None:
        return Profile()
    return Profile(
        weight_kg=row["weight_kg"],
        birth_year=row["birth_year"],
        sex=row["sex"],
        bike_kg=row["bike_kg"],
        tyres=row["tyres"],
        max_hr=row["max_hr"],
    )


def save(connection: sqlite3.Connection, profile: Profile) -> None:
    with transaction(connection):
        connection.execute(
            """
            INSERT INTO profile (id, weight_kg, birth_year, sex, bike_kg, tyres, max_hr, updated_at_ms)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                weight_kg = excluded.weight_kg, birth_year = excluded.birth_year,
                sex = excluded.sex, bike_kg = excluded.bike_kg, tyres = excluded.tyres,
                max_hr = excluded.max_hr, updated_at_ms = excluded.updated_at_ms
            """,
            (
                profile.weight_kg, profile.birth_year, profile.sex, profile.bike_kg,
                profile.tyres, profile.max_hr, int(time.time() * 1000),
            ),
        )


def recorded_max_hr(connection: sqlite3.Connection) -> int | None:
    """The highest heart rate any ride has seen — from the watch or an attached GPX."""
    row = connection.execute(
        """
        SELECT MAX(hr) AS hr FROM (
            SELECT MAX(max_hr_bpm) AS hr FROM sessions
            UNION ALL
            SELECT MAX(hr) FROM companion_samples
        )
        """
    ).fetchone()
    return row["hr"]


def max_hr(
    connection: sqlite3.Connection, profile: Profile, year: int
) -> tuple[float, str] | None:
    """
    The maximum the zones are fractions of, and where it came from.

    Set by hand wins. Otherwise the age estimate — raised to the highest heart rate actually
    recorded, if that is higher, because a heart that has done 175 has a maximum of at least 175,
    whatever a formula says about people of that age in general.
    """
    if profile.max_hr is not None:
        return float(profile.max_hr), "set"

    age = profile.age_in(year)
    if age is None:
        return None
    estimate = effort.estimated_max_hr(age)
    recorded = recorded_max_hr(connection)
    if recorded is not None and recorded > estimate:
        return float(recorded), "recorded"
    return estimate, "age"
