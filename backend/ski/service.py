"""Bounded, cached public feeds. Missing telemetry stays missing, including zero waits.

Each upstream fails independently. Raw responses are kept in memory for five minutes;
on an outage the last response may be shown for up to an hour, explicitly marked stale.
Stale or closed lift reports never supply wait times or a busyness estimate.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import re
from statistics import median
import time
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from backend.config import settings
from backend.models import Coordinate
from backend.ski.catalog import LIFT_FEEDS, RESORTS
from backend.ski.schemas import (
    SkiBusyness, SkiForecast, SkiLift, SkiOperations, SkiResort,
    SkiResponse, SkiRun, SkiSnowReport, SkiSource,
)

UTC = timezone.utc
UTAH = ZoneInfo("America/Denver")
WAIT_MAX_AGE = timedelta(minutes=15)
CACHE_SECONDS = 300
STALE_SECONDS = 3600
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
_cache: dict[str, "Feed"] = {}
_retry_after: dict[str, float] = {}
_pending: dict[str, asyncio.Task] = {}


@dataclass
class Feed:
    data: Any = None
    checked_at: datetime | None = None
    cached: bool = False
    stale: bool = False


def _now() -> datetime:
    return datetime.now(UTC)


def number(value: Any) -> float | None:
    """Do not turn null, placeholders, ranges, booleans or NaN into a measurement."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def integer(value: Any) -> int | None:
    result = number(value)
    return int(result) if result is not None and result.is_integer() else None


def timestamp(value: Any, *, local: bool = False) -> datetime | None:
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, UTC)
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=UTAH if local else UTC)
        return result.astimezone(UTC)
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def _fresh(value: datetime | None, now: datetime, age: timedelta) -> bool:
    return value is not None and -timedelta(minutes=5) <= now - value <= age


def _embedded_json(html: str, assignment: str) -> dict:
    """Decode a JSON assignment without evaluating JavaScript from another site."""
    for match in re.finditer(re.escape(assignment) + r"\s*=\s*", html):
        try:
            data, _ = json.JSONDecoder().raw_decode(html[match.end():])
            if isinstance(data, dict) and "Date(" not in str(data.get("Date", "")):
                return data
        except ValueError:
            continue
    raise ValueError("Resort report JSON is missing")


async def _request_feed(key: str, url: str, params: dict | None) -> Feed:
    try:
        # No retries on the request path, and no provider or secret-bearing errors in responses.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(7.0, connect=3.0), follow_redirects=True,
            headers={"User-Agent": settings.nws_user_agent, "Accept": "application/json,text/html"},
        ) as client:
            response = await asyncio.wait_for(client.get(url, params=params), timeout=8.0)
            response.raise_for_status()
            if key == "alta":
                data = _embedded_json(response.text, "window.Alta")
                if not isinstance(data.get("liftStatus", {}).get("lifts"), list):
                    raise ValueError("Missing Alta lifts")
            elif key == "pcm":
                data = _embedded_json(response.text, "FR.TerrainStatusFeed")
                if data.get("IsSuccessful") is False or not isinstance(data.get("Lifts"), list):
                    raise ValueError("Missing Park City lifts")
            else:
                data = response.json()
                if key == "weather":
                    if not isinstance(data, list) or len(data) != len(RESORTS) or not all(isinstance(x.get("hourly"), dict) for x in data):
                        raise ValueError("Missing snow forecast locations")
                elif key in ("solitude", "deervalley"):
                    if not isinstance(data.get("MountainAreas"), list) or not isinstance(data.get("SnowReport"), dict):
                        raise ValueError("Missing mountain report")
                elif not isinstance(data, list) or not all(isinstance(x, dict) and (x.get("name") or x.get("Name")) for x in data):
                    raise ValueError("Missing lift report")
        result = Feed(data=data, checked_at=_now())
        _cache[key] = result
        _retry_after.pop(key, None)
        return result
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError, TimeoutError):
        _retry_after[key] = time.monotonic() + 60
        return _fallback(key)


