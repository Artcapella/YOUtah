"""Recreation.gov RIDB facilities (campgrounds, day-use areas, permits) for the ``ridb`` places ingest.

Requires RIDB_API_KEY (free: https://ridb.recreation.gov/profile). The key goes in
the ``apikey`` header, never the URL, so it cannot leak into logs or error messages.
"""

from __future__ import annotations

import html
import re
from typing import Any

import httpx

from backend.config import settings
from backend.models import Coordinate, PlaceRecord
from backend.providers.http import ingest_client, request_json

FACILITIES_URL = "https://ridb.recreation.gov/api/v1/facilities"
PAGE_SIZE = 50  # RIDB's maximum page size
MAX_PAGES = 200
DESCRIPTION_CHARS = 280

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def _plain_text(markup: str | None, limit: int = DESCRIPTION_CHARS) -> str:
    """RIDB descriptions are HTML. Strip tags, unescape, collapse whitespace, cut at a word."""
    text = _SPACE.sub(" ", html.unescape(_TAG.sub(" ", markup or ""))).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"


def _display_name(name: str) -> str:
    # Many RIDB names are all caps ("BRIDGE HOLLOW CAMPGROUND").
    return name.title() if name.isupper() else name


def facility_record(raw: dict[str, Any]) -> PlaceRecord | None:
    """Normalize one RIDB facility. Disabled facilities and ones without a location are skipped."""
    facility_id = str(raw.get("FacilityID") or "").strip()
    name = (raw.get("FacilityName") or "").strip()
    lat, lng = raw.get("FacilityLatitude"), raw.get("FacilityLongitude")
    # RIDB reports an unknown location as 0,0.
    if not facility_id or not name or not lat or not lng or raw.get("Enabled") is False:
        return None

    facility_type = (raw.get("FacilityTypeDescription") or "").strip()
    is_campground = facility_type.lower() == "campground"
    source_url = (
        f"https://www.recreation.gov/camping/campgrounds/{facility_id}"
        if is_campground
        else raw.get("FacilityReservationURL") or None
    )
    tags = [facility_type.lower()] if facility_type else []
    if raw.get("Reservable"):
        tags.append("reservable")

    return PlaceRecord(
        external_id=facility_id,
        name=_display_name(name),
        category="park",
        description=_plain_text(raw.get("FacilityDescription")) or (facility_type or "Recreation facility"),
        coordinate=Coordinate(lat=float(lat), lng=float(lng)),
        source_url=source_url,
        tags=tags,
        raw_payload=raw,
    )


async def fetch_facilities(state: str = "UT", client: httpx.AsyncClient | None = None) -> list[PlaceRecord]:
    """Every RIDB facility in a state, paginated. Raises without a key or on upstream failure."""
    if not settings.ridb_api_key:
        raise RuntimeError("RIDB_API_KEY is not set")
    if client is None:
        async with ingest_client(apikey=settings.ridb_api_key) as owned:
            return await fetch_facilities(state, owned)

    raw_facilities: list[dict[str, Any]] = []
    for _ in range(MAX_PAGES):
        payload = await request_json(
            client, "GET", FACILITIES_URL, label="RIDB",
            params={"state": state, "limit": PAGE_SIZE, "offset": len(raw_facilities)},
        )
        page = payload.get("RECDATA") or []
        raw_facilities.extend(page)
        total = ((payload.get("METADATA") or {}).get("RESULTS") or {}).get("TOTAL_COUNT")
        if not page or (total is not None and len(raw_facilities) >= total):
            break
    return [record for record in map(facility_record, raw_facilities) if record is not None]
