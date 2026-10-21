from datetime import datetime, timezone
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_c005_delivery_metadata_update_does_not_commit_or_change_lifecycle():
    from database.models import ConstructionInvitation
    from database.repositories.construction import ConstructionInvitationRepository

    calls = []

    class Session:
        async def flush(self):
            calls.append("flush")

        async def commit(self):
            pytest.fail("Repository must not commit.")

        async def delete(self, *args):
            pytest.fail("Delivery metadata must not delete invitation.")

    expiry = datetime(2026, 10, 8, tzinfo=timezone.utc)
    invitation = ConstructionInvitation(
        id=uuid4(), tenant_id=uuid4(),
        email="person@example.com", role="ADMIN", status="pending",
        token_hash="a" * 64, token_used_at=None,
        expires_at=expiry, grant_expires_at=None,
        created_by_user_id=uuid4(), row_version=1,
        delivery_metadata={"attempt_count": 0},
    )
    metadata = {"attempt_count": 1, "last_error_code": None}
    repository = ConstructionInvitationRepository(Session())
    result = await repository.update_delivery_metadata(
        invitation=invitation, delivery_metadata=metadata,
    )

    assert result is invitation
    assert calls == ["flush"]
    assert invitation.delivery_metadata == metadata
    assert invitation.delivery_metadata is not metadata
    assert invitation.status == "pending"
    assert invitation.token_hash == "a" * 64
    assert invitation.token_used_at is None
    assert invitation.expires_at == expiry
    assert invitation.grant_expires_at is None
