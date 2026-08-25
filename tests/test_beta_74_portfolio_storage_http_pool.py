import ast
from pathlib import Path

import pytest

from services.portfolio_storage import (
    SupabasePortfolioStorage,
)


class FakeResponse:
    def __init__(self, payload=None):
        self.payload = payload or {}

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
        headers,
        timeout,
        content=None,
        json=None,
    ):
        self.calls.append(
            {
                "method": "POST",
                "url": url,
                "headers": headers,
                "timeout": timeout,
                "content": content,
                "json": json,
            }
        )

        if "/object/sign/" in url:
            return FakeResponse(
                {
                    "signedURL": (
                        "/storage/v1/object/"
                        "sign/test"
                    )
                }
            )

        return FakeResponse()

    async def request(
        self,
        method,
        url,
        *,
        headers,
        timeout,
        json,
    ):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "timeout": timeout,
                "json": json,
            }
        )
        return FakeResponse()


@pytest.mark.asyncio
async def test_portfolio_storage_reuses_injected_client():
    client = FakeClient()
    storage = SupabasePortfolioStorage(
        base_url="https://storage.test",
        service_role_key="secret",
        bucket="portfolio",
        timeout_seconds=11,
        client=client,
    )

    await storage.upload(
        storage_path="user/item.pdf",
        content=b"%PDF-test",
        mime_type="application/pdf",
    )
    signed_url = await storage.create_signed_url(
        storage_path="user/item.pdf",
    )
    await storage.delete(
        storage_path="user/item.pdf",
    )

    assert signed_url == (
        "https://storage.test"
        "/storage/v1/object/sign/test"
    )
    assert [
        call["method"]
        for call in client.calls
    ] == [
        "POST",
        "POST",
        "DELETE",
    ]
    assert {
        call["timeout"]
        for call in client.calls
    } == {11}


def test_portfolio_storage_does_not_create_client_per_call():
    source = Path(
        "services/portfolio_storage.py"
    ).read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    storage = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name
        == "SupabasePortfolioStorage"
    )

    block = (
        ast.get_source_segment(
            source,
            storage,
        )
        or ""
    )

    assert (
        "async with httpx.AsyncClient"
        not in block
    )


def test_bot_closes_portfolio_storage_http_pool():
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
        == (
            "close_portfolio_storage_"
            "http_client"
        )
    ]

    assert len(close_calls) == 1
    assert (
        "close_portfolio_storage_http_client"
        in source
    )
