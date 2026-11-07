from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env.local", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    supabase_url: str = Field(default="", validation_alias="NEXT_PUBLIC_SUPABASE_URL")
    supabase_anon_key: str = Field(default="", validation_alias="NEXT_PUBLIC_SUPABASE_ANON_KEY")
    supabase_service_role_key: str = ""
    maptiler_key: str = Field(default="", validation_alias="NEXT_PUBLIC_MAPTILER_KEY")
    ticketmaster_api_key: str = ""
    eventbrite_token: str = ""
    udot_api_key: str = ""
    nps_api_key: str = ""
    ingest_secret: str = ""
    nws_user_agent: str = "ComprehensiveUtah/0.1 contact@example.com"
    cors_origins_raw: str = "http://localhost:8000,http://127.0.0.1:8000"

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
