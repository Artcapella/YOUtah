"""Event adapters: Ticketmaster Discovery (live when TICKETMASTER_API_KEY is set).

Campus Connect ICS and library feeds plug in here next. Every adapter returns the
canonical ``LocalEvent`` model.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx

from backend.config import settings
from backend.models import Coordinate, LocalEvent
from backend.seed_data import demo_events

logger = logging.getLogger(__name__)

TICKETMASTER_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
DOWNTOWN_SLC = Coordinate(lat=40.7608, lng=-111.8910)


def _ticketmaster_event(raw: dict) -> LocalEvent | None:
    venue = (raw.get("_embedded", {}).get("venues") or [{}])[0]
    location = venue.get("location") or {}
    starts = (raw.get("dates", {}).get("start") or {}).get("dateTime")
    if not starts or "latitude" not in location or "longitude" not in location:
        return None
    segment = ((raw.get("classifications") or [{}])[0].get("segment") or {}).get("name")
    prices = [price["min"] for price in raw.get("priceRanges") or [] if price.get("min") is not None]
    # Discovery omits priceRanges for many paid events, so a missing price is never read as "free".
    price_label = f"From ${min(prices):,.2f}".removesuffix(".00") if prices else "See listing"
    return LocalEvent(
        id=f"ticketmaster-{raw['id']}",
        source="Ticketmaster",
        source_url=raw.get("url"),
        title=raw.get("name") or "Untitled event",
        venue=venue.get("name") or "Location TBA",
        category=segment if segment and segment != "Undefined" else "Event",
        starts_at=datetime.fromisoformat(starts.replace("Z", "+00:00")),
        price_label=price_label,
        coordinate=Coordinate(lat=float(location["latitude"]), lng=float(location["longitude"])),
        is_free=False,
    )


async def fetch_ticketmaster_events(point: Coordinate, radius_miles: int = 25, size: int = 50) -> list[LocalEvent]:
    """Upcoming Ticketmaster events near a point. Empty without a key; raises on upstream failure."""
    if not settings.ticketmaster_api_key:
        return []
    params = {
        "apikey": settings.ticketmaster_api_key,
        "latlong": f"{point.lat},{point.lng}",
        "radius": radius_miles,
        "unit": "miles",
        "size": size,
        "sort": "date,asc",
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0)) as client:
        response = await client.get(TICKETMASTER_URL, params=params)
        response.raise_for_status()
    raw_events = response.json().get("_embedded", {}).get("events", [])
    return [event for event in map(_ticketmaster_event, raw_events) if event is not None]


async def fetch_events(point: Coordinate = DOWNTOWN_SLC) -> list[LocalEvent]:
    """Dashboard events lane: live Ticketmaster when configured, otherwise demo events."""
    if settings.ticketmaster_api_key:
        try:
            return await fetch_ticketmaster_events(point)
        except Exception as error:
            logger.warning("Ticketmaster unavailable, serving demo events: %s", error)
    return demo_events()


async def search_ticketmaster_events(
    category: str = "any",
    city: str = "Salt Lake City",
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[LocalEvent]:
    """Fetch Utah events from Ticketmaster by city, category and date window.

    Backs GET /api/events/recommendations. Each event's ``category`` is the requested
    category so ``recommend_events`` can match on it.
    """

    if not settings.ticketmaster_api_key:
        # Keep the existing dashboard functional without API credentials.
        return demo_events()

    today = datetime.now(timezone.utc)

    start = (
        datetime.fromisoformat(start_date).date()
        if start_date
        else today.date()
    )
    end = (
        datetime.fromisoformat(end_date).date()
        if end_date
        else start + timedelta(days=7)
    )

    if end < start:
        raise ValueError("end_date cannot be earlier than start_date")

    params = {
        "apikey": settings.ticketmaster_api_key,
        "city": city,
        "stateCode": "UT",
        "startDateTime": f"{start.isoformat()}T00:00:00Z",
        "endDateTime": f"{(end + timedelta(days=1)).isoformat()}T00:00:00Z",
        "size": 100,
        "sort": "date,asc",
    }

    category_mapping = {
        "sports": "Sports",
        "music": "Music",
        "arts": "Arts & Theatre",
    }

    if category.lower() in category_mapping:
        params["classificationName"] = category_mapping[category.lower()]

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(TICKETMASTER_URL, params=params)
        response.raise_for_status()
        payload = response.json()

    results = []

    for item in payload.get("_embedded", {}).get("events", []):
        venue = (
            item.get("_embedded", {})
            .get("venues", [{}])[0]
        )

        location = venue.get("location", {})
        dates = item.get("dates", {}).get("start", {})

        if not dates.get("dateTime"):
            continue

        try:
            event = LocalEvent(
                id=f"ticketmaster-{item['id']}",
                source="Ticketmaster",
                source_url=item.get("url"),
                title=item.get("name", "Untitled event"),
                venue=venue.get("name", "Unknown venue"),
                category=category,
                starts_at=datetime.fromisoformat(
                    dates["dateTime"].replace("Z", "+00:00")
                ),
                price_label="Price unavailable",
                coordinate=Coordinate(
                    lat=float(location.get("latitude", 40.7608)),
                    lng=float(location.get("longitude", -111.8910)),
                ),
                is_free=False,
            )
            results.append(event)
        except (ValueError, KeyError, TypeError):
            continue

    return results
