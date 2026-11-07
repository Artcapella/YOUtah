"""Event adapters: Ticketmaster Discovery (live when TICKETMASTER_API_KEY is set).

Campus Connect ICS and library feeds plug in here next. Every adapter returns the
canonical ``LocalEvent`` model.
"""

import logging
from datetime import datetime

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
