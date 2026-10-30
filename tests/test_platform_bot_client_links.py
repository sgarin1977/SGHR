from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from api.app import create_app
from api.dependencies import get_api_session
from api.platform_bot_context import PlatformBotApiContext, bind_platform_bot_api_context
from api.routes import platform_bot_client_links as routes
from api.routes.platform_bot_entry import get_platform_bot_users, get_platform_bot_rates
from database.repositories.construction.construction_client_unlink_context import ConstructionClientOwnLinkContext
from services.construction.construction_api_client import ConstructionApiClientError, ConstructionApiResult
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from services.construction.construction_client_own_link_state import ConstructionClientOwnLinkState
from services.construction.construction_client_unlink import (
    ConstructionClientUnlinkDeniedError, ConstructionClientUnlinkResult,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.platform_bot_api_client import PlatformBotApiClient
from test_platform_bot_authentication import case


def supply(value):
    def dependency():
        return value
    return dependency


@pytest.fixture
def owner(case, monkeypatch):
    c = case
    c.user.tenant_id = None
    c.users.get_by_id = AsyncMock(return_value=c.user)
    c.users.has_active_tenant_membership.return_value = False
    c.client_id, c.owner_tenant = uuid4(), uuid4()
    c.missing = False
    c.probe = []
    c.rates = NS(ensure_platform_user_allowed=AsyncMock())

    async def state(*, principal, client_id):
        assert principal.user_id == c.user_id and client_id == c.client_id
        if c.missing:
            raise ConstructionClientUnlinkDeniedError()
        return ConstructionClientOwnLinkState(client_id, c.user_id, 7)

    async def context(*, principal, client_id):
        assert principal.user_id == c.user_id and client_id == c.client_id
        if c.missing:
            raise ConstructionClientUnlinkDeniedError()
        return ConstructionClientOwnLinkContext(client_id, c.owner_tenant, c.user_id)

    c.state_service = NS(get_state=AsyncMock(side_effect=state))
    c.context_service = NS(resolve_own_link=AsyncMock(side_effect=context))
    c.workflow = NS(
        unlink_self=AsyncMock(return_value=ConstructionClientUnlinkResult(
            client_id=c.client_id, tenant_id=c.owner_tenant,
            previous_linked_user_id=c.user_id, linked_user_id=None,
            row_version=8, changed=True, audit_id=uuid4(),
        )),
        unlink_as_editor=AsyncMock(),
    )

    @asynccontextmanager
    async def lookup(**kwargs):
        assert kwargs == {"user_id": c.user_id, "client_id": c.client_id}
        c.probe.append("read-enter")
        try:
            yield NS(kind="lookup")
        finally:
            c.probe.append("read-exit")

    @asynccontextmanager
    async def write(**kwargs):
        assert kwargs == {"tenant_id": c.owner_tenant}
        assert c.probe[-1] == "read-exit"
        c.probe.append("write-enter")
        try:
            yield NS(kind="write")
        finally:
            c.probe.append("write-exit")

    monkeypatch.setattr(routes, "construction_client_unlink_lookup_transaction", lookup)
    monkeypatch.setattr(routes, "construction_transaction", write)
    monkeypatch.setattr(
        routes, "build_construction_client_own_link_state_service",
        lambda **kwargs: c.state_service,
    )
    monkeypatch.setattr(
        routes, "build_construction_client_own_link_context_service",
        lambda **kwargs: c.context_service,
    )
    monkeypatch.setattr(
        routes, "build_construction_client_unlink_workflow",
        lambda **kwargs: c.workflow,
    )

    c.app = create_app()
    for dependency, value in (
        (get_api_session, c.session),
        (get_platform_bot_users, c.users),
        (get_platform_bot_rates, c.rates),
    ):
        c.app.dependency_overrides[dependency] = supply(value)
    c.context = PlatformBotApiContext(c.verifier, c.verifier.proof)
    c.bound = bind_platform_bot_api_context(c.app, context=c.context)
    return c


async def send(c, *, write=False, trusted=True, expected=None, extra=None):
    path = "/api/v1/platform/bot/client-links/" + str(c.client_id)
    user_id = str(expected or c.user_id)
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.ASGITransport(
            app=c.bound if trusted else c.app,
            raise_app_exceptions=False,
        ),
    ) as http:
        if write:
            return await http.post(path + "/unlink", json={
                "expected_user_id": user_id, "expected_row_version": 7,
                **(extra or {}),
            })
        return await http.get(path + "/self", params={
            "expected_user_id": user_id, **(extra or {}),
        })


