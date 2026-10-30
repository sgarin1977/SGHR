from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_c005_invitation_create_rolls_back_on_audit_failure():
    from services.construction.construction_invitations import (
        ConstructionInvitationOperationError, ConstructionInvitationService,
    )
    from services.construction.construction_permissions import (
        CONSTRUCTION_ROLE_PERMISSIONS,
        ConstructionAccessContext, ConstructionGrantContext,
    )

    actor = SimpleNamespace(
        user_id=uuid4(), tenant_id=uuid4(), status="active",
    )
    context = ConstructionAccessContext(
        user_id=actor.user_id, tenant_id=actor.tenant_id, roles=("OWNER",),
        permissions=tuple(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"]),
        grants=(ConstructionGrantContext("OWNER", "tenant", actor.tenant_id),),
    )
    calls = []

    class Session:
        async def commit(self):
            pytest.fail("Failed audit must never commit")

        async def rollback(self):
            calls.append("rollback")

    class Repository:
        async def create_invitation(self, **fields):
            calls.append("create")
            return SimpleNamespace(id=uuid4(), **fields)

        async def create_event(self, **fields):
            calls.append("audit")
            raise RuntimeError("Audit storage is unavailable")

    repository = Repository()
    service = ConstructionInvitationService(
        session=Session(), repository=repository, event_repository=repository,
        now_provider=lambda: datetime(2026, 10, 1, 12, tzinfo=timezone.utc),
    )

    with pytest.raises(ConstructionInvitationOperationError) as error:
        await service.create_invitation(
            actor=actor, access_context=context,
            email="person@example.com", role="ADMIN",
            scopes=[("tenant", actor.tenant_id)],
        )

    assert calls == ["create", "audit", "rollback"]
    assert str(error.value) == "Construction invitation operation failed."
    assert isinstance(error.value.__cause__, RuntimeError)
