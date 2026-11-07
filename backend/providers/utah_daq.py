"""Utah Division of Air Quality (air.utah.gov) adapter.

The public per-county feed ``https://air.utah.gov/csvFeed.php?id=<station>`` is a
tab-separated table of hourly readings, **newest row first**, roughly 10-15 days
deep. ``-9999`` marks a missing instrument value. Wind speed is in mph.

These readings are not quality-assured. They are right for display and for live
model features; train on quality-assured history (``scripts/backfill_aqs_history.py``)
plus the snapshots this project ingests.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import math
from datetime import datetime, timedelta, timezone

import httpx

from backend.config import settings
from backend.geo import haversine_km
from backend.models import AirSnapshot, AirStation, AirStationReading, Coordinate
from backend.seed_data import demo_air

logger = logging.getLogger(__name__)

FEED_URL = "https://air.utah.gov/csvFeed.php?id={station_id}"
HOMEPAGE_URL = "https://air.utah.gov"

# Utah's monitoring network reports local *standard* time year-round (EPA AirNow lists a
# GMT offset of -7 for every Utah site), so the feed's naive timestamps are UTC-7.
UTAH_STANDARD_TIME = timezone(timedelta(hours=-7), "MST")

# The feed has no ceilometer data; this placeholder cap stays until a ceilometer source is connected.
PLACEHOLDER_INVERSION_ESTIMATE_M = 1600


def _station(id: str, name: str, county: str, aqs_site: str, lat: float, lng: float, elevation_m: float) -> AirStation:
    return AirStation(
        id=id, name=name, county=county, aqs_site=aqs_site,
        coordinate=Coordinate(lat=lat, lng=lng), elevation_m=elevation_m,
    )


# One representative station per county on air.utah.gov. Coordinates: EPA AirNow
# monitoring-site file; elevations: USGS Elevation Point Query Service.
STATIONS: tuple[AirStation, ...] = (
    _station("slc", "Hawthorne", "Salt Lake", "490353006", 40.733501, -111.871696, 1306.9),
    _station("bv", "Bountiful Viewmont", "Davis", "490110004", 40.897999, -111.885498, 1314.7),
    _station("ln", "Lindon", "Utah", "490494001", 40.341400, -111.713600, 1429.0),
    _station("hv", "Harrisville", "Weber", "490571003", 41.302799, -111.988297, 1318.0),
    _station("bg", "Brigham City #3", "Box Elder", "490030005", 41.489930, -112.021430, 1316.2),
    _station("sm", "Smithfield", "Cache", "490050007", 41.842778, -111.851940, 1375.7),
    _station("ed", "Erda", "Tooele", "490450004", 40.600556, -112.355000, 1320.4),
    _station("hb", "Heber", "Wasatch", "490510001", 40.497960, -111.397630, 1733.6),
    _station("p2", "Price #2", "Carbon", "490071003", 39.595798, -110.769997, 1735.8),
    _station("rs", "Roosevelt", "Duchesne", "490130002", 40.294300, -110.009000, 1584.1),
    _station("v4", "Vernal #4", "Uintah", "490471004", 40.464722, -109.560830, 1665.6),
    _station("m7", "Moab #7", "Grand", "490190007", 38.566055, -109.537167, 1255.6),
    _station("en", "Enoch", "Iron", "490210005", 37.747445, -113.055524, 1690.7),
    _station("hc", "Hurricane", "Washington", "490530007", 37.179000, -113.305200, 990.7),
)
STATIONS_BY_ID = {station.id: station for station in STATIONS}


def nearest_stations(point: Coordinate) -> list[tuple[AirStation, float]]:
    """All stations with their distance in km, nearest first."""
    return sorted(((station, haversine_km(point, station.coordinate)) for station in STATIONS), key=lambda item: item[1])


def _value(raw: str | None) -> float | None:
    try:
        number = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= -999:  # -9999 is the feed's missing-value sentinel
        return None
    return number


def parse_station_feed(station_id: str, body: str) -> list[AirStationReading]:
    """Parse one station feed into readings, newest first. Rows without a timestamp are skipped."""
    lines = [line for line in body.splitlines() if line.strip()]
    readings: list[AirStationReading] = []
    for row in csv.DictReader(io.StringIO("\n".join(lines)), delimiter="\t"):
        try:
            local = datetime.strptime((row.get("dtstamp") or "").strip(), "%m/%d/%Y %H:%M:%S")
        except ValueError:
            continue
        readings.append(AirStationReading(
            station_id=station_id,
            observed_at=local.replace(tzinfo=UTAH_STANDARD_TIME).astimezone(timezone.utc),
            pm25=_value(row.get("pm25")),
            pm25_24hr_avg=_value(row.get("pm25_24hr_avg")),
            temp_f=_value(row.get("temp")),
            humidity=_value(row.get("rh")),
            wind_mph=_value(row.get("ws")),
            wind_dir_deg=_value(row.get("wd")),
        ))
    readings.sort(key=lambda reading: reading.observed_at, reverse=True)
    return readings


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(10.0), headers={"User-Agent": settings.nws_user_agent})


async def fetch_station_readings(station_id: str, client: httpx.AsyncClient | None = None) -> list[AirStationReading]:
    """Fetch one station's recent hourly readings, newest first. Raises on upstream failure."""
    if station_id not in STATIONS_BY_ID:
        raise ValueError(f"Unknown Utah DAQ station: {station_id}")
    if client is None:
        async with _client() as owned:
            return await fetch_station_readings(station_id, owned)
    response = await client.get(FEED_URL.format(station_id=station_id))
    response.raise_for_status()
    return parse_station_feed(station_id, response.text)


