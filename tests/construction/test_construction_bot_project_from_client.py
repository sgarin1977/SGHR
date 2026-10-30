import json
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_project_create as parent
from handlers.construction import construction_project_from_client as linked
from handlers.construction import construction_client_card as cards
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from test_construction_bot_project_create import (
    wizard, click, review, project_data, project_http,
)


@pytest.fixture
def client_wizard(wizard, monkeypatch):
    c = wizard
    c.client_id = uuid4()
    c.actor_id = UUID(c.service.project_form.return_value["actor_user_id"])
    c.tenant_id = UUID(c.data[parent.CONTEXT]["tenant_id"])
    c.data["last_menu_message_id"] = c.message.message_id
    c.data[linked.CARD] = {
        "nonce": uuid4().hex, "telegram_user_id": 123,
        "tenant_id": str(c.tenant_id), "client_id": str(c.client_id),
        "actor_user_id": str(c.actor_id), "section": "summary",
        "menu_id": 1,
    }
    c.service.client_project_form = AsyncMock(return_value={
        **c.service.project_form.return_value,
        "client_id": str(c.client_id), "client_name": "Selected client",
    })
    c.callback.data = "PF:client:" + c.data[linked.CARD]["nonce"]
    c.shown = []
    original_show = parent.show

    async def show(event, state, form, text, rows, **kwargs):
        c.shown.append((text, rows))
        await original_show(event, state, form, text, rows, **kwargs)

    monkeypatch.setattr(parent, "show", show)
    return c


@pytest.mark.asyncio
async def test_client_project_requires_review_and_keeps_exact_client(client_wizard):
    c = client_wizard
    await review(c)
    assert "Selected client" in c.shown[-1][0]
    assert parent.tr("no_client", "uk") not in c.shown[-1][0]
    c.service.create_project.assert_not_awaited()
    await click(c, "submit")
    command = c.service.create_project.await_args.kwargs
    assert command["client_id"] == c.client_id
    assert "client_id" not in command["payload"]
    assert command["expected_user_id"] == c.actor_id
    assert c.data[parent.FORM]["client_id"] == str(c.client_id)
    assert c.data[parent.FORM]["phase"] == "done"
    targets = [button.callback_data for row in c.shown[-1][1] for button in row]
    assert "CC:open:" + c.client_id.hex in targets


@pytest.mark.asyncio
async def test_cancel_creates_neither_project_nor_client(client_wizard):
    c = client_wizard
    await review(c)
    await click(c, "cancel")
    c.service.create_project.assert_not_awaited()
    assert c.data[parent.FORM] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("nonce", "menu", "tenant", "sender"))
async def test_stale_card_cannot_start_client_project(client_wizard, failure):
    c = client_wizard
    card = c.data[linked.CARD]
    if failure == "nonce":
        card["nonce"] = uuid4().hex
    elif failure == "menu":
        card["menu_id"] = 99
    elif failure == "tenant":
        card["tenant_id"] = str(uuid4())
    else:
        card["telegram_user_id"] = 999
    await parent.project_create_callback(c.callback, c.state)
    c.service.client_project_form.assert_not_awaited()
    c.service.create_project.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_client_read_does_not_restore_another_context(client_wizard):
    c = client_wizard
    result = deepcopy(c.service.client_project_form.return_value)

    async def changed(**kwargs):
        c.data[parent.CONTEXT]["tenant_id"] = str(uuid4())
        return result

    c.service.client_project_form.side_effect = changed
    await parent.project_create_callback(c.callback, c.state)
    assert not c.data.get(parent.FORM)
    assert c.shown == []


@pytest.mark.asyncio
async def test_pending_retry_preserves_client_and_does_not_reread_card(client_wizard):
    c = client_wizard
    await review(c)
    receipt = c.service.create_project.return_value
    c.service.create_project.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_timeout"),
        receipt,
    ]
    await click(c, "submit")
    first = deepcopy(c.service.create_project.await_args.kwargs)
    original_id = c.data[parent.FORM]["id"]
    await parent.suspend_project_form(c.state)
    c.callback.data = "PF:new"
    await parent.project_create_callback(c.callback, c.state)
    assert c.data[parent.FORM]["id"] == original_id
    assert c.data[parent.FORM]["client_id"] == str(c.client_id)
    assert c.service.client_project_form.await_count == 1
    await click(c, "submit")
    assert c.service.create_project.await_args.kwargs == first


