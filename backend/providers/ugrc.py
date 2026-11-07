"""Utah Geospatial Resource Center (UGRC) adapters, via ArcGIS feature services.

Live: statewide trailheads (``UtahTrailheads``, ~570 points, no key) for the
``ugrc`` places ingest. ``fetch_places`` stays the dashboard's demo fallback for
when Supabase holds no places yet.
"""

from __future__ import annotations

from typing import Any

import httpx

from backend.models import Coordinate, Place, PlaceRecord
from backend.providers.arcgis import query_features
from backend.seed_data import demo_places

TRAILHEADS_LAYER_URL = "https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/UtahTrailheads/FeatureServer/0"

# The layer's Comments field holds a primary-use code, e.g. "OVPrimaryUseMTB".
_PRIMARY_USE = {"MTB": "mountain biking", "HIKE": "hiking", "MULTI": "multi-use"}


def trailhead_record(feature: dict[str, Any]) -> PlaceRecord | None:
    """Normalize one trailhead feature. Features without a stable id, name or point are skipped."""
    attributes = feature.get("attributes") or {}
    geometry = feature.get("geometry") or {}
    trailhead_id = (attributes.get("TrailheadID") or "").strip()
    name = (attributes.get("PrimaryName") or "").strip()
    if not trailhead_id or not name or geometry.get("x") is None or geometry.get("y") is None:
        return None

    use = _PRIMARY_USE.get((attributes.get("Comments") or "").removeprefix("OVPrimaryUse"))
    details = [f"{use.capitalize()} trailhead" if use else "Trailhead"]
    if attributes.get("SeasonalRestriction"):
        details.append(f"Seasonal restriction: {attributes['SeasonalRestriction']}")
    if attributes.get("PrimaryMaintenance"):
        details.append(f"Maintained by {attributes['PrimaryMaintenance']}")

    return PlaceRecord(
        external_id=trailhead_id,
        name=name,
        category="trailhead",
        description=". ".join(details) + ".",
        coordinate=Coordinate(lat=float(geometry["y"]), lng=float(geometry["x"])),
        source_url=attributes.get("InfoURL") or None,
        tags=[tag for tag in ("trailhead", use) if tag],
        raw_payload=attributes,
    )


async def fetch_trailheads(client: httpx.AsyncClient | None = None) -> list[PlaceRecord]:
    """Every UGRC trailhead. Raises on upstream failure."""
    features = await query_features(TRAILHEADS_LAYER_URL, client=client)
    return [record for record in map(trailhead_record, features) if record is not None]


async def fetch_places() -> list[Place]:
    """Dashboard fallback while Supabase has no places: demo data."""
    return demo_places()
