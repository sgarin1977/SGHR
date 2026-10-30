"""Confirmed Construction-only block with no automatic unkeyed retry."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr

from handlers.construction import construction_organization_access as parent
from handlers.construction import construction_organization_block as handler
from handlers.construction import construction_team as team
from handlers.construction import construction_team_permissions as permissions
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError,
    ConstructionApiResult, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from test_construction_bot_organization_access import screen
from ui.construction_organization_access import LANGUAGES, LABELS


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [False, True])
async def test_transport_uses_existing_block_route_without_replay_key(empty):
    tenant, actor, target = uuid4(), uuid4(), uuid4()
    identifiers = sorted([uuid4(), uuid4()], key=lambda value: value.int) if not empty else []
    item = {"user_id": str(target), "revoked_grant_ids": [str(value) for value in identifiers]}
    received = []
    def receive(request):
        received.append(request)
        return httpx.Response(200, json={"data": item, "meta": {}, "request_id": "block"})
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(receive),
    ) as http:
        result = await ConstructionApiClient(http=http).block_organization_access(
            session=PlatformApiClientSession(actor, tenant, SecretStr("local-test")),
            target_user_id=target,
        )
    request = received[0]
    assert len(received) == 1 and request.method == "POST"
    assert request.url.path == "/api/v1/construction/team/members/" + str(target) + "/block"
    assert json.loads(request.content) == {}
    assert "idempotency-key" not in request.headers
    assert result.data == item


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["user", "duplicate", "order", "extra"])
async def test_transport_rejects_malformed_block_receipt(failure):
    tenant, target = uuid4(), uuid4()
    ids = sorted([uuid4(), uuid4()], key=lambda value: value.int)
    item = {"user_id": str(target), "revoked_grant_ids": [str(value) for value in ids]}
    if failure == "user":
        item["user_id"] = str(uuid4())
    elif failure == "duplicate":
        item["revoked_grant_ids"] = [str(ids[0]), str(ids[0])]
    elif failure == "order":
        item["revoked_grant_ids"].reverse()
    else:
        item["membership_removed"] = True
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "data": item, "meta": {}, "request_id": "block",
        })),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).block_organization_access(
                session=PlatformApiClientSession(uuid4(), tenant, SecretStr("local-test")),
                target_user_id=target,
            )


@pytest.mark.asyncio
async def test_entry_block_uses_canonical_identity_and_existing_api_only():
    tenant, actor, target = uuid4(), uuid4(), uuid4()
    session = PlatformApiClientSession(actor, tenant, SecretStr("local-test"))
    client = NS(block_organization_access=AsyncMock(return_value=ConstructionApiResult(
        {"user_id": str(target), "revoked_grant_ids": []}, {}, "block",
    )))
    entry = ConstructionBotApiEntry(api=NS(construction=client), context=None, sessions=None)
    entry._client_identity_session = AsyncMock(return_value=(session, "uk"))
    entry._presentation = AsyncMock(side_effect=AssertionError("No read before mutation"))
    await entry.block_organization_access(
        telegram_user_id=123, tenant_id=tenant,
        expected_user_id=actor, target_user_id=target,
    )
    client.block_organization_access.assert_awaited_once_with(
        session=session, target_user_id=target,
    )
    entry._presentation.assert_not_awaited()


@pytest.mark.asyncio
async def test_inspection_can_report_absence_without_claiming_prior_operation_succeeded():
    tenant, actor, target = uuid4(), uuid4(), uuid4()
    session = PlatformApiClientSession(actor, tenant, SecretStr("local-test"))
    client = NS(team_members=AsyncMock(return_value=ConstructionApiResult([], {}, "members")))
    entry = ConstructionBotApiEntry(api=NS(construction=client), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(session, {"name": "Org", "language": "uk"}))
    result = await entry.organization_block_context(
        telegram_user_id=123, tenant_id=tenant, expected_user_id=actor,
        target_user_id=target, allow_absent=True,
    )
    assert result["scope_counts"] == {"tenant": 0, "project": 0, "document": 0}
    assert "changed" not in result and "operation_succeeded" not in result


@pytest_asyncio.fixture
async def blocking(screen):
    c = screen
    c.preview = {
        "scope_counts": {"tenant": 1, "project": 2, "document": 0},
        "actor_user_id": str(c.user), "target_user_id": str(c.target),
        "member_name": "Private block member", "language": "uk",
    }
    c.reply = {
        "item": {"user_id": str(c.target), "revoked_grant_ids": [str(uuid4())]},
        "actor_user_id": str(c.user), "language": "uk",
    }
    c.service.organization_block_context = AsyncMock(
        side_effect=lambda **kwargs: deepcopy(c.preview),
    )
    c.service.block_organization_access = AsyncMock(
        side_effect=lambda **kwargs: deepcopy(c.reply),
    )
    return c


async def click(c, action):
    data = await c.state.get_data()
    c.callback.message.message_id = data["last_menu_message_id"]
    c.callback.data = handler.cb(data[team.VIEW], action)
    await permissions.permissions_callback(c.callback, c.state)


@pytest.mark.asyncio
async def test_review_then_exactly_one_block_without_member_content_in_state(blocking):
    c = blocking
    await click(c, "review")
    c.service.block_organization_access.assert_not_awaited()
    data = await c.state.get_data()
    assert handler.phase(data[team.VIEW]) == "review"
    assert "Private block member" not in repr(data)
    await click(c, "confirm")
    data = await c.state.get_data()
    assert handler.phase(data[team.VIEW]) == "done"
    assert data[handler.ATTEMPT]["status"] == "done"
    c.service.block_organization_access.assert_awaited_once_with(
        telegram_user_id=123, tenant_id=c.tenant,
        expected_user_id=c.user, target_user_id=c.target,
    )
    await click(c, "confirm")
    assert c.service.block_organization_access.await_count == 1


@pytest.mark.asyncio
async def test_noop_receipt_is_success_without_invented_changes(blocking):
    c = blocking
    c.reply["item"]["revoked_grant_ids"] = []
    await click(c, "review")
    await click(c, "confirm")
    assert (await c.state.get_data())[handler.ATTEMPT]["revoked_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("key", handler.PENDING_KEYS)
async def test_unfinished_access_or_membership_operation_blocks_new_block(blocking, key):
    c = blocking
    previous = {"status": "pending", "id": "original-request"}
    await c.state.update_data(**{key: deepcopy(previous)})
    await click(c, "review")
    c.service.organization_block_context.assert_not_awaited()
    c.service.block_organization_access.assert_not_awaited()
    assert (await c.state.get_data())[key] == previous


@pytest.mark.asyncio
async def test_pending_operation_appearing_after_review_prevents_post(blocking):
    c = blocking
    await click(c, "review")
    await c.state.update_data(**{parent.ATTEMPT: {"status": "pending"}})
    await click(c, "confirm")
    c.service.block_organization_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_timeout_is_not_retried_and_inspection_only_reads_current_state(blocking):
    c = blocking
    await click(c, "review")
    c.service.block_organization_access.side_effect = ConstructionApiClientError(
        status_code=504, code="api_timeout",
    )
    await click(c, "confirm")
    data = await c.state.get_data()
    assert handler.phase(data[team.VIEW]) == "uncertain"
    assert data[handler.ATTEMPT]["status"] == "uncertain"
    before = deepcopy(data[handler.ATTEMPT])
    await click(c, "confirm")
    assert c.service.block_organization_access.await_count == 1
    c.preview["scope_counts"] = {"tenant": 0, "project": 0, "document": 0}
    await click(c, "inspect")
    assert c.service.organization_block_context.await_args.kwargs["allow_absent"] is True
    assert (await c.state.get_data())[handler.ATTEMPT] == before
    assert c.service.block_organization_access.await_count == 1


@pytest.mark.asyncio
async def test_owner_boundary_denial_does_not_call_any_fallback_operation(blocking):
    c = blocking
    await click(c, "review")
    c.service.block_organization_access.side_effect = ConstructionApiClientError(
        status_code=403, code="forbidden",
    )
    await click(c, "confirm")
    assert handler.phase((await c.state.get_data())[team.VIEW]) == "denied"
    c.service.create_organization_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_navigation_during_post_does_not_overwrite_new_screen(blocking):
    c = blocking
    await click(c, "review")
    async def leave(**kwargs):
        await c.state.update_data(**{team.VIEW: {}})
        return deepcopy(c.reply)
    c.service.block_organization_access.side_effect = leave
    await click(c, "confirm")
    data = await c.state.get_data()
    assert data[team.VIEW] == {}
    assert data[handler.ATTEMPT]["status"] == "sending"


@pytest.mark.asyncio
async def test_cancelled_dispatch_keeps_attempted_state_without_second_post(blocking):
    c = blocking
    await click(c, "review")
    c.service.block_organization_access.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await click(c, "confirm")
    assert (await c.state.get_data())[handler.ATTEMPT]["status"] == "sending"
    await click(c, "confirm")
    assert c.service.block_organization_access.await_count == 1


@pytest.mark.asyncio
async def test_leaving_review_invalidates_confirmation_without_write(blocking):
    c = blocking
    await click(c, "review")
    c.callback.data = "CS_HOME"
    await permissions.permissions_navigation_middleware(
        AsyncMock(), c.callback, {"state": c.state},
    )
    assert (await c.state.get_data())[team.VIEW] == {}
    c.service.block_organization_access.assert_not_awaited()


def test_labels_and_callbacks_fit_telegram():
    assert all(len(values) == len(LANGUAGES) == 7 for values in LABELS.values())
    for action in ("review", "confirm", "inspect"):
        assert len(handler.cb({"nonce": "a" * 32}, action).encode("utf-8")) <= 64