@pytest.mark.asyncio
async def test_new_standalone_draft_does_not_inherit_client(client_wizard):
    c = client_wizard
    await parent.project_create_callback(c.callback, c.state)
    assert c.data[parent.FORM]["client_id"] == str(c.client_id)
    c.callback.data = "PF:new"
    await parent.project_create_callback(c.callback, c.state)
    assert c.data[parent.FORM].get("client_id") is None


@pytest.mark.asyncio
async def test_adapter_uses_existing_client_read_and_create_contract():
    user, tenant, client_id, project_id = uuid4(), uuid4(), uuid4(), uuid4()
    session = PlatformApiClientSession(user, tenant, SecretStr("test"))
    api = NS(
        client_card=AsyncMock(return_value=NS(data={
            "id": str(client_id), "tenant_id": str(tenant),
            "display_name": "Selected client",
        })),
        create_project=AsyncMock(return_value=NS(data={"id": str(project_id)})),
    )
    entry = ConstructionBotApiEntry(
        api=NS(construction=api), context=None, sessions=None,
    )
    home = {"name": "Organization", "language": "uk", "can_create_project": True}
    entry._presentation = AsyncMock(return_value=(session, home))
    context = dict(
        telegram_user_id=123, tenant_id=tenant,
        client_id=client_id, expected_user_id=user,
    )
    form = await entry.client_project_form(**context)
    assert form["client_id"] == str(client_id)
    assert form["client_name"] == "Selected client"
    api.client_card.assert_awaited_once_with(session=session, client_id=client_id)
    await entry.create_project(
        **context, payload={"name": "Project", "address_raw": "Address", "comment": None},
        assign_self=False, idempotency_key="stable",
    )
    sent = api.create_project.await_args.kwargs
    assert sent["session"] is session
    assert sent["payload"]["client_id"] == str(client_id)
    assert sent["idempotency_key"] == "stable"
    assert api.client_card.await_count == 1
    home["can_create_project"] = False
    api.client_card.reset_mock()
    with pytest.raises(ConstructionApiClientError):
        await entry.client_project_form(**context)
    api.client_card.assert_not_awaited()
    home["can_create_project"] = True
    with pytest.raises(ConstructionApiClientError):
        await entry.client_project_form(**{**context, "expected_user_id": uuid4()})
    api.client_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_transport_sends_client_id_to_existing_post(project_data, project_http):
    client_id = uuid4()
    body = {
        "name": "New project", "address_raw": "Project address",
        "comment": "Comment", "client_id": str(client_id),
        "responsible_user_id": None,
    }
    seen = []

    def transport(request):
        seen.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/v1/construction/projects"
        assert request.headers["Idempotency-Key"] == "client-project"
        assert json.loads(request.content) == body
        return httpx.Response(201, json={
            "data": {**project_data, "client_id": str(client_id)},
            "meta": {}, "request_id": "client-project-test",
        })

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        result = await ConstructionApiClient(http=http).create_project(
            session=PlatformApiClientSession(
                project_http.user, project_http.tenant, SecretStr("test"),
            ),
            payload=body, idempotency_key="client-project",
        )
    assert result.data["client_id"] == str(client_id)
    assert len(seen) == 1


@pytest.mark.parametrize("allowed", (False, True))
def test_client_card_button_uses_server_capability(allowed):
    view = {
        "section": "summary", "nonce": "a" * 32,
        "next_cursors": {}, "catalog_nonce": None,
    }
    result = {"can_create_project": allowed}
    rows = cards.rows_for(view, result, "uk")
    commands = [button.callback_data for row in rows for button in row]
    assert ("PF:client:" + view["nonce"] in commands) is allowed
    assert len(("PF:client:" + view["nonce"]).encode()) <= 64
