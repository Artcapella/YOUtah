"""Supabase clients.

Two clients, least privilege first:

* ``get_public_client`` uses the publishable key, so every query is subject to the
  Row Level Security policies in ``supabase/migrations``. Use it for all reads that
  back public API responses.
* ``get_service_client`` uses the secret key and bypasses RLS. Use it only for
  server-side writes (ingestion). Never return it, or anything derived from its
  key, to the browser.

These are the synchronous clients: they hold no event-loop state, so they are safe
to cache for the life of the process (uvicorn, Vercel, tests). Call ``.execute()``
through ``asyncio.to_thread`` from async code.
"""

from functools import lru_cache

from supabase import Client, ClientOptions, create_client

from backend.config import settings

# Server-side clients have no user session to persist or refresh.
_SERVER_OPTIONS = dict(auto_refresh_token=False, persist_session=False, postgrest_client_timeout=8)


@lru_cache
def get_public_client() -> Client | None:
    if not settings.supabase_read_configured:
        return None
    return create_client(settings.supabase_url, settings.supabase_publishable_key, ClientOptions(**_SERVER_OPTIONS))


@lru_cache
def get_service_client() -> Client | None:
    """Return a server-only client; browser clients must never receive this key."""
    if not settings.supabase_configured:
        return None
    return create_client(settings.supabase_url, settings.supabase_secret_key, ClientOptions(**_SERVER_OPTIONS))
