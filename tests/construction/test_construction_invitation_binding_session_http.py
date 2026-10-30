"""Local OTP -> accept -> scoped session -> real binding workflow.
No native PostgreSQL/RLS, browser or external Telegram/email acceptance.
"""
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import insert, select

from api.auth import get_api_identity_service
from api.routes import platform_telegram_binding as binding_routes
from api.routes.platform_organization_session import get_organization_session_service
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.platform_organization_session import PlatformOrganizationSessionService
from test_platform_identity_sessions import identity_sessions
from test_construction_1a_invitation_acceptance_service import acceptance_case, stored
from test_construction_1a_invitation_acceptance_workflow import pipeline, snapshot
from test_construction_invitation_identity_only import recipient, call
from test_construction_1a_platform_telegram_binding_consume import environment, proof
from test_construction_1a_platform_telegram_binding_workflow import workflow, counts

SESSION = "/api/v1/platform/organizations/session"
BINDING = "/api/v1/platform/integrations/telegram/bindings"


@pytest.fixture
async def transition(recipient, workflow):
    c, w = recipient, workflow
    c.tenant = c.case.invitation.tenant_id
    c.sync.execute(c.tenants.insert().values(id=c.tenant))
    c.sync.commit()
    w.env.session.execute(insert(w.env.users).values(id=c.user, status="active"))
    tenant_table = w.env.token_table.metadata.tables["tenants"]
    w.env.session.execute(insert(tenant_table).values(id=c.tenant))
    w.env.session.commit()
    w.now = c.now

    async def member(*, user_id, tenant_id):
        return any(row["user_id"] == str(user_id) and row["tenant_id"] == str(tenant_id)
                   for row in stored(c.case, "memberships"))

    async def actor(*, user_id, tenant_id):
        user = await c.user_service.get_user_by_id(user_id)
        if user is None or user.status != "active" or not await member(
                user_id=user_id, tenant_id=tenant_id):
            raise ApiIdentityAccessError("Organization unavailable.")
        return ApiActorContext(user_id=user_id, tenant_id=tenant_id, status="active",
            active_role=None, roles=(), language_code="uk", timezone=None)

    c.membership.side_effect = member
    identity = NS(session=c.db, require_actor=AsyncMock(side_effect=actor))
    w.workflow.identity = NS(session=w.env.bridge, require_actor=AsyncMock(side_effect=actor))
    serializer = NS(session=c.db, lock_member=AsyncMock())
    events = NS(session=c.db, create_event=c.audit.create_event)
    service = PlatformOrganizationSessionService(
        session=c.db, tokens=c.auth, identity=identity,
        serializer=serializer, events=events, now_provider=lambda: c.now)

    async def provide_binding():
        if not w.env.session.in_transaction():
            w.env.session.begin()
        return NS(base=w.workflow)

    c.app.dependency_overrides[get_api_identity_service] = lambda: identity
    c.app.dependency_overrides[get_organization_session_service] = lambda: service
    c.app.dependency_overrides[binding_routes.get_binding_workflow] = provide_binding
    c.app.dependency_overrides[binding_routes.get_binding_bot_username] = lambda: "Local_test_bot"
    c.binding = w
    return c


async def select_session(c, tenant_id=None):
    return await c.client.post(SESSION, headers={"Authorization": "Bearer " + c.token},
        json={"tenant_id": str(tenant_id or c.tenant), "refresh_token": c.refresh_token})


async def issue(c, token):
    return await c.client.post(BINDING, json={},
        headers={"Authorization": "Bearer " + token, "Idempotency-Key": "binding-after-accept"})


@pytest.mark.asyncio
async def test_real_local_accept_session_binding_consume_and_historical_replay(transition):
    c, w = transition, transition.binding
    accepted = await call(c, "accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["meta"] == {"organization_id": str(c.tenant)}
    domain = snapshot(c.p)
    before = counts(w)
    assert (await issue(c, c.token)).status_code == 401
    assert counts(w) == before

    selected = await select_session(c)
    assert selected.status_code == 200, selected.text
    tokens = selected.json()["data"]
    assert c.codec.decode_access_token(tokens["access_token"]).tenant_id == c.tenant
    issued = await issue(c, tokens["access_token"])
    assert issued.status_code == 201, issued.text
    assert counts(w) == (2, 0, 1, 1)
    assert snapshot(c.p) == domain
    original = issued.json()["data"]

    consumed = await c.client.post(BINDING + "/consume",
        json={"token": original["code"], "init_data": proof(as_of=c.now)},
        headers={"Idempotency-Key": "consume-after-accept"})
    assert consumed.status_code == 200, consumed.text
    assert counts(w) == (2, 1, 2, 2)

    c.now += timedelta(minutes=16)
    w.now = c.now
    fresh = await c.auth.refresh_tokens(refresh_token=tokens["refresh_token"], now=c.now)
    replay = await issue(c, fresh.access_token)
    assert replay.status_code == 201, replay.text
    assert replay.json()["data"] == original
    assert counts(w) == (2, 1, 2, 2)
    assert snapshot(c.p) == domain
    assert c.sync.scalar(select(c.users.c.tenant_id)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["before_accept", "declined", "removed"])
async def test_no_binding_session_without_current_membership(transition, mode):
    c = transition
    if mode == "declined":
        response = await call(c, "decline")
        assert response.status_code == 200, response.text
    elif mode == "removed":
        response = await call(c, "accept")
        assert response.status_code == 200, response.text
        c.case.db.execute("DELETE FROM memberships")
        c.case.db.commit()
    domain = snapshot(c.p)
    before = counts(c.binding)
    selected = await select_session(c)
    assert selected.status_code == 403, selected.text
    assert snapshot(c.p) == domain
    assert counts(c.binding) == before
