from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_lead_status as handler
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.construction_lead_status import transition_callback, status_label


@pytest.mark.asyncio
@pytest.mark.parametrize("response_kind", [
    "ok", "foreign_tenant", "foreign_lead", "wrong_status",
    "wrong_version", "boolean_version",
])
async def test_patch_uses_version_and_no_idempotency_or_identity_headers(response_kind):
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("test-secret"))
    lead_id = uuid4()
    seen = []
    data = {
        "id": str(lead_id), "tenant_id": str(session.tenant_id),
        "status": "contacted", "row_version": 5,
    }
    if response_kind == "foreign_tenant":
        data["tenant_id"] = str(uuid4())
    elif response_kind == "foreign_lead":
        data["id"] = str(uuid4())
    elif response_kind == "wrong_status":
        data["status"] = "lost"
    elif response_kind == "wrong_version":
        data["row_version"] = 6
    elif response_kind == "boolean_version":
        data["row_version"] = True

    def transport(request):
        import json
        seen.append(request)
        assert request.method == "PATCH"
        assert request.url.path == f"/api/v1/construction/crm/leads/{lead_id}/status"
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert "Idempotency-Key" not in request.headers
        assert "X-Tenant-ID" not in request.headers
        assert "X-User-ID" not in request.headers
        assert json.loads(request.content) == {
            "status": "contacted", "expected_row_version": 4,
        }
        return httpx.Response(200, json={
            "data": data, "meta": {}, "request_id": "status-test",
        })

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        client = ConstructionApiClient(http=http)
        command = dict(
            session=session, lead_id=lead_id,
            next_status="contacted", expected_row_version=4,
        )
        if response_kind == "ok":
            result = await client.change_lead_status(**command)
            assert result.data == data
        else:
            with pytest.raises(ConstructionApiClientError):
                await client.change_lead_status(**command)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_adapter_preserves_the_selected_version_and_rechecks_presentation():
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("test-secret"))
    lead_id = uuid4()
    patch = AsyncMock(return_value=NS(data={
        "id": str(lead_id), "tenant_id": str(session.tenant_id),
        "status": "contacted", "row_version": 5,
    }))
    entry = ConstructionBotApiEntry(
        api=NS(construction=NS(change_lead_status=patch)),
        context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(
        session, {"name": "Organization", "language": "uk"},
    ))
    await entry.change_lead_status(
        telegram_user_id=123, tenant_id=session.tenant_id,
        lead_id=lead_id, next_status="contacted", expected_row_version=4,
    )
    entry._presentation.assert_awaited_once_with(
        telegram_user_id=123, tenant_id=session.tenant_id,
    )
    patch.assert_awaited_once_with(
        session=session, lead_id=lead_id,
        next_status="contacted", expected_row_version=4,
    )


@pytest.fixture
def screen(monkeypatch):
    tenant, lead = uuid4(), uuid4()
    data = {handler.CONTEXT: {
        "telegram_user_id": 123, "tenant_id": str(tenant),
    }}
    state = NS(
        get_data=AsyncMock(return_value=data),
        get_state=AsyncMock(return_value=None),
        update_data=AsyncMock(),
    )
    callback = NS(
        from_user=NS(id=123, is_bot=False, language_code="uk"),
        message=NS(chat=NS(id=123, type="private")),
        answer=AsyncMock(),
        data=transition_callback(lead, 4, "contacted"),
    )
    item = {
        "id": str(lead), "tenant_id": str(tenant), "name": "Contact",
        "status": "contacted", "row_version": 7,
    }
    service = NS(
        change_lead_status=AsyncMock(return_value={
            "item": {**item, "row_version": 5}, "language": "uk",
        }),
        lead=AsyncMock(return_value={"item": item, "language": "uk"}),
    )

    @asynccontextmanager
    async def entry(**kwargs):
        yield service

    factory = Mock(side_effect=entry)
    rendered = AsyncMock()
    monkeypatch.setattr(handler, "construction_bot_api_entry", factory)
    monkeypatch.setattr(handler, "suspend_lead_form", AsyncMock())
    monkeypatch.setattr(handler, "render", rendered)
    return NS(
        tenant=tenant, lead=lead, data=data, state=state,
        callback=callback, service=service, factory=factory, rendered=rendered,
    )


@pytest.mark.asyncio
async def test_conflict_refreshes_buttons_without_resubmitting_or_changing_expected_version(screen):
    c = screen
    c.service.change_lead_status.side_effect = ConstructionApiClientError(
        status_code=409, code="ROW_VERSION_CONFLICT",
    )
    await handler.lead_status_callback(c.callback, c.state)
    c.service.change_lead_status.assert_awaited_once_with(
        telegram_user_id=123, tenant_id=c.tenant,
        lead_id=c.lead, next_status="contacted", expected_row_version=4,
    )
    c.service.lead.assert_awaited_once()
    rows = c.rendered.await_args.args[3]
    callbacks = [button.callback_data for row in rows for button in row]
    assert transition_callback(c.lead, 7, "measurement_scheduled") in callbacks
    assert not any(value.startswith(f"LS:set:{c.lead.hex}:4:") for value in callbacks)


@pytest.mark.asyncio
async def test_uncertain_patch_is_not_automatically_retried(screen):
    c = screen
    c.service.change_lead_status.side_effect = ConstructionApiClientError(
        status_code=504, code="api_timeout",
    )
    await handler.lead_status_callback(c.callback, c.state)
    c.service.change_lead_status.assert_awaited_once()
    c.service.lead.assert_not_awaited()
    rows = c.rendered.await_args.args[3]
    assert all(
        not button.callback_data.startswith("LS:set:")
        for row in rows for button in row
    )


@pytest.mark.asyncio
async def test_successful_patch_remains_successful_if_card_refresh_fails(screen):
    c = screen
    c.service.lead.side_effect = ConstructionApiClientError(
        status_code=503, code="api_unavailable",
    )
    await handler.lead_status_callback(c.callback, c.state)
    c.service.change_lead_status.assert_awaited_once()
    assert "✅" in c.rendered.await_args.args[2]
    assert status_label("contacted", "uk") in c.rendered.await_args.args[2]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["won", "lost"])
async def test_terminal_status_has_no_mutation_buttons(screen, status):
    c = screen
    c.callback.data = "LS:open:" + c.lead.hex
    c.service.lead.return_value["item"]["status"] = status
    await handler.lead_status_callback(c.callback, c.state)
    c.service.change_lead_status.assert_not_awaited()
    rows = c.rendered.await_args.args[3]
    assert all(
        not button.callback_data.startswith("LS:set:")
        for row in rows for button in row
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["group", "other_identity"])
async def test_untrusted_ui_context_cannot_open_api(screen, failure):
    c = screen
    if failure == "group":
        c.callback.message.chat.type = "group"
    else:
        c.data[handler.CONTEXT]["telegram_user_id"] = 999
    await handler.lead_status_callback(c.callback, c.state)
    c.factory.assert_not_called()
    c.service.change_lead_status.assert_not_awaited()


def test_bigint_versions_fit_telegram_callback_and_translations_cover_all_statuses():
    from ui.texts import translations
    from ui.construction_lead_status import STATUS_ORDER
    for status in STATUS_ORDER:
        assert len(transition_callback(uuid4(), 2**63 - 1, status).encode()) <= 64
        assert set(translations["construction_lead_status_" + status]) == {
            "uk", "ru", "en", "pt", "pl", "de", "nl",
        }
