import ast
from pathlib import Path

import pytest

from services.geo_provider import (
    NominatimGeoProvider,
)


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
                "headers": headers,
                "timeout": timeout,
            }
        )
        return FakeResponse()


@pytest.mark.asyncio
async def test_geo_provider_reuses_injected_client():
    client = FakeClient()
    provider = NominatimGeoProvider(
        base_url="https://geo.test",
        user_agent="SGHR tests",
        timeout_seconds=7,
        client=client,
    )

    assert await provider.search(
        query="Kyiv",
        language="en",
    ) == []
    assert await provider.search(
        query="Lviv",
        language="en",
    ) == []

    assert len(client.calls) == 2
    assert {
        call["timeout"]
        for call in client.calls
    } == {7.0}
    assert {
        call["headers"]["User-Agent"]
        for call in client.calls
    } == {"SGHR tests"}


def test_geo_provider_does_not_create_client_per_call():
    source = Path(
        "services/geo_provider.py"
    ).read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    provider = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name
        == "NominatimGeoProvider"
    )

    block = (
        ast.get_source_segment(
            source,
            provider,
        )
        or ""
    )

    assert (
        "async with httpx.AsyncClient"
        not in block
    )


def test_bot_closes_geo_http_pool():
    source = Path("bot.py").read_text(
        encoding="utf-8-sig"
    )
    tree = ast.parse(source)

    close_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and isinstance(
            node.value.func,
            ast.Name,
        )
        and node.value.func.id
        == "close_geo_http_client"
    ]

    assert len(close_calls) == 1
    assert "close_geo_http_client" in source
