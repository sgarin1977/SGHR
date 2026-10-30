"""Real OTP/JWT and invitation workflows with local SQLite collaborators.

No PostgreSQL, RLS, parallelism, browser or email-provider acceptance is claimed.
"""
import json
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Column, MetaData, String, Table, Uuid, select

from api.app import create_app
from api.auth import get_access_token_codec, get_api_email_auth_service, get_api_identity_service
from api.dependencies import (
    get_api_session, get_api_request_rate_limit_service,
    get_tenant_module_access_service, get_tenant_suite_access_service,
)
from api.routes import construction_invitations as accept_routes
from api.routes import construction_invitation_recipient as recipient_routes
from construction_rate_limit_fixture import with_local_construction_rate_limit
from database.repositories.user import UserRepository
from services.construction.construction_invitation_decline import ConstructionInvitationDeclineService
from services.construction.construction_invitation_decline_workflow import ConstructionInvitationDeclineWorkflow
from services.construction.construction_invitation_delivery_material import ConstructionInvitationDeliveryMaterial
from services.construction.construction_invitation_preview import ConstructionInvitationPreviewService
from services.construction.construction_invitation_recipient_context import ConstructionInvitationRecipientContextCodec

from test_platform_identity_sessions import identity_sessions
from test_construction_1a_invitation_acceptance_service import acceptance_case, stored
from test_construction_1a_invitation_acceptance_workflow import pipeline, snapshot


@pytest.fixture
async def recipient(identity_sessions, pipeline):
    c, p = identity_sessions, pipeline
    s = p.case
    # invitation_fixture_valid_email_v1
    c.email = "identity@example.com"
    material = c.challenge_codec.issue_code(email=c.email)
    c.code = material.code
    c.challenge.email = c.email
    c.challenge.challenge_hash = material.challenge_hash
    c.sync.execute(c.users.update().values(id=s.actor.user_id))
    c.user = s.actor.user_id
    c.email_repository.get_email_account_for_update.return_value.user_id = c.user
    c.sync.commit()

    metadata = MetaData()
    c.accounts = Table(
        "user_accounts", metadata, Column("id", Uuid, primary_key=True),
        Column("user_id", Uuid), Column("platform", String),
        Column("platform_user_id", String), Column("email", String),
    )
    events = Table(
        "event_logs", metadata, Column("id", Uuid, primary_key=True),
        Column("user_id", Uuid), Column("entity_id", Uuid),
        Column("event_type", String), Column("entity_type", String),
        Column("platform", String),
    )
    metadata.create_all(c.engine)
    c.sync.execute(c.accounts.insert().values(
        id=uuid4(), user_id=c.user, platform="email",
        platform_user_id=c.email, email=c.email,
    ))
    c.sync.commit()

    async def audit_login(**values):
        identifier = uuid4()
        c.sync.execute(events.insert().values(
            id=identifier, **{name: values[name] for name in (
                "user_id", "entity_id", "event_type", "entity_type", "platform",
            )},
        ))
        return NS(id=identifier)
    c.audit.create_event.side_effect = audit_login

    s.now = c.now
    s.invitation.email = c.email
    s.invitation.expires_at = c.now + timedelta(days=7)
    s.invitation.grant_expires_at = c.now + timedelta(days=30)
    reader = UserRepository(c.db)
    s.context.identity_reader = reader

    class PreviewRepository:
        session = s.session

        async def get_snapshot(self, *, tenant_id, invitation_id):
            assert tenant_id == s.invitation.tenant_id
            assert invitation_id == s.invitation.id
            return NS(
                invitation=s.invitation,
                scopes=tuple((row.scope_type, row.scope_id) for row in s.repository.rows),
            )

    preview = ConstructionInvitationPreviewService(
        session=s.session, repository=PreviewRepository(),
        identity_reader=reader, now_provider=lambda: s.now,
    )

    async def decline_record(*, invitation):
        invitation.status = "declined"
        invitation.row_version += 1
        s.db.execute(
            "UPDATE invitations SET payload=? WHERE id=?",
            (json.dumps({"status": "declined", "row_version": invitation.row_version,
                         "user_id": None}), str(invitation.id)),
        )
        return invitation
    s.repository.decline_invitation = decline_record
    decline = ConstructionInvitationDeclineWorkflow(
        session=s.session,
        decline_service=ConstructionInvitationDeclineService(
            session=s.session, repository=s.repository, event_repository=s.events,
            identity_reader=reader, now_provider=lambda: s.now,
        ),
    )

    def transaction(value):
        # FastAPI must execute SQLite access in the event-loop thread.
        async def dependency():
            if not s.db.in_transaction:
                s.db.execute("BEGIN")
            return value
        return dependency

    c.app = with_local_construction_rate_limit(create_app())
    c.tenant_identity = NS(require_actor=AsyncMock(
        side_effect=AssertionError("Recipient must not require tenant identity.")
    ))
    for dependency, value in (
        (get_api_session, c.db), (get_access_token_codec, c.codec),
        (get_api_email_auth_service, c.email_service),
        (get_api_identity_service, c.tenant_identity),
    ):
        def supply(value):
            return lambda: value
        c.app.dependency_overrides[dependency] = supply(value)

    c.suite = NS(require_enabled=AsyncMock())
    c.module = NS(require_enabled=AsyncMock())
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.module
    c.app.dependency_overrides[accept_routes.get_acceptance_workflow] = transaction(p.workflow)
    c.app.dependency_overrides[recipient_routes.get_preview_service] = transaction(preview)
    c.app.dependency_overrides[recipient_routes.get_decline_workflow] = transaction(decline)

    codec = ConstructionInvitationRecipientContextCodec(
        encryption_key=Fernet.generate_key().decode("ascii")
    )
    material = ConstructionInvitationDeliveryMaterial(
        invitation_id=s.invitation.id, tenant_id=s.invitation.tenant_id,
        email=c.email, expires_at=s.invitation.expires_at, token="one-time-secret",
    )
    envelope = codec.issue_for_delivery(material=material)
    c.app.dependency_overrides[accept_routes.get_recipient_context_codec] = lambda: codec
    c.body = {"invitation_context": envelope.invitation_context,
              "token": envelope.token, "expected_row_version": 4}
    c.p, c.case = p, s

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        c.client = client
        response = await client.post("/api/v1/auth/email/verify", json={
            "challenge_id": str(c.challenge.id), "email": c.email,
            "secret": c.code, "language_code": "uk",
        })
        assert response.status_code == 200, response.text
        c.token = response.json()["data"]["access_token"]
        c.refresh_token = response.json()["data"]["refresh_token"]
        assert c.codec.decode_access_token(c.token).tenant_id is None
        yield c


