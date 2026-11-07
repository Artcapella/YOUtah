"""Small, production-friendly examples for Comprehensive Utah data ingestion.

Use this only from a backend worker (never ship API keys to the browser).
Install: pip install requests python-dotenv
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import requests


class UpstreamError(RuntimeError):
    """An upstream API returned an unexpected response."""


@dataclass
class DataClient:
    """HTTP helper with an honest, small retry policy for batch ingestion."""

    timeout_seconds: int = 20
    user_agent: str = "ComprehensiveUtah/0.1 (contact: replace-with-team-email)"

    def get(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        for attempt in range(3):
            response = requests.get(url, params=params, headers=headers, timeout=self.timeout_seconds)
            if response.status_code not in (429, 500, 502, 503, 504):
                break
            time.sleep(2**attempt)
        if not response.ok:
            raise UpstreamError(f"{response.status_code} from {response.url}: {response.text[:300]}")
        return response.json()


def recreation_gov_facilities(client: DataClient, state: str = "UT", limit: int = 50) -> list[dict[str, Any]]:
    """Campgrounds/facilities from Recreation.gov's RIDB API."""
    api_key = os.environ["RIDB_API_KEY"]
    payload = client.get(
        "https://ridb.recreation.gov/api/v1/facilities",
        params={"apikey": api_key, "state": state, "limit": min(limit, 50)},
    )
    return payload.get("RECDATA", [])


def weather_forecast(client: DataClient, latitude: float, longitude: float) -> dict[str, Any]:
    """NWS forecast, via its required point-to-grid lookup."""
    point = client.get(f"https://api.weather.gov/points/{latitude},{longitude}")
    forecast_url = point["properties"]["forecast"]
    return client.get(forecast_url)


def google_places_text_search(client: DataClient, text_query: str, *, latitude: float | None = None, longitude: float | None = None) -> list[dict[str, Any]]:
    """Google Places API (New). Do not scrape Google Maps HTML or review pages.

    The field mask controls billing and response payload. Store/use results under
    Google's current terms, including attribution, refresh, and caching limits.
    """
    api_key = os.environ["GOOGLE_MAPS_API_KEY"]
    body: dict[str, Any] = {"textQuery": text_query, "pageSize": 20}
    if latitude is not None and longitude is not None:
        body["locationBias"] = {"circle": {"center": {"latitude": latitude, "longitude": longitude}, "radius": 25_000.0}}
    response = requests.post(
        "https://places.googleapis.com/v1/places:searchText",
        json=body,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.location,places.rating,places.userRatingCount,places.googleMapsUri",
        },
        timeout=client.timeout_seconds,
    )
    if not response.ok:
        raise UpstreamError(f"Google Places: {response.status_code}: {response.text[:300]}")
    return response.json().get("places", [])


def google_place_reviews(client: DataClient, place_id: str) -> dict[str, Any]:
    """Limited review content exposed by the authorized Places Details endpoint."""
    api_key = os.environ["GOOGLE_MAPS_API_KEY"]
    response = requests.get(
        f"https://places.googleapis.com/v1/places/{place_id}",
        headers={
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "id,displayName,rating,userRatingCount,reviews,googleMapsUri",
        },
        timeout=client.timeout_seconds,
    )
    if not response.ok:
        raise UpstreamError(f"Google Places Details: {response.status_code}: {response.text[:300]}")
    return response.json()


def udot_arcgis_features(client: DataClient, service_url: str, *, where: str = "1=1", out_fields: str = "*") -> list[dict[str, Any]]:
    """Query a UDOT ArcGIS REST feature layer.

    Find a public layer in UDOT's ArcGIS/Open Data catalog, then pass its *layer*
    URL (for example, one ending in /FeatureServer/0). This generic adapter works
    for roadwork, traffic, cameras, or GIS layers whose schemas differ.
    """
    payload = client.get(
        f"{service_url.rstrip('/')}/query",
        params={"where": where, "outFields": out_fields, "f": "json", "returnGeometry": "true", "resultRecordCount": 2000},
    )
    if "error" in payload:
        raise UpstreamError(f"UDOT ArcGIS error: {payload['error']}")
    return payload.get("features", [])


def overpass_pois(client: DataClient, latitude: float, longitude: float, radius_meters: int = 5000) -> list[dict[str, Any]]:
    """Optional open-data fallback: nearby parks/trails/attractions from OpenStreetMap."""
    query = f"""[out:json][timeout:25];(nwr(around:{radius_meters},{latitude},{longitude})[tourism];nwr(around:{radius_meters},{latitude},{longitude})[leisure=park];);out center tags;"""
    response = requests.post(
        "https://overpass-api.de/api/interpreter", data={"data": query},
        headers={"User-Agent": client.user_agent}, timeout=client.timeout_seconds,
    )
    if not response.ok:
        raise UpstreamError(f"Overpass: {response.status_code}: {response.text[:300]}")
    return response.json().get("elements", [])


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    api = DataClient()
    print(recreation_gov_facilities(api, "UT", limit=5))
    print(weather_forecast(api, 40.7608, -111.8910)["properties"]["periods"][:2])
