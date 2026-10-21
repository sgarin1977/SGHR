from types import SimpleNamespace

import pytest

from services.construction_invitation_workflows import (
    ConstructionInvitationWorkflow,
)


@pytest.mark.parametrize(
    "different", [None, "session", "invitation", "idempotency", "delivery"],
)
def test_c005_workflow_requires_one_shared_transaction_session(different):
    session = object()
    other_session = object()
    invitation_service = SimpleNamespace(
        session=other_session if different == "invitation" else session,
    )
    idempotency_service = SimpleNamespace(
        repository=SimpleNamespace(
            session=other_session if different == "idempotency" else session,
        ),
    )
    delivery_queue = SimpleNamespace(
        session=other_session if different == "delivery" else session,
    )
    arguments = dict(
        session=None if different == "session" else session,
        invitation_service=invitation_service,
        idempotency_service=idempotency_service,
        delivery_queue=delivery_queue,
    )

    if different is not None:
        with pytest.raises(ValueError, match="one shared session"):
            ConstructionInvitationWorkflow(**arguments)
        return

    workflow = ConstructionInvitationWorkflow(**arguments)
    assert workflow.session is session
    assert workflow.invitation_service is invitation_service
    assert workflow.idempotency_service is idempotency_service
    assert workflow.delivery_queue is delivery_queue
