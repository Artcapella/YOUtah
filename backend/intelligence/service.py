"""Assemble intelligence responses from the project's provider adapters and Supabase."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from backend import repository
from backend.config import settings
from backend.intelligence.model import predict_comfort
from backend.intelligence.schemas import AreaBrief, ComfortFeatures, EventSignal, FeedbackEvent, StationRef
from backend.intelligence.scoring import local_pulse
from backend.models import AirStation, AirStationReading, Coordinate, HourlyConditions, LocalEvent
from backend.providers.events import TICKETMASTER_URL, fetch_ticketmaster_events
from backend.providers.nws import fetch_hourly_conditions
from backend.providers.utah_daq import FEED_URL, fetch_nearest_readings, latest_with_pm25
from backend.supabase_client import get_service_client

logger = logging.getLogger(__name__)

# Beyond this distance the nearest monitor is a weak proxy, and the response says so.
FAR_STATION_KM = 40


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _first(*values: float | None) -> float | None:
    return next((value for value in values if value is not None), None)


def comfort_features_from_readings(
    station: AirStation, readings: list[AirStationReading], weather: HourlyConditions | None = None
) -> ComfortFeatures | None:
    """Model features from a station's newest PM2.5 reading. Missing station met values fall
    back to the NWS hourly forecast; returns None when a required input is unavailable."""
    latest = latest_with_pm25(readings)
    if latest is None or latest.pm25_24hr_avg is None:
        return None
    three_hours_earlier = latest.observed_at - timedelta(hours=3)
    earlier = next(
        (r for r in readings if r.observed_at == three_hours_earlier and r.pm25_24hr_avg is not None), None
    )
    temp_f = _first(latest.temp_f, weather.temperature_f if weather else None)
    humidity = _first(latest.humidity, weather.relative_humidity if weather else None)
    wind_mph = _first(latest.wind_mph, weather.wind_mph if weather else None)
    if temp_f is None or humidity is None or wind_mph is None:
        return None
    return ComfortFeatures(
        pm25_24hr_avg=max(0.0, latest.pm25_24hr_avg),
        temp_f=temp_f,
        humidity=_clamp(humidity, 0, 100),
        wind_mph=max(0.0, wind_mph),
        elevation_m=station.elevation_m,
        precip_probability=weather.precip_probability if weather else 0,
        pm25_hourly=max(0.0, latest.pm25) if latest.pm25 is not None else None,
        pm25_trend_3h=latest.pm25_24hr_avg - earlier.pm25_24hr_avg if earlier else None,
        observed_at=latest.observed_at,
    )


def _signal(event: LocalEvent) -> EventSignal:
    return EventSignal(
        id=event.id,
        title=event.title,
        starts_at=event.starts_at,
        lat=event.coordinate.lat,
        lng=event.coordinate.lng,
        category=event.category,
        is_free=event.is_free,
    )


async def load_event_signals(point: Coordinate) -> tuple[list[EventSignal], str]:
    """The canonical events lane (Supabase, then live Ticketmaster). Demo events are never scored."""
    stored = await repository.load_upcoming_events(limit=100)
    if stored:
        return [_signal(event) for event in stored], "cached"
    if not settings.ticketmaster_api_key:
        return [], "not_configured"
    try:
        live = await fetch_ticketmaster_events(point)
    except Exception as error:
        logger.warning("Ticketmaster unavailable for Local Pulse: %s", error)
        return [], "degraded"
    return [_signal(event) for event in live], "live"


async def _optional_weather(point: Coordinate) -> HourlyConditions | None:
    try:
        return await fetch_hourly_conditions(point)
    except Exception as error:
        logger.warning("NWS hourly conditions unavailable: %s", error)
        return None


async def build_area_brief(lat: float, lng: float) -> AreaBrief:
    """Raises LookupError when no nearby air station has usable data."""
    point = Coordinate(lat=lat, lng=lng)
    (station, distance_km, readings), weather, (events, events_source) = await asyncio.gather(
        fetch_nearest_readings(point), _optional_weather(point), load_event_signals(point)
    )
    features = comfort_features_from_readings(station, readings, weather)
    if features is None:
        raise LookupError(f"Station {station.id} is missing required air or weather inputs")

    comfort = predict_comfort(features)
    if distance_km > FAR_STATION_KM:
        note = f"nearest air monitor ({station.name}) is {distance_km:.0f} km away, so treat this as regional"
        comfort = comfort.model_copy(update={"reasons": [note, *comfort.reasons][:3]})

    now = datetime.now(timezone.utc)
    sources = {"air_quality": FEED_URL.format(station_id=station.id), "weather": "https://api.weather.gov"}
    if events_source == "live":
        sources["events"] = TICKETMASTER_URL
    return AreaBrief(
        generated_at=now,
        coordinates={"lat": lat, "lng": lng},
        outdoor_comfort=comfort,
        local_pulse=local_pulse(lat, lng, now, events, comfort, events_available=events_source in {"cached", "live"}),
        air_station=StationRef(
            id=station.id,
            name=station.name,
            distance_km=round(distance_km, 1),
            elevation_m=station.elevation_m,
            observed_at=features.observed_at,
        ),
        events_source=events_source,
        data_freshness={
            "air_quality_minutes": int((now - features.observed_at).total_seconds() // 60),
            "weather_minutes": 0 if weather else None,
        },
        source_urls=sources,
    )


async def store_feedback(event: FeedbackEvent) -> bool:
    """Server-side, service-role insert into analytics_events. The browser never writes the table."""
    client = get_service_client()
    if client is None:
        return False
    row = event.model_dump(mode="json")
    try:
        await asyncio.to_thread(lambda: client.table("analytics_events").insert(row, returning="minimal").execute())
    except Exception as error:
        logger.warning("Could not store feedback event: %s", error)
        return False
    return True
