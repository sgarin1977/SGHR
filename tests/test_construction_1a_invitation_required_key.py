from types import SimpleNamespace

import pytest

from services.construction_invitation_workflows import (
    ConstructionInvitationWorkflow,
)
from services.construction_invitations import (
    ConstructionInvitationValidationError,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "", "   ", 123])
async def test_c005_creation_requires_key_before_any_write(key):
    calls = []

    async def forbidden(**kwargs):
        pytest.fail("Invalid key must be rejected before repository access.")

    class Session:
        async def commit(self):
            pytest.fail("Invalid key must not commit.")

        async def rollback(self):
            calls.append("rollback")

    session = Session()
    invitation_service = SimpleNamespace(
        session=session, create_invitation=forbidden,
    )
    idempotency_service = SimpleNamespace(
        repository=SimpleNamespace(session=session),
        reserve=forbidden, complete=forbidden,
    )
    queue = SimpleNamespace(
        session=session, enqueue_invitation=forbidden,
    )
    workflow = ConstructionInvitationWorkflow(
        session=session,
        invitation_service=invitation_service,
        idempotency_service=idempotency_service,
        delivery_queue=queue,
    )

    with pytest.raises(
        ConstructionInvitationValidationError,
        match="Idempotency key is required",
    ):
        await workflow.create_invitation(
            actor=None, access_context=None, idempotency_key=key,
            email="person@example.com", role="ADMIN", scopes=[],
        )
    assert calls == ["rollback"]
