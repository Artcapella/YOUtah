"""Normalize citation links without fetching arbitrary URLs during a chat request.

This validates URL shape, not availability. Canonical replacements below were
checked against the named destination's official website; unknown listing paths
are preserved rather than guessed.
"""

from __future__ import annotations

import html
import ipaddress
import re
from urllib.parse import quote, urlsplit, urlunsplit


ENSIGN_PEAK_URL = "https://www.nps.gov/places/ensign-peak-memorial-garden-and-ensign-peak-nature-park.htm"
RED_BUTTE_URL = "https://redbuttegarden.org/plan-your-garden-visit/"
MAIN_LIBRARY_URL = "https://services.slcpl.org/main-library"
WHALE_URL = "https://saltlakepublicart.org/art/out-of-the-blue/"
GRANARY_URL = "https://www.thegranarydistrict.com/"
LIBRARY_EVENTS_URL = "https://events.slcpl.org/"
CAMPUS_EVENTS_URL = "https://campusconnect.utah.edu/events"
SALT_LAKE_EVENTS_URL = "https://www.visitsaltlake.com/events/"

# Limit corrections to known demo records and their old generic source pages.
# A real event with a provider-specific listing must keep that listing.
_DESTINATIONS = {
    "ensign-peak": ENSIGN_PEAK_URL,
    "red-butte": RED_BUTTE_URL,
    "slc-library": MAIN_LIBRARY_URL,
    "whale": WHALE_URL,
    "granary-row": GRANARY_URL,
    "library-workshop": LIBRARY_EVENTS_URL,
    "campus-event": CAMPUS_EVENTS_URL,
    "local-show": SALT_LAKE_EVENTS_URL,
}
_LEGACY_TITLES = {
    "ensign peak trailhead": "ensign-peak",
    "red butte garden": "red-butte",
    "salt lake city public library": "slc-library",
    "the whale": "whale",
    "granary row": "granary-row",
    "granary district": "granary-row",
    "community creative workshop": "library-workshop",
    "campus community event": "campus-event",
    "live music tonight": "local-show",
}
_GENERIC_SOURCE_HOSTS = {
    "gis.utah.gov", "library.utah.gov", "slcpl.org", "www.slcpl.org",
    "openstreetmap.org", "www.openstreetmap.org", "ticketmaster.com", "www.ticketmaster.com",
}


def normalize_source_url(value: str | None, *, title: str = "", entity_id: str | None = None) -> str | None:
    """Return a usable public HTTP(S) URL, or None when one wasn't supplied.

    Known demo IDs can gain an official source; arbitrary missing links stay
    missing. In particular, this never invents a destination from a title slug.
    """
    record_id = (entity_id or "").removeprefix("place:").removeprefix("event:")
    if record_id in _DESTINATIONS:
        return _DESTINATIONS[record_id]

    if not isinstance(value, str) or not value.strip():
        return None
    candidate = html.unescape(value.strip())
    if re.search(r"[\x00-\x20\x7f\\]", candidate):
        return None
    if candidate.startswith("//"):
        candidate = "https:" + candidate
    elif re.match(r"^(?:www\.)?[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+(?:/|$)", candidate):
        candidate = "https://" + candidate
    try:
        parts = urlsplit(candidate)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port  # raises for malformed or out-of-range ports
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not host or parts.username is not None or parts.password is not None:
        return None
    if "." not in host or host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid")):
        return None
    try:
        if not ipaddress.ip_address(host).is_global:
            return None
    except ValueError:
        if not re.fullmatch(r"[a-zA-Z0-9.-]+", host) or ".." in host:
            return None
    if port not in (None, 80, 443):
        return None

    key = _LEGACY_TITLES.get(title.casefold().strip())
    if key and host in _GENERIC_SOURCE_HOSTS and parts.path in {"", "/"}:
        return _DESTINATIONS[key]

    # The library publishes its services and events at separate subdomains now.
    # Only these known routes can be repaired confidently; don't carry unknown
    # legacy paths onto a different website and create another broken link.
    if host in {"slcpl.org", "www.slcpl.org"}:
        if parts.path in {"", "/"}:
            return "https://services.slcpl.org/"
        if parts.path.rstrip("/") in {"/events", "/events/index"}:
            return LIBRARY_EVENTS_URL
        return None

    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parts.scheme.lower(), netloc, quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~"), parts.query, parts.fragment))
