from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_lead_convert as handler
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["new", "client", "project"])
async def test_transport_preserves_supported_selection_body_and_stable_replay(mode):
    import json
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("test-secret"))
    lead, client_id, project_id = uuid4(), uuid4(), uuid4()
    payload = {"expected_row_version": 4}
    if mode == "new":
        payload.update(client_type="person", project_name="New project")
    elif mode == "client":
        payload.update(client_id=str(client_id), project_name="New project")
    else:
        payload.update(client_id=str(client_id), project_id=str(project_id))
    seen = []

    def transport(request):
        seen.append(request)
        assert request.method == "POST"
        assert request.url.path == f"/api/v1/construction/crm/leads/{lead}/convert"
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert request.headers["Idempotency-Key"] == "stable-conversion"
        assert "X-Tenant-ID" not in request.headers
        assert json.loads(request.content) == payload
        if len(seen) == 1:
            raise httpx.ReadTimeout("uncertain", request=request)
        return httpx.Response(200, json={
            "data": {
                "lead_id": str(lead), "tenant_id": str(session.tenant_id),
                "client_id": str(client_id), "project_id": str(project_id),
                "status": "won", "row_version": 5,
            },
            "meta": {}, "request_id": "conversion-test",
        })

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        api = ConstructionApiClient(http=http)
        options = dict(
            session=session, lead_id=lead,
            payload=payload, idempotency_key="stable-conversion",
        )
        with pytest.raises(ConstructionApiClientError):
            await api.convert_lead(**options)
        assert len(seen) == 1
        result = await api.convert_lead(**options)
        assert result.data["status"] == "won"
    assert seen[0].content == seen[1].content


@pytest.mark.asyncio
async def test_adapter_does_not_reread_won_lead_before_replay_and_rejects_changed_identity():
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("test-secret"))
    lead = uuid4()
    api = NS(convert_lead=AsyncMock(return_value=NS(data={})), lead=AsyncMock())
    entry = ConstructionBotApiEntry(
        api=NS(construction=api), context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(
        session, {"language": "uk", "name": "Organization"},
    ))
    options = dict(
        telegram_user_id=123, tenant_id=session.tenant_id,
        lead_id=lead, expected_user_id=session.user_id,
        payload={"expected_row_version": 4}, idempotency_key="stable",
    )
    await entry.convert_lead(**options)
    api.lead.assert_not_awaited()
    api.convert_lead.assert_awaited_once()
    api.convert_lead.reset_mock()
    with pytest.raises(ConstructionApiClientError):
        await entry.convert_lead(**{**options, "expected_user_id": uuid4()})
    api.convert_lead.assert_not_awaited()


@pytest.fixture
def case(monkeypatch):
    tenant, lead, user, client_id, project_id = [uuid4() for _ in range(5)]
    data = {handler.CONTEXT: {
        "telegram_user_id": 123, "tenant_id": str(tenant),
    }}

    async def update(**values):
        data.update(values)

    state = NS(
        get_data=AsyncMock(side_effect=lambda: data),
        update_data=AsyncMock(side_effect=update),
        get_state=AsyncMock(return_value=None),
    )
    sender = NS(id=123, is_bot=False, language_code="uk")
    message = NS(
        chat=NS(id=123, type="private"), from_user=sender,
        text=None, answer=AsyncMock(),
    )
    callback = NS(
        from_user=sender, message=message, answer=AsyncMock(),
        data="CV:new:" + lead.hex,
    )
    source = {
        "id": str(lead), "tenant_id": str(tenant), "name": "Contact",
        "phone": "+123", "email": None, "address_raw": "Saved address",
        "work_type_description": "Repair", "comment": "Original comment",
        "responsible_user_id": None, "status": "contacted", "row_version": 4,
        "converted_client_id": None, "converted_project_id": None,
    }
    receipt = {
        "lead_id": str(lead), "tenant_id": str(tenant),
        "client_id": str(client_id), "project_id": str(project_id),
        "status": "won", "row_version": 5,
    }
    service = NS(
        conversion_form=AsyncMock(return_value={
            "actor_user_id": str(user), "organization": "Organization",
            "language": "uk", "item": source,
        }),
        convert_lead=AsyncMock(return_value={"item": receipt}),
    )

    @asynccontextmanager
    async def entry(**kwargs):
        yield service

    async def show(event, state, form, text, rows):
        if form is not None:
            form["menu_id"] = 1
            await state.update_data(**{handler.FORM: form})
        await state.update_data(last_menu_message_id=1)

    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    monkeypatch.setattr(handler, "show", show)
    for name in ("suspend_lead_form", "suspend_activity_form", "suspend_terminal_form"):
        monkeypatch.setattr(handler, name, AsyncMock())
    return NS(
        data=data, state=state, callback=callback, message=message,
        service=service, source=source, receipt=receipt,
    )


async def click(c, action):
    c.callback.data = handler.cb(c.data[handler.FORM], action)
    await handler.conversion_callback(c.callback, c.state)


async def review(c):
    await handler.conversion_callback(c.callback, c.state)
    await click(c, "person")
    c.message.text = "New project"
    await handler.conversion_message(c.message, c.state)
    await click(c, "use")
    assert c.data[handler.FORM]["phase"] == "review"
    c.service.convert_lead.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmation_required_and_abort_creates_nothing(case):
    c = case
    await review(c)
    await click(c, "abort")
    assert c.data[handler.FORM] == {}
    c.service.convert_lead.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_address_requires_input_before_review(case):
    c = case
    c.source["address_raw"] = None
    await handler.conversion_callback(c.callback, c.state)
    await click(c, "person")
    c.message.text = "Project"
    await handler.conversion_message(c.message, c.state)
    assert c.data[handler.FORM]["phase"] == "addressinput"
    c.service.convert_lead.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_conversion_preserves_key_version_and_body(case):
    c = case
    await review(c)
    c.service.convert_lead.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_timeout"),
        {"item": c.receipt},
    ]
    await click(c, "submit")
    first = c.service.convert_lead.await_args.kwargs
    assert first["payload"] == {
        "expected_row_version": 4,
        "client_id": None, "project_id": None,
        "client_type": "person", "project_name": "New project",
        "project_address_raw": "Saved address",
    }
    await click(c, "edit")
    assert c.service.convert_lead.await_count == 1
    await click(c, "submit")
    assert c.service.convert_lead.await_args.kwargs == first
    assert c.data[handler.FORM]["phase"] == "done"
    await click(c, "submit")
    assert c.service.convert_lead.await_count == 2


@pytest.mark.asyncio
async def test_conflict_does_not_refresh_version_and_resubmit(case):
    c = case
    await review(c)
    c.service.convert_lead.side_effect = ConstructionApiClientError(
        status_code=409, code="ROW_VERSION_CONFLICT",
    )
    await click(c, "submit")
    assert c.data[handler.FORM]["phase"] == "conflict"
    await click(c, "submit")
    assert c.service.convert_lead.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["won", "lost"])
async def test_closed_lead_is_not_reopened_by_form(case, status):
    c = case
    c.source["status"] = status
    await handler.conversion_callback(c.callback, c.state)
    assert not c.data.get(handler.FORM)
    c.service.convert_lead.assert_not_awaited()
