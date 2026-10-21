from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_c007_resend_keeps_record_and_grant_expiry_but_invalidates_old_token():
    from database.models import ConstructionInvitation
    from database.repositories.construction import ConstructionInvitationRepository
    from services.construction_invitations import (
        ConstructionInvitationTokenDeniedError,
        hash_construction_invitation_token,
        require_construction_invitation_token,
    )

    calls = []

    class Session:
        async def flush(self):
            calls.append("flush")

        async def commit(self):
            pytest.fail("Repository must not commit.")

        def add(self, record):
            pytest.fail("Resend must not create another invitation.")

        async def delete(self, record):
            pytest.fail("Resend must not delete invitation.")

    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    invitation_id = uuid4()
    grant_expiry = now + timedelta(days=30)
    old_token, new_token = "old-private-token", "new-private-token"
    invitation = ConstructionInvitation(
        id=invitation_id, tenant_id=uuid4(), created_by_user_id=uuid4(),
        email="person@example.com", role="ADMIN", status="pending",
        token_hash=hash_construction_invitation_token(old_token),
        token_used_at=None, expires_at=now + timedelta(days=1),
        grant_expires_at=grant_expiry, row_version=1,
        delivery_metadata={"attempt_count": 2},
    )
    new_expiry = now + timedelta(days=7)
    metadata = {"attempt_count": 0}
    result = await ConstructionInvitationRepository(Session()).resend_invitation(
        invitation=invitation,
        token_hash=hash_construction_invitation_token(new_token),
        expires_at=new_expiry, delivery_metadata=metadata,
    )

    assert result is invitation
    assert calls == ["flush"]
    assert invitation.id == invitation_id
    assert invitation.status == "pending"
    assert invitation.expires_at == new_expiry
    assert invitation.grant_expires_at == grant_expiry
    assert invitation.delivery_metadata == metadata
    assert invitation.delivery_metadata is not metadata
    require_construction_invitation_token(
        invitation=invitation, token=new_token, as_of=now,
    )
    with pytest.raises(ConstructionInvitationTokenDeniedError):
        require_construction_invitation_token(
            invitation=invitation, token=old_token, as_of=now,
        )
