import re
from datetime import datetime, timezone

import httpx

from backend.config import settings
from backend.models import Coordinate, HourlyConditions, WeatherSnapshot


async def _first_hourly_period(point: Coordinate) -> dict:
    """The current NWS hourly-forecast period for a point; raises on upstream failure."""
    headers = {"User-Agent": settings.nws_user_agent, "Accept": "application/geo+json"}
    timeout = httpx.Timeout(8.0)

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        point_response = await client.get(f"https://api.weather.gov/points/{point.lat},{point.lng}")
        point_response.raise_for_status()
        hourly_url = point_response.json()["properties"]["forecastHourly"]

        forecast_response = await client.get(hourly_url)
        forecast_response.raise_for_status()
        return forecast_response.json()["properties"]["periods"][0]


async def fetch_weather(point: Coordinate) -> WeatherSnapshot:
    """Fetch the closest NWS hourly forecast; raise on upstream failure for a safe fallback."""
    period = await _first_hourly_period(point)
    return WeatherSnapshot(
        temperature_f=int(period["temperature"]),
        short_forecast=period["shortForecast"],
        wind=f"{period['windDirection']} {period['windSpeed']}",
        source="live",
        updated_at=datetime.now(timezone.utc),
    )


def _wind_mph(wind_speed: str | None) -> float | None:
    """NWS gives text such as "5 mph" or "5 to 10 mph"; use the upper bound."""
    numbers = re.findall(r"\d+(?:\.\d+)?", wind_speed or "")
    return max(float(number) for number in numbers) if numbers else None


def _quantity(period: dict, key: str) -> float | None:
    value = (period.get(key) or {}).get("value")
    return float(value) if value is not None else None


async def fetch_hourly_conditions(point: Coordinate) -> HourlyConditions:
    """Numeric values from the current NWS hourly period, for model features."""
    period = await _first_hourly_period(point)
    temperature = float(period["temperature"])
    if period.get("temperatureUnit") == "C":
        temperature = temperature * 9 / 5 + 32
    return HourlyConditions(
        starts_at=datetime.fromisoformat(period["startTime"]),
        temperature_f=temperature,
        relative_humidity=_quantity(period, "relativeHumidity"),
        wind_mph=_wind_mph(period.get("windSpeed")),
        precip_probability=_quantity(period, "probabilityOfPrecipitation") or 0,
    )
