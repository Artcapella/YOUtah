"""Shared HTTP plumbing for provider adapters that run in batch ingestion.

Request-path adapters (NWS, DAQ, Ticketmaster) keep their own short timeouts and
never retry, so a slow upstream cannot stall the dashboard. Ingestion jobs use
``request_json``: a small, honest retry policy for transient upstream failures.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from backend.config import settings

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER_SECONDS = 30.0


class UpstreamError(RuntimeError):
    """An upstream API returned an unexpected response."""


def ingest_client(timeout_seconds: float = 30.0, **headers: str) -> httpx.AsyncClient:
    """An AsyncClient that identifies this project to the upstream (see NWS_USER_AGENT)."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_seconds),
        headers={"User-Agent": settings.nws_user_agent, "Accept": "application/json", **headers},
    )


def _retry_delay(response: httpx.Response | None, attempt: int, backoff_seconds: float) -> float:
    retry_after = response.headers.get("Retry-After") if response is not None else None
    if retry_after and retry_after.isdigit():
        return min(float(retry_after), MAX_RETRY_AFTER_SECONDS)
    return backoff_seconds * 2**attempt


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    label: str,
    retries: int = 2,
    backoff_seconds: float = 1.0,
    **kwargs: Any,
) -> Any:
    """Send a request and decode JSON, retrying 429/5xx and transport errors.

    Error messages carry the URL *without* its query string, so a key passed as a
    query parameter never reaches logs, ``ingest_runs.error_message`` or API responses.
    """
    response: httpx.Response | None = None
    for attempt in range(retries + 1):
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TransportError as error:
            if attempt == retries:
                raise UpstreamError(f"{label}: {type(error).__name__} calling {httpx.URL(url).copy_with(query=None)}") from error
            await asyncio.sleep(_retry_delay(None, attempt, backoff_seconds))
            continue
        if response.status_code not in RETRY_STATUSES or attempt == retries:
            break
        await asyncio.sleep(_retry_delay(response, attempt, backoff_seconds))

    assert response is not None
    if not response.is_success:
        raise UpstreamError(f"{label}: {response.status_code} from {response.url.copy_with(query=None)}: {response.text[:300]}")
    try:
        return response.json()
    except ValueError as error:
        raise UpstreamError(f"{label}: non-JSON response from {response.url.copy_with(query=None)}") from error
