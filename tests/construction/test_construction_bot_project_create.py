import json
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import Response
from pydantic import SecretStr

import api.routes.construction_navigation as navigation
from handlers.construction import construction_project_create as handler
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
)
from test_construction_01_project_read_http import project_http, get


@pytest.mark.asyncio
@pytest.mark.parametrize("role,scope,allowed", [
    ("OWNER", "tenant", True),
    ("ADMIN", "tenant", True),
    ("PROJECT_MANAGER", "project", False),
])
async def test_navigation_creation_capability_uses_server_scope_policy(role, scope, allowed):
    tenant, user = uuid4(), uuid4()
    actor = NS(tenant_id=tenant, user_id=user, status="active")
    context = ConstructionAccessContext(
        user, tenant, (role,), tuple(CONSTRUCTION_ROLE_PERMISSIONS[role]),
        (ConstructionGrantContext(role, scope, tenant if scope == "tenant" else uuid4()),),
    )
    result = await navigation.navigation_context(
        request=NS(query_params={}, state=NS(request_id="project-create-navigation-test")), response=Response(),
        actor=actor, context=context,
        access=NS(has_active_platform_membership=AsyncMock(return_value=True)),
        organizations=NS(require_current=AsyncMock(return_value=NS(
            tenant_id=tenant, name="Organization", default_currency="EUR",
        ))),
    )
    body = result if isinstance(result, dict) else json.loads(result.body)
    assert body["data"]["can_create_project"] is allowed


@pytest.fixture
async def project_data(project_http):
    response = await get(project_http)
    assert response.status_code == 200, response.text
    return {
        **response.json()["data"], "name": "New project",
        "address_raw": "Project address", "comment": "Comment",
        "row_version": 1,
    }


@pytest.mark.asyncio
async def test_create_transport_uses_existing_post_and_same_key_after_timeout(project_data, project_http):
    seen = []
    payload = {
        "name": "New project", "address_raw": "Project address",
        "comment": "Comment", "client_id": None, "responsible_user_id": None,
    }
    def transport(request):
        seen.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/v1/construction/projects"
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert request.headers["Idempotency-Key"] == "tg-project:stable"
        assert "X-Tenant-ID" not in request.headers
        assert json.loads(request.content) == payload
        if len(seen) == 1:
            raise httpx.ReadTimeout("uncertain", request=request)
        return httpx.Response(201, json={
            "data": project_data, "meta": {}, "request_id": "project-create-test",
        })
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(transport),
    ) as http:
        api = ConstructionApiClient(http=http)
        options = dict(
            session=PlatformApiClientSession(
                project_http.user, project_http.tenant, SecretStr("test-secret"),
            ),
            payload=payload, idempotency_key="tg-project:stable",
        )
        with pytest.raises(ConstructionApiClientError):
            await api.create_project(**options)
        assert len(seen) == 1
        result = await api.create_project(**options)
        assert result.data["client_id"] is None
    assert seen[0].content == seen[1].content


@pytest.mark.asyncio
async def test_adapter_binds_self_assignment_and_rechecks_identity():
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("test-secret"))
    create = AsyncMock(return_value=NS(data={"id": str(uuid4())}))
    entry = ConstructionBotApiEntry(
        api=NS(construction=NS(create_project=create)), context=None, sessions=None,
    )
    home = {"language": "uk", "name": "Organization", "can_create_project": True}
    entry._presentation = AsyncMock(return_value=(session, home))
    options = dict(
        telegram_user_id=123, tenant_id=session.tenant_id,
        expected_user_id=session.user_id,
        payload={"name": "Project", "address_raw": "Address", "comment": None},
        assign_self=True, idempotency_key="stable",
    )
    await entry.create_project(**options)
    sent = create.await_args.kwargs["payload"]
    assert sent["client_id"] is None
    assert sent["responsible_user_id"] == str(session.user_id)
    create.reset_mock()
    with pytest.raises(ConstructionApiClientError):
        await entry.create_project(**{**options, "expected_user_id": uuid4()})
    create.assert_not_awaited()
    home["can_create_project"] = False
    with pytest.raises(ConstructionApiClientError):
        await entry.create_project(**options)
    create.assert_not_awaited()


