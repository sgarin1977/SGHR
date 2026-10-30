"""Project-only activity HTTP, cursor binding, transport and bot read pages."""
import base64
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from pydantic import SecretStr

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import (
    get_api_request_rate_limit_service, get_tenant_module_access_service,
    get_tenant_suite_access_service,
)
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
import api.routes.construction_project_activities as route
from api.construction_project_activity_cursors import decode_cursor, encode_cursor
from construction_rate_limit_fixture import with_local_construction_rate_limit
from handlers.construction import construction_project_activities as handler
from handlers.construction import construction_project_team as team
from handlers.construction import construction_project_areas as areas
from services.construction.construction_activity_read import ConstructionActivityReadCursor
from services.construction.construction_activity_rules import ConstructionActivityValidationError
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from test_construction_1a_activity_terminal_service import terminal_case
from test_construction_1a_activity_read_service import read_case, project_context, read_only
from test_construction_bot_project_areas import screen
from ui.construction_project_activities import LANGUAGES, LABELS


def supply(value):
    def dependency():
        return value
    return dependency


@pytest.fixture
def http_case(read_case):
    c = read_case
    c.row.lead_id = None
    c.row.client_id = None
    c.row.project_id = c.project.id
    c.row.due_at = c.now - timedelta(days=3)
    c.activity_rows = [c.row]
    async def visible(**values):
        rows = sorted(c.activity_rows, key=lambda row: (row.due_at, row.id))
        after = values["after"]
        if after is not None:
            rows = [row for row in rows if (row.due_at, row.id) > (after.due_at, after.activity_id)]
        return rows[:values["limit"] + 1]
    c.repo.list_visible_planned_activities.side_effect = visible
    c.rate = NS(ensure_authenticated_user_allowed=AsyncMock())
    c.suite = NS(require_enabled=AsyncMock())
    c.module = NS(require_enabled=AsyncMock())
    c.app = with_local_construction_rate_limit(create_app())
    for dependency, value in (
        (get_current_actor, c.actor),
        (get_construction_scoped_session, c.session),
        (route.get_project_activity_read, c.service),
        (get_api_request_rate_limit_service, c.rate),
        (get_tenant_suite_access_service, c.suite),
        (get_tenant_module_access_service, c.module),
    ):
        c.app.dependency_overrides[dependency] = supply(value)
    c.app.dependency_overrides[get_construction_access_context] = lambda: c.context
    return c


async def get(c, *, params=None, project_id=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="http://127.0.0.1",
    ) as http:
        return await http.get(
            "/api/v1/construction/projects/" + str(project_id or c.project.id) + "/activities",
            params=params, headers={"X-Tenant-ID": str(uuid4())},
        )


@pytest.mark.asyncio
async def test_registered_project_read_includes_overdue_without_client_or_writes(http_case):
    c = http_case
    response = await get(c)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"][0]["id"] == str(c.row.id)
    assert body["data"][0]["client_id"] is None
    assert body["data"][0]["project_id"] == str(c.project.id)
    assert body["meta"]["pagination"]["next_cursor"] is None
    assert response.headers["cache-control"] == "private, no-store"
    c.rate.ensure_authenticated_user_allowed.assert_awaited_once()
    read_only(c)


@pytest.mark.asyncio
async def test_project_manager_read_uses_existing_project_scope(http_case):
    c = http_case
    project_context(c)
    response = await get(c)
    assert response.status_code == 200, response.text
    assert c.repo.list_visible_planned_activities.await_args.kwargs["allowed_project_ids"] == (c.project.id,)
    denied = await get(c, project_id=uuid4())
    assert denied.status_code == 403
    read_only(c)


@pytest.mark.asyncio
async def test_pagination_uses_due_at_then_id_and_binds_project(http_case):
    c = http_case
    second = NS(**{**vars(c.row), "id": uuid4(), "due_at": c.row.due_at + timedelta(seconds=1)})
    c.activity_rows.append(second)
    first = await get(c, params={"limit": 1})
    assert first.status_code == 200, first.text
    token = first.json()["meta"]["pagination"]["next_cursor"]
    assert token
    next_page = await get(c, params={"limit": 1, "cursor": token})
    assert next_page.status_code == 200, next_page.text
    assert next_page.json()["data"][0]["id"] == str(second.id)
    assert next_page.json()["meta"]["pagination"]["next_cursor"] is None
    foreign = await get(c, project_id=uuid4(), params={"cursor": token})
    assert foreign.status_code == 422
    read_only(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [
    {"tenant_id": "foreign"}, {"limit": 0}, {"limit": 101},
    {"cursor": ""}, [("limit", "1"), ("limit", "2")],
])
async def test_invalid_queries_are_rejected(http_case, params):
    response = await get(http_case, params=params)
    assert response.status_code == 422, response.text
    read_only(http_case)


@pytest.mark.asyncio
async def test_foreign_or_deleted_parent_does_not_publish_activity(http_case):
    c = http_case
    c.project.deleted_at = c.now
    response = await get(c)
    assert response.status_code == 404
    assert "Private comment" not in response.text
    read_only(c)


@pytest.mark.asyncio
async def test_membership_revoked_during_read_does_not_publish(http_case):
    c = http_case
    c.access.has_active_platform_membership.side_effect = [True, False]
    response = await get(c)
    assert response.status_code == 403
    assert "Private comment" not in response.text
    read_only(c)


