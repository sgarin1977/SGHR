import json
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import Response
from pydantic import SecretStr

from handlers.construction import construction_team as handler
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, ConstructionApiResult,
    PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
)
from ui.construction_team import team_text


def member(user, tenant):
    grant = str(uuid4())
    return {
        "user_id": str(user), "display_name": "Member name",
        "email": "member@example.com", "roles": ["OWNER"],
        "grants": [{
            "id": grant, "role": "OWNER", "scope_type": "tenant",
            "scope_id": str(tenant), "expires_at": None,
        }],
        "scoped_permissions": [{
            "grant_id": grant, "role": "OWNER", "scope_type": "tenant",
            "scope_id": str(tenant), "permissions": ["construction.team.manage"],
        }],
    }


@pytest.mark.asyncio
async def test_transport_calls_existing_get_with_platform_bearer():
    user, tenant, project = uuid4(), uuid4(), uuid4()
    item = member(user, tenant)
    def transport(request):
        assert request.method == "GET"
        assert request.url.path == "/api/v1/construction/team/members"
        assert request.headers["Authorization"] == "Bearer local-test-secret"
        assert dict(request.url.params) == {"project_id": str(project)}
        assert "X-Tenant-ID" not in request.headers
        return httpx.Response(200, json={
            "data": [item], "meta": {}, "request_id": "team-test",
        })
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        result = await ConstructionApiClient(http=http).team_members(
            session=PlatformApiClientSession(user, tenant, SecretStr("local-test-secret")),
            project_id=project,
        )
    assert result.data == [item]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "duplicate_user", "duplicate_grant", "foreign_tenant",
    "role", "permission_scope", "expiry", "extra",
])
async def test_transport_rejects_malformed_team_projection(failure):
    user, tenant = uuid4(), uuid4()
    item = member(user, tenant)
    data = [item]
    if failure == "duplicate_user":
        data.append(deepcopy(item))
    elif failure == "duplicate_grant":
        item["grants"].append(deepcopy(item["grants"][0]))
    elif failure == "foreign_tenant":
        item["grants"][0]["scope_id"] = str(uuid4())
    elif failure == "role":
        item["roles"] = ["ADMIN"]
    elif failure == "permission_scope":
        item["scoped_permissions"][0]["grant_id"] = str(uuid4())
    elif failure == "expiry":
        item["grants"][0]["expires_at"] = "2026-10-23T12:00:00"
    else:
        item["private_marker"] = "must-not-leak"
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "data": data, "meta": {}, "request_id": "team-test",
        })),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).team_members(
                session=PlatformApiClientSession(
                    user, tenant, SecretStr("local-test-secret"),
                ),
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["subject", "permission"])
async def test_entry_checks_fresh_subject_and_server_capability_before_team_get(failure):
    user, tenant = uuid4(), uuid4()
    session = PlatformApiClientSession(user, tenant, SecretStr("local-test-secret"))
    construction = NS(team_members=AsyncMock())
    entry = ConstructionBotApiEntry(
        api=NS(construction=construction), context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(session, {
        "name": "Organization", "language": "uk",
        "can_manage_team": failure != "permission",
    }))
    with pytest.raises(ConstructionApiClientError):
        await entry.team_members(
            telegram_user_id=123, tenant_id=tenant,
            expected_user_id=uuid4() if failure == "subject" else user,
        )
    entry._presentation.assert_awaited_once()
    construction.team_members.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role,scope,allowed", [
    ("OWNER", "tenant", True),
    ("ADMIN", "tenant", True),
    ("PROJECT_MANAGER", "project", False),
    ("OBSERVER", "project", False),
])
async def test_navigation_team_capability_uses_permission_and_scope(role, scope, allowed):
    import api.routes.construction_navigation as navigation
    user, tenant = uuid4(), uuid4()
    actor = NS(user_id=user, tenant_id=tenant, status="active")
    context = ConstructionAccessContext(
        user, tenant, (role,), tuple(CONSTRUCTION_ROLE_PERMISSIONS[role]),
        (ConstructionGrantContext(
            role, scope, tenant if scope == "tenant" else uuid4(),
        ),),
    )
    result = await navigation.navigation_context(
        request=NS(query_params={}, state=NS(request_id="team-navigation")),
        response=Response(), actor=actor, context=context,
        access=NS(has_active_platform_membership=AsyncMock(return_value=True)),
        organizations=NS(require_current=AsyncMock(return_value=NS(
            tenant_id=tenant, name="Organization", default_currency="EUR",
        ))),
    )
    body = result if isinstance(result, dict) else json.loads(result.body)
    assert body["data"]["can_manage_team"] is allowed


