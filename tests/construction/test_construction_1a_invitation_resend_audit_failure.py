from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.construction.construction_invitations import (
    ConstructionInvitationOperationError,
    ConstructionInvitationService,
    hash_construction_invitation_token,
)
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS,
    ConstructionAccessContext,
    ConstructionGrantContext,
)


@pytest.mark.asyncio
async def test_resend_audit_failure_rolls_back_without_commit():
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    actor = SimpleNamespace(
        user_id=uuid4(), tenant_id=uuid4(), status="active",
    )
    context = ConstructionAccessContext(
        user_id=actor.user_id, tenant_id=actor.tenant_id,
        roles=("OWNER",),
        permissions=tuple(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"]),
        grants=(ConstructionGrantContext(
            "OWNER", "tenant", actor.tenant_id,
        ),),
    )
    invitation = SimpleNamespace(
        id=uuid4(), tenant_id=actor.tenant_id, role="ADMIN",
        status="pending", row_version=4,
        expires_at=now + timedelta(days=1),
        grant_expires_at=now + timedelta(days=30),
        token_used_at=None,
        token_hash=hash_construction_invitation_token("old-token"),
        delivery_metadata={"attempt_count": 2},
    )
    calls = []
    audit_failure = RuntimeError("Audit storage unavailable")

    async def mutate(**fields):
        assert fields["invitation"] is invitation
        calls.append("resend")
        return invitation

    async def fail_audit(**fields):
        assert fields["event_type"] == "construction_invitation_resent"
        assert fields["entity_id"] == invitation.id
        calls.append("audit")
        raise audit_failure

    async def rollback():
        calls.append("rollback")

    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(side_effect=rollback),
    )
    repository = SimpleNamespace(
        get_invitation_for_update=AsyncMock(return_value=invitation),
        list_invitation_scopes=AsyncMock(return_value=[SimpleNamespace(
            tenant_id=actor.tenant_id, invitation_id=invitation.id,
            scope_type="tenant", scope_id=actor.tenant_id,
        )]),
        resend_invitation=AsyncMock(side_effect=mutate),
    )
    events = SimpleNamespace(create_event=AsyncMock(side_effect=fail_audit))
    service = ConstructionInvitationService(
        session=session, repository=repository, event_repository=events,
        now_provider=lambda: now, token_provider=lambda: "new-token",
    )

    with pytest.raises(ConstructionInvitationOperationError) as caught:
        await service.resend_invitation(
            actor=actor, access_context=context,
            invitation_id=invitation.id, expected_row_version=4,
        )

    assert caught.value.__cause__ is audit_failure
    assert calls == ["resend", "audit", "rollback"]
    repository.resend_invitation.assert_awaited_once()
    events.create_event.assert_awaited_once()
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
