"""Tenant grant transport and real MemoryStorage UI; no external operations."""
from contextlib import asynccontextmanager
from copy import deepcopy
import json
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

from handlers.construction import construction_organization_access as handler
from handlers.construction import construction_team as team
from handlers.construction import construction_team_permissions as permissions
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, ConstructionApiResult,
    PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.construction_organization_access import LANGUAGES, LABELS


def grant(target, tenant, *, expiry=None):
    return {
        "id": str(uuid4()), "user_id": str(target), "role": "ADMIN",
        "scope_type": "tenant", "scope_id": str(tenant), "status": "active",
        "expires_at": expiry, "granted_at": "2026-10-28T09:00:00Z",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry", [None, "2035-12-31T18:00:00Z"])
async def test_transport_sends_exact_tenant_role_and_frozen_key(expiry):
    target, tenant, actor = uuid4(), uuid4(), uuid4()
    received = []
    item = grant(target, tenant, expiry=expiry)
    def receive(request):
        received.append(request)
        return httpx.Response(201, json={"data": item, "meta": {}, "request_id": "assign"})
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(receive),
    ) as http:
        result = await ConstructionApiClient(http=http).create_organization_access(
            session=PlatformApiClientSession(actor, tenant, SecretStr("local-test")),
            target_user_id=target, role="ADMIN", expires_at=expiry,
            idempotency_key="frozen-key",
        )
    request = received[0]
    assert len(received) == 1 and request.method == "POST"
    assert request.url.path == "/api/v1/construction/team/members/" + str(target) + "/access-grants"
    assert request.headers["idempotency-key"] == "frozen-key"
    body = json.loads(request.content)
    assert body == {
        "role": "ADMIN", "scope_type": "tenant", "scope_id": str(tenant),
        "expires_at": expiry,
    }
    assert result.data["id"] == item["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["target", "tenant", "role", "expiry", "status"])
async def test_transport_rejects_mismatched_grant_receipt(failure):
    target, tenant = uuid4(), uuid4()
    item = grant(target, tenant)
    if failure == "target":
        item["user_id"] = str(uuid4())
    elif failure == "tenant":
        item["scope_id"] = str(uuid4())
    elif failure == "role":
        item["role"] = "OWNER"
    elif failure == "expiry":
        item["expires_at"] = "2035-12-31T18:00:00Z"
    else:
        item["status"] = "revoked"
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(
            201, json={"data": item, "meta": {}, "request_id": "assign"},
        )),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).create_organization_access(
                session=PlatformApiClientSession(uuid4(), tenant, SecretStr("local-test")),
                target_user_id=target, role="ADMIN", expires_at=None, idempotency_key="key",
            )


@pytest.mark.asyncio
async def test_options_are_bound_to_current_tenant_and_server_policy():
    tenant = uuid4()
    received = []
    def receive(request):
        received.append(request)
        return httpx.Response(200, json={
            "data": {
                "scope_type": "tenant", "scope_id": str(tenant),
                "roles": [{"role": "ADMIN", "permissions": ["construction.projects.read"]}],
                "permissions_read_only": True,
            }, "meta": {}, "request_id": "options",
        })
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(receive),
    ) as http:
        result = await ConstructionApiClient(http=http).organization_role_options(
            session=PlatformApiClientSession(uuid4(), tenant, SecretStr("local-test")),
        )
    assert result == ["ADMIN"]
    assert dict(received[0].url.params) == {"scope_type": "tenant", "scope_id": str(tenant)}


@pytest.mark.asyncio
async def test_entry_filters_existing_role_but_does_not_invent_available_roles():
    tenant, actor, target = uuid4(), uuid4(), uuid4()
    session = PlatformApiClientSession(actor, tenant, SecretStr("local-test"))
    client = NS(
        team_members=AsyncMock(return_value=ConstructionApiResult([{
            "user_id": str(target), "display_name": "Private name",
            "grants": [{"role": "ADMIN", "scope_type": "tenant", "scope_id": str(tenant)}],
        }], {}, "members")),
        organization_role_options=AsyncMock(return_value=["ADMIN"]),
    )
    entry = ConstructionBotApiEntry(api=NS(construction=client), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(session, {"name": "Org", "language": "uk"}))
    result = await entry.organization_access_context(
        telegram_user_id=123, tenant_id=tenant, expected_user_id=actor, target_user_id=target,
    )
    assert result["roles"] == []
    client.organization_role_options.assert_awaited_once_with(session=session)


