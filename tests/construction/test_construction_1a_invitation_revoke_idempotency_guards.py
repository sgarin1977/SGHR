from uuid import uuid4

import pytest

from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction.construction_invitation_workflows import (
    ConstructionInvitationIdempotencyInProgressError,
)
from services.construction.construction_invitations import (
    ConstructionInvitationOperationError,
    ConstructionInvitationValidationError,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError,
)
from test_construction_1a_invitation_revoke_service import (
    context_for, current_row, revoke_case, invitation_store,
)
from test_construction_1a_invitation_revoke_workflow import (
    pipeline, invoke, ledger_rows,
)


async def request(pipeline, **changes):
    case = pipeline.case
    fields = {
        "actor": case.actor,
        "access_context": case.context,
        "invitation_id": case.invitation_id,
        "expected_row_version": 4,
        "idempotency_key": "revoke-key",
        "trace_id": "revoke-guards",
    }
    fields.update(changes)
    return await pipeline.workflow.revoke_invitation(**fields)


def snapshot(pipeline):
    case = pipeline.case
    state = (current_row(case), ledger_rows(pipeline))
    case.calls.clear()
    case.events.create_event.reset_mock()
    case.adapter.rollbacks = 0
    return state


def assert_rejected_without_changes(pipeline, before, *, reserved=True):
    case = pipeline.case
    expected_calls = ["reserve", "rollback"] if reserved else ["rollback"]
    assert case.calls == expected_calls
    assert case.adapter.rollbacks == 1
    assert current_row(case) == before[0]
    assert ledger_rows(pipeline) == before[1]
    case.events.create_event.assert_not_awaited()
    case.token_provider.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["invitation_id", "expected_row_version"])
async def test_revoke_same_key_rejects_changed_payload(pipeline, changed):
    await invoke(pipeline)
    before = snapshot(pipeline)
    replacement = uuid4() if changed == "invitation_id" else 5

    with pytest.raises(ApiIdempotencyKeyReusedError):
        await request(pipeline, **{changed: replacement})

    assert_rejected_without_changes(pipeline, before)


@pytest.mark.asyncio
async def test_revoke_processing_reservation_does_not_repeat_operation(pipeline):
    case = pipeline.case
    reservation = await pipeline.idempotency.reserve(
        tenant_id=case.actor.tenant_id,
        principal_type="user",
        principal_id=case.actor.user_id,
        operation="construction.invitation.revoke",
        idempotency_key="revoke-key",
        payload={
            "invitation_id": str(case.invitation_id),
            "expected_row_version": 4,
        },
    )
    assert reservation.is_replay is False
    case.session.commit()

    before = snapshot(pipeline)
    assert before[0]["status"] == "pending"
    assert before[1][0]["status"] == "processing"

    with pytest.raises(ConstructionInvitationIdempotencyInProgressError) as caught:
        await request(pipeline)

    assert caught.value.status_code == 409
    assert_rejected_without_changes(pipeline, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("lost", ["permission", "scope", "delegation"])
async def test_revoke_replay_rechecks_current_authority(pipeline, lost):
    case = pipeline.case
    if lost == "delegation":
        case.session.execute(
            case.table.update().where(
                case.table.c.id == case.invitation_id
            ).values(role="OWNER")
        )
        case.session.commit()

    first = await invoke(pipeline)
    assert first["status"] == "revoked"

    if lost == "permission":
        context = context_for(case.actor, "OBSERVER")
        expected_error = ConstructionPermissionDeniedError
    elif lost == "scope":
        context = context_for(case.actor, "OWNER", "project", uuid4())
        expected_error = ConstructionScopeDeniedError
    else:
        context = context_for(case.actor, "ADMIN")
        expected_error = ConstructionPermissionDeniedError

    before = snapshot(pipeline)
    with pytest.raises(expected_error):
        await request(pipeline, access_context=context)

    assert_rejected_without_changes(pipeline, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "", "   ", 123])
async def test_revoke_requires_valid_key_before_repository_access(pipeline, key):
    before = snapshot(pipeline)

    with pytest.raises(ConstructionInvitationValidationError):
        await request(pipeline, idempotency_key=key)

    assert_rejected_without_changes(pipeline, before, reserved=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [
    "extra_token", "wrong_id", "wrong_status",
    "boolean_version", "empty_scopes", "invalid_scope_uuid",
])
async def test_revoke_does_not_return_invalid_saved_response(pipeline, invalid):
    saved = await invoke(pipeline)
    case = pipeline.case

    if invalid == "extra_token":
        saved["token"] = "test-value-must-not-be-returned"
    elif invalid == "wrong_id":
        saved["id"] = str(uuid4())
    elif invalid == "wrong_status":
        saved["status"] = "pending"
    elif invalid == "boolean_version":
        saved["row_version"] = True
    elif invalid == "empty_scopes":
        saved["scopes"] = []
    else:
        saved["scopes"][0]["scope_id"] = "not-a-uuid"

    record = ledger_rows(pipeline)[0]
    case.session.execute(
        pipeline.table.update().where(
            pipeline.table.c.id == record["id"]
        ).values(response_ciphertext=pipeline.codec.encrypt(saved))
    )
    case.session.commit()
    before = snapshot(pipeline)

    with pytest.raises(ConstructionInvitationOperationError):
        await request(pipeline)

    assert_rejected_without_changes(pipeline, before)