def _fallback(key: str) -> Feed:
    cached = _cache.get(key)
    if cached and cached.checked_at and (_now() - cached.checked_at).total_seconds() <= STALE_SECONDS:
        return Feed(data=cached.data, checked_at=cached.checked_at, cached=True, stale=True)
    return Feed()


async def fetch_feed(key: str, url: str, params: dict | None = None) -> Feed:
    cached = _cache.get(key)
    if cached and cached.checked_at and (_now() - cached.checked_at).total_seconds() < CACHE_SECONDS:
        return Feed(data=cached.data, checked_at=cached.checked_at, cached=True)
    if time.monotonic() < _retry_after.get(key, 0):
        return _fallback(key)
    # Coalesce overlapping map requests so one browser refresh cannot multiply provider calls.
    task = _pending.get(key)
    if task is None or task.get_loop() is not asyncio.get_running_loop():
        task = asyncio.create_task(_request_feed(key, url, params))
        _pending[key] = task
    try:
        return await asyncio.shield(task)
    finally:
        if task.done() and _pending.get(key) is task:
            _pending.pop(key, None)


def _source(name: str, url: str, feed: Feed, reported_at: datetime | None = None) -> SkiSource:
    return SkiSource(
        name=name, url=url, checked_at=feed.checked_at, reported_at=reported_at,
        status="unavailable" if feed.data is None else "stale" if feed.stale else "cached" if feed.cached else "live",
    )


def lift_status(value: Any) -> str:
    value = re.sub(r"[\s_-]", "", str(value).lower())
    if value in ("open", "operating", "running"):
        return "open"
    if value in ("closed", "closedforseason", "closedforseasons", "notoperating"):
        return "closed"
    if value in ("hold", "onhold", "delayed"):
        return "hold"
    if value in ("scheduled", "expected"):
        return "scheduled"
    return "unknown"


