import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TypeVar

from backend import repository
from backend.models import Coordinate, DashboardResponse, InversionRecommendation, InversionResponse, Place, SourceHealth
from backend.providers.events import fetch_events
from backend.providers.nws import fetch_weather
from backend.providers.udot import fetch_advisories
from backend.providers.ugrc import fetch_places
from backend.providers.uta import fetch_transit_alerts
from backend.providers.utah_daq import fetch_air_quality
from backend.geo import haversine_km
from backend.seed_data import demo_advisories, demo_events, demo_places, demo_sources, demo_weather, utcnow

T = TypeVar("T")
logger = logging.getLogger(__name__)

# Ingested places sources without a fixed drawer entry, keyed by `sources.name`
# (supabase/migrations/20261107040000_place_sources.sql). OSM's ODbL requires this attribution.
PLACE_SOURCE_LINKS = {
    "Recreation.gov": ("ridb", "https://www.recreation.gov"),
    "OpenStreetMap contributors": ("osm", "https://www.openstreetmap.org/copyright"),
}


def place_source_health(places: list[Place]) -> list[SourceHealth]:
    """One drawer entry per extra places source actually being served, with its newest row time."""
    latest: dict[str, datetime] = {}
    for place in places:
        if place.source in PLACE_SOURCE_LINKS:
            latest[place.source] = max(place.updated_at, latest.get(place.source, place.updated_at))
    return [
        SourceHealth(id=PLACE_SOURCE_LINKS[name][0], label=name, status="cached", updated_at=updated, href=PLACE_SOURCE_LINKS[name][1])
        for name, updated in latest.items()
    ]


async def _stored_or_provider(
    stored: list[T], provider: Callable[[], Awaitable[list[T]]], fallback: Callable[[], list[T]]
) -> tuple[list[T], bool]:
    """Serve ingested rows first, then a provider, then transparent demo records.

    A dashboard should remain useful during an upstream outage; callers show those
    records as demo rather than pretending they are live.
    """
    if stored:
        return stored, True
    try:
        result = await provider()
        return (result or fallback()), False
    except Exception as error:
        logger.warning("Provider lane unavailable; serving demo fallback: %s", type(error).__name__)
        return fallback(), False


async def build_dashboard(point: Coordinate) -> DashboardResponse:
    weather_state = "live"
    try:
        weather = await fetch_weather(point)
    except Exception:
        weather = demo_weather()
        weather_state = "degraded"

    air, stored_places, stored_events, stored_advisories = await asyncio.gather(
        fetch_air_quality(point),
        repository.load_places(point),
        repository.load_upcoming_events(),
        repository.load_active_advisories(),
    )

    stored_road = [item for item in stored_advisories if item.kind == "road"]
    stored_transit = [item for item in stored_advisories if item.kind == "transit"]
    stored_other = [item for item in stored_advisories if item.kind not in {"road", "transit"}]

    (places, places_stored), (events, _), (road, road_stored), (transit, transit_stored) = await asyncio.gather(
        _stored_or_provider(stored_places, fetch_places, demo_places),
        _stored_or_provider(stored_events, lambda: fetch_events(point), demo_events),
        _stored_or_provider(
            stored_road,
            fetch_advisories,
            lambda: [item for item in demo_advisories() if item.kind == "road"],
        ),
        _stored_or_provider(
            stored_transit,
            fetch_transit_alerts,
            lambda: [item for item in demo_advisories() if item.kind == "transit"],
        ),
    )

    # Lanes served from Supabase are "cached" rather than "demo" in the source drawer.
    stored_lanes = {"ugrc": places_stored, "udot": road_stored, "uta": transit_stored}
    sources = [
        source.model_copy(update={"status": "cached"}) if stored_lanes.get(source.id) else source
        for source in demo_sources(weather_state)
    ]
    sources = [
        source.model_copy(update={"status": air.source, "updated_at": air.updated_at}) if source.id == "daq" else source
        for source in sources
    ]
    if places_stored:
        sources.extend(place_source_health(places))

    return DashboardResponse(
        generated_at=utcnow(),
        weather=weather,
        air=air,
        places=places,
        events=events,
        advisories=[*road, *transit, *stored_other],
        sources=sources,
    )


async def build_inversion_recommendations(point: Coordinate) -> InversionResponse:
    dashboard = await build_dashboard(point)
    cap = dashboard.air.inversion_estimate_m
    candidates = [item for item in dashboard.places if item.category in {"trailhead", "park"}]
    road_alert = any(item.kind == "road" and item.severity == "high" for item in dashboard.advisories)
    recommendations = []
    for place in candidates:
        distance_km = haversine_km(point, place.coordinate)
        elevation_gain = (place.elevation_m or cap) - cap
        above_cap = elevation_gain > 0
        score = 55 + min(35, max(0, elevation_gain) / 20) + max(0, 12 - distance_km / 4)
        if road_alert:
            score -= 12
        access = "check conditions" if road_alert or distance_km > 45 else "good"
        travel = f"{distance_km:.0f} km from your location"
        if road_alert:
            travel += " · active UDOT advisory"
        recommendations.append(InversionRecommendation(
            **place.model_dump(), score=round(max(0, min(100, score))), travel_note=travel,
            air_note=(f"~{elevation_gain:.0f} m above the estimated mixing cap" if above_cap
                      else f"~{abs(elevation_gain):.0f} m below the estimated mixing cap"), access=access,
        ))
    recommendations.sort(key=lambda item: item.score, reverse=True)
    return InversionResponse(
        generated_at=utcnow(),
        inversion_estimate_m=cap,
        recommendations=recommendations,
        disclaimer="Mixing-cap estimate is derived from the nearest Utah DAQ PM2.5 and wind reading, not a ceilometer observation. Check official road, weather, and trail conditions before leaving.",
    )
