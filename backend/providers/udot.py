"""UDOT Traffic alerts adapter (https://www.udottraffic.utah.gov/api/v2/get/alerts)."""

from __future__ import annotations

from backend.config import settings
from backend.models import Advisory
from backend.providers.http import ingest_client, request_json

UDOT_ALERTS_URL = "https://www.udottraffic.utah.gov/api/v2/get/alerts"


def parse_advisories(payload: object) -> list[Advisory]:
    rows = payload.get("Alerts", payload.get("alerts", payload)) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    advisories = []
    for row in rows:
        if not isinstance(row, dict) or row.get("Id", row.get("id")) is None:
            continue
        message = str(row.get("Message", row.get("message", "Road advisory"))).strip()
        notes = str(row.get("Notes", row.get("notes", ""))).strip()
        high = row.get("HighImportance", row.get("highImportance", False))
        advisories.append(Advisory(
            id=f"udot-{row.get('Id', row.get('id'))}", kind="road", severity="high" if high else "medium",
            title=message[:240] or "UDOT road advisory", body=notes or message,
            source="UDOT Traffic", source_url="https://www.udottraffic.utah.gov/List/Alerts",
        ))
    return advisories


async def fetch_advisories() -> list[Advisory]:
    """Return live statewide UDOT alerts. An absent key is an empty lane, never demo data."""
    if not settings.udot_api_key:
        return []
    async with ingest_client(timeout_seconds=10.0) as client:
        payload = await request_json(client, "GET", UDOT_ALERTS_URL, label="UDOT alerts",
                                     params={"key": settings.udot_api_key, "format": "json"}, retries=1)
    return parse_advisories(payload)
