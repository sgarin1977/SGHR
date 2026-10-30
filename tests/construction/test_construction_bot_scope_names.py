"""Human-readable scope names through authorized project reads."""
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from pydantic import SecretStr

from handlers.construction import construction_team_permissions as handler
from services.construction.construction_api_client import ConstructionApiClientError, PlatformApiClientSession
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.construction_team_permissions import scope_text, tr, LANGUAGES
from test_construction_bot_team_permissions import screen


@pytest_asyncio.fixture
async def names_case(screen):
    c = screen
    storage = MemoryStorage()
    c.state = FSMContext(storage, StorageKey(bot_id=29, chat_id=123, user_id=123))
    await c.state.set_data(deepcopy(c.data))
    c.result["project_name"] = "Private named building"
    c.resource_names = {str(c.project): "Private named building"}
    c.service.project_scope_names.side_effect = lambda **kwargs: {
        "actor_user_id": str(c.user), "tenant_id": str(c.tenant),
        "project_names": {
            str(value): c.resource_names.get(str(value), "Other building")
            for value in kwargs["project_ids"]
        },
    }
    yield c
    await storage.close()


async def click(c, action, position=0):
    data = await c.state.get_data()
    c.callback.message.message_id = data["last_menu_message_id"]
    c.callback.data = handler.cb(data[c.key], c.mode + action, position)
    await handler.permissions_callback(c.callback, c.state)


@pytest.mark.asyncio
async def test_scope_names_replace_uuid_and_are_not_cached_in_fsm(names_case):
    c = names_case
    await handler.permissions_callback(c.callback, c.state)
    text = c.sent.await_args.kwargs["text"]
    assert "Private named building" in text
    assert str(c.project) not in text
    assert str(c.tenant) not in text
    assert "Private named building" not in repr(await c.state.get_data())
    if c.mode == "p":
        c.service.project_scope_names.assert_not_awaited()
    else:
        assert c.service.project_scope_names.await_args.kwargs["project_ids"] == (c.project,)
    await click(c, "g")
    assert "Private named building" in c.sent.await_args.kwargs["text"]
    c.resource_names[str(c.project)] = "Renamed private building"
    c.result["project_name"] = "Renamed private building"
    await click(c, "r")
    text = c.sent.await_args.kwargs["text"]
    assert "Renamed private building" in text
    assert "Private named building" not in text


@pytest.mark.asyncio
async def test_tenant_scope_uses_organization_name_without_project_lookup(names_case):
    c = names_case
    c.member["grants"][0].update(scope_type="tenant", scope_id=str(c.tenant))
    c.member["scoped_permissions"][0].update(scope_type="tenant", scope_id=str(c.tenant))
    await handler.permissions_callback(c.callback, c.state)
    text = c.sent.await_args.kwargs["text"]
    assert "Organization" in text
    assert str(c.tenant) not in text
    c.service.project_scope_names.assert_not_awaited()


@pytest.mark.asyncio
async def test_unavailable_resource_name_does_not_expose_uuid(names_case):
    c = names_case
    c.result["project_name"] = ""
    c.resource_names[str(c.project)] = None
    await handler.permissions_callback(c.callback, c.state)
    text = c.sent.await_args.kwargs["text"]
    assert tr("project_unavailable", "uk") in text
    assert str(c.project) not in text
    assert (await c.state.get_data())[c.key]["phase"] == "rights_scopes"
    await click(c, "g")
    assert str(c.project) not in c.sent.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_authorization_failure_does_not_render_previous_names(names_case):
    c = names_case
    c.result["project_name"] = ""
    c.service.project_scope_names.side_effect = ConstructionApiClientError(
        status_code=401, code="invalid_token",
    )
    await handler.permissions_callback(c.callback, c.state)
    assert (await c.state.get_data())[c.key]["phase"] == "rights_error"
    assert "Private named building" not in c.sent.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_navigation_during_name_read_cannot_restore_old_screen(names_case):
    c = names_case
    c.result["project_name"] = ""
    async def leave(**kwargs):
        await c.state.update_data(**{handler.CONTEXT: {}})
        return {
            "actor_user_id": str(c.user), "tenant_id": str(c.tenant),
            "project_names": {str(c.project): "Private named building"},
        }
    c.service.project_scope_names.side_effect = leave
    await handler.permissions_callback(c.callback, c.state)
    assert (await c.state.get_data())[handler.CONTEXT] == {}
    c.sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_grants_for_same_project_use_one_name_read(names_case):
    c = names_case
    c.result["project_name"] = ""
    extra = {
        **deepcopy(c.member["grants"][0]),
        "id": str(uuid4()), "role": "EXECUTOR",
    }
    c.member["grants"].append(extra)
    c.member["scoped_permissions"].append({
        **deepcopy(c.member["scoped_permissions"][0]),
        "grant_id": extra["id"], "role": "EXECUTOR",
    })
    await handler.permissions_callback(c.callback, c.state)
    c.service.project_scope_names.assert_awaited_once()
    assert c.service.project_scope_names.await_args.kwargs["project_ids"] == (c.project,)


