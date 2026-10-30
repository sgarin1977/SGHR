from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_c005_create_keeps_invitation_and_audit_in_current_transaction():
    from services.construction.construction_invitations import (
        ConstructionInvitationService, verify_construction_invitation_token,
    )
    from services.construction.construction_permissions import (
        CONSTRUCTION_ROLE_PERMISSIONS,
        ConstructionAccessContext, ConstructionGrantContext,
    )

    actor = SimpleNamespace(
        user_id=uuid4(), tenant_id=uuid4(), status="active",
    )
    project_id = uuid4()
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    token = "invitation-test-secret-not-for-storage"
    calls, writes, events = [], [], []
    context = ConstructionAccessContext(
        user_id=actor.user_id, tenant_id=actor.tenant_id, roles=("OWNER",),
        permissions=tuple(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"]),
        grants=(ConstructionGrantContext("OWNER", "tenant", actor.tenant_id),),
    )

    class Session:
        async def commit(self):
            pytest.fail("Commit must follow idempotency completion")

        async def rollback(self):
            calls.append("rollback")

    class Repository:
        async def get_active_project(self, *, tenant_id, project_id):
            calls.append("project")
            return SimpleNamespace(id=project_id, tenant_id=tenant_id, deleted_at=None)

        async def create_invitation(self, **fields):
            calls.append("create")
            writes.append(fields)
            return SimpleNamespace(id=uuid4(), **fields)

        async def create_event(self, **fields):
            calls.append("audit")
            events.append(fields)
            return SimpleNamespace(**fields)

    repository = Repository()
    service = ConstructionInvitationService(
        session=Session(), repository=repository,
        project_repository=repository, event_repository=repository,
        now_provider=lambda: now, token_provider=lambda: token,
    )
    result = await service.create_invitation(
        actor=actor, access_context=context,
        email="  Person@EXAMPLE.COM  ", role="surveyor",
        scopes=[("project", project_id)],
        grant_expires_at=now + timedelta(days=30),
        trace_id="invitation-create",
    )

    assert calls == ["project", "create", "audit"]
    assert len(writes) == len(events) == 1
    invitation = result.invitation
    assert invitation.email == "person@example.com"
    assert invitation.role == "SURVEYOR"
    assert invitation.tenant_id == actor.tenant_id
    assert invitation.created_by_user_id == actor.user_id
    assert invitation.user_id is None
    assert invitation.expires_at == now + timedelta(days=7)
    assert invitation.grant_expires_at == now + timedelta(days=30)
    assert result.token == token
    assert verify_construction_invitation_token(
        token=token, expected_hash=invitation.token_hash,
    )
    assert token not in str(writes)
    assert token not in repr(result)
    event = events[0]
    assert event["event_type"] == "construction_invitation_created"
    assert event["tenant_id"] == actor.tenant_id
    assert event["user_id"] == actor.user_id
    assert event["entity_id"] == invitation.id
    assert event["trace_id"] == "invitation-create"
    assert event["payload"]["operation"] == "create"
    assert token not in str(event)
    assert invitation.token_hash not in str(event)
