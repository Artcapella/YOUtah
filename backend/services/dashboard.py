import asyncio

from backend.models import Coordinate, DashboardResponse, InversionRecommendation, InversionResponse
from backend.providers.events import fetch_events
from backend.providers.nws import fetch_weather
from backend.providers.udot import fetch_advisories
from backend.providers.ugrc import fetch_places
from backend.providers.uta import fetch_transit_alerts
from backend.providers.utah_daq import fetch_air_quality
from backend.seed_data import demo_sources, demo_weather, utcnow


async def build_dashboard(point: Coordinate) -> DashboardResponse:
    weather_state = "live"
    try:
        weather = await fetch_weather(point)
    except Exception:
        weather = demo_weather()
        weather_state = "degraded"

    air, places, events, road_advisories, transit_advisories = await asyncio.gather(
        fetch_air_quality(point),
        fetch_places(),
        fetch_events(),
        fetch_advisories(),
        fetch_transit_alerts(),
    )

    return DashboardResponse(
        generated_at=utcnow(),
        weather=weather,
        air=air,
        places=places,
        events=events,
        advisories=[*road_advisories, *transit_advisories],
        sources=demo_sources(weather_state),
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