@pytest.mark.asyncio
async def test_real_trusted_adapter_needs_no_tenant_session_or_membership(owner):
    c = owner
    cache = NS(get=AsyncMock(side_effect=AssertionError("No tenant session.")))
    async with PlatformBotApiClient(application=c.app, context=c.context) as api:
        entry = ConstructionBotApiEntry(api=api, context=c.context, sessions=cache)
        values = dict(
            telegram_user_id=123, tenant_id=c.owner_tenant,
            client_id=c.client_id, expected_user_id=c.user_id,
        )
        state = await entry.own_client_link_state(**values)
        assert state["item"] == {
            "client_id": str(c.client_id),
            "linked_user_id": str(c.user_id), "row_version": 7,
        }
        result = await entry.unlink_client(
            **values, expected_row_version=7, as_owner=True,
        )
    assert result["item"] == {
        "client_id": str(c.client_id), "linked_user_id": None,
        "row_version": 8, "changed": True,
    }
    cache.get.assert_not_awaited()
    c.users.has_active_tenant_membership.assert_not_awaited()
    c.sessions.create_session.assert_not_awaited()
    c.workflow.unlink_as_editor.assert_not_awaited()
    command = c.workflow.unlink_self.await_args.kwargs
    assert command["actor"].user_id == c.user_id
    assert command["actor"].tenant_id is None
    assert command["tenant_id"] == c.owner_tenant
    assert command["client_id"] == c.client_id
    assert command["expected_row_version"] == 7
    assert command["idempotency_key"] is None
    assert c.probe[-4:] == [
        "read-enter", "read-exit", "write-enter", "write-exit",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("write", (False, True))
async def test_external_request_cannot_supply_trusted_context(owner, write):
    c = owner
    response = await send(c, trusted=False, write=write)
    assert response.status_code == 401
    assert c.probe == []
    c.workflow.unlink_self.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("write", (False, True))
async def test_changed_canonical_user_is_denied_before_lookup(owner, write):
    c = owner
    response = await send(c, expected=uuid4(), write=write)
    assert response.status_code == 403
    assert c.probe == []
    c.workflow.unlink_self.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("unlinked", "blocked"))
@pytest.mark.parametrize("write", (False, True))
async def test_inactive_identity_is_denied_before_lookup(owner, failure, write):
    c = owner
    if failure == "unlinked":
        c.account.status = "unlinked"
    else:
        c.user.status = "blocked"
    response = await send(c, write=write)
    assert response.status_code == 403
    assert c.probe == []
    c.workflow.unlink_self.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("write", (False, True))
async def test_missing_or_foreign_link_has_no_editor_fallback(owner, write):
    c = owner
    c.missing = True
    response = await send(c, write=write)
    assert response.status_code == 404
    c.workflow.unlink_self.assert_not_awaited()
    c.workflow.unlink_as_editor.assert_not_awaited()
    assert "write-enter" not in c.probe


@pytest.mark.asyncio
async def test_stale_version_is_preserved_as_409(owner):
    c = owner
    c.workflow.unlink_self.side_effect = ConstructionRowVersionConflictError()
    response = await send(c, write=True)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ROW_VERSION_CONFLICT"
    assert c.workflow.unlink_self.await_args.kwargs["expected_row_version"] == 7
    c.workflow.unlink_as_editor.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("write", (False, True))
async def test_caller_cannot_supply_tenant_context(owner, write):
    c = owner
    response = await send(c, write=write, extra={"tenant_id": str(uuid4())})
    assert response.status_code == 422
    assert c.probe == []
    c.workflow.unlink_self.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ("actor", "client", "extra"))
async def test_transport_rejects_unbound_or_overbroad_owner_response(bad):
    user, client = uuid4(), uuid4()
    data = {"client_id": str(client), "linked_user_id": str(user), "row_version": 7}
    meta = {"actor_user_id": str(user)}
    if bad == "actor":
        meta["actor_user_id"] = str(uuid4())
    elif bad == "client":
        data["client_id"] = str(uuid4())
    else:
        data["display_name"] = "must not be disclosed"
    api = object.__new__(PlatformBotApiClient)
    api._bootstrap = AsyncMock(return_value=ConstructionApiResult(data, meta, "local"))
    with pytest.raises(ConstructionApiClientError):
        await api.own_client_link_state(client_id=client, expected_user_id=user)


def test_internal_owner_adapter_is_absent_from_public_openapi(owner):
    paths = owner.app.openapi()["paths"]
    assert not any("/platform/bot/client-links" in path for path in paths)
    assert "/api/v1/construction/clients/{id}/user-link/unlink" in paths
