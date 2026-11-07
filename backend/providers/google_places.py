"""Google Places API (New): live text search and place details with reviews.

Uses the authorized API, never Maps HTML scraping. Server-side only, and every call
is billed: the field masks below decide the SKU (rating, userRatingCount and reviews
are Enterprise-tier fields). Google Maps Platform terms forbid caching this content
beyond ``place_id``, so these results are never written to Supabase. Show reviews
with their author attribution and a Google Maps attribution.

Not yet called by any route: exposing it publicly needs rate limiting so anonymous
traffic cannot run up the bill.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from backend.config import settings
from backend.models import Coordinate, GooglePlace, GoogleReview
from backend.providers.http import request_json

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"
SEARCH_FIELD_MASK = ",".join(f"places.{field}" for field in (
    "id", "displayName", "formattedAddress", "location", "rating", "userRatingCount", "googleMapsUri",
))
DETAILS_FIELD_MASK = "id,displayName,formattedAddress,location,rating,userRatingCount,reviews,googleMapsUri"
TIMEOUT_SECONDS = 8.0


def _review(raw: dict[str, Any]) -> GoogleReview:
    author = raw.get("authorAttribution") or {}
    published = raw.get("publishTime")
    return GoogleReview(
        author_name=author.get("displayName"),
        author_uri=author.get("uri"),
        rating=raw.get("rating"),
        text=(raw.get("text") or {}).get("text"),
        relative_time=raw.get("relativePublishTimeDescription"),
        published_at=datetime.fromisoformat(published.replace("Z", "+00:00")) if published else None,
    )


def parse_place(raw: dict[str, Any]) -> GooglePlace:
    location = raw.get("location") or {}
    has_point = location.get("latitude") is not None and location.get("longitude") is not None
    return GooglePlace(
        place_id=raw["id"],
        name=(raw.get("displayName") or {}).get("text") or "Unnamed place",
        address=raw.get("formattedAddress"),
        coordinate=Coordinate(lat=location["latitude"], lng=location["longitude"]) if has_point else None,
        rating=raw.get("rating"),
        user_rating_count=raw.get("userRatingCount"),
        google_maps_uri=raw.get("googleMapsUri"),
        reviews=[_review(review) for review in raw.get("reviews") or []],
    )


def _client(field_mask: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(TIMEOUT_SECONDS),
        headers={"X-Goog-Api-Key": settings.google_maps_api_key, "X-Goog-FieldMask": field_mask},
    )


async def search_text(text_query: str, near: Coordinate | None = None, radius_m: float = 25_000.0) -> list[GooglePlace]:
    """Places matching a free-text query, biased toward ``near``. Empty without a key; raises on upstream failure."""
    if not settings.google_maps_api_key:
        return []
    body: dict[str, Any] = {"textQuery": text_query, "pageSize": 20}
    if near is not None:
        body["locationBias"] = {"circle": {"center": {"latitude": near.lat, "longitude": near.lng}, "radius": radius_m}}
    async with _client(SEARCH_FIELD_MASK) as client:
        payload = await request_json(client, "POST", SEARCH_URL, label="Google Places", retries=0, json=body)
    return [parse_place(raw) for raw in payload.get("places") or []]


async def place_details(place_id: str) -> GooglePlace | None:
    """One place with Google's (limited) review content. None without a key; raises on upstream failure."""
    if not settings.google_maps_api_key:
        return None
    async with _client(DETAILS_FIELD_MASK) as client:
        payload = await request_json(client, "GET", DETAILS_URL.format(place_id=place_id), label="Google Places Details", retries=0)
    return parse_place(payload)
