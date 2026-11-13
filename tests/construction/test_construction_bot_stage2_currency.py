"""Local currency HTTP boundary and shared settings UI regression."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
from pydantic import SecretStr
import pytest

from handlers.construction import construction_stage2_settings as handler
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_currency_bot_api import ConstructionCurrencyBotApi
from test_construction_bot_stage2_settings import language_data, screen, click


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "tenant", "version", "duplicate", "missing_default"])
async def test_currency_read_validates_response_context(fault):
    tenant, user = uuid4(), uuid4()
    data = {
        "tenant_id": str(tenant),
        "organization_default_currency": "EUR",
        "organization_updated_at": datetime.now(timezone.utc).isoformat(),
        "row_version": 7,
        "currencies": [
            {"code": "EUR", "name": "Euro"},
            {"code": "USD", "name": "US Dollar"},
        ],
    }
    if fault == "tenant":
        data["tenant_id"] = str(uuid4())
    elif fault == "version":
        data["row_version"] = True
    elif fault == "duplicate":
        data["currencies"].append(data["currencies"][0])
    elif fault == "missing_default":
        data["organization_default_currency"] = "UAH"
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={
            "data": data, "meta": {}, "request_id": "local",
        })
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        api = ConstructionApiClient(http=http)
        api.company_profile_capabilities = AsyncMock(return_value=NS(data={
            "tenant_id": str(tenant),
            "can_manage_settings": True, "can_manage_profile": True,
        }))
        session = PlatformApiClientSession(user, tenant, SecretStr("local"))
        entry = NS(
            _presentation=AsyncMock(return_value=(session, {"language": "uk", "name": "Org"})),
            api=NS(construction=api),
        )
        call = ConstructionCurrencyBotApi(entry).read(
            telegram_user_id=123, tenant_id=tenant, expected_user_id=user,
        )
        if fault:
            with pytest.raises(ConstructionApiClientError):
                await call
        else:
            result = await call
            assert result["language"] == "uk"
            assert result["item"]["organization_default_currency"] == "EUR"
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url.path == "/api/v1/construction/settings/currency"
        assert not calls[0].url.query


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "change", "noop", "settings_denied", "profile_denied",
    "actor_changed", "conflict", "invalid_response",
])
async def test_write_keeps_version_and_existing_authority(case):
    tenant, user = uuid4(), uuid4()
    calls = []
    def transport(request):
        calls.append(request)
        if case == "conflict":
            return httpx.Response(409, json={"error": {
                "code": "ORGANIZATION_SETTINGS_CONFLICT",
                "request_id": "local",
            }})
        return httpx.Response(200, json={
            "data": {
                "tenant_id": str(tenant),
                "organization_default_currency": "EUR",
                "organization_updated_at": datetime.now(timezone.utc).isoformat(),
                "row_version": 7 if case == "noop" else 8,
                "changed": case != "noop",
                **({"extra": "forbidden"} if case == "invalid_response" else {}),
            }, "meta": {}, "request_id": "local",
        })
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        api = ConstructionApiClient(http=http)
        api.company_profile_capabilities = AsyncMock(return_value=NS(data={
            "tenant_id": str(tenant),
            "can_manage_settings": case != "settings_denied",
            "can_manage_profile": case != "profile_denied",
        }))
        session = PlatformApiClientSession(user, tenant, SecretStr("local"))
        entry = NS(
            _presentation=AsyncMock(return_value=(session, {"language": "uk"})),
            api=NS(construction=api),
        )
        call = ConstructionCurrencyBotApi(entry).write(
            telegram_user_id=123, tenant_id=tenant,
            expected_user_id=uuid4() if case == "actor_changed" else user,
            currency_code="EUR", expected_row_version=7,
        )
        if case in ("change", "noop"):
            result = await call
            assert result["item"]["changed"] is (case == "change")
        else:
            with pytest.raises(ConstructionApiClientError) as caught:
                await call
            assert caught.value.status_code == (
                409 if case == "conflict" else
                502 if case == "invalid_response" else 403
            )
        if case in ("settings_denied", "profile_denied", "actor_changed"):
            assert calls == []
        else:
            assert len(calls) == 1
            request = calls[0]
            assert request.method == "PATCH"
            assert request.url.path == "/api/v1/construction/settings/currency"
            assert json.loads(request.content) == {
                "default_currency": "EUR", "expected_row_version": 7,
            }
            assert "idempotency-key" not in request.headers
            assert not request.url.query


@pytest.fixture
def currency_screen(screen, monkeypatch):
    c = screen
    c.currency_result = deepcopy(c.result)
    c.currency_result["item"] = {
        "tenant_id": c.result["item"]["tenant_id"],
        "organization_default_currency": "EUR",
        "organization_updated_at": c.result["item"]["organization_updated_at"],
        "row_version": 7,
        "currencies": [
            {"code": code, "name": code + " currency"}
            for code in (
                "EUR", "USD", "UAH", "GBP", "PLN",
                "CAD", "CHF", "JPY", "AUD", "BRL",
            )
        ],
    }
    c.currency = NS(
        read=AsyncMock(return_value=c.currency_result),
        write=AsyncMock(return_value={
            "item": {"changed": True}, "language": "uk",
        }),
    )
    monkeypatch.setattr(
        handler, "ConstructionCurrencyBotApi", lambda service: c.currency,
    )
    return c


async def open_currency(c):
    await handler.settings_callback(c.callback, c.state)
    rows = c.sent.await_args.kwargs["reply_markup"].inline_keyboard
    assert any(":currency:" in b.callback_data for row in rows for b in row)
    await click(c, "currency")


@pytest.mark.asyncio
async def test_currency_review_save_duplicate_and_language_switch(currency_screen):
    c = currency_screen
    await open_currency(c)
    await click(c, "c", 1)
    assert c.data[handler.VIEW]["selected_code"] == "USD"
    c.currency.write.assert_not_awaited()
    old = handler.cb(c.data[handler.VIEW], "save")
    await click(c, "save")
    c.currency.write.assert_awaited_once_with(
        telegram_user_id=123,
        tenant_id=UUID(c.result["item"]["tenant_id"]),
        expected_user_id=UUID(c.result["actor_user_id"]),
        expected_row_version=7, currency_code="USD",
    )
    c.callback.data = old
    await handler.settings_callback(c.callback, c.state)
    assert c.currency.write.await_count == 1
    await click(c, "languages")
    assert c.data[handler.VIEW]["resource"] == "language"
    assert c.data[handler.VIEW]["choice_codes"] == ["uk", "ru", "en"]
    c.protocol.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_currency_pagination_and_reorder_pin_code(currency_screen):
    c = currency_screen
    await open_currency(c)
    await click(c, "p", 1)
    assert c.data[handler.VIEW]["choice_codes"] == ["AUD", "BRL"]
    c.currency_result["item"]["currencies"].reverse()
    await click(c, "c", 0)
    assert c.data[handler.VIEW]["selected_code"] == "AUD"
    c.currency.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_refreshes_currency_without_write(currency_screen):
    c = currency_screen
    await open_currency(c)
    await click(c, "c", 1)
    await click(c, "refresh")
    assert c.data[handler.VIEW]["phase"] == "menu"
    assert c.data[handler.VIEW]["resource"] == "currency"
    assert "EUR" in c.sent.await_args.kwargs["text"]
    c.currency.write.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["readonly", "version", "timeout", "late"])
async def test_currency_failures_do_not_bypass_review_or_context(currency_screen, failure):
    c = currency_screen
    await open_currency(c)
    if failure == "readonly":
        c.currency_result["can_manage"] = False
        await click(c, "currency")
        rows = c.sent.await_args.kwargs["reply_markup"].inline_keyboard
        assert not any(":c:" in b.callback_data for row in rows for b in row)
    elif failure == "version":
        c.currency_result["item"]["row_version"] = 8
        await click(c, "c", 1)
        assert c.data[handler.VIEW]["phase"] == "blocked"
    elif failure == "timeout":
        await click(c, "c", 1)
        c.currency.write.side_effect = ConstructionApiClientError(
            status_code=504, code="api_timeout",
        )
        await click(c, "save")
        assert c.data[handler.VIEW]["phase"] == "blocked"
        rows = c.sent.await_args.kwargs["reply_markup"].inline_keyboard
        assert not any(":save:" in b.callback_data for row in rows for b in row)
        await click(c, "save")
        assert c.currency.write.await_count == 1
    else:
        count = c.sent.await_count
        async def late(**kwargs):
            c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
            return c.currency_result
        c.currency.read.side_effect = late
        await click(c, "p", 1)
        assert c.sent.await_count == count
    if failure != "timeout":
        c.currency.write.assert_not_awaited()
    c.protocol.write.assert_not_awaited()
