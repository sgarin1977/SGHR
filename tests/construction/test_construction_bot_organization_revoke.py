"""Selected organization grant revocation; no external operations."""
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
from handlers.construction import construction_organization_revoke as handler
from handlers.construction import construction_team as team
from handlers.construction import construction_team_permissions as permissions
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError,
    ConstructionApiResult, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from services.construction.construction_team_transport import Grant
from test_construction_bot_organization_access import screen, grant
from ui.construction_organization_access import LANGUAGES, LABELS


def selected(tenant):
    return {
        "id": str(uuid4()), "role": "ADMIN", "scope_type": "tenant",
        "scope_id": str(tenant), "expires_at": "2035-01-01T12:00:00Z",
    }


def revoked(target, tenant, old):
    item = grant(target, tenant, expiry=old["expires_at"])
    item.update(id=old["id"], role=old["role"], status="revoked")
    return item


@pytest.mark.asyncio
async def test_transport_revokes_exact_grant_with_empty_body_and_original_key():
    tenant, target = uuid4(), uuid4()
    old = selected(tenant)
    received = []
    def receive(request):
        received.append(request)
        return httpx.Response(200, json={
            "data": revoked(target, tenant, old), "meta": {}, "request_id": "revoke",
        })
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(receive),
    ) as http:
        result = await ConstructionApiClient(http=http).revoke_organization_access(
            session=PlatformApiClientSession(uuid4(), tenant, SecretStr("local-test")),
            target_user_id=target, original_grant=old, idempotency_key="frozen-key",
        )
    request = received[0]
    assert len(received) == 1 and request.method == "POST"
    assert request.url.path == "/api/v1/construction/team/access-grants/" + old["id"] + "/revoke"
    assert json.loads(request.content) == {}
    assert request.headers["idempotency-key"] == "frozen-key"
    assert result.data["id"] == old["id"] and result.data["status"] == "revoked"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["grant", "target", "tenant", "role", "expiry", "status"])
async def test_transport_rejects_mismatched_revocation_receipt(failure):
    tenant, target = uuid4(), uuid4()
    old = selected(tenant)
    item = revoked(target, tenant, old)
    if failure == "grant":
        item["id"] = str(uuid4())
    elif failure == "target":
        item["user_id"] = str(uuid4())
    elif failure == "tenant":
        item["scope_id"] = str(uuid4())
    elif failure == "role":
        item["role"] = "OWNER"
    elif failure == "expiry":
        item["expires_at"] = None
    else:
        item["status"] = "active"
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "data": item, "meta": {}, "request_id": "revoke",
        })),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).revoke_organization_access(
                session=PlatformApiClientSession(uuid4(), tenant, SecretStr("local-test")),
                target_user_id=target, original_grant=old, idempotency_key="key",
            )


@pytest.mark.asyncio
async def test_entry_replays_frozen_grant_without_active_team_discovery():
    tenant, actor, target = uuid4(), uuid4(), uuid4()
    old = selected(tenant)
    session = PlatformApiClientSession(actor, tenant, SecretStr("local-test"))
    client = NS(revoke_organization_access=AsyncMock(return_value=ConstructionApiResult(
        revoked(target, tenant, old), {}, "receipt",
    )))
    entry = ConstructionBotApiEntry(api=NS(construction=client), context=None, sessions=None)
    entry._client_identity_session = AsyncMock(return_value=(session, "uk"))
    entry._presentation = AsyncMock(side_effect=AssertionError("No discovery on replay"))
    await entry.revoke_organization_access(
        telegram_user_id=123, tenant_id=tenant, expected_user_id=actor,
        target_user_id=target, original_grant=old, idempotency_key="same-key",
    )
    entry._presentation.assert_not_awaited()
    client.revoke_organization_access.assert_awaited_once_with(
        session=session, target_user_id=target, original_grant=old,
        idempotency_key="same-key",
    )


