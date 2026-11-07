"""Generic ArcGIS REST feature-layer query (UGRC, UDOT and other Utah GIS layers).

Pass a *layer* URL, one ending in ``/FeatureServer/<n>`` or ``/MapServer/<n>``.
Schemas differ per layer, so callers map ``attributes`` themselves. Before wiring a
new layer to a job, confirm its metadata (``<layer>?f=json``) and its license.
"""

from __future__ import annotations

from typing import Any

import httpx

from backend.providers.http import UpstreamError, ingest_client, request_json

# Hosted layers cap a page at their maxRecordCount (2000 on ArcGIS Online by default).
PAGE_SIZE = 2000
# Guard against a layer that keeps reporting exceededTransferLimit.
MAX_FEATURES = 100_000


async def query_features(
    layer_url: str,
    *,
    where: str = "1=1",
    out_fields: str = "*",
    client: httpx.AsyncClient | None = None,
    page_size: int = PAGE_SIZE,
) -> list[dict[str, Any]]:
    """Every matching feature, following pagination, with point geometry in WGS84.

    ``outSR=4326`` matters: Utah's hosted layers are stored in Web Mercator, so
    without it ``geometry.x``/``y`` come back in meters, not longitude/latitude.
    Raises UpstreamError on HTTP failures and on ArcGIS's HTTP-200 error envelope.
    """
    if client is None:
        async with ingest_client() as owned:
            return await query_features(layer_url, where=where, out_fields=out_fields, client=owned, page_size=page_size)

    features: list[dict[str, Any]] = []
    while True:
        payload = await request_json(
            client,
            "GET",
            f"{layer_url.rstrip('/')}/query",
            label="ArcGIS",
            params={
                "where": where,
                "outFields": out_fields,
                "returnGeometry": "true",
                "outSR": 4326,
                "resultOffset": len(features),
                "resultRecordCount": page_size,
                "f": "json",
            },
        )
        if "error" in payload:
            raise UpstreamError(f"ArcGIS error from {layer_url}: {payload['error']}")
        page = payload.get("features") or []
        features.extend(page)
        if not page or not payload.get("exceededTransferLimit") or len(features) >= MAX_FEATURES:
            return features
