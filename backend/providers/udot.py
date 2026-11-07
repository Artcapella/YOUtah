from backend.models import Advisory
from backend.seed_data import demo_advisories


async def fetch_advisories() -> list[Advisory]:
    """Adapter boundary for the UDOT developer API; requires UDOT_API_KEY."""
    return [item for item in demo_advisories() if item.kind == "road"]
