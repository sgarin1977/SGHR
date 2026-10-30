"""Project terminal UI: exact command, replay, scope and navigation."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr

from handlers.construction import construction_activity_terminal as terminal
from handlers.construction import construction_project_activity_terminal as project_terminal
from handlers.construction import construction_project_activities as pages
from handlers.construction import construction_project_team as team
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from test_construction_project_activities import (
    bot_screen, http_case, read_case, terminal_case, screen,
)


@pytest_asyncio.fixture
async def project_case(bot_screen, monkeypatch):
    c = bot_screen
    c.activity = deepcopy(c.result["items"][0])
    c.prepared = {
        "activity": c.activity, "project_id": str(c.project),
        "actor_user_id": str(c.user), "subject_name": "Project",
        "organization": "Org", "language": "uk",
    }
    c.service.project_activity_terminal_context = AsyncMock(
        side_effect=lambda **kwargs: deepcopy(c.prepared),
    )
    async def execute(**kwargs):
        item = {
            **deepcopy(c.activity),
            "status": "completed" if kwargs["operation"] == "complete" else "cancelled",
            "row_version": kwargs["payload"]["expected_row_version"] + 1,
        }
        return {"item": item, "language": "uk"}
    c.execute = execute
    c.service.terminal_project_activity = AsyncMock(side_effect=execute)
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.service
    monkeypatch.setattr(project_terminal, "construction_bot_api_entry", entry)
    monkeypatch.setattr(terminal, "construction_bot_api_entry", entry)
    c.menu_counter = 10
    async def sent(**kwargs):
        c.menu_counter += 1
        return NS(message_id=c.menu_counter)
    c.sent.side_effect = sent
    await pages.activities_callback(c.callback, c.state)
    return c


async def choose(c):
    data = await c.state.get_data()
    c.callback.message.message_id = data["last_menu_message_id"]
    c.callback.data = f'AT:project:{data[team.VIEW]["nonce"]}:0'
    await terminal.terminal_callback(c.callback, c.state)


async def click(c, action):
    data = await c.state.get_data()
    c.callback.message.message_id = data["last_menu_message_id"]
    c.callback.data = terminal.cb(data[terminal.FORM], action)
    await terminal.terminal_callback(c.callback, c.state)


async def review(c, operation="complete"):
    await choose(c)
    await click(c, operation)
    await click(c, "skip")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["complete", "cancel"])
async def test_selected_activity_is_versioned_and_returns_to_current_page(project_case, operation):
    c = project_case
    await review(c, operation)
    form = deepcopy((await c.state.get_data())[terminal.FORM])
    await click(c, "submit")
    c.service.terminal_project_activity.assert_awaited_once_with(
        telegram_user_id=123, tenant_id=c.tenant, project_id=c.project,
        activity_id=UUID(c.activity["id"]), expected_user_id=c.user,
        expected_links={
            key: c.activity[key] for key in ("lead_id", "client_id", "project_id")
        },
        operation=operation,
        payload={"expected_row_version": c.activity["row_version"]},
        idempotency_key="tg-activity-terminal:" + form["id"],
    )
    data = await c.state.get_data()
    assert data[terminal.FORM]["phase"] == "done"
    assert data[c.source_key]["menu_id"] == data["last_menu_message_id"]
    c.callback.message.message_id = data["last_menu_message_id"]
    c.callback.data = terminal.terminal_parent_row("uk", data[terminal.FORM])[0].callback_data
    await pages.activities_callback(c.callback, c.state)
    assert (await c.state.get_data())[team.VIEW]["phase"] == "project_activities"


@pytest.mark.asyncio
async def test_uncertain_post_reopens_original_command_after_activity_disappears(project_case):
    c = project_case
    await review(c)
    async def uncertain(**kwargs):
        raise ConstructionApiClientError(status_code=503, code="service_unavailable")
    c.service.terminal_project_activity.side_effect = uncertain
    await click(c, "submit")
    first = deepcopy(c.service.terminal_project_activity.await_args.kwargs)
    data = await c.state.get_data()
    assert data[terminal.FORM]["phase"] == "pending"
    c.result["items"] = []
    c.callback.message.message_id = data["last_menu_message_id"]
    c.callback.data = terminal.terminal_parent_row("uk", data[terminal.FORM])[0].callback_data
    await pages.activities_callback(c.callback, c.state)
    data = await c.state.get_data()
    assert data[team.VIEW]["activity_ids"] == []
    c.callback.message.message_id = data["last_menu_message_id"]
    c.callback.data = "AT:project-resume:" + data[team.VIEW]["nonce"]
    await terminal.terminal_callback(c.callback, c.state)
    c.service.project_activity_terminal_context.reset_mock()
    c.service.terminal_project_activity.side_effect = c.execute
    await click(c, "submit")
    assert c.service.terminal_project_activity.await_args.kwargs == first
    c.service.project_activity_terminal_context.assert_not_awaited()
    assert (await c.state.get_data())[terminal.FORM]["phase"] == "done"


@pytest.mark.asyncio
async def test_version_conflict_does_not_rebase_or_repeat(project_case):
    c = project_case
    await review(c)
    c.service.terminal_project_activity.side_effect = ConstructionApiClientError(
        status_code=409, code="ROW_VERSION_CONFLICT",
    )
    await click(c, "submit")
    assert (await c.state.get_data())[terminal.FORM]["phase"] == "conflict"
    await click(c, "submit")
    assert c.service.terminal_project_activity.await_count == 1


@pytest.mark.asyncio
async def test_changed_organization_cannot_submit_or_consume_result_input(project_case):
    c = project_case
    await choose(c)
    await click(c, "complete")
    await c.state.update_data(construction_suite_context={
        "telegram_user_id": 123, "tenant_id": str(uuid4()),
    })
    message = NS(
        text="Result", from_user=c.callback.from_user,
        chat=c.callback.message.chat,
    )
    assert not await terminal.ResultInput()(message, c.state)
    await click(c, "skip")
    c.service.terminal_project_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_navigation_while_selecting_cannot_replace_new_screen(project_case):
    c = project_case
    async def leave(**kwargs):
        await c.state.update_data(construction_suite_context={})
        return deepcopy(c.prepared)
    c.service.project_activity_terminal_context.side_effect = leave
    await choose(c)
    assert (await c.state.get_data())["construction_suite_context"] == {}
    c.service.terminal_project_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_abort_does_not_mutate_activity(project_case):
    c = project_case
    await choose(c)
    await click(c, "abort")
    assert (await c.state.get_data())[terminal.FORM] == {}
    c.service.terminal_project_activity.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["complete", "cancel"])
async def test_transport_uses_approved_post_and_validates_all_relations(project_case, operation):
    c = project_case
    session = PlatformApiClientSession(c.user, c.tenant, SecretStr("local-test"))
    links = {key: c.activity[key] for key in ("lead_id", "client_id", "project_id")}
    instant = datetime.now(timezone.utc).isoformat()
    item = {
        **c.activity,
        "status": "completed" if operation == "complete" else "cancelled",
        "completed_at": instant if operation == "complete" else None,
        "cancelled_at": instant if operation == "cancel" else None,
        "updated_at": instant,
        "row_version": c.activity["row_version"] + 1, "result": "Finished",
    }
    payload = {"expected_row_version": c.activity["row_version"], "result": "Finished"}
    def respond(request):
        assert request.method == "POST"
        assert request.url.path == (
            f'/api/v1/construction/crm/activities/{c.activity["id"]}/{operation}'
        )
        assert json.loads(request.content) == payload
        assert request.headers["Idempotency-Key"] == "project-terminal"
        assert request.headers["Authorization"] == "Bearer local-test"
        assert "X-Tenant-ID" not in request.headers
        return httpx.Response(200, json={"data": item, "meta": {}, "request_id": "project-terminal"})
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(respond),
    ) as http:
        client = ConstructionApiClient(http=http)
        options = dict(
            session=session, project_id=c.project, activity_id=UUID(c.activity["id"]),
            expected_links=links, operation=operation, payload=payload,
            idempotency_key="project-terminal",
        )
        result = await client.terminal_project_activity(**options)
        assert result.data["row_version"] == payload["expected_row_version"] + 1
        item["lead_id"] = str(uuid4())
        with pytest.raises(ConstructionApiClientError):
            await client.terminal_project_activity(**options)


@pytest.mark.asyncio
async def test_adapter_replay_does_not_read_planned_list_or_project(project_case):
    c = project_case
    session = PlatformApiClientSession(c.user, c.tenant, SecretStr("local-test"))
    api = NS(
        terminal_project_activity=AsyncMock(return_value=NS(data={"id": c.activity["id"]})),
        project_planned_activities=AsyncMock(), project_details=AsyncMock(),
    )
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        session, {"name": "Org", "language": "uk"},
    ))
    options = dict(
        telegram_user_id=123, tenant_id=c.tenant, project_id=c.project,
        activity_id=UUID(c.activity["id"]), expected_user_id=c.user,
        expected_links={key: c.activity[key] for key in ("lead_id", "client_id", "project_id")},
        operation="complete", payload={"expected_row_version": c.activity["row_version"]},
        idempotency_key="stable-project-terminal",
    )
    await entry.terminal_project_activity(**options)
    api.project_planned_activities.assert_not_awaited()
    api.project_details.assert_not_awaited()
    api.terminal_project_activity.assert_awaited_once()
    api.terminal_project_activity.reset_mock()
    with pytest.raises(ConstructionApiClientError):
        await entry.terminal_project_activity(**{**options, "expected_user_id": uuid4()})
    api.terminal_project_activity.assert_not_awaited()


def test_selection_callback_fits_telegram_limit():
    view = {
        "nonce": "a" * 32, "phase": "project_activities",
        "activity_ids": [str(uuid4()) for _ in range(5)],
    }
    for row in project_terminal.controls(view, {}, "uk"):
        assert len(row[0].callback_data.encode("utf-8")) <= 64
