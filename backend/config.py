from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env.local", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"

    # Supabase. The first alias is the current name; the second keeps older
    # .env.local / Vercel variable names working.
    supabase_url: str = Field(default="", validation_alias=AliasChoices("SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL"))
    # Publishable (formerly "anon") key: browser-safe, every query is subject to RLS.
    supabase_publishable_key: str = Field(
        default="", validation_alias=AliasChoices("SUPABASE_PUBLISHABLE_KEY", "NEXT_PUBLIC_SUPABASE_ANON_KEY")
    )
    # Secret (formerly "service_role") key: bypasses RLS. Server-side ingestion only.
    supabase_secret_key: str = Field(
        default="", validation_alias=AliasChoices("SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY")
    )

    maptiler_key: str = Field(default="", validation_alias="NEXT_PUBLIC_MAPTILER_KEY")
    ticketmaster_api_key: str = ""
    eventbrite_token: str = ""
    udot_api_key: str = ""
    nps_api_key: str = ""
    ingest_secret: str = ""
    # Vercel Cron sends `Authorization: Bearer $CRON_SECRET` to scheduled routes.
    cron_secret: str = ""
    nws_user_agent: str = "ComprehensiveUtah/0.1 contact@example.com"
    cors_origins_raw: str = "http://localhost:8000,http://127.0.0.1:8000"

    # Outdoor Comfort model. Only an artifact that passed the training gate is ever written here.
    outdoor_comfort_model_path: str = "backend/intelligence/artifacts/outdoor_comfort.json"
    intelligence_enable_trained_model: bool = True

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]

    @property
    def supabase_read_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_publishable_key)

    @property
    def supabase_configured(self) -> bool:
        """True when the server can write (ingestion)."""
        return bool(self.supabase_url and self.supabase_secret_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
