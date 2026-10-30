"""Platform removal transport and explicit team confirmation; no external operations."""
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

from handlers.construction import construction_organization_remove as handler
from handlers.construction import construction_team as team
from handlers.construction import construction_team_permissions as permissions
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, ConstructionApiResult,
    PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.construction_organization_remove import LANGUAGES, LABELS


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [True, False])
async def test_transport_uses_platform_route_frozen_key_and_empty_body(changed):
    target = uuid4()
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("local-removal-test"))
    requests = []
    def receive(request):
        requests.append(request)
        return httpx.Response(200, json={
            "data": {"user_id": str(target), "changed": changed},
            "meta": {}, "request_id": "removal-test",
        })
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(receive),
    ) as http:
        result = await ConstructionApiClient(http=http).remove_organization_member(
            session=session, target_user_id=target, idempotency_key="original-key",
        )
    assert result.data == {"user_id": str(target), "changed": changed}
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v1/platform/organizations/current/members/" + str(target) + "/remove"
    assert not request.url.query
    assert json.loads(request.content) == {}
    assert request.headers["idempotency-key"] == "original-key"
    assert request.headers["authorization"] == "Bearer local-removal-test"
    assert "x-tenant-id" not in request.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["target", "bool", "extra"])
async def test_transport_rejects_receipt_for_another_command(failure):
    target = uuid4()
    receipt = {"user_id": str(target), "changed": True}
    if failure == "target":
        receipt["user_id"] = str(uuid4())
    elif failure == "bool":
        receipt["changed"] = 1
    else:
        receipt["tenant_id"] = str(uuid4())
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "data": receipt, "meta": {}, "request_id": "removal-test",
        })),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).remove_organization_member(
                session=PlatformApiClientSession(uuid4(), uuid4(), SecretStr("local-test")),
                target_user_id=target, idempotency_key="original-key",
            )


@pytest.mark.asyncio
async def test_entry_keeps_platform_authorization_and_does_not_reread_target():
    session = PlatformApiClientSession(uuid4(), uuid4(), SecretStr("local-test"))
    target = uuid4()
    result = ConstructionApiResult({"user_id": str(target), "changed": True}, {}, "receipt")
    client = NS(remove_organization_member=AsyncMock(return_value=result))
    entry = ConstructionBotApiEntry(api=NS(construction=client), context=None, sessions=None)
    entry._client_identity_session = AsyncMock(return_value=(session, "uk"))
    entry._presentation = AsyncMock(side_effect=AssertionError("No Construction authority substitution"))
    response = await entry.remove_organization_member(
        telegram_user_id=123, tenant_id=session.tenant_id,
        expected_user_id=session.user_id, target_user_id=target,
        idempotency_key="same-key",
    )
    entry._client_identity_session.assert_awaited_once_with(
        telegram_user_id=123, tenant_id=session.tenant_id,
        expected_user_id=session.user_id,
    )
    entry._presentation.assert_not_awaited()
    client.remove_organization_member.assert_awaited_once_with(
        session=session, target_user_id=target, idempotency_key="same-key",
    )
    assert response["item"] == result.data
    assert response["actor_user_id"] == str(session.user_id)


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
        team.CONTEXT: {"telegram_user_id": 123, "tenant_id": str(c.tenant)},
        team.VIEW: deepcopy(c.view), "last_menu_message_id": 1,
    })
    c.callback = NS(
        data=handler.cb(c.view, "open"), answer=AsyncMock(),
        from_user=NS(id=123, is_bot=False, language_code="uk"),
        message=NS(message_id=1, chat=NS(id=123, type="private"), answer=AsyncMock()),
    )
    c.result = {
        "items": [{
            "user_id": str(c.target), "display_name": "Private member name",
            "email": "member-private@example.test",
        }],
        "actor_user_id": str(c.user), "organization": "Organization", "language": "uk",
    }
    c.service = NS(
        team_members=AsyncMock(side_effect=lambda **kwargs: deepcopy(c.result)),
        remove_organization_member=AsyncMock(return_value={
            "item": {"user_id": str(c.target), "changed": True},
            "actor_user_id": str(c.user), "language": "uk",
        }),
    )
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.service
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    c.sent = AsyncMock(return_value=NS(message_id=1))
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", c.sent)
    yield c
    await c.storage.close()


async def click(c, action):
    data = await c.state.get_data()
    c.callback.message.message_id = data["last_menu_message_id"]
    c.callback.data = handler.cb(data[team.VIEW], action)
    await permissions.permissions_callback(c.callback, c.state)


