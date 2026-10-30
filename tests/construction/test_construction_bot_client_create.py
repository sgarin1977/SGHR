from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from handlers.construction import construction_client_create as handler
from services.construction.construction_api_client import ConstructionApiClientError, PlatformApiClientSession
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from pydantic import SecretStr


@pytest.fixture
def wizard(monkeypatch):
    c = NS()
    tenant, user, client_id = uuid4(), uuid4(), uuid4()
    c.data = {handler.CONTEXT: {"telegram_user_id": 123, "tenant_id": str(tenant)}}
    async def update(**values):
        c.data.update(values)
    c.state = NS(
        get_data=AsyncMock(side_effect=lambda: c.data),
        update_data=AsyncMock(side_effect=update),
        get_state=AsyncMock(return_value=None),
    )
    sender = NS(id=123, is_bot=False, language_code="uk")
    c.message = NS(from_user=sender, chat=NS(id=123, type="private"), text=None, answer=AsyncMock())
    c.callback = NS(from_user=sender, message=c.message, answer=AsyncMock(), data="CF:new")
    c.service = NS(
        client_form=AsyncMock(return_value={
            "actor_user_id": str(user), "organization": "Organization", "language": "uk",
        }),
        create_client=AsyncMock(return_value={"item": {"id": str(client_id)}}),
    )
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.service
    async def show(event, state, form, text, rows):
        if form is not None:
            form["menu_id"] = 1
            await state.update_data(**{handler.FORM: form})
        await state.update_data(last_menu_message_id=1)
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    monkeypatch.setattr(handler, "show", show)
    return c

async def click(c, action):
    c.callback.data = handler.cb(c.data[handler.FORM], action)
    await handler.client_create_callback(c.callback, c.state)

async def review(c):
    await handler.client_create_callback(c.callback, c.state)
    await click(c, "person")
    c.message.text = "Client"
    await handler.client_create_message(c.message, c.state)
    for _ in range(3):
        await click(c, "skip")
    assert c.data[handler.FORM]["phase"] == "review"
    c.service.create_client.assert_not_awaited()

@pytest.mark.asyncio
async def test_review_allows_optional_contacts_and_cancel_creates_nothing(wizard):
    c = wizard
    await review(c)
    assert c.data[handler.FORM]["payload"] == {
        "client_type": "person", "display_name": "Client",
        "phone": None, "email": None, "notes": None,
    }
    await click(c, "cancel")
    assert c.data[handler.FORM] == {}
    c.service.create_client.assert_not_awaited()

@pytest.mark.asyncio
async def test_uncertain_client_creation_keeps_original_payload_and_key(wizard):
    c = wizard
    await review(c)
    receipt = c.service.create_client.return_value
    c.service.create_client.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_timeout"), receipt,
    ]
    await click(c, "submit")
    first = c.service.create_client.await_args.kwargs
    await click(c, "edit")
    assert c.data[handler.FORM]["phase"] == "pending"
    assert c.service.create_client.await_count == 1
    await click(c, "submit")
    assert c.service.create_client.await_args.kwargs == first
    assert c.data[handler.FORM]["phase"] == "done"
    await click(c, "submit")
    assert c.service.create_client.await_count == 2

@pytest.mark.asyncio
async def test_input_does_not_capture_other_form_or_legacy_state(wizard):
    c = wizard
    await handler.client_create_callback(c.callback, c.state)
    await click(c, "person")
    c.message.text = "Client"
    check = handler.ClientFormInput()
    assert await check(c.message, c.state)
    c.data["construction_project_create_form"] = {"menu_id": 1}
    assert not await check(c.message, c.state)
    c.data["construction_project_create_form"] = {}
    c.state.get_state.return_value = "legacy:input"
    assert not await check(c.message, c.state)

@pytest.mark.asyncio
async def test_changed_canonical_subject_is_denied_before_post():
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("test-secret"))
    create = AsyncMock()
    entry = ConstructionBotApiEntry(
        api=NS(construction=NS(create_client=create)), context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(
        session, {"name": "Organization", "language": "uk", "can_create_project": True},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.create_client(
            telegram_user_id=123, tenant_id=session.tenant_id,
            expected_user_id=uuid4(),
            payload={"display_name": "Client", "client_type": "person",
                     "phone": None, "email": None, "notes": None},
            idempotency_key="stable",
        )
    create.assert_not_awaited()
