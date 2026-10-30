from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.construction.construction_invitation_workflows import (
    ConstructionInvitationIdempotencyInProgressError,
    ConstructionInvitationWorkflow,
)
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS,
    ConstructionAccessContext,
    ConstructionGrantContext,
)


@pytest.mark.asyncio
async def test_c005_incomplete_replay_does_not_create_or_deliver_invitation():
    actor = SimpleNamespace(
        user_id=uuid4(), tenant_id=uuid4(), status="active",
    )
    context = ConstructionAccessContext(
        user_id=actor.user_id, tenant_id=actor.tenant_id,
        roles=("OWNER",),
        permissions=tuple(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"]),
        grants=(ConstructionGrantContext("OWNER", "tenant", actor.tenant_id),),
    )
    calls = []

    async def forbidden(**kwargs):
        pytest.fail("Incomplete replay must not perform new writes.")

    class Session:
        async def commit(self):
            pytest.fail("Incomplete replay must not commit.")

        async def rollback(self):
            calls.append("rollback")

    async def reserve(**kwargs):
        calls.append("reserve")
        return SimpleNamespace(
            is_replay=True, response_status=None, response_payload=None,
        )

    session = Session()
    workflow = ConstructionInvitationWorkflow(
        session=session,
        invitation_service=SimpleNamespace(
            session=session, create_invitation=forbidden,
        ),
        idempotency_service=SimpleNamespace(
            repository=SimpleNamespace(session=session),
            reserve=reserve, complete=forbidden,
        ),
        delivery_queue=SimpleNamespace(
            session=session, enqueue_invitation=forbidden,
        ),
    )
    with pytest.raises(ConstructionInvitationIdempotencyInProgressError) as error:
        await workflow.create_invitation(
            actor=actor, access_context=context,
            idempotency_key="processing-invitation",
            email="person@example.com", role="ADMIN",
            scopes=[("tenant", actor.tenant_id)],
        )
    assert error.value.status_code == 409
    assert calls == ["reserve", "rollback"]
