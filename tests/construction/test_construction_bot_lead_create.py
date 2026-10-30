import json
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_lead_create as form_ui
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry


@pytest.mark.asyncio
async def test_post_uses_existing_route_bearer_and_same_key_without_retry():
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("test-secret"))
    payload = dict(source="custom_channel", name="Contact",
                   work_type_description="Repair", phone="+123")
    requests = []
    lead_id = uuid4()

    def transport(request):
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/v1/construction/crm/leads"
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert request.headers["Idempotency-Key"] == "tg-lead:stable"
        assert json.loads(request.content) == payload
        assert "X-Tenant-ID" not in request.headers
        if len(requests) == 1:
            raise httpx.ReadTimeout("uncertain", request=request)
        return httpx.Response(201, json={
            "data": dict(id=str(lead_id), tenant_id=str(session.tenant_id),
                         status="new", row_version=1),
            "meta": {}, "request_id": "test",
        })

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        client = ConstructionApiClient(http=http)
        with pytest.raises(ConstructionApiClientError) as caught:
            await client.create_lead(
                session=session, payload=payload, idempotency_key="tg-lead:stable",
            )
        assert caught.value.status_code == 504
        assert len(requests) == 1
        result = await client.create_lead(
            session=session, payload=payload, idempotency_key="tg-lead:stable",
        )
        assert result.data["id"] == str(lead_id)
    assert requests[0].content == requests[1].content


@pytest.mark.asyncio
async def test_adapter_uses_canonical_session_identity_for_self_assignment():
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("test-secret"))
    create = AsyncMock(return_value=NS(data={"id": str(uuid4())}))
    entry = ConstructionBotApiEntry(
        api=NS(construction=NS(create_lead=create)), context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(
        session, {"language": "uk", "name": "Organization"},
    ))
    payload = dict(source="new_channel", name="Contact",
                   work_type_description="Repair", email="a@example.test")
    await entry.create_lead(
        telegram_user_id=123, tenant_id=session.tenant_id,
        expected_user_id=session.user_id, payload=payload,
        assign_self=True, idempotency_key="stable",
    )
    sent = create.await_args.kwargs
    assert sent["payload"] == {**payload, "responsible_user_id": str(session.user_id)}
    assert payload.get("responsible_user_id") is None
    create.reset_mock()
    with pytest.raises(ConstructionApiClientError):
        await entry.create_lead(
            telegram_user_id=123, tenant_id=session.tenant_id,
            expected_user_id=uuid4(), payload=payload,
            assign_self=True, idempotency_key="stable",
        )
    create.assert_not_awaited()


@pytest.fixture
def wizard(monkeypatch):
    user, tenant, lead = uuid4(), uuid4(), uuid4()
    data = {form_ui.CONTEXT: {"telegram_user_id": 123, "tenant_id": str(tenant)}}

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
        answer=AsyncMock(), text=None, message_id=1,
    )
    callback = NS(
        from_user=sender, message=message, answer=AsyncMock(), data="LC:new",
    )
    service = NS(
        lead_address_suggestions=AsyncMock(return_value={
            "items": [], "actor_user_id": str(user), "tenant_id": str(tenant),
            "language": "uk",
        }),
        lead_form=AsyncMock(return_value={
            "actor_user_id": str(user), "organization": "Organization",
            "language": "uk",
        }),
        create_lead=AsyncMock(return_value={"item": {"id": str(lead)}}),
    )

    @asynccontextmanager
    async def entry(**kwargs):
        yield service

    async def show(event, state, form, text, rows, **kwargs):
        if form is not None:
            form["menu_id"] = 1
            await state.update_data(**{form_ui.FORM: form})
        await state.update_data(last_menu_message_id=1)

    monkeypatch.setattr(form_ui, "construction_bot_api_entry", entry)
    monkeypatch.setattr(form_ui, "show", show)
    return NS(
        state=state, data=data, callback=callback, message=message,
        service=service, lead=lead,
    )


async def click(c, action):
    form = c.data[form_ui.FORM]
    c.callback.data = form_ui.cb(form, action)
    await form_ui.lead_create_callback(c.callback, c.state)


async def review(c):
    await form_ui.lead_create_callback(c.callback, c.state)
    for value in (
        "Contact", "+123", "a@example.test", "Repair",
        "custom_channel", "Address", "Comment",
    ):
        c.message.text = value
        await form_ui.lead_create_message(c.message, c.state)
        if c.data[form_ui.FORM].get("phase") == "address_suggestions":
            await click(c, "address_continue")
    await click(c, "none")
    assert c.data[form_ui.FORM]["phase"] == "review"
    c.service.create_lead.assert_not_awaited()


@pytest.mark.asyncio
async def test_form_requires_review_and_cancel_creates_nothing(wizard):
    c = wizard
    await review(c)
    await click(c, "cancel")
    assert c.data[form_ui.FORM] == {}
    c.service.create_lead.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_submission_reuses_frozen_payload_and_key(wizard):
    c = wizard
    await review(c)
    c.service.create_lead.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_timeout"),
        {"item": {"id": str(c.lead)}},
    ]
    await click(c, "submit")
    assert c.data[form_ui.FORM]["phase"] == "pending"
    first = c.service.create_lead.await_args.kwargs
    await click(c, "edit")
    assert c.data[form_ui.FORM]["phase"] == "pending"
    assert c.service.create_lead.await_count == 1
    await click(c, "submit")
    assert c.service.create_lead.await_args.kwargs == first
    assert c.data[form_ui.FORM]["phase"] == "done"
    await click(c, "submit")
    assert c.service.create_lead.await_count == 2


@pytest.mark.asyncio
async def test_form_does_not_capture_other_identity_group_or_other_menu(wizard):
    c = wizard
    await form_ui.lead_create_callback(c.callback, c.state)
    check = form_ui.LeadFormInput()
    assert await check(c.message, c.state)
    c.message.chat.type = "group"
    assert not await check(c.message, c.state)
    c.message.chat.type = "private"
    c.data["last_menu_message_id"] = 99
    assert not await check(c.message, c.state)
    c.data["last_menu_message_id"] = 1
    c.data[form_ui.CONTEXT]["telegram_user_id"] = 999
    assert not await check(c.message, c.state)


def test_all_form_texts_have_seven_languages():
    from ui.texts import translations
    rows = [value for key, value in translations.items()
            if key.startswith("construction_lead_form_")]
    assert rows
    for row in rows:
        assert set(row) == {"uk", "ru", "en", "pt", "pl", "de", "nl"}
        assert all(isinstance(value, str) and value for value in row.values())

# lead_address_suggestions_v1