async def call(c, operation, *, token=None, body=None, key="no-tenant-accept"):
    headers = {"Authorization": "Bearer " + (token or c.token)}
    if operation == "accept" and key is not None:
        headers["Idempotency-Key"] = key
    payload = dict(c.body) if body is None else body
    if operation == "preview":
        payload = {k: v for k, v in payload.items() if k != "expected_row_version"}
    return await c.client.post(
        "/api/v1/construction/invitations/" + operation,
        headers=headers, json=payload,
    )


@pytest.mark.asyncio
async def test_identity_preview_accept_and_receipt_replay_without_any_tenant(recipient):
    c, s = recipient, recipient.case
    headers = {"Authorization": "Bearer " + c.token}
    before = snapshot(c.p)
    identity = await c.client.get("/api/v1/platform/identity", headers=headers)
    assert identity.status_code == 200, identity.text
    assert identity.json()["data"] == {"id": str(c.user), "status": "active"}
    assert identity.headers["Cache-Control"] == "private, no-store"
    preview = await call(c, "preview")
    assert preview.status_code == 200, preview.text
    assert snapshot(c.p) == before
    for path in ("/api/v1/me", "/api/v1/construction/ui/navigation-context"):
        denied = await c.client.get(path, headers=headers)
        assert denied.status_code == 401, denied.text
    accepted = await call(c, "accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["data"]["status"] == "accepted"
    assert len(stored(s, "memberships")) == 1
    assert len(stored(s, "grants")) == 2
    assert len(stored(s, "audits")) == 3
    receipt = snapshot(c.p)
    s.invitation.expires_at = s.now - timedelta(seconds=1)
    replay = await call(c, "accept")
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == accepted.json()["data"]
    assert snapshot(c.p) == receipt
    s.service.accept_invitation.assert_awaited_once()
    assert c.sync.scalar(select(c.users.c.tenant_id)) is None
    c.tenant_identity.require_actor.assert_not_awaited()


@pytest.mark.asyncio
async def test_decline_without_membership_creates_no_membership_or_grant(recipient):
    c, s = recipient, recipient.case
    result = await call(c, "decline")
    assert result.status_code == 200, result.text
    assert result.json()["data"]["status"] == "declined"
    assert stored(s, "memberships") == stored(s, "grants") == []
    assert len(stored(s, "audits")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "wrong_email", "different_user", "blocked", "signature", "expired_access",
    "expired_invitation", "invitation_token",
])
async def test_no_recipient_authority_without_fresh_identity_email_and_token(recipient, failure):
    c, s = recipient, recipient.case
    status, token = 403, c.token
    if failure == "wrong_email":
        c.sync.execute(c.accounts.update().values(email="other@example.test"))
        c.sync.commit()
    elif failure == "different_user":
        other = uuid4()
        c.sync.execute(c.users.insert().values(id=other, tenant_id=None, status="active"))
        c.sync.commit()
        token = c.codec.issue_access_token(user_id=other, tenant_id=None, now=c.now)
    elif failure == "invitation_token":
        c.body["token"] = "different-token"
    elif failure == "blocked":
        c.sync.execute(c.users.update().values(status="blocked"))
        c.sync.commit()
        status = 401
    elif failure == "signature":
        parts = token.split(".")
        parts[2] = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
        token, status = ".".join(parts), 401
    elif failure == "expired_access":
        c.now += timedelta(minutes=11)
        status = 401
    else:
        s.invitation.expires_at = s.now
    before = snapshot(c.p)
    response = await call(c, "accept", token=token)
    assert response.status_code == status, response.text
    assert snapshot(c.p) == before
    assert stored(s, "memberships") == stored(s, "grants") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "audit", "commit"])
