from datetime import datetime, timedelta, timezone

from backend.models import Advisory, AirSnapshot, Coordinate, LocalEvent, Place, SourceHealth, WeatherSnapshot

# Canonical official pages, shared with the guide's citation repair for old data.
# Keep this independent of backend.guide's package initializer to avoid a cycle
# through guide -> dashboard -> provider -> seed_data.
ENSIGN_PEAK_URL = "https://www.nps.gov/places/ensign-peak-memorial-garden-and-ensign-peak-nature-park.htm"
RED_BUTTE_URL = "https://redbuttegarden.org/plan-your-garden-visit/"
MAIN_LIBRARY_URL = "https://services.slcpl.org/main-library"
WHALE_URL = "https://saltlakepublicart.org/art/out-of-the-blue/"
GRANARY_URL = "https://www.thegranarydistrict.com/"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def demo_weather() -> WeatherSnapshot:
    return WeatherSnapshot(
        temperature_f=46,
        short_forecast="Partly cloudy",
        wind="NW 6 mph",
        source="demo",
        updated_at=utcnow(),
    )


def demo_air() -> AirSnapshot:
    return AirSnapshot(
        aqi=71,
        category="Moderate",
        pm25=21.4,
        inversion_estimate_m=1600,
        source="demo",
        updated_at=utcnow(),
    )


def demo_places() -> list[Place]:
    created = utcnow()
    return [
        Place(id="ensign-peak", source="National Park Service", source_url=ENSIGN_PEAK_URL, name="Ensign Peak Trailhead", category="trailhead", description="A popular viewpoint hike above downtown, with routes to an overlook and the summit.", coordinate=Coordinate(lat=40.7901, lng=-111.8907), elevation_m=1672, tags=["viewpoint", "hiking", "popular"], updated_at=created),
        Place(id="red-butte", source="Red Butte Garden", source_url=RED_BUTTE_URL, name="Red Butte Garden", category="park", description="Botanical gardens and natural areas beside the University of Utah. Check the visitor page for admission and seasonal hours.", coordinate=Coordinate(lat=40.7659, lng=-111.8244), elevation_m=1495, tags=["garden", "music", "university"], updated_at=created),
        Place(id="slc-library", source="Salt Lake City Public Library", source_url=MAIN_LIBRARY_URL, name="Salt Lake City Public Library", category="library", description="A downtown community space with books, public programs, and Library Square. Check the library's site for current access and hours.", coordinate=Coordinate(lat=40.7594, lng=-111.8848), tags=["free", "events", "workspace"], updated_at=created),
        Place(id="whale", source="Salt Lake City Public Art Program", source_url=WHALE_URL, name="The Whale", category="art", description="Out of the Blue, the whale sculpture at 900 South and 1100 East, is a neighborhood public-art landmark.", coordinate=Coordinate(lat=40.7497, lng=-111.8598), tags=["local lore", "photo", "9th and 9th"], updated_at=created),
        Place(id="granary-row", source="Granary District Alliance", source_url=GRANARY_URL, name="Granary District", category="community", description="A Salt Lake neighborhood with restaurants, makers, breweries, and public art. This is a district to explore, not an individual restaurant or a verified budget meal.", coordinate=Coordinate(lat=40.7428, lng=-111.9064), tags=["art", "neighborhood", "community"], updated_at=created),
    ]


def demo_events() -> list[LocalEvent]:
    """Clearly marked UI samples. The linked calendars do not verify these events."""
    start = utcnow()
    return [
        LocalEvent(id="library-workshop", source="Demo — City Library event calendar", source_url="https://events.slcpl.org/", title="Sample: community creative workshop", venue="Salt Lake City Public Library", category="Community", starts_at=start + timedelta(hours=3), price_label="Sample only — check calendar", coordinate=Coordinate(lat=40.7594, lng=-111.8848), is_free=False),
        LocalEvent(id="campus-event", source="Demo — University of Utah event calendar", source_url="https://campusconnect.utah.edu/events", title="Sample: campus community event", venue="University of Utah", category="Campus", starts_at=start + timedelta(hours=5), price_label="Sample only — check calendar", coordinate=Coordinate(lat=40.7649, lng=-111.8421), is_free=False),
        LocalEvent(id="local-show", source="Demo — Visit Salt Lake event calendar", source_url="https://www.visitsaltlake.com/events/", title="Sample: local music event", venue="Salt Lake City", category="Music", starts_at=start + timedelta(hours=7), price_label="Sample only — check calendar", coordinate=Coordinate(lat=40.7616, lng=-111.8910), is_free=False),
    ]


def demo_advisories() -> list[Advisory]:
    return [
        Advisory(id="demo-road", kind="road", severity="low", title="Check canyon road conditions", body="Use UDOT before departing; conditions can change rapidly above the valley.", source="UDOT Traffic", source_url="https://udottraffic.utah.gov"),
        Advisory(id="demo-transit", kind="transit", severity="low", title="Ski bus and rail data available", body="Live UTA arrivals and service alerts will appear when the GTFS adapter is enabled.", source="Utah Transit Authority", source_url="https://rideuta.com"),
    ]


def demo_sources(weather_state: str = "demo") -> list[SourceHealth]:
    timestamp = utcnow()
    return [
        SourceHealth(id="nws", label="National Weather Service", status=weather_state, updated_at=timestamp, href="https://weather.gov"),
        SourceHealth(id="daq", label="Utah DAQ", status="demo", updated_at=timestamp, href="https://air.utah.gov"),
        SourceHealth(id="udot", label="UDOT Traffic", status="demo", updated_at=timestamp, href="https://udottraffic.utah.gov"),
        SourceHealth(id="uta", label="UTA", status="demo", updated_at=timestamp, href="https://rideuta.com"),
        SourceHealth(id="ugrc", label="UGRC", status="demo", updated_at=timestamp, href="https://gis.utah.gov"),
    ]
