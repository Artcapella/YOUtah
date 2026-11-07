"""OpenStreetMap points of interest via the Overpass API, for the ``osm`` places ingest.

Keyless, but shared and rate-limited: call it from scheduled ingestion only, never
per request. Overpass rejects generic or placeholder User-Agents with HTTP 406, so
NWS_USER_AGENT must name this app and a real contact. Data is (c) OpenStreetMap
contributors under the ODbL: keep the attribution wherever these places are shown.
"""

from __future__ import annotations

from typing import Any

import httpx

from backend.models import Coordinate, PlaceCategory, PlaceRecord
from backend.providers.http import UpstreamError, ingest_client, request_json

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
UTAH_AREA = 'area["ISO3166-2"="US-UT"][admin_level=4]'
QUERY_TIMEOUT_SECONDS = 90

# OSM tag value -> dashboard category. Lodging and info boards are left out on purpose.
_CATEGORIES: dict[tuple[str, str], PlaceCategory] = {
    ("leisure", "park"): "park",
    ("leisure", "nature_reserve"): "park",
    ("tourism", "viewpoint"): "park",
    ("tourism", "picnic_site"): "park",
    ("tourism", "museum"): "art",
    ("tourism", "gallery"): "art",
    ("tourism", "artwork"): "art",
    ("tourism", "attraction"): "art",
    ("tourism", "zoo"): "venue",
    ("tourism", "theme_park"): "venue",
    ("amenity", "library"): "library",
}


def build_query(around: tuple[Coordinate, int] | None = None) -> str:
    """Named POIs statewide, or within ``around=(point, radius_m)``."""
    if around is None:
        prelude, scope = f"{UTAH_AREA}->.utah;", "(area.utah)"
    else:
        point, radius_m = around
        prelude, scope = "", f"(around:{int(radius_m)},{point.lat},{point.lng})"
    by_key: dict[str, list[str]] = {}
    for key, value in _CATEGORIES:
        by_key.setdefault(key, []).append(value)
    selectors = "".join(f'nwr{scope}["{key}"~"^({"|".join(values)})$"]["name"];' for key, values in by_key.items())
    return f"[out:json][timeout:{QUERY_TIMEOUT_SECONDS}];{prelude}({selectors});out center tags;"


def _kind(tags: dict[str, str]) -> tuple[str, PlaceCategory] | None:
    for (key, value), category in _CATEGORIES.items():
        if tags.get(key) == value:
            return value, category
    return None


def _address(tags: dict[str, str]) -> str | None:
    street = " ".join(part for part in (tags.get("addr:housenumber"), tags.get("addr:street")) if part)
    parts = [part for part in (street, tags.get("addr:city")) if part]
    return ", ".join(parts) or None


def element_record(element: dict[str, Any]) -> PlaceRecord | None:
    """Normalize one Overpass element. Ways and relations use their ``center``."""
    tags = element.get("tags") or {}
    name = (tags.get("name") or "").strip()
    kind = _kind(tags)
    point = element if "lat" in element else element.get("center") or {}
    if not name or kind is None or point.get("lat") is None or point.get("lon") is None:
        return None
    value, category = kind
    osm_ref = f"{element['type']}/{element['id']}"
    return PlaceRecord(
        external_id=osm_ref,
        name=name,
        category=category,
        description=(tags.get("description") or value.replace("_", " ").capitalize()).strip(),
        coordinate=Coordinate(lat=float(point["lat"]), lng=float(point["lon"])),
        address=_address(tags),
        website_url=tags.get("website") or tags.get("contact:website"),
        source_url=f"https://www.openstreetmap.org/{osm_ref}",
        tags=[value.replace("_", " ")],
        raw_payload={"type": element["type"], "id": element["id"], "tags": tags},
    )


async def fetch_pois(around: tuple[Coordinate, int] | None = None, client: httpx.AsyncClient | None = None) -> list[PlaceRecord]:
    """Named discovery POIs statewide (default) or near a point. Raises on upstream failure."""
    if client is None:
        # No JSON Accept header: `[out:json]` in the query already selects the format.
        async with ingest_client(timeout_seconds=QUERY_TIMEOUT_SECONDS + 30, Accept="*/*") as owned:
            return await fetch_pois(around, owned)
    try:
        payload = await request_json(client, "POST", OVERPASS_URL, label="Overpass", data={"data": build_query(around)})
    except UpstreamError as error:
        if ": 406 " in str(error):
            raise UpstreamError(f"{error} (Overpass rejects generic User-Agents: set NWS_USER_AGENT to this app's name and a real contact)") from error
        raise
    if payload.get("remark") and not payload.get("elements"):
        raise UpstreamError(f"Overpass: {payload['remark'][:300]}")
    return [record for record in map(element_record, payload.get("elements") or []) if record is not None]
