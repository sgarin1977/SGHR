from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from services.construction_invitation_delivery import (
    ConstructionInvitationDeliveryQueue,
    ConstructionInvitationTokenCodec,
)
from services.construction_invitation_workflows import ConstructionInvitationWorkflow
from services.construction_invitations import (
    ConstructionInvitationOperationError,
    ConstructionInvitationService,
    hash_construction_invitation_token,
)
from services.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS,
    ConstructionAccessContext,
    ConstructionGrantContext,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "enqueue", "complete"])
async def test_resend_workflow_transaction_and_final_public_response(failure):
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    actor = SimpleNamespace(
        user_id=uuid4(), tenant_id=uuid4(), status="active",
    )
    context = ConstructionAccessContext(
        user_id=actor.user_id, tenant_id=actor.tenant_id,
        roles=("OWNER",),
        permissions=tuple(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"]),
        grants=(ConstructionGrantContext("OWNER", "tenant", actor.tenant_id),),
    )
    old_token, new_token = "private-old-token", "private-new-token"
    invitation = SimpleNamespace(
        id=uuid4(), tenant_id=actor.tenant_id,
        email="person@example.com", role="ADMIN",
        status="pending", row_version=4,
        expires_at=now + timedelta(days=1),
        grant_expires_at=now + timedelta(days=30),
        token_used_at=None,
        token_hash=hash_construction_invitation_token(old_token),
        delivery_metadata={"attempt_count": 2},
    )
    scope = SimpleNamespace(
        tenant_id=actor.tenant_id, invitation_id=invitation.id,
        scope_type="tenant", scope_id=actor.tenant_id,
    )
    calls, events, jobs, completions, reservations = [], [], [], [], []
    reservation = SimpleNamespace(is_replay=False)
    injected_error = RuntimeError("Injected storage failure")

    class Session:
        async def commit(self):
            calls.append("commit")

        async def rollback(self):
            calls.append("rollback")

    session = Session()

    class Repository:
        async def get_invitation_for_update(self, **fields):
            assert fields == {
                "tenant_id": actor.tenant_id,
                "invitation_id": invitation.id,
            }
            calls.append("lock")
            return invitation

        async def list_invitation_scopes(self, **fields):
            assert fields == {
                "tenant_id": actor.tenant_id,
                "invitation_id": invitation.id,
            }
            calls.append("scopes")
            return [scope]

        async def resend_invitation(self, **fields):
            assert fields.pop("invitation") is invitation
            calls.append("resend")
            for key, value in fields.items():
                setattr(invitation, key, value)
            invitation.row_version += 1
            return invitation

        async def update_delivery_metadata(self, **fields):
            assert fields["invitation"] is invitation
            calls.append("metadata")
            invitation.delivery_metadata = dict(fields["delivery_metadata"])
            invitation.row_version += 1
            return invitation

        async def create_event(self, **fields):
            calls.append("audit")
            events.append(fields)
            return SimpleNamespace(**fields)

    repository = Repository()
    repository.session = session

    class JobQueue:
        async def enqueue(self, *, job):
            calls.append("enqueue")
            assert job.tenant_id == actor.tenant_id
            assert job.resource_id == invitation.id
            assert job.job_type == "INVITATION_EMAIL"
            if failure == "enqueue":
                raise injected_error
            jobs.append(job)
            return job.id

    job_queue = JobQueue()
    job_queue.session = session
    codec = ConstructionInvitationTokenCodec(
        encryption_key=Fernet.generate_key().decode("ascii"),
    )
    delivery = ConstructionInvitationDeliveryQueue(
        session=session, queue=job_queue,
        invitation_repository=repository, token_codec=codec,
        now_provider=lambda: now,
    )
    service = ConstructionInvitationService(
        session=session, repository=repository,
        event_repository=repository,
        now_provider=lambda: now, token_provider=lambda: new_token,
    )

    class Idempotency:
        async def reserve(self, **fields):
            calls.append("reserve")
            reservations.append(fields)
            return reservation

        async def complete(self, **fields):
            calls.append("complete")
            assert fields["reservation"] is reservation
            completions.append(fields)
            if failure == "complete":
                raise injected_error

    idempotency = Idempotency()
    idempotency.repository = SimpleNamespace(session=session)
    workflow = ConstructionInvitationWorkflow(
        session=session, invitation_service=service,
        idempotency_service=idempotency, delivery_queue=delivery,
    )
    request = dict(
        actor=actor, access_context=context,
        invitation_id=invitation.id, expected_row_version=4,
        idempotency_key="resend-transaction", trace_id="resend-test",
    )

    prefix = ["reserve", "lock", "scopes", "resend", "audit", "scopes", "enqueue"]
    if failure is None:
        response = await workflow.resend_invitation(**request)
        assert calls == prefix + ["metadata", "complete", "commit"]
        assert response == {
            "id": str(invitation.id),
            "email": invitation.email,
            "role": "ADMIN",
            "status": "pending",
            "scopes": [{
                "scope_type": "tenant", "scope_id": str(actor.tenant_id),
            }],
            "expires_at": (now + timedelta(days=7)).isoformat(),
            "grant_expires_at": (now + timedelta(days=30)).isoformat(),
            "row_version": 6,
        }
        assert completions[0]["response_payload"] == response
    else:
        with pytest.raises(ConstructionInvitationOperationError) as caught:
            await workflow.resend_invitation(**request)
        suffix = [] if failure == "enqueue" else ["metadata", "complete"]
        assert calls == prefix + suffix + ["rollback"]
        assert caught.value.__cause__ is injected_error
        assert "commit" not in calls

    assert reservations == [{
        "tenant_id": actor.tenant_id,
        "principal_type": "user",
        "principal_id": actor.user_id,
        "operation": "construction.invitation.resend",
        "idempotency_key": "resend-transaction",
        "payload": {
            "invitation_id": str(invitation.id),
            "expected_row_version": 4,
            "expires_at": None,
        },
    }]
    assert len(events) == 1
    assert events[0]["event_type"] == "construction_invitation_resent"
    assert events[0]["entity_id"] == invitation.id
    assert invitation.token_hash == hash_construction_invitation_token(new_token)

    public_data = repr(events)
    if completions:
        assert len(completions) == 1
        assert completions[0]["response_status"] == 200
        assert completions[0]["response_payload"]["row_version"] == 6
        public_data += repr(completions[0]["response_payload"])

    for secret in (
        old_token, new_token,
        hash_construction_invitation_token(old_token),
        invitation.token_hash,
    ):
        assert secret not in public_data
    for private_field in ("token_hash", "token_ciphertext", "delivery_metadata"):
        assert private_field not in public_data

    if failure != "enqueue":
        assert len(jobs) == 1
        metadata = invitation.delivery_metadata
        assert metadata["job_id"] == str(jobs[0].id)
        assert metadata["trace_id"] == "resend-test"
        assert codec.decrypt(
            ciphertext=metadata["token_ciphertext"],
            tenant_id=actor.tenant_id, invitation_id=invitation.id,
        ) == new_token
        assert metadata["token_ciphertext"] not in public_data
