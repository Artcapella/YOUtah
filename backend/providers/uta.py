"""UTA's official GTFS-Realtime service-alert feed."""

from __future__ import annotations

import httpx

from backend.models import Advisory

UTA_ALERTS_URL = "https://apps.rideuta.com/tms/gtfs/Alert"


def _text(translations) -> str:
    return " ".join(item.text for item in translations if getattr(item, "text", "")).strip()


def parse_transit_alerts(feed) -> list[Advisory]:
    """Normalize a decoded GTFS-RT FeedMessage; kept separate for fixture-based tests."""
    result = []
    for entity in feed.entity:
        if not entity.HasField("alert"):
            continue
        alert = entity.alert
        title = _text(alert.header_text.translation) or "UTA service alert"
        body = _text(alert.description_text.translation) or title
        effect = int(alert.effect) if alert.HasField("effect") else 0
        severity = "high" if effect in {3, 4, 5, 6} else "medium"
        result.append(Advisory(id=f"uta-{entity.id}", kind="transit", severity=severity, title=title[:240], body=body,
                               source="Utah Transit Authority", source_url="https://www.rideuta.com/Rider-Info/Service-Alerts"))
    return result


async def fetch_transit_alerts() -> list[Advisory]:
    """Fetch and decode the public protobuf feed. Failure propagates so ingestion is marked failed."""
    try:
        from google.transit import gtfs_realtime_pb2
    except ImportError as error:
        raise RuntimeError("gtfs-realtime-bindings must be installed to ingest UTA alerts") from error
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), headers={"Accept": "application/x-protobuf"}) as client:
        response = await client.get(UTA_ALERTS_URL)
        response.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(response.content)
    return parse_transit_alerts(feed)
