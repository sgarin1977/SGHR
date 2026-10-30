from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "enqueue", "complete"])
async def test_c005_creation_workflow_commits_only_after_all_required_writes(failure):
    from services.construction.construction_invitation_workflows import ConstructionInvitationWorkflow
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
    token = "workflow-test-secret-not-for-public-response"
    calls, events, completions, deliveries = [], [], [], []
    invitation_id = uuid4()

    class Session:
        async def commit(self):
            assert failure is None
            assert calls[-1] == "complete"
            calls.append("commit")

        async def rollback(self):
            calls.append("rollback")

    session = Session()

    class Repository:
        async def create_invitation(self, **fields):
            calls.append("create")
            return SimpleNamespace(
                id=invitation_id, status="pending", row_version=1, **fields,
            )

        async def create_event(self, **fields):
            calls.append("audit")
            events.append(fields)
            return SimpleNamespace(**fields)

    repository = Repository()
    service = ConstructionInvitationService(
        session=session, repository=repository, event_repository=repository,
        now_provider=lambda: datetime(2026, 10, 1, 12, tzinfo=timezone.utc),
        token_provider=lambda: token,
    )

    class IdempotencyService:
        async def reserve(self, **fields):
            calls.append("reserve")
            return SimpleNamespace(is_replay=False)

        async def complete(self, **fields):
            calls.append("complete")
            completions.append(fields)
            if failure == "complete":
                raise RuntimeError("Idempotency storage failed")

    idempotency_service = IdempotencyService()
    idempotency_service.repository = SimpleNamespace(session=session)

    class DeliveryQueue:
        async def enqueue_invitation(self, **fields):
            calls.append("enqueue")
            deliveries.append(fields)
            if failure == "enqueue":
                raise RuntimeError("Delivery queue failed")

    queue = DeliveryQueue()
    queue.session = session
    workflow = ConstructionInvitationWorkflow(
        session=session, invitation_service=service,
        idempotency_service=idempotency_service, delivery_queue=queue,
    )
    request = dict(
        actor=actor, access_context=context,
        idempotency_key="new-invitation-key",
        email="person@example.com", role="ADMIN",
        scopes=[("tenant", actor.tenant_id)],
        trace_id="creation-workflow",
    )

    if failure is None:
        response = await workflow.create_invitation(**request)
        assert calls == ["reserve", "create", "audit", "enqueue", "complete", "commit"]
        assert response == completions[0]["response_payload"]
        assert response["id"] == str(invitation_id)
        assert response["status"] == "pending"
        assert response["row_version"] == 1
        assert token not in str(response)
    else:
        with pytest.raises(ConstructionInvitationOperationError) as error:
            await workflow.create_invitation(**request)
        expected = ["reserve", "create", "audit", "enqueue"]
        if failure == "complete":
            expected.append("complete")
        assert calls == expected + ["rollback"]
        assert isinstance(error.value.__cause__, RuntimeError)

    assert len(events) == len(deliveries) == 1
    assert events[0]["event_type"] == "construction_invitation_created"
    assert events[0]["entity_id"] == invitation_id
    assert token not in str(events)
    assert deliveries[0]["actor"] is actor
    assert deliveries[0]["invitation"].id == invitation_id
    assert deliveries[0]["token"] == token
    if completions:
        assert completions[0]["response_status"] == 201
        assert token not in str(completions[0]["response_payload"])
        assert "token_hash" not in completions[0]["response_payload"]
