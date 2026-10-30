"""Targeted local API/wizard tests; no native or deployed acceptance."""
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_tenant_module_access_service, get_tenant_suite_access_service
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from api.routes import construction_project_area_create as routes
from construction_rate_limit_fixture import with_local_construction_rate_limit
from handlers.construction import construction_area_create as wizard
from handlers.construction import construction_project_areas as areas
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from test_construction_01_area_create_workflow import area_case, counts
from test_construction_01_project_areas_http import area_http, get as get_areas
from test_construction_bot_project_areas import screen


@pytest.fixture
def create_http(area_case):
    c = area_case
    c.app = with_local_construction_rate_limit(create_app())
    c.suite = NS(require_enabled=AsyncMock())
    c.module = NS(require_enabled=AsyncMock())
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_access_context] = lambda: c.context
    c.app.dependency_overrides[get_construction_scoped_session] = lambda: c.session
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.module
    c.app.dependency_overrides[routes.get_area_create_workflow] = lambda: c.workflow
    return c


async def post(c, *, body=None, key="area-http-key"):
    if not c.sync.in_transaction():
        c.sync.begin()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        return await client.post(
            f"/api/v1/construction/projects/{c.project_id}/areas",
            json=body if body is not None else {
                "area_type": "ROOM", "name": "Kitchen", "sort_order": None,
            },
            headers={"Idempotency-Key": key} if key is not None else {},
        )


@pytest.mark.asyncio
async def test_http_creation_and_terminal_parent_replay_share_one_receipt(create_http):
    c = create_http
    first = await post(c)
    assert first.status_code == 201, first.text
    assert counts(c) == (1, 1, 1)
    c.project.status = "completed"
    replay = await post(c)
    assert replay.status_code == 201, replay.text
    assert replay.json()["data"] == first.json()["data"]
    assert counts(c) == (1, 1, 1)
    assert first.json()["data"]["sort_order"] is None


@pytest.mark.asyncio
async def test_same_key_changed_body_conflicts(create_http):
    c = create_http
    assert (await post(c)).status_code == 201
    response = await post(c, body={
        "area_type": "ROOM", "name": "Another", "sort_order": None,
    })
    assert response.status_code == 409, response.text
    assert counts(c) == (1, 1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [
    {"tenant_id": str(uuid4())}, {"created_by": str(uuid4())},
    {"measurement_id": str(uuid4())}, {"row_version": 1},
    {"sort_order": True},
])
async def test_http_rejects_foreign_context_and_invalid_fields(create_http, extra):
    c = create_http
    response = await post(c, body={
        "area_type": "ROOM", "name": "Kitchen", "sort_order": None, **extra,
    })
    assert response.status_code == 422, response.text
    assert counts(c) == (0, 0, 0)


@pytest.mark.asyncio
async def test_http_requires_key(create_http):
    response = await post(create_http, key=None)
    assert response.status_code == 422, response.text
    assert counts(create_http) == (0, 0, 0)


@pytest.mark.asyncio
async def test_terminal_project_cannot_create_new_area(create_http):
    c = create_http
    c.project.status = "cancelled"
    response = await post(c)
    assert response.status_code == 409, response.text
    assert counts(c) == (0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [
    ("active", True), ("completed", False), ("cancelled", False),
])
async def test_capability_respects_parent_lifecycle(area_http, status, expected):
    area_http.project.status = status
    response = await get_areas(area_http)
    assert response.status_code == 200, response.text
    assert response.json()["meta"]["capabilities"]["can_create_area"] is expected


@pytest.fixture
async def form_case(screen, monkeypatch):
    c = screen
    c.result["can_create_area"] = True
    c.service.create_project_area = AsyncMock(return_value={
        "item": {"id": str(uuid4())}, "language": "uk",
    })
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.service
    monkeypatch.setattr(wizard, "construction_bot_api_entry", entry)
    monkeypatch.setattr(wizard, "edit_or_replace_menu_message", c.sent)
    await areas.areas_callback(c.callback, c.state)
    c.callback.data = "AC:new:" + c.data[areas.VIEW]["nonce"]
    c.callback.message.message_id = c.data["last_menu_message_id"]
    await wizard.area_callback(c.callback, c.state)
    assert c.data[wizard.FORM]["phase"] == "type"
    return c


async def click(c, action):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = wizard.cb(c.data[wizard.FORM], action)
    await wizard.area_callback(c.callback, c.state)


async def review(c):
    await click(c, "ROOM")
    message = NS(
        from_user=c.callback.from_user, chat=c.callback.message.chat,
        text="Kitchen", answer=AsyncMock(return_value=NS(message_id=1)),
    )
    await wizard.area_name(message, c.state)
    assert c.data[wizard.FORM]["phase"] == "review"
    c.service.create_project_area.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_does_not_create(form_case):
    c = form_case
    await click(c, "cancel")
    c.service.create_project_area.assert_not_awaited()
    assert c.data[wizard.FORM] == {}


@pytest.mark.asyncio
async def test_wizard_explicit_submit_and_stale_button(form_case):
    c = form_case
    await review(c)
    await click(c, "submit")
    assert c.data[wizard.FORM]["phase"] == "done"
    c.service.create_project_area.assert_awaited_once()
    kwargs = c.service.create_project_area.await_args.kwargs
    assert kwargs["project_id"] == c.project
    assert kwargs["expected_user_id"] == c.user
    assert kwargs["payload"] == {
        "area_type": "ROOM", "name": "Kitchen", "sort_order": None,
    }
    await wizard.area_callback(c.callback, c.state)
    c.service.create_project_area.assert_awaited_once()


@pytest.mark.asyncio
async def test_uncertain_retry_keeps_body_and_key(form_case):
    c = form_case
    await review(c)
    c.service.create_project_area.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_timeout"),
        {"item": {"id": str(uuid4())}, "language": "uk"},
    ]
    await click(c, "submit")
    assert c.data[wizard.FORM]["phase"] == "pending"
    await click(c, "edit")
    assert c.service.create_project_area.await_count == 1
    await click(c, "submit")
    calls = c.service.create_project_area.await_args_list
    assert len(calls) == 2
    assert calls[0].kwargs == calls[1].kwargs
    assert c.data[wizard.FORM]["phase"] == "done"


@pytest.mark.asyncio
async def test_back_preserves_pending_request(form_case):
    c = form_case
    await review(c)
    c.service.create_project_area.side_effect = ConstructionApiClientError(
        status_code=504, code="api_timeout",
    )
    await click(c, "submit")
    saved = dict(c.data[wizard.FORM])
    c.callback.data = f'PA:page:{c.data[areas.VIEW]["nonce"]}:0'
    c.callback.message.message_id = c.data["last_menu_message_id"]
    await areas.areas_callback(c.callback, c.state)
    current = c.data[wizard.FORM]
    assert current["id"] == saved["id"]
    assert current["payload"] == saved["payload"]
    assert current["attempted_at"] == saved["attempted_at"]
    assert current["menu_id"] is None


@pytest.mark.asyncio
async def test_changed_identity_stops_before_mutation():
    user, tenant = uuid4(), uuid4()
    api = NS(create_project_area=AsyncMock())
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local-test-secret")),
        {"name": "Organization", "language": "uk"},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.create_project_area(
            telegram_user_id=123, tenant_id=tenant, project_id=uuid4(),
            expected_user_id=uuid4(), payload={}, idempotency_key="test",
        )
    api.create_project_area.assert_not_awaited()
