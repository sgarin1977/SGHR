"""Tenant role/expiry replacement reuses the existing confirmed grant UI."""
from copy import deepcopy
from datetime import datetime
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr

from handlers.construction import construction_organization_access as handler
from handlers.construction import construction_team as team
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, ConstructionApiResult,
    PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from services.construction.construction_team_transport import Grant
from test_construction_bot_organization_access import screen, click, grant


def original(tenant):
    return {
        "id": str(uuid4()), "role": "ADMIN", "scope_type": "tenant",
        "scope_id": str(tenant), "expires_at": "2035-01-01T12:00:00Z",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("role,expiry", [
    ("OWNER", "2035-01-01T12:00:00Z"),
    ("ADMIN", None),
    ("ADMIN", "2035-12-31T12:00:00Z"),
])
async def test_transport_replaces_exact_old_grant_with_role_and_expiry(role, expiry):
    tenant, target = uuid4(), uuid4()
    old = original(tenant)
    item = grant(target, tenant, expiry=expiry)
    item["role"] = role
    calls = []
    def receive(request):
        calls.append(request)
        return httpx.Response(200, json={"data": item, "meta": {}, "request_id": "replace"})
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(receive),
    ) as http:
        result = await ConstructionApiClient(http=http).replace_organization_access(
            session=PlatformApiClientSession(uuid4(), tenant, SecretStr("local-test")),
            target_user_id=target, original_grant=old, role=role,
            expires_at=expiry, idempotency_key="original-replacement",
        )
    assert len(calls) == 1
    request = calls[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v1/construction/team/access-grants/" + old["id"] + "/replace"
    assert request.headers["idempotency-key"] == "original-replacement"
    assert json.loads(request.content) == {
        "role": role, "scope_type": "tenant", "scope_id": str(tenant), "expires_at": expiry,
    }
    assert result.data["id"] == item["id"] and result.data["id"] != old["id"]


@pytest.mark.asyncio
async def test_transport_does_not_submit_unchanged_role_and_expiry():
    tenant = uuid4()
    old = original(tenant)
    calls = []
    def forbidden(request):
        calls.append(request)
        raise AssertionError("No-op must not be sent.")
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(forbidden),
    ) as http:
        with pytest.raises(ValueError):
            await ConstructionApiClient(http=http).replace_organization_access(
                session=PlatformApiClientSession(uuid4(), tenant, SecretStr("local-test")),
                target_user_id=uuid4(), original_grant=old, role="ADMIN",
                expires_at=old["expires_at"], idempotency_key="unused",
            )
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["same_id", "target", "scope", "role", "expiry"])
async def test_transport_rejects_mismatched_replacement_receipt(failure):
    tenant, target = uuid4(), uuid4()
    old = original(tenant)
    item = grant(target, tenant)
    item["role"] = "OWNER"
    if failure == "same_id":
        item["id"] = old["id"]
    elif failure == "target":
        item["user_id"] = str(uuid4())
    elif failure == "scope":
        item["scope_id"] = str(uuid4())
    elif failure == "role":
        item["role"] = "ADMIN"
    else:
        item["expires_at"] = old["expires_at"]
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"data": item, "meta": {}, "request_id": "replace"},
        )),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).replace_organization_access(
                session=PlatformApiClientSession(uuid4(), tenant, SecretStr("local-test")),
                target_user_id=target, original_grant=old, role="OWNER",
                expires_at=None, idempotency_key="same-key",
            )


@pytest.mark.asyncio
async def test_entry_replacement_options_retain_current_role_and_exclude_other_existing_roles():
    tenant, actor, target = uuid4(), uuid4(), uuid4()
    old = original(tenant)
    other = {**old, "id": str(uuid4()), "role": "OWNER"}
    session = PlatformApiClientSession(actor, tenant, SecretStr("local-test"))
    client = NS(
        team_members=AsyncMock(return_value=ConstructionApiResult([{
            "user_id": str(target), "display_name": "Private name",
            "grants": [old, other],
        }], {}, "members")),
        organization_role_options=AsyncMock(return_value=["ADMIN", "OWNER"]),
    )
    entry = ConstructionBotApiEntry(api=NS(construction=client), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(session, {"name": "Org", "language": "uk"}))
    response = await entry.organization_access_context(
        telegram_user_id=123, tenant_id=tenant, expected_user_id=actor,
        target_user_id=target, grant_id=Grant.model_validate(old).id,
    )
    assert response["roles"] == ["ADMIN"]
    assert Grant.model_validate(response["original_grant"]) == Grant.model_validate(old)


@pytest.mark.asyncio
async def test_entry_replay_does_not_read_active_team_or_role_options():
    tenant, actor, target = uuid4(), uuid4(), uuid4()
    old = original(tenant)
    session = PlatformApiClientSession(actor, tenant, SecretStr("local-test"))
    item = grant(target, tenant)
    client = NS(replace_organization_access=AsyncMock(
        return_value=ConstructionApiResult(item, {}, "receipt"),
    ))
    entry = ConstructionBotApiEntry(api=NS(construction=client), context=None, sessions=None)
    entry._client_identity_session = AsyncMock(return_value=(session, "uk"))
    entry._presentation = AsyncMock(side_effect=AssertionError("No discovery on replay"))
    await entry.replace_organization_access(
        telegram_user_id=123, tenant_id=tenant, expected_user_id=actor,
        target_user_id=target, original_grant=old, role="ADMIN",
        expires_at=None, idempotency_key="frozen",
    )
    client.replace_organization_access.assert_awaited_once_with(
        session=session, target_user_id=target, original_grant=old,
        role="ADMIN", expires_at=None, idempotency_key="frozen",
    )
    entry._presentation.assert_not_awaited()


