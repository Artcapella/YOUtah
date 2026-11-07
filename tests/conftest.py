"""Keep the suite offline: no test may reach Supabase or write to it."""

import pytest

from backend.intelligence import model as model_module


@pytest.fixture(autouse=True)
def _offline_supabase(monkeypatch):
    monkeypatch.setattr("backend.supabase_client.get_service_client", lambda: None)
    monkeypatch.setattr("backend.intelligence.service.get_service_client", lambda: None)
    monkeypatch.setattr("backend.services.ingest.get_service_client", lambda: None)
    monkeypatch.setattr("backend.supabase_client.get_public_client", lambda: None)
    monkeypatch.setattr("backend.repository.get_public_client", lambda: None)


@pytest.fixture(autouse=True)
def _fresh_model_cache():
    model_module.load_model.cache_clear()
    yield
    model_module.load_model.cache_clear()
