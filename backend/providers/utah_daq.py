from backend.models import AirSnapshot, Coordinate
from backend.seed_data import demo_air


async def fetch_air_quality(_: Coordinate) -> AirSnapshot:
    """Adapter boundary for Utah DAQ. Replace demo_air with validated DAQ API parsing."""
    return demo_air()
