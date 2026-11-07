from backend.models import Advisory
from backend.seed_data import demo_advisories


async def fetch_transit_alerts() -> list[Advisory]:
    """Adapter boundary for UTA GTFS-Realtime alerts."""
    return [item for item in demo_advisories() if item.kind == "transit"]