@pytest.fixture
def screen(monkeypatch):
    c = NS(user=uuid4(), tenant=uuid4())
    c.data = {
        handler.CONTEXT: {"telegram_user_id": 123, "tenant_id": str(c.tenant)},
        "last_menu_message_id": 1,
    }
    async def update(**values):
        c.data.update(values)
    c.state = NS(
        get_data=AsyncMock(side_effect=lambda: c.data),
        update_data=AsyncMock(side_effect=update),
        get_state=AsyncMock(return_value=None),
    )
    c.callback = NS(
        data="CT:open", from_user=NS(id=123, is_bot=False, language_code="uk"),
        answer=AsyncMock(),
        message=NS(
            message_id=1, chat=NS(id=123, type="private"), answer=AsyncMock(),
        ),
    )
    c.result = {
        "items": [member(uuid4(), c.tenant) for _ in range(6)],
        "actor_user_id": str(c.user), "organization": "Organization", "language": "uk",
    }
    c.service = NS(team_members=AsyncMock(return_value=c.result))
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.service
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    monkeypatch.setattr(handler, "suspend_forms", AsyncMock())
    c.sent = AsyncMock(return_value=NS(message_id=1))
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", c.sent)
    return c


@pytest.mark.asyncio
async def test_page_refreshes_api_and_fsm_contains_no_team_payload(screen):
    c = screen
    await handler.team_callback(c.callback, c.state)
    view = c.data[handler.VIEW]
    old_nonce = view["nonce"]
    c.callback.data = "CT:page:" + old_nonce + ":5"
    await handler.team_callback(c.callback, c.state)
    assert c.service.team_members.await_count == 2
    assert c.service.team_members.await_args.kwargs["expected_user_id"] == c.user
    assert c.data[handler.VIEW]["offset"] == 5
    assert c.data[handler.VIEW]["nonce"] != old_nonce
    assert "member@example.com" not in repr(c.data)
    assert "Member name" not in repr(c.data)
    assert "scoped_permissions" not in repr(c.data)
    await handler.team_callback(c.callback, c.state)
    assert c.service.team_members.await_count == 2


@pytest.mark.asyncio
async def test_permission_revocation_does_not_show_cached_team(screen):
    c = screen
    await handler.team_callback(c.callback, c.state)
    c.callback.data = "CT:page:" + c.data[handler.VIEW]["nonce"] + ":0"
    c.service.team_members.side_effect = ConstructionApiClientError(
        status_code=403, code="forbidden",
    )
    await handler.team_callback(c.callback, c.state)
    assert c.data[handler.VIEW] == {}
    text = c.sent.await_args.kwargs["text"]
    assert "member@example.com" not in text and "Member name" not in text


@pytest.mark.asyncio
async def test_foreign_sender_cannot_reuse_saved_team_page(screen):
    c = screen
    await handler.team_callback(c.callback, c.state)
    c.callback.data = "CT:page:" + c.data[handler.VIEW]["nonce"] + ":0"
    c.callback.from_user.id = c.callback.message.chat.id = 999
    c.service.team_members.reset_mock()
    await handler.team_callback(c.callback, c.state)
    c.service.team_members.assert_not_awaited()


@pytest.mark.parametrize("language", ["uk", "ru", "en", "pt", "pl", "de", "nl"])
def test_localized_team_does_not_expose_raw_ids_or_permission_codes(language):
    user, tenant = uuid4(), uuid4()
    result = {
        "items": [member(user, tenant)],
        "actor_user_id": str(user), "organization": "Organization",
    }
    text = team_text(result, offset=0, language=language)
    assert 0 < len(text) < 3800
    assert str(user) not in text and str(tenant) not in text
    assert "construction.team.manage" not in text
    assert "OWNER" not in text
