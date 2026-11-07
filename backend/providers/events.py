from backend.models import LocalEvent
from backend.seed_data import demo_events


async def fetch_events() -> list[LocalEvent]:
    """Adapter boundary for Ticketmaster, Campus Connect ICS, and library feeds."""
    return demo_events()