async def fetch_all_station_readings() -> dict[str, list[AirStationReading] | Exception]:
    """Every station concurrently; a failed station maps to its exception instead of failing the batch."""
    async with _client() as client:
        results = await asyncio.gather(
            *(fetch_station_readings(station.id, client) for station in STATIONS), return_exceptions=True
        )
    return {station.id: result for station, result in zip(STATIONS, results)}


def latest_with_pm25(readings: list[AirStationReading]) -> AirStationReading | None:
    return next((reading for reading in readings if reading.pm25_24hr_avg is not None), None)


async def fetch_nearest_readings(
    point: Coordinate, max_age: timedelta = timedelta(hours=6), candidates: int = 3
) -> tuple[AirStation, float, list[AirStationReading]]:
    """Readings from the nearest station that has a fresh PM2.5 average.

    Tries the ``candidates`` nearest stations in order; if none is fresh, returns the
    freshest one found. Raises LookupError when no station returned usable data.
    """
    best: tuple[AirStation, float, list[AirStationReading]] | None = None
    best_time: datetime | None = None
    now = datetime.now(timezone.utc)
    async with _client() as client:
        for station, distance_km in nearest_stations(point)[:candidates]:
            try:
                readings = await fetch_station_readings(station.id, client)
            except Exception as error:
                logger.warning("Utah DAQ station %s unavailable: %s", station.id, error)
                continue
            latest = latest_with_pm25(readings)
            if latest is None:
                continue
            if now - latest.observed_at <= max_age:
                return station, distance_km, readings
            if best_time is None or latest.observed_at > best_time:
                best, best_time = (station, distance_km, readings), latest.observed_at
    if best is None:
        raise LookupError("No Utah DAQ station near this point returned PM2.5 data")
    return best


# EPA PM2.5 AQI breakpoints (24-hour average, 2024 revision): (C_low, C_high, I_low, I_high, category).
_PM25_AQI_BREAKPOINTS = (
    (0.0, 9.0, 0, 50, "Good"),
    (9.1, 35.4, 51, 100, "Moderate"),
    (35.5, 55.4, 101, 150, "Unhealthy for Sensitive Groups"),
    (55.5, 125.4, 151, 200, "Unhealthy"),
    (125.5, 225.4, 201, 300, "Very Unhealthy"),
    (225.5, 325.4, 301, 500, "Hazardous"),
)


def pm25_aqi(pm25_24hr_avg: float) -> tuple[int, str]:
    concentration = math.floor(max(0.0, pm25_24hr_avg) * 10) / 10  # EPA truncates to one decimal
    for c_low, c_high, i_low, i_high, category in _PM25_AQI_BREAKPOINTS:
        if concentration <= c_high:
            return round((i_high - i_low) / (c_high - c_low) * (concentration - c_low) + i_low), category
    return 500, "Hazardous"


async def fetch_air_quality(point: Coordinate) -> AirSnapshot:
    """Live AQI from the nearest DAQ station; demo values marked ``degraded`` if DAQ is unreachable."""
    try:
        _, _, readings = await fetch_nearest_readings(point)
    except Exception as error:
        logger.warning("Utah DAQ unavailable, serving demo air quality: %s", error)
        return demo_air().model_copy(update={"source": "degraded"})
    latest = latest_with_pm25(readings)
    assert latest is not None and latest.pm25_24hr_avg is not None  # guaranteed by fetch_nearest_readings
    aqi, category = pm25_aqi(latest.pm25_24hr_avg)
    return AirSnapshot(
        aqi=aqi,
        category=category,
        pm25=latest.pm25_24hr_avg,
        inversion_estimate_m=PLACEHOLDER_INVERSION_ESTIMATE_M,
        source="live",
        updated_at=latest.observed_at,
    )
