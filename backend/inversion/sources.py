"""Escape the Inversion: data fetching. Every fetch has a cache and an offline fallback,
so the page still renders when a provider is slow or down."""
from __future__ import annotations

import asyncio
import csv
import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from backend.config import settings

DATA = Path(__file__).parent / "data"
BBOX = (-112.7, 39.95, -111.25, 41.95)  # Wasatch Front smog-map area (Logan to Spanish Fork): W, S, E, N
HEADERS = {"User-Agent": settings.nws_user_agent}

# Approximate locations of Utah DAQ monitors (the XML feed has names only). Keys = feed names.
DAQ_SITES = {
    "Salt Lake County": (40.7343, -111.8721),  # Hawthorne
    "Rose Park": (40.7955, -111.9309),
    "Herriman": (40.4966, -112.0363),
    "Copper View": (40.5981, -111.8942),
    "Red Butte": (40.7664, -111.8282),
    "Near Road": (40.6629, -111.9018),
    "Magna #2": (40.7085, -112.0947),
    "Lake Park": (40.7098, -112.0089),
    "Davis County": (40.9030, -111.8845),
    "Tooele County": (40.5434, -112.2989),
    "Utah County": (40.3414, -111.7137),
    "Spanish Fork": (40.1364, -111.6603),
    "Weber County": (41.2068, -111.9752),
    "Box Elder County": (41.4925, -112.0186),
    "Cache County": (41.8428, -111.8522),
    "Wasatch County": (40.4968, -111.4206),
    "Duchesne County": (40.2944, -110.0090),
    "Uintah County": (40.4552, -109.5282),
    "Price": (39.6106, -110.8001),
    "Grand County": (38.5738, -109.5463),
    "Washington County": (37.1790, -113.3050),
    "Enoch": (37.7731, -113.0245),
}
STATION_RADIUS_KM = 60   # weather stations used to find the lid around the starting point
ELEV_OK = (600, 4100)    # Utah's lowest point is ~660 m; drop obviously bad station elevations

_cache: dict[str, tuple[float, object]] = {}


