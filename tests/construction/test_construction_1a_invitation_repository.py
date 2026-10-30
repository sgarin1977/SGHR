from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_c005_repository_creates_invitation_and_scopes_without_commit():
    from database.models import ConstructionInvitation, ConstructionInvitationScope
    from database.repositories.construction import ConstructionInvitationRepository

    class Session:
        def __init__(self):
            self.added = []
            self.flushes = 0

        def add(self, record):
            self.added.append(record)

        async def flush(self):
            self.flushes += 1
            for record in self.added:
                if record.id is None:
                    record.id = uuid4()

        async def commit(self):
            pytest.fail("Repository must not commit")

    tenant_id, creator_id = uuid4(), uuid4()
    projects = [uuid4(), uuid4()]
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    session = Session()
    invitation = await ConstructionInvitationRepository(session).create_invitation(
        tenant_id=tenant_id,
        email="person@example.com",
        user_id=None,
        role="SURVEYOR",
        scopes=[("project", project_id) for project_id in projects],
        expires_at=now + timedelta(days=7),
        grant_expires_at=now + timedelta(days=30),
        created_by_user_id=creator_id,
        token_hash="ab" * 32,
        delivery_metadata={"attempt_count": 0},
    )

    assert isinstance(invitation, ConstructionInvitation)
    assert invitation.id is not None
    assert invitation.tenant_id == tenant_id
    assert invitation.created_by_user_id == creator_id
    assert invitation.email == "person@example.com"
    assert invitation.user_id is None
    assert invitation.role == "SURVEYOR"
    assert invitation.status == "pending"
    assert invitation.expires_at == now + timedelta(days=7)
    assert invitation.grant_expires_at == now + timedelta(days=30)
    assert invitation.token_hash == "ab" * 32
    assert invitation.token_used_at is None
    assert invitation.delivery_metadata == {"attempt_count": 0}
    scopes = [r for r in session.added if isinstance(r, ConstructionInvitationScope)]
    assert len(session.added) == 3
    assert len(scopes) == 2
    assert all(r.tenant_id == tenant_id and r.invitation_id == invitation.id for r in scopes)
    assert {(r.scope_type, r.scope_id) for r in scopes} == {
        ("project", project_id) for project_id in projects
    }
    assert session.flushes > 0
