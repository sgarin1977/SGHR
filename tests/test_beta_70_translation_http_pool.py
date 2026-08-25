import ast
from pathlib import Path

import pytest

from services.translation import (
    LibreTranslateProvider,
)


SOURCE_PATH = Path(
    "services/translation.py"
)


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self):
        self.calls = []

    async def post(
        self,
        url,
        *,
        json,
        timeout,
    ):
        self.calls.append(
            {
                "url": url,
                "json": json,
                "timeout": timeout,
            }
        )

        if url.endswith("/detect"):
            return FakeResponse(
                [
                    {
                        "language": "uk",
                        "confidence": 0.99,
                    }
                ]
            )

        return FakeResponse(
            {
                "translatedText": "Hello",
            }
        )


@pytest.mark.asyncio
async def test_provider_reuses_injected_client():
    client = FakeClient()
    provider = LibreTranslateProvider(
        base_url="https://translate.test",
        timeout_seconds=7,
        client=client,
    )

    detected = await provider.detect_language(
        text="Привіт",
    )
    translated = await provider.translate(
        text="Привіт",
        source_language="uk",
        target_language="en",
    )

    assert detected == "uk"
    assert translated == "Hello"
    assert len(client.calls) == 2
    assert {
        id(client)
    } == {
        id(provider.client)
    }
    assert all(
        call["timeout"] == 7
        for call in client.calls
    )


def test_provider_does_not_create_client_per_call():
    source = SOURCE_PATH.read_text(
        encoding="utf-8-sig"
    )
    tree = ast.parse(source)

    provider = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name
        == "LibreTranslateProvider"
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
    assert (
        "_get_translation_http_client"
        in block
    )
    assert (
        "close_translation_http_client"
        in source
    )


def test_bot_closes_translation_http_pool():
    source = Path("bot.py").read_text(
        encoding="utf-8-sig"
    )

    assert (
        "close_translation_http_client"
        in source
    )
    tree = ast.parse(source)

    close_awaits = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and isinstance(
            node.value.func,
            ast.Name,
        )
        and node.value.func.id
        == "close_translation_http_client"
    ]

    assert len(close_awaits) == 1
