from datetime import datetime, timezone

from cryptography.fernet import Fernet


def test_c005_workflow_factory_binds_all_writes_to_one_session():
    from database.repositories.construction import (
        ConstructionInvitationRepository,
        ConstructionProjectRepository,
    )
    from database.repositories.event import EventRepository
    from services.api_idempotency import (
        ApiIdempotencyResponseCodec,
        ApiIdempotencyService,
    )
    from services.construction.construction_invitation_delivery import (
        ConstructionInvitationDeliveryQueue,
    )
    from services.construction.construction_invitation_workflows import (
        build_construction_invitation_workflow,
    )
    from services.construction.construction_invitations import ConstructionInvitationService

    session = object()
    idempotency_key = Fernet.generate_key().decode("ascii")
    delivery_key = Fernet.generate_key().decode("ascii")
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    workflow = build_construction_invitation_workflow(
        session=session,
        idempotency_encryption_key=idempotency_key,
        delivery_encryption_key=delivery_key,
        now_provider=lambda: now,
        token_provider=lambda: "factory-private-token",
    )

    assert workflow.session is session
    service = workflow.invitation_service
    assert isinstance(service, ConstructionInvitationService)
    assert service.session is session
    assert isinstance(service.repository, ConstructionInvitationRepository)
    assert service.repository.session is session
    assert isinstance(service.project_repository, ConstructionProjectRepository)
    assert service.project_repository.session is session
    assert isinstance(service.audit_logger.event_repository, EventRepository)
    assert service.audit_logger.event_repository.session is session
    assert service.now_provider() == now
    assert service.token_provider() == "factory-private-token"

    assert isinstance(workflow.idempotency_service, ApiIdempotencyService)
    assert workflow.idempotency_service.repository.session is session
    ciphertext = workflow.idempotency_service.response_codec.encrypt({"id": "safe"})
    assert ApiIdempotencyResponseCodec(
        encryption_key=idempotency_key,
    ).decrypt(ciphertext) == {"id": "safe"}

    delivery = workflow.delivery_queue
    assert isinstance(delivery, ConstructionInvitationDeliveryQueue)
    assert delivery.session is session
    assert delivery.queue.session is session
    assert delivery.invitation_repository.session is session
    assert delivery.now_provider() == now
