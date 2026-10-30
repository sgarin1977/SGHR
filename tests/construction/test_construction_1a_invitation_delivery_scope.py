from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["foreign_tenant", "inactive_actor"])
async def test_c005_delivery_rejects_invalid_context_before_any_write(failure):
    from services.construction.construction_invitation_delivery import (
        ConstructionInvitationDeliveryQueue,
    )
    from services.construction.construction_invitations import (
        ConstructionInvitationOperationError,
        hash_construction_invitation_token,
    )

    async def forbidden_write(**kwargs):
        pytest.fail("Invalid context must not access queue or repository.")

    def forbidden_encrypt(**kwargs):
        pytest.fail("Invalid context must not encrypt delivery material.")

    session = object()
    actor = SimpleNamespace(
        user_id=uuid4(), tenant_id=uuid4(),
        status="blocked" if failure == "inactive_actor" else "active",
    )
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    token = "valid-test-invitation-token"
    invitation = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4() if failure == "foreign_tenant" else actor.tenant_id,
        status="pending", expires_at=now + timedelta(days=7),
        token_hash=hash_construction_invitation_token(token),
        token_used_at=None, delivery_metadata={"attempt_count": 0},
    )
    adapter = ConstructionInvitationDeliveryQueue(
        session=session,
        queue=SimpleNamespace(session=session, enqueue=forbidden_write),
        invitation_repository=SimpleNamespace(
            session=session, update_delivery_metadata=forbidden_write,
        ),
        token_codec=SimpleNamespace(encrypt=forbidden_encrypt),
        now_provider=lambda: now,
    )

    with pytest.raises(ConstructionInvitationOperationError):
        await adapter.enqueue_invitation(
            actor=actor, invitation=invitation, token=token,
        )
    assert invitation.delivery_metadata == {"attempt_count": 0}
