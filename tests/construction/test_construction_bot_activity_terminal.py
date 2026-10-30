from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_activity_terminal as handler
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["complete", "cancel"])
@pytest.mark.parametrize("provide_result", [False, True])
async def test_http_targets_exact_activity_with_key_version_and_optional_result(operation, provide_result):
    import json
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("test-secret"))
    lead, activity = uuid4(), uuid4()
    payload = {"expected_row_version": 3}
    if provide_result:
        payload["result"] = "Finished"
    seen = []

    def transport(request):
        seen.append(request)
        assert request.method == "POST"
        assert request.url.path == f"/api/v1/construction/crm/activities/{activity}/{operation}"
        assert request.headers["Idempotency-Key"] == "stable-terminal"
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert "X-Tenant-ID" not in request.headers
        assert json.loads(request.content) == payload
        if len(seen) == 1:
            raise httpx.ReadTimeout("uncertain", request=request)
        instant = "2026-10-22T12:00:00Z"
        data = {
            "id": str(activity), "tenant_id": str(session.tenant_id),
            "type": "call",
            "status": "completed" if operation == "complete" else "cancelled",
            "lead_id": str(lead), "client_id": None, "project_id": None,
            "due_at": "2026-10-23T12:30:00Z", "timezone": "Europe/Kyiv",
            "responsible_user_id": None, "comment": None,
            "result": "Finished" if provide_result else "Previous result",
            "completed_at": instant if operation == "complete" else None,
            "cancelled_at": instant if operation == "cancel" else None,
            "created_by_user_id": str(uuid4()),
            "created_at": "2026-10-21T12:00:00Z",
            "updated_at": instant, "row_version": 4,
        }
        return httpx.Response(200, json={
            "data": data, "meta": {}, "request_id": "terminal-test",
        })

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        client = ConstructionApiClient(http=http)
        options = dict(
            session=session, lead_id=lead, activity_id=activity,
            operation=operation, payload=payload, idempotency_key="stable-terminal",
        )
        with pytest.raises(ConstructionApiClientError):
            await client.terminal_lead_activity(**options)
        assert len(seen) == 1
        result = await client.terminal_lead_activity(**options)
        assert result.data["row_version"] == 4
    assert seen[0].content == seen[1].content


@pytest.mark.asyncio
async def test_adapter_replays_original_activity_without_fetching_a_new_next_action():
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("test-secret"))
    lead, activity = uuid4(), uuid4()
    api = NS(
        terminal_lead_activity=AsyncMock(return_value=NS(data={"id": str(activity)})),
        lead=AsyncMock(),
    )
    entry = ConstructionBotApiEntry(
        api=NS(construction=api), context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(
        session, {"name": "Organization", "language": "uk"},
    ))
    options = dict(
        telegram_user_id=123, tenant_id=session.tenant_id,
        lead_id=lead, activity_id=activity, expected_user_id=session.user_id,
        operation="complete", payload={"expected_row_version": 3},
        idempotency_key="stable",
    )
    await entry.terminal_lead_activity(**options)
    api.lead.assert_not_awaited()
    api.terminal_lead_activity.assert_awaited_once_with(
        session=session, lead_id=lead, activity_id=activity,
        operation="complete", payload={"expected_row_version": 3},
        idempotency_key="stable",
    )
    api.terminal_lead_activity.reset_mock()
    with pytest.raises(ConstructionApiClientError):
        await entry.terminal_lead_activity(**{
            **options, "expected_user_id": uuid4(),
        })
    api.terminal_lead_activity.assert_not_awaited()


