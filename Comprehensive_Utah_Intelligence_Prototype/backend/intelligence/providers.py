from __future__ import annotations

import csv
import io
import os
import re
from datetime import datetime, timezone

import httpx

from backend.intelligence.schemas import ComfortFeatures, EventSignal

NWS_USER_AGENT = os.getenv("NWS_USER_AGENT", "ComprehensiveUtah/0.1 contact@example.com")
DAQ_CURRENT_URL = "https://air.utah.gov/csvFeed.php?id=slc"
TICKETMASTER_URL = "https://app.ticketmaster.com/discovery/v2/events.json"


def _number(value: str | int | float | None, default: float = 0) -> float:
    if value is None:
        return default
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else default


async def fetch_comfort_features(lat: float, lng: float) -> tuple[ComfortFeatures, dict[str, str]]:
    """Fetch public DAQ current conditions + numeric NWS hourly weather.

    DAQ current data is not quality-assured. It is appropriate for fresh
    display/nowcasting, while the training script should receive a quality-
    assured historical export.
    """
    headers = {"User-Agent": NWS_USER_AGENT, "Accept": "application/geo+json"}
    timeout = httpx.Timeout(8.0)
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        daq_task = client.get(DAQ_CURRENT_URL)
        point_task = client.get(f"https://api.weather.gov/points/{lat},{lng}")
        daq_response, point_response = await _gather(daq_task, point_task)
        point_response.raise_for_status()
        forecast_url = point_response.json()["properties"]["forecastHourly"]
        forecast_response = await client.get(forecast_url)
        forecast_response.raise_for_status()

    daq = _parse_daq_tsv(daq_response.text)
    period = forecast_response.json()["properties"]["periods"][0]
    observed_at = datetime.now(timezone.utc)
    return (
        ComfortFeatures(
            pm25_24hr_avg=daq["pm25_24hr_avg"],
            temp_f=daq.get("temp_f", float(period.get("temperature", 50))),
            humidity=daq.get("humidity", period.get("relativeHumidity", {}).get("value") or 45),
            wind_mph=_number(period.get("windSpeed"), 5),
            elevation_m=1300,
            precip_probability=(period.get("probabilityOfPrecipitation", {}).get("value") or 0),
            observed_at=observed_at,
        ),
        {
            "air_quality": DAQ_CURRENT_URL,
            "weather": "https://api.weather.gov",
        },
    )


async def _gather(*awaitables):
    """Small local helper avoids an asyncio import at every provider call."""
    import asyncio
    return await asyncio.gather(*awaitables)


def _parse_daq_tsv(body: str) -> dict[str, float]:
    lines = [line for line in body.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError("DAQ returned no tabular measurements")
    reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter="\t")
    rows = list(reader)
    if not rows:
        raise ValueError("DAQ current feed had no records")
    last = rows[-1]
    pm25 = _number(last.get("pm25_24hr_avg"), _number(last.get("pm25")))
    return {
        "pm25_24hr_avg": pm25,
        "temp_f": _number(last.get("temp"), 50),
        "humidity": _number(last.get("rh"), 45),
    }


async def fetch_ticketmaster_events(lat: float, lng: float) -> list[EventSignal]:
    """Optional standalone event adapter. Existing canonical events can replace it."""
    api_key = os.getenv("TICKETMASTER_API_KEY", "")
    if not api_key:
        return []
    params = {
        "apikey": api_key,
        "latlong": f"{lat},{lng}",
        "radius": 25,
        "unit": "miles",
        "size": 50,
        "sort": "date,asc",
    }
    async with httpx.AsyncClient(timeout=8.0) as client:
        response = await client.get(TICKETMASTER_URL, params=params)
        response.raise_for_status()
    raw_events = response.json().get("_embedded", {}).get("events", [])
    parsed: list[EventSignal] = []
    for event in raw_events:
        venue = event.get("_embedded", {}).get("venues", [{}])[0]
        location = venue.get("location", {})
        date_time = event.get("dates", {}).get("start", {}).get("dateTime")
        if not date_time or "latitude" not in location or "longitude" not in location:
            continue
        classifications = event.get("classifications", [{}])
        category = classifications[0].get("segment", {}).get("name", "community")
        parsed.append(EventSignal(
            id=event["id"],
            title=event.get("name", "Untitled event"),
            starts_at=datetime.fromisoformat(date_time.replace("Z", "+00:00")),
            lat=float(location["latitude"]),
            lng=float(location["longitude"]),
            category=category,
            is_free=event.get("priceRanges") is None,
        ))
    return parsed