async def _cached(key: str, ttl: int, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = await fn()
    _cache[key] = (time.time(), value)
    return value


def _read_csv(name: str) -> list[dict]:
    with open(DATA / name, newline="") as f:
        return [{k: (float(v) if k in {"lat", "lon", "elevation_m", "air_temp_c"} and v != "" else v)
                 for k, v in row.items()} for row in csv.DictReader(f)]


def load_trailheads() -> list[dict]:
    return [{"name": r["name"], "lat": r["lat"], "lon": r["lon"], "elevation_m": r["elevation_m"], "use": r.get("use") or ""}
            for r in _read_csv("trailheads_elev.csv")]


def load_grid() -> list[dict]:
    return _read_csv("grid_elev.csv")


def demo_stations() -> list[dict]:
    return _read_csv("stations_demo_inversion.csv")


def _station_list() -> list[dict]:
    """All ~1,400 NWS stations in Utah with a sane elevation."""
    out = []
    for f in json.loads((DATA / "nws_ut_stations.json").read_text())["features"]:
        lon, lat = f["geometry"]["coordinates"][:2]
        p = f["properties"]
        elev = (p.get("elevation") or {}).get("value")
        if elev and ELEV_OK[0] <= elev <= ELEV_OK[1]:
            out.append(dict(stid=p["stationIdentifier"], name=p["name"], lat=lat, lon=lon, elevation_m=elev))
    return out


def _by_elevation_band(stations: list[dict], per_band: int = 6, band_m: int = 100) -> list[dict]:
    """Keep up to per_band stations per 100 m elevation band (input is nearest-first), so dense valley
    networks don't crowd out the few mountain stations that reveal an inversion."""
    seen: dict[int, int] = {}
    out = []
    for st in stations:
        b = int(st["elevation_m"] // band_m)
        if seen.get(b, 0) < per_band:
            seen[b] = seen.get(b, 0) + 1
            out.append(st)
    return out


def in_wasatch(lat: float, lon: float) -> bool:
    return BBOX[0] <= lon <= BBOX[2] and BBOX[1] <= lat <= BBOX[3]


async def fetch_stations(lat: float, lon: float) -> tuple[list[dict], str]:
    """Live NWS temperatures for up to 90 stations within STATION_RADIUS_KM of the starting point."""
    from backend.inversion.core import nearby
    async def go():
        stations = _by_elevation_band(nearby(_station_list(), lat, lon, STATION_RADIUS_KM))
        sem = asyncio.Semaphore(16)
        async with httpx.AsyncClient(headers=HEADERS, timeout=8) as client:
            async def one(s):
                async with sem:
                    try:
                        r = await client.get(f"https://api.weather.gov/stations/{s['stid']}/observations/latest")
                        t = r.json()["properties"]["temperature"]["value"]
                        return {**s, "air_temp_c": t} if t is not None else None
                    except Exception:
                        return None
            rows = [x for x in await asyncio.gather(*map(one, stations)) if x]
        if len(rows) < 3:
            raise RuntimeError("too few stations")
        return rows, f"NWS live ({len(rows)} stations within {STATION_RADIUS_KM} km)"
    try:
        return await _cached(f"stations:{round(lat * 4) / 4}:{round(lon * 4) / 4}", 600, go)
    except Exception:
        if in_wasatch(lat, lon):
            return demo_stations(), "saved data (live stations unavailable)"
        return [], "live stations unavailable"


async def fetch_monitors() -> list[dict]:
    """Latest PM2.5 at Wasatch Front monitors from the Utah DAQ XML feed."""
    async def go():
        async with httpx.AsyncClient(headers=HEADERS, timeout=10) as client:
            r = await client.get("https://air.utah.gov/xmlFeed.php")
        rows = []
        for site in ET.fromstring(r.content).iter("site"):
            name = site.findtext("name")
            if name not in DAQ_SITES:
                continue
            for d in site.iter("data"):
                v = d.findtext("pm25")
                if v:
                    lat, lon = DAQ_SITES[name]
                    rows.append(dict(name=name, lat=lat, lon=lon, pm25=float(v)))
                    break
        return rows
    try:
        return await _cached("monitors", 600, go)
    except Exception:
        return []


async def fetch_forecast_hours(lat: float = 40.76, lon: float = -111.89) -> tuple[list[dict], float]:
    """Hourly 7-day forecast (Open-Meteo, free, no key): ground temp, 850 hPa temp, mixing height."""
    async def go():
        params = dict(latitude=lat, longitude=lon, forecast_days=7, timezone="America/Denver",
                      hourly="temperature_2m,temperature_850hPa,boundary_layer_height")
        async with httpx.AsyncClient(timeout=15) as client:
            for attempt in range(2):  # one retry: the provider occasionally drops a connection
                try:
                    j = (await client.get("https://api.open-meteo.com/v1/forecast", params=params)).json()
                    break
                except httpx.HTTPError:
                    if attempt:
                        raise
                    await asyncio.sleep(1)
        h = j["hourly"]
        hours = [dict(time=t, temperature_2m=a, temperature_850hPa=b, boundary_layer_height=c)
                 for t, a, b, c in zip(h["time"], h["temperature_2m"], h["temperature_850hPa"],
                                       h["boundary_layer_height"])]
        return hours, j.get("elevation", 1300)
    return await _cached(f"forecast:{round(lat, 1)}:{round(lon, 1)}", 3600, go)


async def geocode(address: str) -> dict | None:
    """US address -> {lat, lon, address} via the free US Census geocoder."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
                                 params=dict(address=address, benchmark="Public_AR_Current", format="json"))
        m = r.json()["result"]["addressMatches"]
        if m:
            return dict(lat=m[0]["coordinates"]["y"], lon=m[0]["coordinates"]["x"], address=m[0]["matchedAddress"])
    except Exception:
        pass
    return None
