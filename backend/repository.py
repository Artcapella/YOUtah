"""Read normalized Utah data from Supabase and map rows onto the API domain models.

All reads use the publishable-key client, so they see exactly what the RLS
policies expose publicly. Every loader returns an empty list when Supabase is not
configured, unreachable, or the schema has not been pushed yet; callers then fall
back to the provider adapters instead of failing the request.
"""

import asyncio
import logging
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any, Literal, TypeVar

from postgrest.exceptions import APIError
from pydantic import BaseModel, ValidationError

from backend.models import Advisory, Coordinate, LocalEvent, Place
from backend.supabase_client import get_public_client

logger = logging.getLogger(__name__)

SupabaseStatus = Literal["ok", "not_configured", "schema_missing", "error", "unreachable"]
ModelT = TypeVar("ModelT", bound=BaseModel)

# Attribution for rows written without a `sources` entry (hand-curated content).
CURATED_SOURCE = "Curated"

# Dashboard places lane: the nearest few of each category around the requested point.
PLACES_RADIUS_KM = 40.0
PLACES_PER_CATEGORY = 8


def _now() -> str:
    # Second precision with a Z suffix stays unambiguous inside PostgREST `or=(...)` filters.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _read(label: str, build: Callable[[Any], Any]) -> list[dict[str, Any]]:
    try:
        client = get_public_client()
        if client is None:
            return []
        response = await asyncio.to_thread(lambda: build(client).execute())
    except Exception as error:  # network, auth, or missing table must never break the dashboard
        logger.warning("Supabase read from %s failed, using provider fallback: %s", label, error)
        return []
    return response.data or []


async def _select(table: str, build: Callable[[Any], Any]) -> list[dict[str, Any]]:
    return await _read(table, lambda client: build(client.table(table)))


def _parse(model: type[ModelT], table: str, rows: Iterable[dict[str, Any]]) -> list[ModelT]:
    items = []
    for row in rows:
        try:
            items.append(model.model_validate(row))
        except ValidationError as error:
            logger.warning("Skipping %s row %s: %s", table, row.get("id"), error.errors(include_url=False))
    return items


def _source_name(row: dict[str, Any]) -> str:
    return (row.get("source") or {}).get("name") or CURATED_SOURCE


def _price_label(is_free: bool, price_low: float | None) -> str:
    if is_free:
        return "Free"
    if price_low is None:
        return "See listing"
    return f"From ${price_low:,.2f}".removesuffix(".00")


async def load_places(
    point: Coordinate, radius_km: float = PLACES_RADIUS_KM, per_category: int = PLACES_PER_CATEGORY
) -> list[Place]:
    """The nearest places to ``point``, at most ``per_category`` per category, nearest first.

    Uses the ``nearby_places`` SQL function (migration 20261107040000_place_sources).
    """
    rows = await _read(
        "nearby_places",
        lambda client: client.rpc(
            "nearby_places",
            {"lat": point.lat, "lng": point.lng, "radius_m": radius_km * 1000, "per_category": per_category},
        ),
    )
    return _parse(
        Place,
        "places",
        (
            {
                "id": row["id"],
                "source": row.get("source_name") or CURATED_SOURCE,
                "source_url": row.get("source_url") or row.get("website_url"),
                "name": row["name"],
                "category": row["category"],
                "description": row.get("description") or "",
                "coordinate": {"lat": row["latitude"], "lng": row["longitude"]},
                "elevation_m": row.get("elevation_m"),
                "tags": row.get("tags") or [],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ),
    )


async def load_upcoming_events(limit: int = 24) -> list[LocalEvent]:
    now = _now()
    rows = await _select(
        "events",
        lambda q: q.select(
            "id, title, category, starts_at, price_low, is_free, ticket_url, source_url, latitude, longitude, "
            "source:sources(name), venue:places!venue_place_id(name, latitude, longitude)"
        )
        .or_(f"starts_at.gte.{now},ends_at.gte.{now}")
        .order("starts_at")
        .limit(limit),
    )

    def to_event(row: dict[str, Any]) -> dict[str, Any]:
        venue = row.get("venue") or {}
        is_free = row.get("is_free") is True or row.get("price_low") == 0
        # Prefer the event's own point, then its venue's; rows with neither fail validation and are skipped.
        if row.get("latitude") is not None and row.get("longitude") is not None:
            coordinate = {"lat": row["latitude"], "lng": row["longitude"]}
        elif venue.get("latitude") is not None and venue.get("longitude") is not None:
            coordinate = {"lat": venue["latitude"], "lng": venue["longitude"]}
        else:
            coordinate = None
        return {
            "id": row["id"],
            "source": _source_name(row),
            "source_url": row.get("ticket_url") or row.get("source_url"),
            "title": row["title"],
            "venue": venue.get("name") or "Location TBA",
            "category": row.get("category") or "Event",
            "starts_at": row["starts_at"],
            "price_label": _price_label(is_free, row.get("price_low")),
            "coordinate": coordinate,
            "is_free": is_free,
        }

    return _parse(LocalEvent, "events", map(to_event, rows))


async def load_active_advisories(limit: int = 20) -> list[Advisory]:
    now = _now()
    rows = await _select(
        "advisories",
        lambda q: q.select("id, kind, severity, title, body, source_url, source:sources(name)")
        .or_(f"ends_at.is.null,ends_at.gte.{now}")
        .order("updated_at", desc=True)
        .limit(limit),
    )
    return _parse(
        Advisory,
        "advisories",
        (
            {
                "id": row["id"],
                "kind": row["kind"],
                # An unrated advisory is shown as medium rather than silently downplayed.
                "severity": row.get("severity") or "medium",
                "title": row["title"],
                "body": row.get("body") or "",
                "source": _source_name(row),
                "source_url": row.get("source_url"),
            }
            for row in rows
        ),
    )


async def check_connection() -> SupabaseStatus:
    """Cheap round trip for /api/health. Returns a status word only, never error details."""
    try:
        client = get_public_client()
        if client is None:
            return "not_configured"
        await asyncio.to_thread(lambda: client.table("sources").select("slug").limit(1).execute())
    except APIError as error:
        return "schema_missing" if error.code in {"PGRST205", "42P01"} else "error"
    except Exception:
        return "unreachable"
    return "ok"
