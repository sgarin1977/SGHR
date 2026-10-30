"""Organization selection: real local sessions/JWT; no native PG acceptance."""
import json
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import Column, MetaData, String, Table, Uuid, select

from api.app import create_app
from api.auth import get_access_token_codec, get_api_identity_service
from api.dependencies import get_api_session, get_api_request_rate_limit_service
from api.routes.platform_organization_session import get_organization_session_service
from construction_rate_limit_fixture import with_local_construction_rate_limit
from database.models import ApiAuthSession
from services.api_auth import ApiAuthRefreshError
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.platform_organization_session import PlatformOrganizationSessionService
from test_platform_identity_sessions import identity_sessions

PATH = "/api/v1/platform/organizations/session"


@pytest.fixture
async def organization_session(identity_sessions):
    c = identity_sessions
    c.source = await c.login()
    c.tenant = uuid4()
    metadata = MetaData()
    c.members = Table("local_memberships", metadata,
        Column("user_id", Uuid, primary_key=True),
        Column("tenant_id", Uuid, primary_key=True))
    c.logs = Table("local_session_audits", metadata,
        Column("id", Uuid, primary_key=True), Column("payload", String))
    metadata.create_all(c.engine)
    c.sync.execute(c.tenants.insert().values(id=c.tenant))
    c.sync.execute(c.members.insert().values(user_id=c.user, tenant_id=c.tenant))
    c.sync.commit()

    async def member(*, user_id, tenant_id):
        return c.sync.scalar(select(c.members.c.user_id).where(
            c.members.c.user_id == user_id, c.members.c.tenant_id == tenant_id)) == user_id
    c.membership.side_effect = member

    async def actor(*, user_id, tenant_id):
        user = await c.user_service.get_user_by_id(user_id)
        if (user is None or user.status != "active"
                or not (user.tenant_id == tenant_id
                        or await member(user_id=user_id, tenant_id=tenant_id))):
            raise ApiIdentityAccessError("Organization unavailable.")
        return ApiActorContext(user_id=user_id, tenant_id=tenant_id,
            active_role=None, roles=(), language_code="uk", timezone=None, status="active")

    async def audit(**values):
        identifier = uuid4()
        c.sync.execute(c.logs.insert().values(
            id=identifier, payload=json.dumps(values, default=str)))
        return NS(id=identifier)

    c.identity = NS(session=c.db, require_actor=AsyncMock(side_effect=actor))
    # Explicit local collaborator: no SQLite serialization fallback in runtime.
    c.serializer = NS(session=c.db, lock_member=AsyncMock())
    c.events = NS(session=c.db, create_event=AsyncMock(side_effect=audit))
    c.auth.prepare_token_pair = AsyncMock(wraps=c.auth.prepare_token_pair)
    c.service = PlatformOrganizationSessionService(
        session=c.db, tokens=c.auth, identity=c.identity,
        serializer=c.serializer, events=c.events, now_provider=lambda: c.now)
    app = with_local_construction_rate_limit(create_app())
    app.dependency_overrides[get_api_session] = lambda: c.db
    app.dependency_overrides[get_access_token_codec] = lambda: c.codec
    app.dependency_overrides[get_api_identity_service] = lambda: c.identity
    app.dependency_overrides[get_organization_session_service] = lambda: c.service
    c.app = app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        c.client = client
        yield c


def state(c):
    sessions = c.sync.execute(select(
        ApiAuthSession.id, ApiAuthSession.tenant_id, ApiAuthSession.status,
        ApiAuthSession.refresh_token_hash,
    ).order_by(ApiAuthSession.id)).all()
    return sessions, c.sync.execute(select(c.logs).order_by(c.logs.c.id)).all()


async def post(c, *, body=None, bearer=None, query=""):
    return await c.client.post(PATH + query, json=body if body is not None else {
        "tenant_id": str(c.tenant), "refresh_token": c.source.refresh_token,
    }, headers={"Authorization": "Bearer " + (bearer or c.source.access_token)})


