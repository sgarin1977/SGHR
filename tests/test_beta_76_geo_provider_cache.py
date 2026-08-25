from pathlib import Path

import pytest

from services import geo_provider


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return []


class FakeClient:
    def __init__(self):
        self.calls = []

    async def get(
        self,
        url,
        *,
        params,
        headers,
        timeout,
    ):
        self.calls.append(
            {
                "url": url,
                "params": params,
            }
        )
        return FakeResponse()


@pytest.mark.asyncio
async def test_identical_geo_queries_share_cache(
    monkeypatch,
):
    clear_cache = getattr(
        geo_provider,
        "clear_geo_response_cache",
    )
    clear_cache()

    now = [100.0]
    monkeypatch.setattr(
        geo_provider,
        "monotonic",
        lambda: now[0],
    )
    monkeypatch.setattr(
        geo_provider,
        "GEO_RESPONSE_CACHE_TTL_SECONDS",
        10.0,
    )

    first_client = FakeClient()
    second_client = FakeClient()

    first = geo_provider.NominatimGeoProvider(
        base_url="https://cache.test",
        client=first_client,
    )
    second = geo_provider.NominatimGeoProvider(
        base_url="https://cache.test",
        client=second_client,
    )

    assert await first.search(
        query="Kyiv",
        language="en",
    ) == []

    now[0] = 105.0

    assert await second.search(
        query="Kyiv",
        language="en",
    ) == []

    assert len(first_client.calls) == 1
    assert second_client.calls == []

    now[0] = 111.0

    assert await second.search(
        query="Kyiv",
        language="en",
    ) == []

    assert len(second_client.calls) == 1


@pytest.mark.asyncio
async def test_geo_cache_is_bounded(
    monkeypatch,
):
    clear_cache = getattr(
        geo_provider,
        "clear_geo_response_cache",
    )
    clear_cache()

    monkeypatch.setattr(
        geo_provider,
        "monotonic",
        lambda: 100.0,
    )
    monkeypatch.setattr(
        geo_provider,
        "GEO_RESPONSE_CACHE_TTL_SECONDS",
        60.0,
    )
    monkeypatch.setattr(
        geo_provider,
        "GEO_RESPONSE_CACHE_MAX_ENTRIES",
        2,
    )

    client = FakeClient()
    provider = geo_provider.NominatimGeoProvider(
        base_url="https://bounded-cache.test",
        client=client,
    )

    for query in ("Kyiv", "Lviv", "Odesa"):
        await provider.search(
            query=query,
            language="en",
        )

    assert len(client.calls) == 3

    # Kyiv was the oldest entry and must
    # have been evicted.
    await provider.search(
        query="Kyiv",
        language="en",
    )

    assert len(client.calls) == 4


def test_geo_cache_settings_are_documented():
    source = Path(".env.example").read_text(
        encoding="utf-8-sig"
    )

    assert (
        "NOMINATIM_CACHE_TTL_SECONDS=3600"
        in source
    )
    assert (
        "NOMINATIM_CACHE_MAX_ENTRIES=512"
        in source
    )