@pytest.fixture
def case(monkeypatch):
    tenant, lead, activity, user = uuid4(), uuid4(), uuid4(), uuid4()
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
    callback = NS(from_user=sender, message=message, answer=AsyncMock(), data="")
    action = {
        "id": str(activity), "tenant_id": str(tenant), "lead_id": str(lead),
        "status": "planned", "row_version": 3, "type": "call",
        "due_at": "2026-10-23T12:30:00Z", "timezone": "Europe/Kyiv",
    }
    read = {
        "language": "uk", "organization": "Organization",
        "item": {"id": str(lead), "name": "Contact", "next_action": action},
    }
    service = NS(
        activity_terminal_context=AsyncMock(return_value={
            "actor_user_id": str(user), "organization": "Organization",
            "language": "uk", "lead_name": "Contact", "next_action": dict(action),
        }),
        terminal_lead_activity=AsyncMock(return_value={
            "item": {
                "id": str(activity), "status": "completed", "row_version": 4,
            },
            "language": "uk",
        }),
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
    monkeypatch.setattr(handler, "suspend_lead_form", AsyncMock())
    monkeypatch.setattr(handler, "suspend_activity_form", AsyncMock())
    monkeypatch.setattr(handler, "show", show)
    return NS(
        tenant=tenant, lead=lead, activity=activity, data=data,
        state=state, callback=callback, message=message,
        service=service, read=read,
    )


async def start(c, operation="complete"):
    buttons = await handler.next_action_buttons(
        c.state, c.read, telegram_user_id=123, tenant_id=c.tenant,
    )
    c.callback.data = buttons[0 if operation == "complete" else 1][0].callback_data
    await handler.terminal_callback(c.callback, c.state)


async def click(c, action):
    c.callback.data = handler.cb(c.data[handler.FORM], action)
    await handler.terminal_callback(c.callback, c.state)


@pytest.mark.asyncio
async def test_confirmation_required_and_aborting_does_not_cancel_activity(case):
    c = case
    await start(c, "cancel")
    await click(c, "skip")
    assert c.data[handler.FORM]["phase"] == "review"
    c.service.terminal_lead_activity.assert_not_awaited()
    await click(c, "abort")
    assert c.data[handler.FORM] == {}
    c.service.terminal_lead_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_selection_is_not_retargeted(case):
    c = case
    c.service.activity_terminal_context.return_value["next_action"]["id"] = str(uuid4())
    await start(c)
    assert not c.data.get(handler.FORM)
    c.service.terminal_lead_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_operation_keeps_activity_version_body_and_key(case):
    c = case
    await start(c)
    c.message.text = "Client contacted"
    await handler.terminal_message(c.message, c.state)
    c.service.terminal_lead_activity.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_timeout"),
        {"item": {"id": str(c.activity), "status": "completed", "row_version": 4}},
    ]
    await click(c, "submit")
    first = c.service.terminal_lead_activity.await_args.kwargs
    assert first["payload"] == {
        "expected_row_version": 3, "result": "Client contacted",
    }
    c.read["item"]["next_action"] = {
        **c.read["item"]["next_action"], "id": str(uuid4()), "row_version": 1,
    }
    await handler.next_action_buttons(
        c.state, c.read, telegram_user_id=123, tenant_id=c.tenant,
    )
    await click(c, "edit")
    assert c.service.terminal_lead_activity.await_count == 1
    await click(c, "submit")
    assert c.service.terminal_lead_activity.await_args.kwargs == first
    assert c.data[handler.FORM]["phase"] == "done"
    await click(c, "submit")
    assert c.service.terminal_lead_activity.await_count == 2


@pytest.mark.asyncio
async def test_version_conflict_does_not_refresh_version_and_resubmit(case):
    c = case
    await start(c)
    await click(c, "skip")
    c.service.terminal_lead_activity.side_effect = ConstructionApiClientError(
        status_code=409, code="ROW_VERSION_CONFLICT",
    )
    await click(c, "submit")
    assert c.data[handler.FORM]["phase"] == "conflict"
    assert "result" not in c.service.terminal_lead_activity.await_args.kwargs["payload"]
    await click(c, "submit")
    assert c.service.terminal_lead_activity.await_count == 1

