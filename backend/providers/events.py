
from datetime import datetime, timedelta, timezone

import httpx

from backend.config import settings
from backend.models import Coordinate, LocalEvent
from backend.seed_data import demo_events


TICKETMASTER_URL = "https://app.ticketmaster.com/discovery/v2/events.json"


async def fetch_events(
    category: str = "any",
    city: str = "Salt Lake City",
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[LocalEvent]:
    """Fetch Utah events from Ticketmaster."""

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