@pytest.mark.asyncio
async def test_creation_entry_uses_exact_platform_identity_without_discovery():
    tenant, actor, target = uuid4(), uuid4(), uuid4()
    session = PlatformApiClientSession(actor, tenant, SecretStr("local-test"))
    client = NS(create_organization_access=AsyncMock(return_value=ConstructionApiResult(
        grant(target, tenant), {}, "grant",
    )))
    entry = ConstructionBotApiEntry(api=NS(construction=client), context=None, sessions=None)
    entry._client_identity_session = AsyncMock(return_value=(session, "uk"))
    entry._presentation = AsyncMock(side_effect=AssertionError("No discovery on replay"))
    result = await entry.create_organization_access(
        telegram_user_id=123, tenant_id=tenant, expected_user_id=actor,
        target_user_id=target, role="ADMIN", expires_at=None, idempotency_key="frozen",
    )
    client.create_organization_access.assert_awaited_once_with(
        session=session, target_user_id=target, role="ADMIN", expires_at=None,
        idempotency_key="frozen",
    )
    entry._presentation.assert_not_awaited()
    assert result["actor_user_id"] == str(actor)


@pytest_asyncio.fixture
async def screen(monkeypatch):
    c = NS(user=uuid4(), target=uuid4(), tenant=uuid4())
    c.storage = MemoryStorage()
    c.state = FSMContext(c.storage, StorageKey(bot_id=17, chat_id=123, user_id=123))
    c.view = {
        "telegram_user_id": 123, "tenant_id": str(c.tenant),
        "actor_user_id": str(c.user), "rights_user_id": str(c.target),
        "phase": "rights_scopes", "nonce": "source", "offset": 0, "menu_id": 1,
    }
    await c.state.update_data(**{
        team.VIEW: deepcopy(c.view), "last_menu_message_id": 1,
        team.CONTEXT: {"telegram_user_id": 123, "tenant_id": str(c.tenant)},
    })
    c.callback = NS(
        data=handler.cb(c.view, "open"), answer=AsyncMock(),
        from_user=NS(id=123, is_bot=False, language_code="uk"),
        message=NS(message_id=1, chat=NS(id=123, type="private"), answer=AsyncMock()),
    )
    c.context = {
        "roles": ["ADMIN"], "member_name": "Private member name",
        "actor_user_id": str(c.user), "organization": "Organization", "language": "uk",
    }
    async def create(**kwargs):
        return {
            "item": grant(c.target, c.tenant, expiry=kwargs["expires_at"]),
            "actor_user_id": str(c.user), "language": "uk",
        }
    c.service = NS(
        organization_access_context=AsyncMock(side_effect=lambda **kwargs: deepcopy(c.context)),
        create_organization_access=AsyncMock(side_effect=create),
    )
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.service
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    c.sent = AsyncMock(return_value=NS(message_id=1))
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", c.sent)
    yield c
    await c.storage.close()


async def click(c, action, value=None):
    data = await c.state.get_data()
    c.callback.message.message_id = data["last_menu_message_id"]
    c.callback.data = handler.cb(data[team.VIEW], action, value)
    await permissions.permissions_callback(c.callback, c.state)


async def review(c):
    await click(c, "open")
    await click(c, "role", 0)
    await click(c, "none")


@pytest.mark.asyncio
async def test_explicit_role_expiry_review_then_one_command(screen):
    c = screen
    await review(c)
    data = await c.state.get_data()
    assert handler.phase(data[team.VIEW]) == "review"
    assert "Private member name" not in repr(data)
    c.service.create_organization_access.assert_not_awaited()
    await click(c, "submit")
    data = await c.state.get_data()
    assert handler.phase(data[team.VIEW]) == "done"
    args = c.service.create_organization_access.await_args.kwargs
    assert args["target_user_id"] == c.target and args["tenant_id"] == c.tenant
    assert args["expected_user_id"] == c.user and args["role"] == "ADMIN"
    assert args["expires_at"] is None
    assert "permissions" not in args
    await click(c, "submit")
    assert c.service.create_organization_access.await_count == 1