@pytest.mark.asyncio
async def test_identity_session_selects_membership_and_preserves_source_method(organization_session):
    c = organization_session
    assert c.codec.decode_access_token(c.source.access_token).tenant_id is None
    result = await post(c)
    assert result.status_code == 200, result.text
    assert result.headers["Cache-Control"] == "private, no-store"
    data = result.json()["data"]
    claims = c.codec.decode_access_token(data["access_token"])
    assert claims.user_id == c.user and claims.tenant_id == c.tenant
    assert data["user_id"] == str(c.user) and data["tenant_id"] == str(c.tenant)
    assert data["refresh_token"] != c.source.refresh_token
    rows = c.sync.scalars(select(ApiAuthSession)).all()
    assert len(rows) == 2
    active = next(row for row in rows if row.status == "active")
    assert active.auth_method == "email" and active.tenant_id == c.tenant
    assert sum(row.status == "revoked" for row in rows) == 1
    assert c.sync.scalar(select(c.users.c.tenant_id)) is None
    assert c.sync.execute(select(c.members)).all() == [(c.user, c.tenant)]
    c.serializer.lock_member.assert_awaited_once_with(
        tenant_id=c.tenant, user_id=c.user)
    assert len(c.sync.execute(select(c.logs)).all()) == 1
    receipt = state(c)
    replay = await post(c)
    assert replay.status_code == 401 and state(c) == receipt
    refreshed = await c.auth.refresh_tokens(refresh_token=data["refresh_token"], now=c.now)
    assert c.codec.decode_access_token(refreshed.access_token).tenant_id == c.tenant
    with pytest.raises(ApiAuthRefreshError):
        await c.auth.refresh_tokens(refresh_token=c.source.refresh_token, now=c.now)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,status", [
    ("membership", 403), ("unknown_tenant", 403), ("blocked", 401),
    ("revoked", 401), ("expired", 401), ("wrong_refresh", 401), ("other_user", 401),
])
async def test_unavailable_identity_membership_or_session_cannot_select(organization_session, failure, status):
    c = organization_session
    body = {"tenant_id": str(c.tenant), "refresh_token": c.source.refresh_token}
    bearer = None
    source = c.sync.scalar(select(ApiAuthSession))
    if failure == "membership":
        c.sync.execute(c.members.delete())
    elif failure == "unknown_tenant":
        body["tenant_id"] = str(uuid4())
    elif failure == "blocked":
        c.sync.execute(c.users.update().values(status="blocked"))
    elif failure == "revoked":
        source.status = "revoked"
    elif failure == "expired":
        source.expires_at = c.now - timedelta(seconds=1)
    elif failure == "wrong_refresh":
        body["refresh_token"] = "invalid-refresh"
    else:
        other = uuid4()
        c.sync.execute(c.users.insert().values(id=other, status="active", tenant_id=None))
        bearer = c.codec.issue_access_token(user_id=other, tenant_id=None, now=c.now)
    c.sync.commit()
    before = state(c)
    result = await post(c, body=body, bearer=bearer)
    assert result.status_code == status, result.text
    assert state(c) == before
    c.auth.prepare_token_pair.assert_not_awaited()
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit"])
async def test_session_and_source_revocation_roll_back_together(organization_session, failure):
    c = organization_session
    before = state(c)
    if failure == "audit":
        c.events.create_event.side_effect = RuntimeError("private audit error")
    else:
        c.db.fail_commit = True
    try:
        result = await post(c)
        assert result.status_code == 500, result.text
        assert state(c) == before
        c.auth.prepare_token_pair.assert_awaited_once()
        c.events.create_event.assert_awaited_once()
        assert c.db.rollbacks >= 1
        assert "private audit error" not in result.text
    finally:
        c.db.fail_commit = False


@pytest.mark.asyncio
async def test_membership_is_rechecked_after_serialization(organization_session):
    c = organization_session
    async def removed_while_waiting(**values):
        c.sync.execute(c.members.delete())
        c.sync.commit()
    c.serializer.lock_member.side_effect = removed_while_waiting
    before = state(c)
    result = await post(c)
    assert result.status_code == 403, result.text
    assert state(c) == before
    c.auth.prepare_token_pair.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["user_id", "roles", "permissions", "auth_method"])
async def test_body_cannot_supply_identity_or_authority(organization_session, field):
    c = organization_session
    body = {"tenant_id": str(c.tenant), "refresh_token": c.source.refresh_token, field: "spoofed"}
    before = state(c)
    result = await post(c, body=body)
    assert result.status_code == 422 and state(c) == before
    c.auth.prepare_token_pair.assert_not_awaited()


@pytest.mark.asyncio
async def test_shared_user_rate_limit_stops_selection_before_transaction(organization_session):
    c = organization_session
    rates = c.app.dependency_overrides[get_api_request_rate_limit_service]()
    for _ in range(120):
        await rates.ensure_platform_user_allowed(user_id=c.user)
    before = state(c)
    result = await post(c)
    assert result.status_code == 429 and state(c) == before
    c.serializer.lock_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_removed_source_tenant_cannot_be_bypassed_by_selection(organization_session):
    c = organization_session
    old = uuid4()
    c.sync.execute(c.tenants.insert().values(id=old))
    source = c.sync.scalar(select(ApiAuthSession))
    source.tenant_id = old
    c.sync.commit()
    before = state(c)
    result = await post(c)
    assert result.status_code == 401 and state(c) == before
    c.auth.prepare_token_pair.assert_not_awaited()


@pytest.mark.asyncio
async def test_query_selectors_and_missing_authentication_are_rejected(organization_session):
    c = organization_session
    before = state(c)
    result = await post(c, query="?user_id=" + str(uuid4()))
    assert result.status_code == 422 and state(c) == before
    result = await c.client.post(PATH, json={
        "tenant_id": str(c.tenant), "refresh_token": c.source.refresh_token})
    assert result.status_code == 401 and state(c) == before
