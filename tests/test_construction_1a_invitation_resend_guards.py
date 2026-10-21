from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from services.construction_foundation import ConstructionRowVersionConflictError
from services.construction_invitations import (
    ConstructionInvitationService,
    ConstructionInvitationTransitionError,
)
from services.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS,
    ConstructionAccessContext,
    ConstructionGrantContext,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,days,used,version,error", [
    ("pending", 1, False, 3, ConstructionRowVersionConflictError),
    ("pending", 0, False, 4, ConstructionInvitationTransitionError),
    ("pending", -1, False, 4, ConstructionInvitationTransitionError),
    ("pending", 1, True, 4, ConstructionInvitationTransitionError),
    ("accepted", 1, False, 4, ConstructionInvitationTransitionError),
    ("declined", 1, False, 4, ConstructionInvitationTransitionError),
    ("expired", 1, False, 4, ConstructionInvitationTransitionError),
    ("revoked", 1, False, 4, ConstructionInvitationTransitionError),
])
async def test_resend_rejects_invalid_state_before_token_or_mutation(
    status, days, used, version, error,
):
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    actor = SimpleNamespace(
        user_id=uuid4(), tenant_id=uuid4(), status="active",
    )
    context = ConstructionAccessContext(
        user_id=actor.user_id,
        tenant_id=actor.tenant_id,
        roles=("OWNER",),
        permissions=tuple(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"]),
        grants=(ConstructionGrantContext(
            "OWNER", "tenant", actor.tenant_id,
        ),),
    )
    invitation = SimpleNamespace(
        id=uuid4(), tenant_id=actor.tenant_id,
        role="ADMIN", status=status, row_version=4,
        expires_at=now + timedelta(days=days),
        grant_expires_at=now + timedelta(days=30),
        token_used_at=now if used else None,
        token_hash="a" * 64,
        delivery_metadata={"attempt_count": 2},
    )
    before = {
        **vars(invitation),
        "delivery_metadata": dict(invitation.delivery_metadata),
    }
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    repository = SimpleNamespace(
        get_invitation_for_update=AsyncMock(return_value=invitation),
        list_invitation_scopes=AsyncMock(return_value=[SimpleNamespace(
            tenant_id=actor.tenant_id, invitation_id=invitation.id,
            scope_type="tenant", scope_id=actor.tenant_id,
        )]),
        resend_invitation=AsyncMock(),
    )
    events = SimpleNamespace(create_event=AsyncMock())
    token_provider = Mock(return_value="new-private-token")
    service = ConstructionInvitationService(
        session=session, repository=repository, event_repository=events,
        now_provider=lambda: now, token_provider=token_provider,
    )

    with pytest.raises(error):
        await service.resend_invitation(
            actor=actor, access_context=context,
            invitation_id=invitation.id, expected_row_version=version,
        )

    repository.get_invitation_for_update.assert_awaited_once_with(
        tenant_id=actor.tenant_id, invitation_id=invitation.id,
    )
    token_provider.assert_not_called()
    repository.resend_invitation.assert_not_awaited()
    events.create_event.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()
    assert vars(invitation) == before