@pytest.fixture
def wizard(monkeypatch):
    tenant, user, project = uuid4(), uuid4(), uuid4()
    c = NS()
    c.data = {handler.CONTEXT: {
        "telegram_user_id": 123, "tenant_id": str(tenant),
    }}
    async def update(**values):
        c.data.update(values)
    c.state = NS(
        get_data=AsyncMock(side_effect=lambda: c.data),
        update_data=AsyncMock(side_effect=update),
        get_state=AsyncMock(return_value=None),
    )
    sender = NS(id=123, is_bot=False, language_code="uk")
    c.message = NS(
        from_user=sender, chat=NS(id=123, type="private"),
        message_id=1, text=None, answer=AsyncMock(),
    )
    c.callback = NS(
        from_user=sender, message=c.message, answer=AsyncMock(), data="PF:new",
    )
    c.service = NS(
        project_address_suggestions=AsyncMock(return_value={
            "items": [], "actor_user_id": str(user), "tenant_id": str(tenant),
            "language": "uk",
        }),
        project_form=AsyncMock(return_value={
            "actor_user_id": str(user), "organization": "Organization",
            "language": "uk",
        }),
        create_project=AsyncMock(return_value={"item": {"id": str(project)}}),
    )
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.service
    async def show(event, state, form, text, rows, **kwargs):
        if form is not None:
            form["menu_id"] = 1
            await state.update_data(**{handler.FORM: form})
        await state.update_data(last_menu_message_id=1)
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    monkeypatch.setattr(handler, "show", show)
    return c


async def click(c, action):
    c.callback.data = handler.cb(c.data[handler.FORM], action)
    await handler.project_create_callback(c.callback, c.state)


async def review(c):
    await handler.project_create_callback(c.callback, c.state)
    for value in ("Project", "Address"):
        c.message.text = value
        await handler.project_create_message(c.message, c.state)
    await click(c, "address_continue")
    await click(c, "skip")
    await click(c, "none")
    assert c.data[handler.FORM]["phase"] == "review"
    c.service.create_project.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_review_and_cancel_do_not_create_project(wizard):
    c = wizard
    await review(c)
    await click(c, "cancel")
    assert c.data[handler.FORM] == {}
    c.service.create_project.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_submission_freezes_body_and_replays_same_operation(wizard):
    c = wizard
    await review(c)
    receipt = c.service.create_project.return_value
    c.service.create_project.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_timeout"),
        receipt,
    ]
    await click(c, "submit")
    first = c.service.create_project.await_args.kwargs
    assert first["payload"] == {
        "name": "Project", "address_raw": "Address", "comment": None,
    }
    await click(c, "edit")
    assert c.service.create_project.await_count == 1
    assert c.data[handler.FORM]["phase"] == "pending"
    await click(c, "submit")
    assert c.service.create_project.await_args.kwargs == first
    assert c.data[handler.FORM]["phase"] == "done"
    await click(c, "submit")
    assert c.service.create_project.await_count == 2


@pytest.mark.asyncio
async def test_project_input_does_not_capture_another_form_or_legacy_state(wizard):
    c = wizard
    await handler.project_create_callback(c.callback, c.state)
    c.message.text = "Project"
    check = handler.ProjectFormInput()
    assert await check(c.message, c.state)
    c.data["construction_activity_plan_form"] = {"menu_id": 1}
    assert not await check(c.message, c.state)
    c.data["construction_activity_plan_form"] = {}
    c.state.get_state.return_value = "legacy:input"
    assert not await check(c.message, c.state)


@pytest.mark.asyncio
async def test_suspension_preserves_pending_operation_but_hides_input(wizard):
    c = wizard
    await review(c)
    c.service.create_project.side_effect = ConstructionApiClientError(
        status_code=504, code="api_timeout",
    )
    await click(c, "submit")
    original = dict(c.data[handler.FORM])
    await handler.suspend_project_form(c.state)
    saved = c.data[handler.FORM]
    assert saved["id"] == original["id"]
    assert saved["payload"] == original["payload"]
    assert saved["attempted_at"] == original["attempted_at"]
    assert saved["menu_id"] is None

# standalone_project_address_suggestions_v1

# project_action_visibility_v1
@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [False, True])
async def test_created_project_actions_require_fresh_read(
    wizard, project_data, monkeypatch, allowed,
):
    c = wizard
    await review(c)
    captured = []
    original_show = handler.show

    async def capture(event, state, form, text, rows, **kwargs):
        captured.append(rows)
        await original_show(event, state, form, text, rows, **kwargs)

    monkeypatch.setattr(handler, "show", capture)
    c.service.read_created_project = AsyncMock()
    await click(c, "submit")
    prefixes = ("PDE:open:", "AP:project:", "PT:open:")

    def action_prefixes():
        callbacks = [
            button.callback_data for row in captured[-1] for button in row
        ]
        return {
            prefix for prefix in prefixes
            if any(value.startswith(prefix) for value in callbacks)
        }

    assert action_prefixes() == set()
    c.service.read_created_project.assert_not_awaited()
    form = c.data[handler.FORM]
    c.service.read_created_project.return_value = {
        "item": {
            **project_data,
            "id": form["project_id"],
            "tenant_id": form["tenant_id"],
        },
        "actor_user_id": form["actor_user_id"],
        "organization": "Organization", "language": "uk",
        "can_edit_project": allowed,
        "can_plan_activity": allowed,
        "can_open_team": allowed,
        "can_create_project": False,
    }
    await click(c, "open")
    c.service.read_created_project.assert_awaited_once()
    assert action_prefixes() == (set(prefixes) if allowed else set())