# Client planned activity terminal regression v1.
@pytest.fixture
def client_case(case):
    from copy import deepcopy
    from uuid import UUID
    c = case
    c.client = uuid4()
    c.user = UUID(c.service.activity_terminal_context.return_value["actor_user_id"])
    c.callback.message.message_id = 1
    c.data["last_menu_message_id"] = 1
    c.data["construction_client_card_view"] = {
        "nonce": "client-page", "telegram_user_id": 123,
        "tenant_id": str(c.tenant), "client_id": str(c.client),
        "actor_user_id": str(c.user), "menu_id": 1, "section": "activities",
        "activity_ids": [str(c.activity)],
        "positions": {"activities": "original-activity-page"},
    }
    c.planned = {
        **c.read["item"]["next_action"],
        "lead_id": None, "client_id": str(c.client), "project_id": None,
    }
    c.service.client_activity_terminal_context = AsyncMock(side_effect=lambda **kwargs: {
        "activity": deepcopy(c.planned), "client_id": str(c.client),
        "actor_user_id": str(c.user), "organization": "Organization",
        "subject_name": "Client", "language": "uk",
    })
    c.service.terminal_client_activity = AsyncMock(return_value={
        "item": {"id": str(c.activity), "status": "completed", "row_version": 4},
        "language": "uk",
    })
    return c


async def start_client(c, operation="complete"):
    c.callback.data = "AT:client:client-page:0"
    await handler.terminal_callback(c.callback, c.state)
    assert c.data[handler.FORM]["phase"] == "choose"
    await click(c, operation)
    assert c.data[handler.FORM]["phase"] == "result"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["complete", "cancel"])
async def test_client_activity_terminal_requires_operation_result_and_confirmation(client_case, operation):
    c = client_case
    c.service.terminal_client_activity.return_value["item"]["status"] = (
        "completed" if operation == "complete" else "cancelled"
    )
    await start_client(c, operation)
    await click(c, "skip")
    c.service.terminal_client_activity.assert_not_awaited()
    await click(c, "submit")
    args = c.service.terminal_client_activity.await_args.kwargs
    assert args["activity_id"] == c.activity and args["client_id"] == c.client
    assert args["operation"] == operation
    assert args["payload"] == {"expected_row_version": 3}
    assert args["expected_links"] == {
        "lead_id": None, "client_id": str(c.client), "project_id": None,
    }
    assert c.data[handler.FORM]["phase"] == "done"
    c.service.terminal_lead_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_client_activity_result_is_optional_and_preserved_in_frozen_command(client_case):
    c = client_case
    await start_client(c)
    c.message.text = "  Call completed  "
    await handler.terminal_message(c.message, c.state)
    c.service.terminal_client_activity.assert_not_awaited()
    await click(c, "submit")
    assert c.service.terminal_client_activity.await_args.kwargs["payload"] == {
        "expected_row_version": 3, "result": "Call completed",
    }


@pytest.mark.asyncio
async def test_client_terminal_abort_does_not_cancel_activity(client_case):
    c = client_case
    await start_client(c, "cancel")
    await click(c, "skip")
    await click(c, "abort")
    assert c.data[handler.FORM] == {}
    c.service.terminal_client_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_client_terminal_replay_survives_absence_from_planned_list(client_case):
    from copy import deepcopy
    c = client_case
    await start_client(c)
    await click(c, "skip")
    c.service.terminal_client_activity.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_unavailable"),
        {"item": {"id": str(c.activity), "status": "completed", "row_version": 4}, "language": "uk"},
    ]
    await click(c, "submit")
    frozen = deepcopy(c.data[handler.FORM])
    c.data[handler.FORM]["menu_id"] = None
    c.data["construction_client_card_view"]["activity_ids"] = []
    c.service.client_activity_terminal_context.side_effect = ConstructionApiClientError(
        status_code=404, code="not_found",
    )
    c.callback.data = "AT:resume:client-page"
    await handler.terminal_callback(c.callback, c.state)
    assert c.data[handler.FORM]["id"] == frozen["id"]
    c.service.terminal_client_activity.assert_awaited_once()
    await click(c, "submit")
    first, second = c.service.terminal_client_activity.await_args_list
    assert first.kwargs == second.kwargs
    assert c.data[handler.FORM]["phase"] == "done"
    c.service.client_activity_terminal_context.assert_awaited_once()