@pytest.mark.asyncio
async def test_date_input_is_canonical_and_never_creates_before_confirmation(screen):
    c = screen
    await click(c, "open")
    await click(c, "role", 0)
    await click(c, "date")
    message = NS(
        text="2035-12-31 18:00+02:00",
        from_user=c.callback.from_user, chat=c.callback.message.chat,
        answer=AsyncMock(return_value=NS(message_id=2)),
    )
    assert await handler.OrganizationAccessDateInput()(message, c.state)
    await handler.date_message(message, c.state)
    data = await c.state.get_data()
    assert handler.phase(data[team.VIEW]) == "review"
    assert data[team.VIEW]["assign_expires_at"] == "2035-12-31T16:00:00Z"
    c.service.create_organization_access.assert_not_awaited()
    await click(c, "submit")
    assert c.service.create_organization_access.await_args.kwargs["expires_at"] == "2035-12-31T16:00:00Z"


@pytest.mark.asyncio
async def test_role_revoked_during_selection_is_not_accepted(screen):
    c = screen
    await click(c, "open")
    c.context["roles"] = []
    await click(c, "role", 0)
    assert handler.phase((await c.state.get_data())[team.VIEW]) == "denied"
    c.service.create_organization_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_result_retries_exact_role_target_expiry_and_key(screen):
    c = screen
    await review(c)
    original_create = c.service.create_organization_access.side_effect
    c.service.create_organization_access.side_effect = ConstructionApiClientError(
        status_code=504, code="api_timeout",
    )
    await click(c, "submit")
    original = deepcopy(c.service.create_organization_access.await_args.kwargs)
    c.context["roles"] = []
    reads = c.service.organization_access_context.await_count
    c.service.create_organization_access.side_effect = original_create
    await click(c, "submit")
    assert c.service.create_organization_access.await_args.kwargs == original
    assert c.service.organization_access_context.await_count == reads
    assert (await c.state.get_data())[handler.ATTEMPT]["status"] == "done"


@pytest.mark.asyncio
async def test_pending_request_survives_team_navigation_and_blocks_new_target(screen):
    c = screen
    await review(c)
    c.service.create_organization_access.side_effect = ConstructionApiClientError(
        status_code=503, code="service_unavailable",
    )
    await click(c, "submit")
    before = deepcopy((await c.state.get_data())[handler.ATTEMPT])
    another = {**c.view, "nonce": "another", "rights_user_id": str(uuid4())}
    await c.state.update_data(**{team.VIEW: another})
    await click(c, "open")
    data = await c.state.get_data()
    assert data[handler.ATTEMPT] == before
    assert data[team.VIEW]["rights_user_id"] == str(c.target)
    assert c.service.create_organization_access.await_count == 1


@pytest.mark.asyncio
async def test_navigation_during_creation_cannot_overwrite_new_screen(screen):
    c = screen
    await review(c)
    async def leave(**kwargs):
        await c.state.update_data(**{team.VIEW: {}})
        return {
            "item": grant(c.target, c.tenant), "actor_user_id": str(c.user), "language": "uk",
        }
    c.service.create_organization_access.side_effect = leave
    await click(c, "submit")
    data = await c.state.get_data()
    assert data[team.VIEW] == {}
    assert data[handler.ATTEMPT]["status"] == "pending"


@pytest.mark.asyncio
async def test_navigation_invalidates_unsigned_review(screen):
    c = screen
    await review(c)
    c.callback.data = "CS_HOME"
    await permissions.permissions_navigation_middleware(
        AsyncMock(), c.callback, {"state": c.state},
    )
    assert (await c.state.get_data())[team.VIEW] == {}
    c.service.create_organization_access.assert_not_awaited()


def test_seven_languages_and_bounded_callback_data():
    assert all(len(values) == len(LANGUAGES) == 7 for values in LABELS.values())
    for action in ("open", "resume", "role", "date", "none", "submit"):
        assert len(handler.cb({"nonce": "a" * 32}, action, 0).encode("utf-8")) <= 64
