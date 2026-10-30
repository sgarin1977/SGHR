from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["email", "role", "scopes", "grant_expiry"])
async def test_c005_reused_key_rejects_changed_request_before_creation(changed):
    from services.api_idempotency import (
        ApiIdempotencyKeyReusedError, ApiIdempotencyService, hash_request_payload,
    )
    from services.construction.construction_invitation_workflows import ConstructionInvitationWorkflow
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
    original = {
        "email": "person@example.com",
        "role": "SURVEYOR",
        "scopes": [{"scope_type": "project", "scope_id": str(project_id)}],
        "expires_at": None,
        "grant_expires_at": None,
    }
    record = SimpleNamespace(
        request_hash=hash_request_payload(original), status="completed",
    )
    calls, lookups = [], []

    class Session:
        async def commit(self):
            pytest.fail("Conflicting request must not commit")

        async def rollback(self):
            calls.append("rollback")

    session = Session()

    class IdempotencyRepository:
        async def get_active_record_for_update(self, **fields):
            calls.append("lookup")
            lookups.append(fields)
            return record

        async def create_record(self, **fields):
            pytest.fail("Existing key must not create another reservation")

        async def complete_record(self, **fields):
            pytest.fail("Conflicting request must not change saved response")

    repository = IdempotencyRepository()
    repository.session = session

    class InvitationService:
        async def create_invitation(self, **fields):
            pytest.fail("Conflict must not create invitation, token, or audit")

    service = InvitationService()
    service.session = session
    workflow = ConstructionInvitationWorkflow(
        session=session, invitation_service=service,
        idempotency_service=ApiIdempotencyService(repository=repository),
    )
    request = dict(
        actor=actor, access_context=context,
        idempotency_key="same-invitation-key",
        email="person@example.com", role="SURVEYOR",
        scopes=[("project", project_id)],
    )
    if changed == "email":
        request["email"] = "other@example.com"
    elif changed == "role":
        request["role"] = "ESTIMATOR"
    elif changed == "scopes":
        request["scopes"] = [("project", uuid4())]
    else:
        request["grant_expires_at"] = datetime(2026, 11, 1, tzinfo=timezone.utc)

    with pytest.raises(ApiIdempotencyKeyReusedError):
        await workflow.create_invitation(**request)

    assert calls == ["lookup", "rollback"]
    assert lookups[0]["tenant_id"] == actor.tenant_id
    assert lookups[0]["principal_id"] == actor.user_id
    assert record.request_hash == hash_request_payload(original)
    assert record.status == "completed"