@pytest.mark.asyncio
async def test_review_requires_explicit_confirm_and_pins_fresh_target(screen):
    c = screen
    await click(c, "open")
    c.service.remove_organization_member.assert_not_awaited()
    c.service.team_members.assert_awaited_once_with(
        telegram_user_id=123, tenant_id=c.tenant, expected_user_id=c.user,
    )
    data = await c.state.get_data()
    assert data[team.VIEW]["phase"] == "rights_removal_review"
    assert "Private member name" not in repr(data)
    assert "member-private@example.test" not in repr(data)
    await click(c, "confirm")
    data = await c.state.get_data()
    assert data[team.VIEW]["phase"] == "rights_removal_done"
    assert data[handler.ATTEMPT]["status"] == "done"
    args = c.service.remove_organization_member.await_args.kwargs
    assert args["tenant_id"] == c.tenant and args["target_user_id"] == c.target
    assert args["expected_user_id"] == c.user
    assert args["idempotency_key"] == data[handler.ATTEMPT]["idempotency_key"]
    await click(c, "confirm")
    assert c.service.remove_organization_member.await_count == 1


@pytest.mark.asyncio
async def test_member_disappearing_before_review_causes_no_mutation(screen):
    c = screen
    c.result["items"] = []
    await click(c, "open")
    c.service.remove_organization_member.assert_not_awaited()
    assert (await c.state.get_data())[team.VIEW]["phase"] == "rights_removal_denied"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["sender", "tenant", "menu", "nonce"])
async def test_stale_origin_never_calls_an_api(screen, failure):
    c = screen
    if failure == "sender":
        c.callback.from_user.id = c.callback.message.chat.id = 999
    elif failure == "tenant":
        await c.state.update_data(**{
            team.CONTEXT: {"telegram_user_id": 123, "tenant_id": str(uuid4())},
        })
    elif failure == "menu":
        c.callback.message.message_id = 99
    else:
        c.callback.data = "TP:rm:old:open"
    await permissions.permissions_callback(c.callback, c.state)
    c.service.team_members.assert_not_awaited()
    c.service.remove_organization_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_platform_denial_is_not_replaced_with_construction_permission(screen):
    c = screen
    await click(c, "open")
    c.service.remove_organization_member.side_effect = ConstructionApiClientError(
        status_code=403, code="forbidden",
    )
    await click(c, "confirm")
    data = await c.state.get_data()
    assert data[team.VIEW]["phase"] == "rights_removal_denied"
    assert data[handler.ATTEMPT]["status"] == "denied"


@pytest.mark.asyncio
async def test_unknown_result_replays_original_target_without_active_team_read(screen):
    c = screen
    await click(c, "open")
    c.service.remove_organization_member.side_effect = ConstructionApiClientError(
        status_code=504, code="api_timeout",
    )
    await click(c, "confirm")
    first = deepcopy(c.service.remove_organization_member.await_args.kwargs)
    data = await c.state.get_data()
    assert data[handler.ATTEMPT]["status"] == "pending"
    c.result["items"] = []
    # The target may already be absent after a committed operation.
    fresh = {**c.view, "phase": "list", "nonce": "new-list", "user_ids": []}
    await c.state.update_data(**{team.VIEW: fresh})
    assert handler.recovery_rows(fresh, await c.state.get_data(), "uk")
    await click(c, "resume")
    assert c.service.remove_organization_member.await_count == 1
    reads = c.service.team_members.await_count
    c.service.remove_organization_member.side_effect = None
    await click(c, "retry")
    assert c.service.remove_organization_member.await_args.kwargs == first
    assert c.service.team_members.await_count == reads
    assert (await c.state.get_data())[handler.ATTEMPT]["status"] == "done"


@pytest.mark.asyncio
async def test_later_denial_preserves_an_earlier_uncertain_request(screen):
    c = screen
    await click(c, "open")
    c.service.remove_organization_member.side_effect = ConstructionApiClientError(
        status_code=503, code="service_unavailable",
    )
    await click(c, "confirm")
    before = deepcopy((await c.state.get_data())[handler.ATTEMPT])
    c.service.remove_organization_member.side_effect = ConstructionApiClientError(
        status_code=403, code="forbidden",
    )
    await click(c, "retry")
    assert (await c.state.get_data())[handler.ATTEMPT] == before


@pytest.mark.asyncio
async def test_navigation_during_rpc_cannot_overwrite_new_screen(screen):
    c = screen
    await click(c, "open")
    async def leave(**kwargs):
        await c.state.update_data(**{team.VIEW: {}})
        return {
            "item": {"user_id": str(c.target), "changed": True},
            "actor_user_id": str(c.user), "language": "uk",
        }
    c.service.remove_organization_member.side_effect = leave
    await click(c, "confirm")
    data = await c.state.get_data()
    assert data[team.VIEW] == {}
    assert data[handler.ATTEMPT]["status"] == "pending"


@pytest.mark.asyncio
async def test_leaving_review_invalidates_confirmation_but_retains_pending_receipt(screen):
    c = screen
    await click(c, "open")
    c.callback.data = "CS_HOME"
    await permissions.permissions_navigation_middleware(
        AsyncMock(), c.callback, {"state": c.state},
    )
    assert (await c.state.get_data())[team.VIEW] == {}
    c.service.remove_organization_member.assert_not_awaited()


def test_translations_and_callbacks_fit_telegram():
    assert all(len(values) == len(LANGUAGES) == 7 for values in LABELS.values())
    for action in ("open", "resume", "confirm", "retry"):
        assert len(handler.cb({"nonce": "a" * 32}, action).encode("utf-8")) <= 64