@pytest.mark.asyncio
async def test_context_requires_original_role_in_fresh_server_options():
    tenant, actor, target = uuid4(), uuid4(), uuid4()
    old = selected(tenant)
    client = NS(
        team_members=AsyncMock(return_value=ConstructionApiResult([{
            "user_id": str(target), "display_name": "Private name", "grants": [old],
        }], {}, "members")),
        organization_role_options=AsyncMock(return_value=[]),
    )
    entry = ConstructionBotApiEntry(api=NS(construction=client), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(actor, tenant, SecretStr("local-test")),
        {"name": "Org", "language": "uk"},
    ))
    with pytest.raises(ConstructionApiClientError) as caught:
        await entry.organization_revoke_context(
            telegram_user_id=123, tenant_id=tenant, expected_user_id=actor,
            target_user_id=target, grant_id=Grant.model_validate(old).id,
        )
    assert caught.value.status_code == 403


@pytest_asyncio.fixture
async def revoking(screen):
    c = screen
    c.original = selected(c.tenant)
    c.view.update(phase="rights_permissions", rights_grant_id=c.original["id"])
    await c.state.update_data(**{team.VIEW: deepcopy(c.view)})
    c.revoke_context = {
        "grant": deepcopy(c.original),
        "target_user_id": str(c.target), "actor_user_id": str(c.user),
        "member_name": "Private revocation member", "language": "uk",
    }
    async def revoke(**kwargs):
        return {
            "item": revoked(c.target, c.tenant, kwargs["original_grant"]),
            "actor_user_id": str(c.user), "language": "uk",
        }
    c.successful_revoke = revoke
    c.service.organization_revoke_context = AsyncMock(
        side_effect=lambda **kwargs: deepcopy(c.revoke_context),
    )
    c.service.revoke_organization_access = AsyncMock(side_effect=revoke)
    c.service.replace_organization_access = AsyncMock()
    return c


async def click(c, action):
    data = await c.state.get_data()
    c.callback.message.message_id = data["last_menu_message_id"]
    c.callback.data = handler.cb(data[team.VIEW], action)
    await permissions.permissions_callback(c.callback, c.state)


@pytest.mark.asyncio
async def test_review_then_exactly_one_revocation_without_payload_in_state(revoking):
    c = revoking
    await click(c, "review")
    c.service.revoke_organization_access.assert_not_awaited()
    c.service.organization_revoke_context.assert_awaited_once_with(
        telegram_user_id=123, tenant_id=c.tenant, expected_user_id=c.user,
        target_user_id=c.target, grant_id=Grant.model_validate(c.original).id,
    )
    assert "Private revocation member" not in repr(await c.state.get_data())
    await click(c, "confirm")
    data = await c.state.get_data()
    assert handler.phase(data[team.VIEW]) == "done"
    assert data[parent.ATTEMPT]["operation"] == "revoke"
    args = c.service.revoke_organization_access.await_args.kwargs
    assert args["target_user_id"] == c.target and args["expected_user_id"] == c.user
    assert Grant.model_validate(args["original_grant"]) == Grant.model_validate(c.original)
    await click(c, "confirm")
    assert c.service.revoke_organization_access.await_count == 1
    c.service.create_organization_access.assert_not_awaited()
    c.service.replace_organization_access.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["grant", "scope", "actor", "target"])
