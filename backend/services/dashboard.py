import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from backend import repository
from backend.models import Coordinate, DashboardResponse, InversionRecommendation, InversionResponse
from backend.providers.events import fetch_events
from backend.providers.nws import fetch_weather
from backend.providers.udot import fetch_advisories
from backend.providers.ugrc import fetch_places
from backend.providers.uta import fetch_transit_alerts
from backend.providers.utah_daq import fetch_air_quality
from backend.seed_data import demo_sources, demo_weather, utcnow

T = TypeVar("T")


async def _stored_or_provider(stored: list[T], provider: Callable[[], Awaitable[list[T]]]) -> tuple[list[T], bool]:
    """Serve ingested Supabase rows when a lane has any; otherwise ask the provider adapter."""
    if stored:
        return stored, True
    return await provider(), False


async def build_dashboard(point: Coordinate) -> DashboardResponse:
    weather_state = "live"
    try:
        weather = await fetch_weather(point)
    except Exception:
        weather = demo_weather()
        weather_state = "degraded"

    air, stored_places, stored_events, stored_advisories = await asyncio.gather(
        fetch_air_quality(point),
        repository.load_places(),
        repository.load_upcoming_events(),
        repository.load_active_advisories(),
    )

    stored_road = [item for item in stored_advisories if item.kind == "road"]
    stored_transit = [item for item in stored_advisories if item.kind == "transit"]
    stored_other = [item for item in stored_advisories if item.kind not in {"road", "transit"}]

    (places, places_stored), (events, _), (road, road_stored), (transit, transit_stored) = await asyncio.gather(
        _stored_or_provider(stored_places, fetch_places),
        _stored_or_provider(stored_events, lambda: fetch_events(point)),
        _stored_or_provider(stored_road, fetch_advisories),
        _stored_or_provider(stored_transit, fetch_transit_alerts),
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
    recommendations = [
        InversionRecommendation(
            **place.model_dump(),
            score=max(68, 94 - index * 13),
            travel_note="15 min from downtown · car or rideshare" if index == 0 else "UTA connection nearby · check the final approach",
            air_note="Above the estimated inversion cap" if place.elevation_m and place.elevation_m > cap else "Near the estimated inversion cap",
            access="good" if index == 0 else "check conditions",
        )
        for index, place in enumerate(candidates)
    ]
    recommendations.sort(key=lambda item: item.score, reverse=True)
    return InversionResponse(
        generated_at=utcnow(),
        inversion_estimate_m=cap,
        recommendations=recommendations,
        disclaimer="Inversion height is an estimate. Check official road, weather, and trail conditions before leaving.",
    )