@pytest.mark.asyncio
async def test_client_activity_conflict_does_not_rebase_version(client_case):
    c = client_case
    await start_client(c)
    await click(c, "skip")
    c.service.terminal_client_activity.side_effect = ConstructionApiClientError(
        status_code=409, code="ROW_VERSION_CONFLICT",
    )
    await click(c, "submit")
    assert c.data[handler.FORM]["phase"] == "conflict"
    await click(c, "submit")
    c.service.terminal_client_activity.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "menu", "nonce", "index"])
async def test_stale_client_activity_selection_never_reads_or_writes(client_case, failure):
    c = client_case
    c.callback.data = "AT:client:client-page:0"
    if failure == "tenant":
        c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
    elif failure == "menu":
        c.callback.message.message_id = 99
    elif failure == "nonce":
        c.callback.data = "AT:client:obsolete:0"
    else:
        c.callback.data = "AT:client:client-page:9"
    await handler.terminal_callback(c.callback, c.state)
    c.service.client_activity_terminal_context.assert_not_awaited()
    c.service.terminal_client_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_client_terminal_adapter_does_not_read_card_during_replay():
    from services.construction.construction_api_client import ConstructionApiResult
    user, tenant, client_id, activity_id = uuid4(), uuid4(), uuid4(), uuid4()
    api = NS(
        client_card=AsyncMock(),
        terminal_client_activity=AsyncMock(return_value=ConstructionApiResult({}, {}, "local")),
    )
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local")),
        {"language": "uk"},
    ))
    await entry.terminal_client_activity(
        telegram_user_id=123, tenant_id=tenant, client_id=client_id,
        activity_id=activity_id, expected_user_id=user,
        expected_links={"client_id": str(client_id), "lead_id": None, "project_id": None},
        operation="complete", payload={"expected_row_version": 3},
        idempotency_key="same-operation",
    )
    api.client_card.assert_not_awaited()
    api.terminal_client_activity.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("relation", ["client_id", "lead_id", "project_id"])
@pytest.mark.parametrize("operation", ["complete", "cancel"])
async def test_client_terminal_transport_preserves_direct_or_indirect_links(relation, operation):
    import json
    user, tenant, client_id, activity_id = uuid4(), uuid4(), uuid4(), uuid4()
    links = {"client_id": None, "lead_id": None, "project_id": None}
    links[relation] = str(client_id if relation == "client_id" else uuid4())
    def transport(request):
        assert request.method == "POST"
        assert request.url.path == f"/api/v1/construction/crm/activities/{activity_id}/{operation}"
        assert request.headers["Idempotency-Key"] == "client-terminal-local"
        assert json.loads(request.content) == {"expected_row_version": 3}
        terminal_time = "2026-10-27T12:00:00Z"
        return httpx.Response(200, json={
            "data": {
                "id": str(activity_id), "tenant_id": str(tenant), "type": "call",
                "status": "completed" if operation == "complete" else "cancelled",
                **links, "due_at": "2026-10-28T12:30:00Z", "timezone": "Europe/Kyiv",
                "responsible_user_id": None, "comment": None, "result": None,
                "completed_at": terminal_time if operation == "complete" else None,
                "cancelled_at": terminal_time if operation == "cancel" else None,
                "created_by_user_id": str(user), "created_at": "2026-10-26T12:00:00Z",
                "updated_at": terminal_time, "row_version": 4,
            }, "meta": {}, "request_id": "local",
        })
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        result = await ConstructionApiClient(http=http).terminal_client_activity(
            session=PlatformApiClientSession(user, tenant, SecretStr("local")),
            client_id=client_id, activity_id=activity_id, expected_links=links,
            operation=operation, payload={"expected_row_version": 3},
            idempotency_key="client-terminal-local",
        )
    assert all(result.data[key] == value for key, value in links.items())


def test_client_terminal_callbacks_and_labels_are_bounded():
    from ui.construction_client_card import LABELS
    assert all(len(values) == 7 for values in LABELS.values())
    assert len(("AT:client:" + "a" * 32 + ":4").encode("utf-8")) <= 64
    assert len(("AT:resume:" + "a" * 32).encode("utf-8")) <= 64