@pytest.mark.asyncio
async def test_client_transport_uses_registered_read_with_bearer(http_case):
    c = http_case
    body = (await get(c)).json()
    calls = []
    def receive(request):
        calls.append(request)
        return httpx.Response(200, json=body)
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(receive),
    ) as http:
        result = await ConstructionApiClient(http=http).project_planned_activities(
            session=PlatformApiClientSession(c.user, c.tenant, SecretStr("local-test")),
            project_id=c.project.id, limit=5,
        )
    assert result.data[0]["id"] == str(c.row.id)
    assert calls[0].headers["authorization"] == "Bearer local-test"
    assert dict(calls[0].url.params) == {"limit": "5"}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "project", "duplicate", "status", "private"])
async def test_transport_rejects_wrong_project_or_nonpublic_projection(http_case, failure):
    c = http_case
    body = (await get(c)).json()
    item = body["data"][0]
    if failure in ("tenant", "project"):
        item[failure + "_id"] = str(uuid4())
    elif failure == "duplicate":
        body["data"].append(deepcopy(item))
    elif failure == "status":
        item["status"] = "completed"
    else:
        item["private_field"] = "hidden"
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).project_planned_activities(
                session=PlatformApiClientSession(c.user, c.tenant, SecretStr("local-test")),
                project_id=c.project.id,
            )


def test_cursor_cannot_be_reused_for_another_actor_or_project(read_case):
    c = read_case
    cursor = ConstructionActivityReadCursor(
        c.tenant, c.user, (None, None, c.project.id, None), c.now, c.row.id,
    )
    token = encode_cursor(cursor, tenant_id=c.tenant, user_id=c.user, project_id=c.project.id)
    for user, project in ((uuid4(), c.project.id), (c.user, uuid4())):
        with pytest.raises(ConstructionActivityValidationError):
            decode_cursor(token, tenant_id=c.tenant, user_id=user, project_id=project)
    with pytest.raises(ConstructionActivityValidationError):
        decode_cursor(
            base64.urlsafe_b64encode(b'{"v":1,"v":1}').decode().rstrip("="),
            tenant_id=c.tenant, user_id=c.user, project_id=c.project.id,
        )


@pytest_asyncio.fixture
async def bot_screen(screen, http_case, monkeypatch):
    c = screen
    storage = MemoryStorage()
    c.state = FSMContext(storage, StorageKey(bot_id=17, chat_id=123, user_id=123))
    await c.state.set_data(deepcopy(c.data))
    item = (await get(http_case)).json()["data"][0]
    item["tenant_id"], item["project_id"] = str(c.tenant), str(c.project)
    c.result = {
        "items": [item], "next_cursor": "opaque-next-position",
        "actor_user_id": str(c.user), "project_id": str(c.project),
        "project_name": "Project", "organization": "Org", "language": "uk",
    }
    c.service.project_planned_activities = AsyncMock(
        side_effect=lambda **kwargs: deepcopy(c.result),
    )
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.service
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    monkeypatch.setattr(team, "edit_or_replace_menu_message", c.sent)
    c.callback.data = "PAV:open:" + c.token
    yield c
    await storage.close()


async def page(c, action):
    data = await c.state.get_data()
    c.callback.message.message_id = data["last_menu_message_id"]
    c.callback.data = handler.cb(data[team.VIEW], action)
    await handler.activities_callback(c.callback, c.state)


@pytest.mark.asyncio
async def test_bot_pages_keep_only_navigation_and_preserve_project_origin(bot_screen):
    c = bot_screen
    await handler.activities_callback(c.callback, c.state)
    data = await c.state.get_data()
    assert data[team.VIEW]["phase"] == "project_activities"
    assert "Private comment" not in repr(data[team.VIEW])
    assert "items" not in data[team.VIEW]
    await page(c, "next")
    args = c.service.project_planned_activities.await_args.kwargs
    assert args["cursor"] == "opaque-next-position"
    assert args["expected_user_id"] == c.user and args["project_id"] == c.project
    assert data[c.source_key]["menu_id"] == data["last_menu_message_id"]


@pytest.mark.asyncio
async def test_navigation_during_read_cannot_overwrite_new_screen(bot_screen):
    c = bot_screen
    async def leave(**kwargs):
        await c.state.update_data(**{areas.CONTEXT: {}})
        return deepcopy(c.result)
    c.service.project_planned_activities.side_effect = leave
    await handler.activities_callback(c.callback, c.state)
    assert (await c.state.get_data())[areas.CONTEXT] == {}
    c.sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorization_loss_does_not_render_cached_activities(bot_screen):
    c = bot_screen
    await handler.activities_callback(c.callback, c.state)
    c.service.project_planned_activities.side_effect = ConstructionApiClientError(
        status_code=403, code="forbidden",
    )
    await page(c, "refresh")
    assert "Private comment" not in c.sent.await_args.kwargs["text"]


def test_labels_and_callbacks_are_bounded():
    assert all(len(values) == len(LANGUAGES) == 7 for values in LABELS.values())
    for action in ("next", "first", "refresh"):
        assert len(handler.cb({"nonce": "a" * 32}, action).encode("utf-8")) <= 64
