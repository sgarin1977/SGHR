from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet


@pytest.mark.asyncio
async def test_c005_delivery_queue_uses_shared_session_and_encrypted_token():
    from database.models import ConstructionInvitation
    from database.repositories.construction import ConstructionInvitationRepository
    from services.construction.construction_invitation_delivery import (
        ConstructionInvitationDeliveryQueue,
        ConstructionInvitationTokenCodec,
    )
    from services.construction.construction_invitations import hash_construction_invitation_token

    calls, jobs = [], []

    class Session:
        async def flush(self):
            calls.append("flush")

        async def commit(self):
            pytest.fail("Delivery adapter must not commit.")

    session = Session()
    actor = SimpleNamespace(
        tenant_id=uuid4(), user_id=uuid4(), status="active",
    )
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    token = "secret-token-for-delivery-only"
    invitation = ConstructionInvitation(
        id=uuid4(), tenant_id=actor.tenant_id,
        created_by_user_id=actor.user_id,
        email="person@example.com", role="ADMIN", status="pending",
        token_hash=hash_construction_invitation_token(token),
        token_used_at=None, expires_at=now + timedelta(days=7),
        grant_expires_at=None, row_version=1,
        delivery_metadata={"attempt_count": 0},
    )

    class Queue:
        async def enqueue(self, *, job):
            calls.append("enqueue")
            jobs.append(job)
            return job.id

    queue = Queue()
    queue.session = session
    codec = ConstructionInvitationTokenCodec(
        encryption_key=Fernet.generate_key().decode("ascii"),
    )
    adapter = ConstructionInvitationDeliveryQueue(
        session=session, queue=queue,
        invitation_repository=ConstructionInvitationRepository(session),
        token_codec=codec, now_provider=lambda: now,
    )
    job_id = await adapter.enqueue_invitation(
        actor=actor, invitation=invitation, token=token, trace_id="delivery-test",
    )

    assert calls == ["enqueue", "flush"]
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == job_id
    assert job.tenant_id == actor.tenant_id
    assert job.user_id == actor.user_id
    assert job.resource_type == "construction_invitation"
    assert job.resource_id == invitation.id
    assert job.job_type == "INVITATION_EMAIL"
    assert job.status == "QUEUED"
    assert job.available_at == now
    assert job.idempotency_key.endswith(invitation.token_hash)
    assert token not in repr(job.__dict__)

    metadata = invitation.delivery_metadata
    assert metadata["attempt_count"] == 0
    assert metadata["job_id"] == str(job_id)
    assert metadata["trace_id"] == "delivery-test"
    assert token not in repr(metadata)
    assert codec.decrypt(
        ciphertext=metadata["token_ciphertext"],
        tenant_id=actor.tenant_id, invitation_id=invitation.id,
    ) == token
