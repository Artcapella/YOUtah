from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Coordinates(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class ComfortFeatures(BaseModel):
    """Inputs must represent values available at the requested prediction time."""

    pm25_24hr_avg: float = Field(ge=0, le=1000)
    temp_f: float = Field(ge=-80, le=150)
    humidity: float = Field(ge=0, le=100)
    wind_mph: float = Field(ge=0, le=200)
    elevation_m: float = Field(default=1300, ge=-500, le=9000)
    precip_probability: float = Field(default=0, ge=0, le=100)
    observed_at: datetime


class ComfortPrediction(BaseModel):
    label: Literal["good", "caution", "poor"]
    probabilities: dict[str, float]
    confidence: float = Field(ge=0, le=1)
    model_kind: Literal["trained_nowcast", "conservative_fallback"]
    horizon_hours: int
    reasons: list[str]
    disclaimer: str


class PulseEstimate(BaseModel):
    score: int = Field(ge=0, le=100)
    kind: Literal["explainable_estimate"] = "explainable_estimate"
    nearby_event_count: int = Field(ge=0)
    drivers: list[str]


class EventSignal(BaseModel):
    id: str
    title: str
    starts_at: datetime
    lat: float
    lng: float
    category: str = "community"
    is_free: bool = False


class AreaBrief(BaseModel):
    generated_at: datetime
    coordinates: Coordinates
    outdoor_comfort: ComfortPrediction
    local_pulse: PulseEstimate
    data_freshness: dict[str, int | None]
    source_urls: dict[str, str]


class FeedbackEvent(BaseModel):
    """Privacy-safe event. Do not add user IDs, raw IPs, or movement histories."""

    entity_id: str = Field(min_length=1, max_length=200)
    entity_type: Literal["event", "area", "place"]
    event_type: Literal["detail_opened", "saved", "directions_requested", "dismissed"]
    occurred_at: datetime
    session_hash: str | None = Field(default=None, max_length=128)
