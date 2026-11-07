from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Coordinate(BaseModel):
    lat: float
    lng: float


SourceState = Literal["live", "cached", "demo", "degraded"]


class SourceHealth(BaseModel):
    id: str
    label: str
    status: SourceState
    updated_at: datetime
    href: str | None = None


class Place(BaseModel):
    id: str
    source: str
    source_url: str | None = None
    name: str
    category: Literal["trailhead", "park", "library", "food", "art", "venue", "community"]
    description: str
    coordinate: Coordinate
    elevation_m: float | None = None
    tags: list[str] = Field(default_factory=list)
    updated_at: datetime


class LocalEvent(BaseModel):
    id: str
    source: str
    source_url: str | None = None
    title: str
    venue: str
    category: str
    starts_at: datetime
    price_label: str
    coordinate: Coordinate
    is_free: bool


class WeatherSnapshot(BaseModel):
    temperature_f: int
    short_forecast: str
    wind: str
    source: SourceState
    updated_at: datetime


class AirStation(BaseModel):
    """A Utah DAQ monitoring station. `id` is the air.utah.gov feed id; `aqs_site` the EPA AQS site id."""

    id: str
    name: str
    county: str
    aqs_site: str
    coordinate: Coordinate
    elevation_m: float


class AirStationReading(BaseModel):
    """One hourly station reading. Missing instrument values are None, never a sentinel."""

    station_id: str
    observed_at: datetime
    pm25: float | None = None
    pm25_24hr_avg: float | None = None
    temp_f: float | None = None
    humidity: float | None = None
    wind_mph: float | None = None
    wind_dir_deg: float | None = None


class HourlyConditions(BaseModel):
    """Numeric NWS hourly-forecast values for model features."""

    starts_at: datetime
    temperature_f: float
    relative_humidity: float | None = None
    wind_mph: float | None = None
    precip_probability: float = 0


class AirSnapshot(BaseModel):
    aqi: int
    category: str
    pm25: float
    inversion_estimate_m: int
    source: SourceState
    updated_at: datetime


class Advisory(BaseModel):
    id: str
    kind: Literal["road", "transit", "park", "avalanche"]
    severity: Literal["low", "medium", "high"]
    title: str
    body: str
    source: str
    source_url: str | None = None


class InversionRecommendation(Place):
    score: int
    travel_note: str
    air_note: str
    access: Literal["good", "check conditions"]


class DashboardResponse(BaseModel):
    generated_at: datetime
    weather: WeatherSnapshot
    air: AirSnapshot
    places: list[Place]
    events: list[LocalEvent]
    advisories: list[Advisory]
    sources: list[SourceHealth]


class InversionResponse(BaseModel):
    generated_at: datetime
    inversion_estimate_m: int
    recommendations: list[InversionRecommendation]
    disclaimer: str


class IngestRequest(BaseModel):
    source: Literal["nws", "utah-daq", "udot", "uta", "ugrc", "events"]
