from datetime import datetime, timezone

import httpx

from backend.config import settings
from backend.models import Coordinate, WeatherSnapshot


async def fetch_weather(point: Coordinate) -> WeatherSnapshot:
    """Fetch the closest NWS hourly forecast; raise on upstream failure for a safe fallback."""
    headers = {"User-Agent": settings.nws_user_agent, "Accept": "application/geo+json"}
    timeout = httpx.Timeout(8.0)

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        point_response = await client.get(f"https://api.weather.gov/points/{point.lat},{point.lng}")
        point_response.raise_for_status()
        hourly_url = point_response.json()["properties"]["forecastHourly"]

        forecast_response = await client.get(hourly_url)
        forecast_response.raise_for_status()
        period = forecast_response.json()["properties"]["periods"][0]

    return WeatherSnapshot(
        temperature_f=int(period["temperature"]),
        short_forecast=period["shortForecast"],
        wind=f"{period['windDirection']} {period['windSpeed']}",
        source="live",
        updated_at=datetime.now(timezone.utc),
    )