@pytest_asyncio.fixture
async def editing(screen):
    c = screen
    c.original = original(c.tenant)
    c.context.update(
        roles=["ADMIN", "OWNER"], original_grant=deepcopy(c.original),
    )
    c.view.update(
        phase="rights_permissions", rights_grant_id=c.original["id"],
    )
    await c.state.update_data(**{team.VIEW: deepcopy(c.view)})
    async def replace(**kwargs):
        item = grant(c.target, c.tenant, expiry=kwargs["expires_at"])
        item["role"] = kwargs["role"]
        return {"item": item, "actor_user_id": str(c.user), "language": "uk"}
    c.successful_replace = replace
    c.service.replace_organization_access = AsyncMock(side_effect=replace)
    return c


async def replacement_review(c, *, role_index=1, expiry_action="keep"):
    await click(c, "replace")
    await click(c, "role", role_index)
    await click(c, expiry_action)


@pytest.mark.asyncio
async def test_role_change_preserves_expiry_until_explicit_confirmation(editing):
    c = editing
    await replacement_review(c)
    c.service.replace_organization_access.assert_not_awaited()
    c.service.create_organization_access.assert_not_awaited()
    await click(c, "submit")
    args = c.service.replace_organization_access.await_args.kwargs
    assert args["target_user_id"] == c.target and args["expected_user_id"] == c.user
    assert args["role"] == "OWNER"
    assert Grant.model_validate(args["original_grant"]) == Grant.model_validate(c.original)
    assert datetime.fromisoformat(args["expires_at"].replace("Z", "+00:00")) == (
        Grant.model_validate(c.original).expires_at
    )
    assert (await c.state.get_data())[handler.ATTEMPT]["status"] == "done"
    await click(c, "submit")
    assert c.service.replace_organization_access.await_count == 1


@pytest.mark.asyncio
async def test_same_role_and_expiry_has_no_confirmation_write(editing):
    c = editing
    await replacement_review(c, role_index=0)
    data = await c.state.get_data()
    assert handler.no_change(data[team.VIEW])
    rows = c.sent.await_args.kwargs["reply_markup"].inline_keyboard
    assert not any(
        getattr(button, "callback_data", "").endswith(":submit")
        for row in rows for button in row
    )
    await click(c, "submit")
    c.service.replace_organization_access.assert_not_awaited()
    c.service.create_organization_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_role_can_explicitly_clear_expiry(editing):
    c = editing
    await replacement_review(c, role_index=0, expiry_action="none")
    await click(c, "submit")
    args = c.service.replace_organization_access.await_args.kwargs
    assert args["role"] == "ADMIN" and args["expires_at"] is None


@pytest.mark.asyncio
async def test_same_role_can_set_new_expiry_with_explicit_time_zone(editing):
    c = editing
    await click(c, "replace")
    await click(c, "role", 0)
    await click(c, "date")
    message = NS(
        text="2035-12-31 18:00+02:00",
        from_user=c.callback.from_user, chat=c.callback.message.chat,
        answer=AsyncMock(return_value=NS(message_id=2)),
    )
    await handler.date_message(message, c.state)
    await click(c, "submit")
    args = c.service.replace_organization_access.await_args.kwargs
    assert args["role"] == "ADMIN"
    assert args["expires_at"] == "2035-12-31T16:00:00Z"


@pytest.mark.asyncio
async def test_changed_original_grant_is_not_silently_substituted(editing):
    c = editing
    await click(c, "replace")
    c.context["original_grant"]["id"] = str(uuid4())
    await click(c, "role", 1)
    assert handler.phase((await c.state.get_data())[team.VIEW]) == "denied"
    c.service.replace_organization_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_server_owner_boundary_denial_has_no_creation_fallback(editing):
    c = editing
    await replacement_review(c)
    c.service.replace_organization_access.side_effect = ConstructionApiClientError(
        status_code=403, code="forbidden",
    )
    await click(c, "submit")
    assert handler.phase((await c.state.get_data())[team.VIEW]) == "denied"
    c.service.create_organization_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_replacement_replays_original_command_after_team_navigation(editing):
    c = editing
    await replacement_review(c)
    c.service.replace_organization_access.side_effect = ConstructionApiClientError(
        status_code=504, code="api_timeout",
    )
    await click(c, "submit")
    first = deepcopy(c.service.replace_organization_access.await_args.kwargs)
    reads = c.service.organization_access_context.await_count
    fresh = {**c.view, "phase": "list", "nonce": "fresh-list"}
    await c.state.update_data(**{team.VIEW: fresh})
    c.context.update(roles=[], original_grant=None)
    await click(c, "resume")
    c.service.replace_organization_access.side_effect = c.successful_replace
    await click(c, "submit")
    assert c.service.replace_organization_access.await_args.kwargs == first
    assert c.service.organization_access_context.await_count == reads
    c.service.create_organization_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_navigation_during_replacement_preserves_pending_without_overwriting_screen(editing):
    c = editing
    await replacement_review(c)
    async def leave(**kwargs):
        await c.state.update_data(**{team.VIEW: {}})
        return await c.successful_replace(**kwargs)
    c.service.replace_organization_access.side_effect = leave
    await click(c, "submit")
    data = await c.state.get_data()
    assert data[team.VIEW] == {}
    assert data[handler.ATTEMPT]["status"] == "pending"


def test_replacement_callbacks_fit_telegram():
    for action in ("replace", "role", "keep", "date", "none", "submit", "resume"):
        assert len(handler.cb({"nonce": "a" * 32}, action, 0).encode("utf-8")) <= 64
