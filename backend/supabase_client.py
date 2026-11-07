from functools import lru_cache

from supabase import Client, create_client

from backend.config import settings


@lru_cache
def get_service_client() -> Client | None:
    """Return a server-only client; browser clients must never receive this key."""
    if not settings.supabase_configured:
        return None
    return create_client(settings.supabase_url, settings.supabase_service_role_key)
