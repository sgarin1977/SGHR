from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from services.api_idempotency import (
    ApiIdempotencyKeyReusedError,
    ApiIdempotencyResponseCodec,
    ApiIdempotencyService,
    hash_request_payload,
)
from services.construction.construction_invitation_workflows import (
    ConstructionInvitationIdempotencyInProgressError,
    ConstructionInvitationWorkflow,
)
from services.construction.construction_invitations import ConstructionInvitationValidationError
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS,
    ConstructionAccessContext,
    ConstructionGrantContext,
    ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError,
)


def make_case(saved_role="ADMIN"):
    actor = SimpleNamespace(
        user_id=uuid4(), tenant_id=uuid4(), status="active",
    )
    context = ConstructionAccessContext(
        user_id=actor.user_id, tenant_id=actor.tenant_id,
        roles=("OWNER",),
        permissions=tuple(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"]),
        grants=(ConstructionGrantContext("OWNER", "tenant", actor.tenant_id),),
    )
    invitation_id = uuid4()
    request = dict(
        actor=actor, access_context=context,
        invitation_id=invitation_id, expected_row_version=4,
        idempotency_key="same-resend-key", expires_at=None,
    )
    saved = {
        "id": str(invitation_id),
        "email": "person@example.com",
        "role": saved_role,
        "status": "pending",
        "scopes": [{
            "scope_type": "tenant", "scope_id": str(actor.tenant_id),
        }],
        "expires_at": "2026-10-08T12:00:00+00:00",
        "grant_expires_at": None,
        "row_version": 6,
    }
    codec = ApiIdempotencyResponseCodec(
        encryption_key=Fernet.generate_key().decode("ascii"),
    )
    record = SimpleNamespace(
        request_hash=hash_request_payload({
            "invitation_id": str(invitation_id),
            "expected_row_version": 4,
            "expires_at": None,
        }),
        status="completed",
        response_status=200,
        response_ciphertext=codec.encrypt(saved),
    )
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    repository = SimpleNamespace(
        session=session,
        get_active_record_for_update=AsyncMock(return_value=record),
        create_record=AsyncMock(
            side_effect=AssertionError("Existing key must not create a record."),
        ),
        complete_record=AsyncMock(
            side_effect=AssertionError("Replay must not replace saved response."),
        ),
    )
    invitation_service = SimpleNamespace(
        session=session,
        resend_invitation=AsyncMock(
            side_effect=AssertionError("Replay or rejection must not mutate."),
        ),
    )
    delivery = SimpleNamespace(
        session=session,
        enqueue_invitation=AsyncMock(
            side_effect=AssertionError("Replay or rejection must not enqueue."),
        ),
    )
    workflow = ConstructionInvitationWorkflow(
        session=session,
        invitation_service=invitation_service,
        idempotency_service=ApiIdempotencyService(
            repository=repository, response_codec=codec,
        ),
        delivery_queue=delivery,
    )
    return SimpleNamespace(
        actor=actor, context=context, request=request, saved=saved,
        record=record, session=session, repository=repository,
        invitation_service=invitation_service, delivery=delivery,
        workflow=workflow,
    )


def assert_rejected_without_writes(case):
    case.invitation_service.resend_invitation.assert_not_awaited()
    case.delivery.enqueue_invitation.assert_not_awaited()
    case.repository.create_record.assert_not_awaited()
    case.repository.complete_record.assert_not_awaited()
    case.session.commit.assert_not_awaited()
    case.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("lost", ["permission", "scope", "delegation"])
async def test_resend_replay_rechecks_current_authority(lost):
    case = make_case(saved_role="OWNER" if lost == "delegation" else "ADMIN")
    before = dict(vars(case.record))

    # The same saved response is accessible before authority changes.
    response = await case.workflow.resend_invitation(**case.request)
    assert response == case.saved
    case.session.commit.assert_awaited_once()
    case.session.rollback.assert_not_awaited()
    case.session.commit.reset_mock()

    if lost == "permission":
        role = "OBSERVER"
        scope_type, scope_id = "project", uuid4()
        expected_error = ConstructionPermissionDeniedError
    elif lost == "scope":
        role = "OWNER"
        scope_type, scope_id = "project", uuid4()
        expected_error = ConstructionScopeDeniedError
    else:
        role = "ADMIN"
        scope_type, scope_id = "tenant", case.actor.tenant_id
        expected_error = ConstructionPermissionDeniedError

    case.request["access_context"] = ConstructionAccessContext(
        user_id=case.actor.user_id, tenant_id=case.actor.tenant_id,
        roles=(role,),
        permissions=tuple(CONSTRUCTION_ROLE_PERMISSIONS[role]),
        grants=(ConstructionGrantContext(role, scope_type, scope_id),),
    )
    with pytest.raises(expected_error):
        await case.workflow.resend_invitation(**case.request)

    assert_rejected_without_writes(case)
    assert vars(case.record) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed", ["invitation_id", "expected_row_version", "expires_at"],
)
async def test_resend_same_key_rejects_changed_payload(changed):
    case = make_case()
    before = dict(vars(case.record))
    replacements = {
        "invitation_id": uuid4(),
        "expected_row_version": 5,
        "expires_at": datetime(2026, 11, 1, tzinfo=timezone.utc),
    }
    case.request[changed] = replacements[changed]

    with pytest.raises(ApiIdempotencyKeyReusedError):
        await case.workflow.resend_invitation(**case.request)

    case.repository.get_active_record_for_update.assert_awaited_once()
    assert_rejected_without_writes(case)
    assert vars(case.record) == before


@pytest.mark.asyncio
async def test_resend_processing_reservation_does_not_repeat_operation():
    case = make_case()
    case.record.status = "processing"
    case.record.response_status = None
    case.record.response_ciphertext = None
    before = dict(vars(case.record))

    with pytest.raises(ConstructionInvitationIdempotencyInProgressError) as caught:
        await case.workflow.resend_invitation(**case.request)

    assert caught.value.status_code == 409
    case.repository.get_active_record_for_update.assert_awaited_once()
    assert_rejected_without_writes(case)
    assert vars(case.record) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "", "   ", 123])
async def test_resend_requires_valid_key_before_repository_access(key):
    case = make_case()
    before = dict(vars(case.record))
    case.request["idempotency_key"] = key

    with pytest.raises(ConstructionInvitationValidationError):
        await case.workflow.resend_invitation(**case.request)

    case.repository.get_active_record_for_update.assert_not_awaited()
    assert_rejected_without_writes(case)
    assert vars(case.record) == before
