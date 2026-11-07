from datetime import datetime, timedelta, timezone

from backend.models import Advisory, AirSnapshot, Coordinate, LocalEvent, Place, SourceHealth, WeatherSnapshot


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
        Place(id="ensign-peak", source="UGRC", source_url="https://gis.utah.gov", name="Ensign Peak Trailhead", category="trailhead", description="A close-to-downtown climb with a skyline view and a quick local-history lesson.", coordinate=Coordinate(lat=40.7901, lng=-111.8907), elevation_m=1672, tags=["viewpoint", "sunset", "walk"], updated_at=created),
        Place(id="red-butte", source="UGRC", source_url="https://gis.utah.gov", name="Red Butte Garden", category="park", description="Botanical garden, concerts, and a quiet foothill escape beside the University of Utah.", coordinate=Coordinate(lat=40.7659, lng=-111.8244), elevation_m=1495, tags=["garden", "music", "university"], updated_at=created),
        Place(id="slc-library", source="Utah State Library", source_url="https://library.utah.gov", name="Salt Lake City Public Library", category="library", description="A free downtown third place with views, events, study space, and a rooftop garden.", coordinate=Coordinate(lat=40.7608, lng=-111.8911), tags=["free", "events", "workspace"], updated_at=created),
        Place(id="whale", source="Curated", name="The Whale", category="art", description="A giant local landmark with surprisingly devoted fans in the heart of 9th & 9th.", coordinate=Coordinate(lat=40.7368, lng=-111.8632), tags=["local lore", "photo", "9th and 9th"], updated_at=created),
        Place(id="granary-row", source="OpenStreetMap", source_url="https://www.openstreetmap.org", name="Granary Row", category="food", description="A creative corridor for food, art, music, and the changing side of Salt Lake City.", coordinate=Coordinate(lat=40.7428, lng=-111.9064), tags=["food", "art", "neighborhood"], updated_at=created),
    ]


def demo_events() -> list[LocalEvent]:
    start = utcnow()
    return [
        LocalEvent(id="library-workshop", source="Salt Lake City Public Library", source_url="https://www.slcpl.org", title="Community creative workshop", venue="Salt Lake City Public Library", category="Community", starts_at=start + timedelta(hours=3), price_label="Free", coordinate=Coordinate(lat=40.7608, lng=-111.8911), is_free=True),
        LocalEvent(id="campus-event", source="University of Utah Campus Connect", source_url="https://campusconnect.utah.edu/events", title="Campus community event", venue="University of Utah", category="Campus", starts_at=start + timedelta(hours=5), price_label="Free", coordinate=Coordinate(lat=40.7649, lng=-111.8421), is_free=True),
        LocalEvent(id="local-show", source="Ticketmaster", source_url="https://www.ticketmaster.com", title="Live music tonight", venue="Downtown Salt Lake City", category="Music", starts_at=start + timedelta(hours=7), price_label="From $18", coordinate=Coordinate(lat=40.7616, lng=-111.8910), is_free=False),
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
