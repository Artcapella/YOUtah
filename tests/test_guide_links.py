import pytest

from backend.guide.links import (
    ENSIGN_PEAK_URL,
    LIBRARY_EVENTS_URL,
    MAIN_LIBRARY_URL,
    WHALE_URL,
    normalize_source_url,
)
from backend.seed_data import demo_events, demo_places


def test_known_seed_links_use_actual_destination_pages() -> None:
    assert normalize_source_url("https://gis.utah.gov", title="Ensign Peak Trailhead") == ENSIGN_PEAK_URL
    assert normalize_source_url(None, entity_id="whale") == WHALE_URL
    assert normalize_source_url("https://library.utah.gov/", title="Salt Lake City Public Library") == MAIN_LIBRARY_URL


def test_library_calendar_link_is_repaired_without_copying_stale_paths() -> None:
    assert normalize_source_url("https://www.slcpl.org/events") == LIBRARY_EVENTS_URL
    assert normalize_source_url("https://www.slcpl.org/unknown-legacy-page") is None


def test_specific_listing_url_is_not_replaced_by_a_generic_calendar() -> None:
    listing = "https://www.ticketmaster.com/real-event/event/1A00234"
    assert normalize_source_url(listing, title="Live music tonight") == listing


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "data:text/html,test", "file:///tmp/page", "http://localhost/",
    "https://127.0.0.1/", "https://192.168.1.1/", "https://host.internal/",
    "https://user:password@example.com/", "https://example.com:wrong/", "https://example.com\\@localhost/",
    "https://example.com/\nunsafe", "not-a-link",
])
def test_unusable_source_urls_are_not_emitted(url: str) -> None:
    assert normalize_source_url(url) is None


def test_provider_urls_with_missing_scheme_are_normalized() -> None:
    assert normalize_source_url("www.example.com/trail?x=1&amp;y=2") == "https://www.example.com/trail?x=1&y=2"


def test_samples_do_not_claim_to_be_real_scheduled_free_events() -> None:
    assert all(event.title.startswith("Sample:") and event.source.startswith("Demo") for event in demo_events())
    assert all("check calendar" in event.price_label for event in demo_events())
    assert all(event.source_url and event.source_url != "https://www.ticketmaster.com" for event in demo_events())
    district = next(place for place in demo_places() if place.id == "granary-row")
    assert district.category == "community"