async def test_changed_review_context_never_submits(revoking, failure):
    c = revoking
    if failure == "grant":
        c.revoke_context["grant"]["id"] = str(uuid4())
    elif failure == "scope":
        c.revoke_context["grant"]["scope_type"] = "project"
    elif failure == "actor":
        c.revoke_context["actor_user_id"] = str(uuid4())
    else:
        c.revoke_context["target_user_id"] = str(uuid4())
    await click(c, "review")
    assert handler.phase((await c.state.get_data())[team.VIEW]) == "denied"
    c.service.revoke_organization_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_server_owner_boundary_denial_has_no_other_operation_fallback(revoking):
    c = revoking
    await click(c, "review")
    c.service.revoke_organization_access.side_effect = ConstructionApiClientError(
        status_code=403, code="forbidden",
    )
    await click(c, "confirm")
    data = await c.state.get_data()
    assert handler.phase(data[team.VIEW]) == "denied"
    assert data[parent.ATTEMPT]["status"] == "denied"
    c.service.create_organization_access.assert_not_awaited()
    c.service.replace_organization_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_recovery_keeps_original_grant_and_key_without_new_discovery(revoking):
    c = revoking
    await click(c, "review")
    c.service.revoke_organization_access.side_effect = ConstructionApiClientError(
        status_code=504, code="api_timeout",
    )
    await click(c, "confirm")
    first = deepcopy(c.service.revoke_organization_access.await_args.kwargs)
    reads = c.service.organization_revoke_context.await_count
    await c.state.update_data(**{
        team.VIEW: {**c.view, "nonce": "team-again", "phase": "list"},
    })
    rows = parent.recovery_rows(
        (await c.state.get_data())[team.VIEW], await c.state.get_data(), "uk",
    )
    assert rows[0][0].callback_data.startswith("TP:rv:")
    await click(c, "resume")
    c.service.revoke_organization_access.side_effect = c.successful_revoke
    await click(c, "retry")
    assert c.service.revoke_organization_access.await_args.kwargs == first
    assert c.service.organization_revoke_context.await_count == reads
    assert (await c.state.get_data())[parent.ATTEMPT]["status"] == "done"


@pytest.mark.asyncio
async def test_pending_revoke_cannot_execute_as_creation_or_replacement(revoking):
    c = revoking
    await click(c, "review")
    c.service.revoke_organization_access.side_effect = ConstructionApiClientError(
        status_code=503, code="service_unavailable",
    )
    await click(c, "confirm")
    before = deepcopy((await c.state.get_data())[parent.ATTEMPT])
    data = await c.state.get_data()
    c.callback.data = parent.cb(data[team.VIEW], "submit")
    await permissions.permissions_callback(c.callback, c.state)
    assert (await c.state.get_data())[parent.ATTEMPT] == before
    assert c.service.revoke_organization_access.await_count == 1
    c.service.create_organization_access.assert_not_awaited()
    c.service.replace_organization_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_later_denial_does_not_erase_an_uncertain_previous_result(revoking):
    c = revoking
    await click(c, "review")
    c.service.revoke_organization_access.side_effect = ConstructionApiClientError(
        status_code=504, code="api_timeout",
    )
    await click(c, "confirm")
    before = deepcopy((await c.state.get_data())[parent.ATTEMPT])
    c.service.revoke_organization_access.side_effect = ConstructionApiClientError(
        status_code=403, code="forbidden",
    )
    await click(c, "retry")
    assert (await c.state.get_data())[parent.ATTEMPT] == before


@pytest.mark.asyncio
async def test_navigation_during_rpc_cannot_overwrite_the_new_screen(revoking):
    c = revoking
    await click(c, "review")
    async def leave(**kwargs):
        await c.state.update_data(**{team.VIEW: {}})
        return await c.successful_revoke(**kwargs)
    c.service.revoke_organization_access.side_effect = leave
    await click(c, "confirm")
    data = await c.state.get_data()
    assert data[team.VIEW] == {}
    assert data[parent.ATTEMPT]["status"] == "pending"


@pytest.mark.asyncio
async def test_leaving_review_invalidates_confirmation_without_mutation(revoking):
    c = revoking
    await click(c, "review")
    c.callback.data = "CS_HOME"
    await permissions.permissions_navigation_middleware(
        AsyncMock(), c.callback, {"state": c.state},
    )
    assert (await c.state.get_data())[team.VIEW] == {}
    c.service.revoke_organization_access.assert_not_awaited()


def test_revocation_labels_and_callbacks_are_complete():
    assert all(len(values) == len(LANGUAGES) == 7 for values in LABELS.values())
    for action in ("review", "resume", "confirm", "retry"):
        assert len(handler.cb({"nonce": "a" * 32}, action).encode("utf-8")) <= 64
