from datetime import datetime, timezone
from uuid import uuid4

from cryptography.fernet import Fernet


def test_c005_delivery_factory_uses_existing_queue_and_shared_session():
    from database.models import ConstructionBackgroundJobRecord
    from database.repositories.construction import ConstructionInvitationRepository
    from database.repositories.job_queue import PostgresJobQueue
    from services.construction_invitation_delivery import (
        ConstructionInvitationTokenCodec,
        build_construction_invitation_delivery_queue,
    )

    session = object()
    key = Fernet.generate_key().decode("ascii")
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    adapter = build_construction_invitation_delivery_queue(
        session=session, encryption_key=key, now_provider=lambda: now,
    )

    assert adapter.session is session
    assert isinstance(adapter.queue, PostgresJobQueue)
    assert adapter.queue.session is session
    assert adapter.queue.model is ConstructionBackgroundJobRecord
    assert adapter.queue.idempotency_columns == (
        "tenant_id", "user_id", "job_type", "idempotency_key",
    )
    assert adapter.queue.idempotency_payload_columns == (
        "resource_type", "resource_id",
    )
    assert isinstance(adapter.invitation_repository, ConstructionInvitationRepository)
    assert adapter.invitation_repository.session is session
    assert adapter.now_provider() == now

    tenant_id, invitation_id = uuid4(), uuid4()
    ciphertext = adapter.token_codec.encrypt(
        tenant_id=tenant_id, invitation_id=invitation_id,
        token="factory-private-token",
    )
    reader = ConstructionInvitationTokenCodec(encryption_key=key)
    assert reader.decrypt(
        ciphertext=ciphertext,
        tenant_id=tenant_id, invitation_id=invitation_id,
    ) == "factory-private-token"
