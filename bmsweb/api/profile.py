"""The rider profile: read it, with what the service works out from it, and change it."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import effort, profile
from ..profile import Profile
from . import deps

router = APIRouter()


class ProfileBody(BaseModel):
    """Every field optional: a figure that needs one that is missing says so rather than failing."""

    weight_kg: float | None = Field(default=None, ge=25, le=250)
    birth_year: int | None = Field(default=None, ge=1900, le=date.today().year)
    sex: Literal["male", "female"] | None = None
    bike_kg: float | None = Field(default=None, ge=5, le=100)
    tyres: Literal["road", "mixed", "offroad"] | None = None
    max_hr: int | None = Field(default=None, ge=100, le=230)


@router.get("/profile")
def read(connection: sqlite3.Connection = Depends(deps.connection)) -> dict:
    return _with_derived(connection, profile.load(connection))


@router.put("/profile", dependencies=[Depends(deps.require_token)])
def write(body: ProfileBody, connection: sqlite3.Connection = Depends(deps.connection)) -> dict:
    stored = Profile(**body.model_dump())
    profile.save(connection, stored)
    return _with_derived(connection, stored)


def _with_derived(connection: sqlite3.Connection, stored: Profile) -> dict:
    """What the form shows beside the fields: the maximum heart rate in use, and its zones."""
    year = date.today().year
    age = stored.age_in(year)
    resolved = profile.max_hr(connection, stored, year)
    return {
        **stored.as_dict(),
        "defaults": {"bike_kg": profile.DEFAULT_BIKE_KG, "tyres": effort.DEFAULT_TYRES},
        "age": age,
        "max_hr_estimated": round(effort.estimated_max_hr(age)) if age is not None else None,
        "max_hr_recorded": profile.recorded_max_hr(connection),
        "max_hr_used": round(resolved[0]) if resolved else None,
        "max_hr_source": resolved[1] if resolved else None,
        "zone_bounds": effort.zone_bounds(resolved[0]) if resolved else None,
    }