async def test_acceptance_failures_roll_back_all_local_domain_writes(recipient, failure):
    c, s = recipient, recipient.case
    before = snapshot(c.p)
    membership_write = AsyncMock(wraps=s.memberships.ensure_invitation_tenant_membership)
    s.memberships.ensure_invitation_tenant_membership = membership_write
    if failure == "commit":
        c.p.flags.fail_commit = True
    else:
        s.fail = "audit:3" if failure == "audit" else "membership"
    response = await call(c, "accept")
    assert response.status_code == 500, response.text
    assert snapshot(c.p) == before
    # A framework/dependency error must not masquerade as an injected failure.
    s.service.accept_invitation.assert_awaited_once()
    membership_write.assert_awaited_once()
    s.session.rollback.assert_awaited()
    if failure == "commit":
        s.session.commit.assert_awaited_once()
    elif failure == "audit":
        assert s.audit_count == 3 and s.grant_count == 2
    else:
        assert s.audit_count == 0 and s.grant_count == 0


@pytest.mark.asyncio
async def test_identity_endpoint_is_authenticated_and_has_no_selectors(recipient):
    c = recipient
    anonymous = await c.client.get("/api/v1/platform/identity")
    assert anonymous.status_code == 401
    selected = await c.client.get(
        "/api/v1/platform/identity",
        headers={"Authorization": "Bearer " + c.token},
        params={"user_id": str(uuid4()), "tenant_id": str(c.case.invitation.tenant_id)},
    )
    assert selected.status_code == 422
    response = await call(c, "accept", key=None)
    assert response.status_code == 422
    assert stored(c.case, "memberships") == []


@pytest.mark.asyncio
async def test_identity_and_recipient_share_platform_user_limit(recipient):
    c = recipient
    rate = c.app.dependency_overrides[get_api_request_rate_limit_service]()
    for _ in range(120):
        await rate.ensure_platform_user_allowed(user_id=c.user)
    before = snapshot(c.p)
    identity = await c.client.get(
        "/api/v1/platform/identity",
        headers={"Authorization": "Bearer " + c.token},
    )
    assert identity.status_code == 429, identity.text
    denied = await call(c, "preview")
    assert denied.status_code == 429, denied.text
    assert snapshot(c.p) == before

# invitation_identity_only_v1

# invitation_binding_session_v1