@pytest.mark.asyncio
async def test_api_adapter_reads_only_requested_projects_and_handles_hidden_resources():
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("local-test"))
    projects = (uuid4(), uuid4(), uuid4())
    async def read(*, session, project_id):
        if project_id == projects[1]:
            raise ConstructionApiClientError(status_code=404, code="not_found")
        return NS(data={
            "id": str(project_id), "tenant_id": str(session.tenant_id),
            "name": "  Authorized\nbuilding  ",
        })
    api = NS(project_details=AsyncMock(side_effect=read))
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._client_identity_session = AsyncMock(return_value=(session, "uk"))
    result = await entry.project_scope_names(
        telegram_user_id=123, tenant_id=session.tenant_id,
        expected_user_id=session.user_id, project_ids=projects,
    )
    assert result["project_names"] == {
        str(projects[0]): "Authorized building",
        str(projects[1]): None,
        str(projects[2]): "Authorized building",
    }
    assert {
        call.kwargs["project_id"] for call in api.project_details.await_args_list
    } == set(projects)
    assert all(
        call.kwargs["session"] == session for call in api.project_details.await_args_list
    )


@pytest.mark.asyncio
async def test_api_adapter_rejects_wrong_actor_before_project_reads():
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("local-test"))
    api = NS(project_details=AsyncMock())
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._client_identity_session = AsyncMock(return_value=(session, "uk"))
    with pytest.raises(ConstructionApiClientError):
        await entry.project_scope_names(
            telegram_user_id=123, tenant_id=session.tenant_id,
            expected_user_id=uuid4(), project_ids=(uuid4(),),
        )
    api.project_details.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["id", "tenant_id"])
async def test_foreign_project_response_is_rejected_after_all_reads_settle(field):
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("local-test"))
    projects = (uuid4(), uuid4())
    async def read(*, session, project_id):
        data = {
            "id": str(project_id), "tenant_id": str(session.tenant_id), "name": "Building",
        }
        if project_id == projects[0]:
            data[field] = str(uuid4())
        return NS(data=data)
    api = NS(project_details=AsyncMock(side_effect=read))
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._client_identity_session = AsyncMock(return_value=(session, "uk"))
    with pytest.raises(ConstructionApiClientError):
        await entry.project_scope_names(
            telegram_user_id=123, tenant_id=session.tenant_id,
            expected_user_id=session.user_id, project_ids=projects,
        )
    assert api.project_details.await_count == 2


@pytest.mark.asyncio
async def test_more_than_five_names_cannot_trigger_unbounded_reads():
    entry = ConstructionBotApiEntry(api=NS(), context=None, sessions=None)
    entry._client_identity_session = AsyncMock()
    with pytest.raises(ValueError):
        await entry.project_scope_names(
            telegram_user_id=123, tenant_id=uuid4(), expected_user_id=uuid4(),
            project_ids=tuple(uuid4() for _ in range(6)),
        )
    entry._client_identity_session.assert_not_awaited()


def test_scope_fallbacks_are_localized_and_never_show_technical_ids():
    identifier = str(uuid4())
    for language in LANGUAGES:
        for kind in ("tenant", "project", "document"):
            value = scope_text({"scope_type": kind, "scope_id": identifier}, language)
            assert identifier not in value
            assert value
    assert "Company name" in scope_text(
        {"scope_type": "tenant", "scope_id": identifier},
        "en", organization="Company name",
    )
