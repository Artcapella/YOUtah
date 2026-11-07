from backend.models import Place
from backend.seed_data import demo_places


async def fetch_places() -> list[Place]:
    """Adapter boundary for UGRC ArcGIS feature services and Open Source Places."""
    return demo_places()
