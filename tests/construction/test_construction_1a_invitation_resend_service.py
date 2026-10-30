from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_c007_service_resends_same_invitation_and_audits_without_commit():
    from services.construction.construction_invitations import (
        ConstructionInvitationService,
        ConstructionInvitationTokenDeniedError,
        hash_construction_invitation_token,
        require_construction_invitation_token,
    )
    from services.construction.construction_permissions import (
        CONSTRUCTION_ROLE_PERMISSIONS,
        ConstructionAccessContext, ConstructionGrantContext,
    )

    actor = SimpleNamespace(user_id=uuid4(), tenant_id=uuid4(), status="active")
    context = ConstructionAccessContext(
        user_id=actor.user_id, tenant_id=actor.tenant_id, roles=("OWNER",),
        permissions=tuple(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"]),
        grants=(ConstructionGrantContext("OWNER", "tenant", actor.tenant_id),),
    )
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    old_token, new_token = "old-private-token", "new-private-token"
    old_expiry, grant_expiry = now + timedelta(days=1), now + timedelta(days=30)
    creator_id = uuid4()
    invitation = SimpleNamespace(
        id=uuid4(), tenant_id=actor.tenant_id,
        created_by_user_id=creator_id,
        email="person@example.com", user_id=None, role="ADMIN",
        status="pending", row_version=4, expires_at=old_expiry,
        grant_expires_at=grant_expiry, token_used_at=None,
        token_hash=hash_construction_invitation_token(old_token),
        delivery_metadata={"attempt_count": 2},
    )
    calls, events = [], []

    class Session:
        async def commit(self):
            pytest.fail("Domain service must not commit.")

        async def rollback(self):
            pytest.fail("Successful resend must not roll back.")

    class Repository:
        async def get_invitation_for_update(self, **fields):
            assert fields == {
                "tenant_id": actor.tenant_id, "invitation_id": invitation.id,
            }
            calls.append("lock")
            return invitation

        async def list_invitation_scopes(self, **fields):
            assert fields == {
                "tenant_id": actor.tenant_id, "invitation_id": invitation.id,
            }
            calls.append("scopes")
            return [SimpleNamespace(
                tenant_id=actor.tenant_id, invitation_id=invitation.id,
                scope_type="tenant", scope_id=actor.tenant_id,
            )]

        async def resend_invitation(self, **fields):
            assert fields.pop("invitation") is invitation
            assert set(fields) == {"token_hash", "expires_at", "delivery_metadata"}
            calls.append("resend")
            for key, value in fields.items():
                setattr(invitation, key, value)
            invitation.row_version += 1  # Simulate the ORM increment on flush.
            return invitation

        async def create_invitation(self, **fields):
            pytest.fail("Resend must not create another invitation.")

        async def create_event(self, **fields):
            calls.append("audit")
            events.append(fields)
            return SimpleNamespace(**fields)

    repository = Repository()
    service = ConstructionInvitationService(
        session=Session(), repository=repository, event_repository=repository,
        now_provider=lambda: now, token_provider=lambda: new_token,
    )
    result = await service.resend_invitation(
        actor=actor, access_context=context, invitation_id=invitation.id,
        expected_row_version=4, trace_id="resend-service",
    )

    assert calls == ["lock", "scopes", "resend", "audit"]
    assert result.invitation is invitation
    assert result.token == new_token
    assert new_token not in repr(result)
    assert invitation.status == "pending"
    assert invitation.row_version == 5
    assert invitation.expires_at == now + timedelta(days=7)
    assert invitation.grant_expires_at == grant_expiry
    assert invitation.created_by_user_id == creator_id
    assert invitation.user_id is None
    assert invitation.delivery_metadata == {"attempt_count": 0}
    require_construction_invitation_token(
        invitation=invitation, token=new_token, as_of=now,
    )
    with pytest.raises(ConstructionInvitationTokenDeniedError):
        require_construction_invitation_token(
            invitation=invitation, token=old_token, as_of=now,
        )

    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == "construction_invitation_resent"
    assert event["entity_id"] == invitation.id
    assert event["tenant_id"] == actor.tenant_id
    assert event["user_id"] == actor.user_id
    assert event["trace_id"] == "resend-service"
    assert event["payload"]["operation"] == "resend"
    assert event["payload"]["changes"] == [{
        "field": "expires_at",
        "old_value": old_expiry.isoformat(),
        "new_value": invitation.expires_at.isoformat(),
    }]
    assert old_token not in repr(event)
    assert new_token not in repr(event)
