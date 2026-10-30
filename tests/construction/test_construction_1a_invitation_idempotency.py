from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_c005_creation_replay_returns_saved_response_without_new_invitation():
    from services.construction.construction_invitation_workflows import (
        ConstructionInvitationWorkflow,
    )
    from services.construction.construction_permissions import (
        CONSTRUCTION_ROLE_PERMISSIONS,
        ConstructionAccessContext, ConstructionGrantContext,
    )

    actor = SimpleNamespace(
        user_id=uuid4(), tenant_id=uuid4(), status="active",
    )
    project_id = uuid4()
    context = ConstructionAccessContext(
        user_id=actor.user_id, tenant_id=actor.tenant_id, roles=("OWNER",),
        permissions=tuple(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"]),
        grants=(ConstructionGrantContext("OWNER", "tenant", actor.tenant_id),),
    )
    stored = {
        "id": str(uuid4()), "status": "pending",
        "email": "person@example.com", "role": "SURVEYOR",
    }
    calls, reservations = [], []

    class Session:
        async def commit(self):
            calls.append("commit")

        async def rollback(self):
            pytest.fail("Successful replay must not roll back")

    session = Session()

    class InvitationService:
        async def create_invitation(self, **fields):
            pytest.fail("Replay must not create invitation, token, or audit")

    invitation_service = InvitationService()
    invitation_service.session = session

    class IdempotencyService:
        async def reserve(self, **fields):
            calls.append("reserve")
            reservations.append(fields)
            return SimpleNamespace(
                is_replay=True, response_status=201, response_payload=stored,
            )

        async def complete(self, **fields):
            pytest.fail("Replay must not complete the record again")

    idempotency_service = IdempotencyService()
    idempotency_service.repository = SimpleNamespace(session=session)
    workflow = ConstructionInvitationWorkflow(
        session=session, invitation_service=invitation_service,
        idempotency_service=idempotency_service,
    )
    result = await workflow.create_invitation(
        actor=actor, access_context=context,
        idempotency_key="invitation-create-key",
        email="  Person@EXAMPLE.COM  ", role="surveyor",
        scopes=[("project", project_id)],
    )

    assert result == stored
    assert calls == ["reserve", "commit"]
    reservation = reservations[0]
    assert reservation["tenant_id"] == actor.tenant_id
    assert reservation["principal_type"] == "user"
    assert reservation["principal_id"] == actor.user_id
    assert reservation["operation"] == "construction.invitation.create"
    assert reservation["idempotency_key"] == "invitation-create-key"
    assert reservation["payload"]["email"] == "person@example.com"
    assert reservation["payload"]["role"] == "SURVEYOR"
    assert reservation["payload"]["scopes"] == [
        {"scope_type": "project", "scope_id": str(project_id)},
    ]
