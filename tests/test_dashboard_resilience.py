import asyncio

from backend.services.dashboard import _stored_or_provider


def test_dashboard_provider_failure_uses_labeled_demo_lane() -> None:
    async def unavailable():
        raise RuntimeError("upstream offline")

    items, from_storage = asyncio.run(_stored_or_provider([], unavailable, lambda: ["demo-place"]))

    assert items == ["demo-place"]
    assert from_storage is False
