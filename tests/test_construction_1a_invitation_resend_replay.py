from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.construction_invitation_workflows import (
    ConstructionInvitationWorkflow,
)
from services.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS,
    ConstructionAccessContext,
    ConstructionGrantContext,
)


@pytest.mark.asyncio
async def test_resend_replay_returns_saved_response_without_new_delivery():
    actor = SimpleNamespace(
        user_id=uuid4(), tenant_id=uuid4(), status="active",
    )
    context = ConstructionAccessContext(
        user_id=actor.user_id, tenant_id=actor.tenant_id,
        roles=("OWNER",),
        permissions=tuple(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"]),
        grants=(ConstructionGrantContext(
            "OWNER", "tenant", actor.tenant_id,
        ),),
    )
    invitation_id = uuid4()
    saved = {
        "id": str(invitation_id),
        "role": "ADMIN",
        "scopes": [{
            "scope_type": "tenant",
            "scope_id": str(actor.tenant_id),
        }],
        "status": "pending",
        "row_version": 5,
    }
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service = SimpleNamespace(
        session=session,
        resend_invitation=AsyncMock(
            side_effect=AssertionError("Replay must not repeat domain mutation.")
        ),
    )
    reservation = SimpleNamespace(
        is_replay=True,
        response_status=200,
        response_payload=saved,
    )
    idempotency = SimpleNamespace(
        repository=SimpleNamespace(session=session),
        reserve=AsyncMock(return_value=reservation),
        complete=AsyncMock(),
    )
    delivery = SimpleNamespace(
        session=session,
        enqueue_invitation=AsyncMock(),
    )
    workflow = ConstructionInvitationWorkflow(
        session=session, invitation_service=service,
        idempotency_service=idempotency, delivery_queue=delivery,
    )

    response = await workflow.resend_invitation(
        actor=actor, access_context=context,
        invitation_id=invitation_id,
        expected_row_version=4,
        idempotency_key="resend-replay",
    )

    assert response == saved
    idempotency.reserve.assert_awaited_once()
    fields = idempotency.reserve.await_args.kwargs
    assert fields["tenant_id"] == actor.tenant_id
    assert fields["principal_id"] == actor.user_id
    assert fields["principal_type"] == "user"
    assert fields["operation"] == "construction.invitation.resend"
    assert fields["idempotency_key"] == "resend-replay"
    assert fields["payload"]["invitation_id"] == str(invitation_id)
    assert fields["payload"]["expected_row_version"] == 4
    service.resend_invitation.assert_not_awaited()
    delivery.enqueue_invitation.assert_not_awaited()
    idempotency.complete.assert_not_awaited()
    session.rollback.assert_not_awaited()
