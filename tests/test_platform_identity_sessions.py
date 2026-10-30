"""Identity-only Platform sessions: local SQLite, real OTP/JWT and HTTP gates."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import jwt
import jwt.api_jwt as pyjwt_impl
import pytest
from fastapi import Depends
from sqlalchemy import (
    Column, MetaData, String, Table, Uuid, create_engine, event, func, select,
)
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import api.security as security
import services.api_auth as auth_module
import services.api_email_auth as email_module
from api.app import create_app
from api.auth import (
    get_access_token_codec, get_api_email_auth_service, get_api_identity_service,
)
from api.dependencies import get_api_session
from api.platform_principal import get_authenticated_platform_principal
from api.security import (
    AccessTokenCodec, ApiAuthenticationError, EmailAuthChallengeCodec,
    RefreshTokenCodec,
)
from database.models import ApiAuthSession, ApiEmailAuthChallenge
from database.repositories.api_auth import ApiAuthSessionRepository
from database.repositories.api_email_auth import ApiEmailAuthChallengeRepository
from services.api_auth import ApiAuthRefreshError, ApiAuthService
from services.api_email_auth import ApiEmailAuthService
from services.api_email_identity import ApiEmailIdentityError, ApiEmailIdentityService


@pytest.fixture
async def identity_sessions(monkeypatch):
    c = NS(now=datetime(2026, 10, 29, 12, tzinfo=UTC), user=uuid4())
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(c.now.timestamp(), tz)

    c.now = FixedDatetime.fromtimestamp(c.now.timestamp(), UTC)

    for module in (security, pyjwt_impl, auth_module, email_module):
        monkeypatch.setattr(module, "datetime", FixedDatetime)

    c.engine = create_engine(
        "sqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    @event.listens_for(c.engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata = MetaData()
    c.tenants = Table(
        "tenants", metadata, Column("id", Uuid, primary_key=True),
    )
    c.users = Table(
        "users", metadata,
        Column("id", Uuid, primary_key=True),
        Column("tenant_id", Uuid, nullable=True),
        Column("status", String(20), nullable=False),
    )
    ApiAuthSession.__table__.to_metadata(metadata)
    ApiEmailAuthChallenge.__table__.to_metadata(metadata)
    metadata.create_all(c.engine)
    c.sync = Session(c.engine, expire_on_commit=False)
    c.sync.execute(c.users.insert().values(
        id=c.user, tenant_id=None, status="active",
    ))
    c.sync.commit()

    class Bridge:
        fail_commit = False
        commits = 0
        rollbacks = 0

        @property
        def no_autoflush(self):
            return c.sync.no_autoflush

        def add(self, value):
            c.sync.add(value)

        async def execute(self, statement):
            return c.sync.execute(statement)

        async def flush(self):
            c.sync.flush()

        async def commit(self):
            if self.fail_commit:
                raise RuntimeError("local commit failure")
            c.sync.commit()
            self.commits += 1

        async def rollback(self):
            c.sync.rollback()
            self.rollbacks += 1

    c.db = Bridge()
    async def load_user(user_id):
        row = c.sync.execute(
            select(c.users).where(c.users.c.id == user_id)
        ).mappings().one_or_none()
        return None if row is None else NS(**dict(row))

    c.membership = AsyncMock(return_value=False)
    c.user_service = NS(
        get_user_by_id=AsyncMock(side_effect=load_user),
        repository=NS(has_active_tenant_membership=c.membership),
    )
    c.email_repository = NS(
        get_email_account_for_update=AsyncMock(return_value=NS(user_id=c.user)),
        get_by_id=AsyncMock(side_effect=load_user),
        create_email_user_core=AsyncMock(),
        create_email_account=AsyncMock(),
    )
    c.codec = AccessTokenCodec(
        secret="local-identity-session-test-secret-at-least-32-characters",
        issuer="identity-session-tests", audience="platform",
        access_ttl_seconds=600,
    )
    c.refresh_codec = RefreshTokenCodec()
    c.auth = ApiAuthService(
        session=c.db, telegram_verifier=None, user_service=c.user_service,
        access_token_codec=c.codec, refresh_token_codec=c.refresh_codec,
        session_repository=ApiAuthSessionRepository(c.db),
        refresh_token_ttl_seconds=86400,
    )
    c.email = "identity@example.test"
    c.challenge_codec = EmailAuthChallengeCodec(secret="local-email-proof-" * 4)
    material = c.challenge_codec.issue_code(email=c.email)
    c.code = material.code
    c.challenges = ApiEmailAuthChallengeRepository(c.db)
    c.challenge = await c.challenges.create_challenge(
        tenant_id=None, requested_user_id=None, email=c.email,
        challenge_hash=material.challenge_hash, challenge_type="otp",
        purpose="login", device_id=None, device_name=None, max_attempts=5,
        expires_at=c.now + timedelta(minutes=10),
    )
    await c.db.commit()
    c.db.commits = 0
    c.audit = NS(create_event=AsyncMock(return_value=NS(id=uuid4())))
    c.email_service = ApiEmailAuthService(
        session=c.db, challenge_repository=c.challenges,
        challenge_codec=c.challenge_codec, email_delivery=object(),
        identity_service=ApiEmailIdentityService(
            user_repository=c.email_repository, default_tenant_id=uuid4(),
        ),
        token_service=c.auth, event_repository=c.audit,
        code_ttl_seconds=600, max_attempts=5,
        request_limit=5, request_window_seconds=900,
    )
    async def login():
        return await c.email_service.verify_challenge(
            challenge_id=c.challenge.id, email=c.email, secret=c.code,
            language_code="uk", now=c.now,
        )
    c.login = login
    try:
        yield c
    finally:
        c.sync.close()
        c.engine.dispose()


@pytest.mark.asyncio
async def test_real_email_login_without_organization_and_http_authority_boundary(identity_sessions):
    c = identity_sessions
    app = create_app()
    tenant_identity = NS(require_actor=AsyncMock(
        side_effect=AssertionError("Tenant identity must not be loaded.")
    ))
    app.dependency_overrides[get_api_email_auth_service] = lambda: c.email_service
    app.dependency_overrides[get_api_identity_service] = lambda: tenant_identity
    app.dependency_overrides[get_api_session] = lambda: c.db
    app.dependency_overrides[get_access_token_codec] = lambda: c.codec

    @app.get("/identity-session-proof")
    async def identity_proof(principal=Depends(get_authenticated_platform_principal)):
        return {"user_id": str(principal.user_id)}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        response = await client.post("/api/v1/auth/email/verify", json={
            "challenge_id": str(c.challenge.id), "email": c.email,
            "secret": c.code, "language_code": "uk",
        })
        assert response.status_code == 200, response.text
        tokens = response.json()["data"]
        headers = {"Authorization": "Bearer " + tokens["access_token"]}
        proof = await client.get("/identity-session-proof", headers=headers)
        assert proof.status_code == 200
        assert proof.json() == {"user_id": str(c.user)}
        denied = await client.get("/api/v1/me", headers=headers)
        assert denied.status_code == 401
        assert denied.json()["error"]["code"] == "invalid_token"

    tenant_identity.require_actor.assert_not_awaited()
    claims = c.codec.decode_access_token(tokens["access_token"])
    assert claims.user_id == c.user and claims.tenant_id is None
    payload = jwt.decode(
        tokens["access_token"], c.codec.secret, algorithms=["HS256"],
        audience=c.codec.audience, issuer=c.codec.issuer,
    )
    assert "tenant_id" in payload and payload["tenant_id"] is None
    assert not {"roles", "permissions", "scopes"} & payload.keys()
    row = c.sync.execute(select(ApiAuthSession)).scalar_one()
    assert row.tenant_id is None and row.user_id == c.user
    assert row.refresh_token_hash == c.refresh_codec.hash_refresh_token(tokens["refresh_token"])
    assert not hasattr(row, "refresh_token")
    assert c.sync.scalar(select(func.count()).select_from(c.tenants)) == 0
    assert c.sync.scalar(select(c.users.c.tenant_id)) is None
    c.email_repository.create_email_user_core.assert_not_awaited()
    c.email_repository.create_email_account.assert_not_awaited()
    c.audit.create_event.assert_awaited_once()
    assert c.audit.create_event.await_args.kwargs["tenant_id"] is None
    assert c.db.commits == 1


@pytest.mark.asyncio
async def test_identity_refresh_stays_identity_only_after_join_and_rotates_secret(identity_sessions):
    c = identity_sessions
    first = await c.login()
    tenant = uuid4()
    c.sync.execute(c.tenants.insert().values(id=tenant))
    c.sync.execute(c.users.update().where(c.users.c.id == c.user).values(tenant_id=tenant))
    c.sync.commit()

    second = await c.auth.refresh_tokens(refresh_token=first.refresh_token, now=c.now)
    assert second.refresh_token != first.refresh_token
    assert c.codec.decode_access_token(second.access_token).tenant_id is None
    row = c.sync.execute(select(ApiAuthSession)).scalar_one()
    assert row.tenant_id is None
    assert row.refresh_token_hash == c.refresh_codec.hash_refresh_token(second.refresh_token)
    c.membership.assert_not_awaited()
    with pytest.raises(ApiAuthRefreshError):
        await c.auth.refresh_tokens(refresh_token=first.refresh_token, now=c.now)
    await c.auth.logout(refresh_token=second.refresh_token, now=c.now)
    assert c.sync.execute(select(ApiAuthSession.status)).scalar_one() == "revoked"


@pytest.mark.asyncio
async def test_removed_tenant_session_cannot_downgrade_to_identity_refresh(identity_sessions):
    c = identity_sessions
    tenant = uuid4()
    c.sync.execute(c.tenants.insert().values(id=tenant))
    c.sync.execute(c.users.update().where(c.users.c.id == c.user).values(tenant_id=tenant))
    c.sync.commit()
    tokens = await c.login()
    assert c.codec.decode_access_token(tokens.access_token).tenant_id == tenant
    c.sync.execute(c.users.update().where(c.users.c.id == c.user).values(tenant_id=None))
    c.sync.commit()
    with pytest.raises(ApiAuthRefreshError):
        await c.auth.refresh_tokens(refresh_token=tokens.refresh_token, now=c.now)
    row = c.sync.execute(select(ApiAuthSession)).scalar_one()
    assert row.tenant_id == tenant
    assert row.refresh_token_hash == c.refresh_codec.hash_refresh_token(tokens.refresh_token)
    c.membership.assert_awaited_once_with(user_id=c.user, tenant_id=tenant)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["blocked_user", "revoked_session", "expired_session"])
async def test_identity_refresh_rejects_inactive_state(identity_sessions, failure):
    c = identity_sessions
    tokens = await c.login()
    row = c.sync.execute(select(ApiAuthSession)).scalar_one()
    old_hash = row.refresh_token_hash
    if failure == "blocked_user":
        c.sync.execute(c.users.update().where(c.users.c.id == c.user).values(status="blocked"))
    elif failure == "revoked_session":
        row.status = "revoked"
    else:
        row.expires_at = c.now
    c.sync.commit()
    with pytest.raises(ApiAuthRefreshError):
        await c.auth.refresh_tokens(refresh_token=tokens.refresh_token, now=c.now)
    assert c.sync.execute(select(ApiAuthSession.refresh_token_hash)).scalar_one() == old_hash


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit"])
async def test_email_failure_rolls_back_real_session_and_consumption(identity_sessions, failure):
    c = identity_sessions
    if failure == "audit":
        c.audit.create_event.side_effect = RuntimeError("local audit failure")
    else:
        c.db.fail_commit = True
    with pytest.raises(RuntimeError):
        await c.login()
    assert c.sync.scalar(select(func.count()).select_from(ApiAuthSession)) == 0
    assert c.sync.execute(select(ApiEmailAuthChallenge.status)).scalar_one() == "pending"
    assert c.db.rollbacks == 1
    c.email_repository.create_email_user_core.assert_not_awaited()


@pytest.mark.asyncio
async def test_blocked_existing_identity_cannot_create_session(identity_sessions):
    c = identity_sessions
    c.sync.execute(c.users.update().where(c.users.c.id == c.user).values(status="blocked"))
    c.sync.commit()
    with pytest.raises(ApiEmailIdentityError):
        await c.login()
    assert c.sync.scalar(select(func.count()).select_from(ApiAuthSession)) == 0
    assert c.sync.execute(select(ApiEmailAuthChallenge.status)).scalar_one() == "pending"


@pytest.mark.asyncio
async def test_wrong_otp_cannot_create_identity_session(identity_sessions):
    from services.api_email_auth import ApiEmailAuthVerificationError
    c = identity_sessions
    wrong = "000000" if c.code != "000000" else "111111"
    with pytest.raises(ApiEmailAuthVerificationError):
        await c.email_service.verify_challenge(
            challenge_id=c.challenge.id, email=c.email, secret=wrong,
            language_code="uk", now=c.now,
        )
    assert c.sync.scalar(select(func.count()).select_from(ApiAuthSession)) == 0
    c.email_repository.get_by_id.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant_scope", [False, True])
@pytest.mark.parametrize("invalid", [
    "missing_tenant", "invalid_tenant", "signature", "expired",
    "future", "issuer", "audience", "type",
])
async def test_jwt_scope_extension_preserves_verification(identity_sessions, tenant_scope, invalid):
    c = identity_sessions
    token = c.codec.issue_access_token(
        user_id=c.user, tenant_id=uuid4() if tenant_scope else None, now=c.now,
    )
    payload = jwt.decode(
        token, c.codec.secret, algorithms=["HS256"],
        audience=c.codec.audience, issuer=c.codec.issuer,
    )
    key = c.codec.secret
    if invalid == "missing_tenant":
        del payload["tenant_id"]
    elif invalid == "invalid_tenant":
        payload["tenant_id"] = "None"
    elif invalid == "signature":
        key = "different-local-signature-key-at-least-32-characters"
    elif invalid == "expired":
        payload["exp"] = int(c.now.timestamp())
    elif invalid == "future":
        payload["nbf"] = int(c.now.timestamp()) + 1
    elif invalid == "issuer":
        payload["iss"] = "different-issuer"
    elif invalid == "audience":
        payload["aud"] = "different-audience"
    else:
        payload["typ"] = "refresh"
    with pytest.raises(ApiAuthenticationError):
        c.codec.decode_access_token(jwt.encode(payload, key, algorithm="HS256"))
