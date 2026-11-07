from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from backend.models import Coordinate


class SkiSource(BaseModel):
    name: str
    url: str
    status: Literal["live", "cached", "stale", "unavailable"] = "unavailable"
    checked_at: datetime | None = None
    reported_at: datetime | None = None


class SkiLift(BaseModel):
    name: str
    status: Literal["open", "closed", "hold", "scheduled", "unknown"] = "unknown"
    wait_minutes: float | None = None
    wait_updated_at: datetime | None = None


class SkiRun(BaseModel):
    name: str
    status: Literal["open", "closed", "hold", "scheduled", "unknown"] = "unknown"
    difficulty: str | None = None
    groomed: bool | None = None


class SkiOperations(BaseModel):
    source: SkiSource
    lifts_open: int | None = None
    lifts_total: int | None = None
    trails_open: int | None = None
    trails_total: int | None = None
    lifts: list[SkiLift] = Field(default_factory=list)
    runs: list[SkiRun] = Field(default_factory=list)
    wait_minutes: float | None = None
    max_wait_minutes: float | None = None
    reporting_lifts: int = 0
    wait_status: Literal["reported", "not_reported", "no_open_lifts", "stale"] = "not_reported"


class SkiSnowReport(BaseModel):
    source: SkiSource
    snowfall_24h_in: float | None = None
    base_depth_in: float | None = None


class SkiForecast(BaseModel):
    source: SkiSource
    # Every value here is weather-model output, including recent snowfall and cover.
    modeled: bool = True
    snowfall_24h_in: float | None = None
    snowfall_next_24h_in: float | None = None
    snowfall_next_48h_in: float | None = None
    snow_depth_in: float | None = None
    temperature_f: float | None = None
    wind_mph: float | None = None
    valid_at: datetime | None = None


class SkiBusyness(BaseModel):
    level: Literal["quiet", "moderate", "busy", "no_open_lifts", "unknown"] = "unknown"
    basis: Literal["reported_lift_waits", "lift_status", "unavailable"] = "unavailable"
    description: str = "The resort has not published fresh lift-line waits."


class SkiResort(BaseModel):
    id: str
    name: str
    coordinate: Coordinate
    elevation_m: int
    region: str
    website: str
    webcams_url: str
    description: str = ""
    operations: SkiOperations
    snow_report: SkiSnowReport
    forecast: SkiForecast
    busyness: SkiBusyness


class SkiResponse(BaseModel):
    generated_at: datetime
    refresh_after_seconds: int = 300
    resorts: list[SkiResort]
    note: str = (
        "Snow forecasts and modeled snow cover are Open-Meteo estimates. "
        "Lift waits are published resort reports where available. Busyness describes "
        "the reporting lift lines, not resort attendance. Check the resort for current operations."
    )