def parse_operations(resort: dict, feed: Feed, now: datetime) -> SkiOperations:
    result = SkiOperations(source=_source(resort["name"], resort["website"], feed))
    if feed.data is None:
        return result
    key, data = resort["id"], feed.data
    reported_at = None
    if key == "alta":
        rows = data.get("liftStatus", {}).get("lifts", [])
        totals = data.get("operations", {})
        result.trails_open = integer(totals.get("runs", {}).get("open"))
        result.trails_total = integer(totals.get("runs", {}).get("total"))
    elif key == "pcm":
        rows = data.get("Lifts", [])
        reported_at = timestamp(data.get("Date"))
    elif key in ("solitude", "deervalley"):
        # The full mountain report has a report timestamp and authoritative totals.
        rows = [lift for area in data.get("MountainAreas", []) for lift in area.get("Lifts", [])]
        report = data.get("SnowReport", {})
        result.trails_open, result.trails_total = integer(report.get("TotalOpenTrails")), integer(report.get("TotalTrails"))
        run_rows = [run for area in data.get("MountainAreas", []) for run in area.get("Trails", [])]
        result.runs = parse_runs(run_rows)
        reported_at = timestamp(data.get("LastUpdate"))
    else:
        rows = data
    result.source.reported_at = reported_at
    if reported_at and not _fresh(reported_at, now, timedelta(hours=24)):
        result.source.status = "stale"
    seen = set()
    for row in rows:
        name = str(row.get("name") or row.get("Name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        status = ("open" if row.get("open") is True else "closed" if row.get("open") is False else "unknown") if key == "alta" else lift_status(row.get("status") or row.get("StatusEnglish") or row.get("Status"))
        wait, wait_at = None, None
        if key == "snowbird":
            telemetry = row.get("waitTime") or {}
            seconds = number(telemetry.get("waitTimeSecs"))
            wait = seconds / 60 if seconds is not None else None
            wait_at = timestamp(telemetry.get("waitTimeUpdated"))
        elif key == "pcm":
            wait = number(row.get("WaitTimeInMinutes"))
            wait_at = reported_at
        # Other feeds expose undocumented wait fields; do not guess their units or freshness.
        if status != "open" or feed.stale or result.source.status == "stale" or not _fresh(wait_at, now, WAIT_MAX_AGE) or wait is None or wait > 180:
            wait, wait_at = None, None
        result.lifts.append(SkiLift(name=name, status=status, wait_minutes=round(wait, 1) if wait is not None else None, wait_updated_at=wait_at))
    if result.lifts:
        result.lifts_open = sum(lift.status == "open" for lift in result.lifts)
        result.lifts_total = len(result.lifts)
        if not any(lift.status != "unknown" for lift in result.lifts):
            result.lifts_open = None
    # An empty board is unknown/off-season, never an invented 0/0 lift report.
    waits = [lift.wait_minutes for lift in result.lifts if lift.wait_minutes is not None]
    result.reporting_lifts = len(waits)
    if waits:
        result.wait_minutes = round(median(waits), 1)
        result.max_wait_minutes = max(waits)
        result.wait_status = "reported"
    elif result.source.status == "stale":
        result.wait_status = "stale"
    elif result.lifts_open == 0:
        result.wait_status = "no_open_lifts"
    return result


def parse_runs(rows: list[dict]) -> list[SkiRun]:
    result, names = [], set()
    for row in rows:
        name = str(row.get("name") or row.get("Name") or "").strip()
        if not name or name in names:
            continue
        names.add(name)
        grooming = row.get("groom_status", row.get("Grooming"))
        result.append(SkiRun(
            name=name, status=lift_status(row.get("status") or row.get("StatusEnglish") or row.get("Status")),
            difficulty=row.get("difficulty") or row.get("Difficulty"),
            groomed=True if grooming in ("groomed", "Yes") else False if grooming in ("not_groomed", "No") else None,
        ))
    return result


def parse_snow_report(resort: dict, feed: Feed, now: datetime) -> SkiSnowReport:
    result = SkiSnowReport(source=_source(resort["name"], resort["website"], Feed()))
    if feed.data is None:
        return result
    if resort["id"] == "alta":
        report = feed.data.get("conditions", {})
        updated = timestamp(report.get("dated_at"), local=True)
        snow, base = number(report.get("last24")), number(report.get("base_depth"))
    elif resort["id"] in ("solitude", "deervalley"):
        report = feed.data.get("SnowReport", {})
        updated = timestamp(report.get("LastUpdate"))
        areas = [report.get(k, {}) for k in ("MidMountainArea", "BaseArea", "SummitArea")]
        snow = next((number(area.get("Last24HoursIn")) for area in areas if number(area.get("Last24HoursIn")) is not None), None)
        base = next((number(area.get("BaseIn")) for area in areas if number(area.get("BaseIn")) is not None), None)
    else:
        return result
    result.source = _source(resort["name"], resort["website"], feed, updated)
    if not _fresh(updated, now, timedelta(hours=36)):
        result.source.status = "stale"
    result.snowfall_24h_in, result.base_depth_in = snow, base
    return result


def _signed_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def parse_forecast(data: dict | None, feed: Feed, now: datetime) -> SkiForecast:
    result = SkiForecast(source=_source("Open-Meteo · CC BY 4.0", "https://open-meteo.com/", feed))
    if not data:
        return result
    hours = data.get("hourly", {})
    times = [timestamp(t) for t in hours.get("time", [])]
    # Units are checked, never assumed: snowfall is cm even with Fahrenheit/mph selected.
    units = data.get("hourly_units", {})
    def at(field, index):
        values = hours.get(field, [])
        return values[index] if index < len(values) else None
    def snowfall(start, end):
        indexes = [i for i, t in enumerate(times) if t and start < t <= end]
        values = [number(at("snowfall", i)) for i in indexes]
        if units.get("snowfall") != "cm" or len(values) != 24 * round((end - start).total_seconds() / 86400) or any(v is None for v in values):
            return None
        return round(sum(values) / 2.54, 1)
    # Hourly snowfall covers the preceding hour. Align to the latest completed hour.
    anchor = now.replace(minute=0, second=0, microsecond=0)
    result.snowfall_24h_in = snowfall(anchor - timedelta(hours=24), anchor)
    result.snowfall_next_24h_in = snowfall(anchor, anchor + timedelta(hours=24))
    result.snowfall_next_48h_in = snowfall(anchor, anchor + timedelta(hours=48))
    current = next((i for i, t in enumerate(times) if t == anchor), None)
    if current is not None:
        result.valid_at = times[current]
        depth = number(at("snow_depth", current))
        if depth is not None and units.get("snow_depth") == "m":
            result.snow_depth_in = round(depth / .0254, 1)
        if units.get("temperature_2m") == "°F":
            result.temperature_f = _signed_number(at("temperature_2m", current))
        if units.get("wind_speed_10m") == "mp/h":
            result.wind_mph = number(at("wind_speed_10m", current))
    return result


def busyness(operations: SkiOperations) -> SkiBusyness:
    if operations.wait_status == "no_open_lifts":
        return SkiBusyness(level="no_open_lifts", basis="lift_status", description="No lifts are reported running. This is not a crowd measurement.")
    if operations.wait_minutes is None:
        return SkiBusyness()
    level = "quiet" if operations.wait_minutes < 5 else "moderate" if operations.wait_minutes < 15 else "busy"
    return SkiBusyness(level=level, basis="reported_lift_waits", description=(
        f"Based on the median published wait at {operations.reporting_lifts} of "
        f"{operations.lifts_open} open lifts. Under 5 min: quiet; 5–14 min: moderate; "
        "15+ min: busy. This describes reporting lift lines, not resort attendance."
    ))


async def ski_conditions() -> SkiResponse:
    weather_params = {
        "latitude": ",".join(str(r["lat"]) for r in RESORTS),
        "longitude": ",".join(str(r["lng"]) for r in RESORTS),
        "elevation": ",".join(str(r["elevation_m"]) for r in RESORTS),
        "hourly": "snowfall,snow_depth,temperature_2m,wind_speed_10m",
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
        "timeformat": "unixtime", "past_hours": 24, "forecast_hours": 49,
    }
    weather_url = OPEN_METEO_URL
    if settings.open_meteo_api_key:
        weather_url = "https://customer-api.open-meteo.com/v1/forecast"
        weather_params["apikey"] = settings.open_meteo_api_key
    keys = list(LIFT_FEEDS)
    results = await asyncio.gather(fetch_feed("weather", weather_url, weather_params), *(fetch_feed(key, LIFT_FEEDS[key]) for key in keys), fetch_feed("snowbird-runs", "https://api.snowbird.com/api/v1/dor/drupal/trails"))
    weather, feeds = results[0], dict(zip(keys, results[1:1 + len(keys)]))
    now, resorts = _now(), []
    for index, resort in enumerate(RESORTS):
        feed = feeds.get(resort["id"], Feed())
        try:
            operations = parse_operations(resort, feed, now)
            report = parse_snow_report(resort, feed, now)
        except (ValueError, TypeError, KeyError, AttributeError):
            operations = SkiOperations(source=_source(resort["name"], resort["website"], Feed()))
            report = SkiSnowReport(source=_source(resort["name"], resort["website"], Feed()))
        if resort["id"] == "snowbird" and results[-1].data is not None:
            run_feed = results[-1]
            if not run_feed.stale:
                try:
                    operations.runs = parse_runs(run_feed.data)
                    operations.trails_total = len(operations.runs) or None
                    operations.trails_open = sum(run.status == "open" for run in operations.runs) if operations.runs else None
                except (ValueError, TypeError, KeyError, AttributeError):
                    pass
        try:
            forecast = parse_forecast(weather.data[index] if weather.data else None, weather, now)
        except (ValueError, TypeError, KeyError, AttributeError, IndexError):
            forecast = SkiForecast(source=_source("Open-Meteo · CC BY 4.0", "https://open-meteo.com/", Feed()))
        resorts.append(SkiResort(
            **{k: v for k, v in resort.items() if k not in ("lat", "lng")},
            coordinate=Coordinate(lat=resort["lat"], lng=resort["lng"]),
            operations=operations, snow_report=report, forecast=forecast,
            busyness=busyness(operations),
        ))
    return SkiResponse(generated_at=now, resorts=resorts)
