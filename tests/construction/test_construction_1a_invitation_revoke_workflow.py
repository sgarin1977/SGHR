from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Column, MetaData, Table, UniqueConstraint, select

from database.models import ApiIdempotencyRecord
from database.repositories.api_idempotency import ApiIdempotencyRepository
from services.api_idempotency import (
    ApiIdempotencyResponseCodec, ApiIdempotencyService,
)
from services.construction.construction_invitations import ConstructionInvitationOperationError
from services.construction.construction_invitation_workflows import ConstructionInvitationWorkflow
from test_construction_1a_invitation_revoke_service import (
    revoke_case, invitation_store, current_row,
)


@pytest.fixture
def pipeline(revoke_case):
    case = revoke_case
    metadata = MetaData()
    original = ApiIdempotencyRecord.__table__
    table = Table(
        original.name,
        metadata,
        *[
            Column(
                column.name, column.type,
                primary_key=column.primary_key,
                nullable=column.nullable,
            )
            for column in original.columns
        ],
        UniqueConstraint(
            "tenant_id", "principal_type", "principal_id",
            "operation", "key_hash",
        ),
        schema=original.schema,
    )
    metadata.create_all(case.session.connection())
    case.session.commit()
    flags = SimpleNamespace(fail_complete=False, fail_commit=False)

    class SQLiteIdempotencyRepository(ApiIdempotencyRepository):
        # Atomic PostgreSQL INSERT is covered by its existing SQL tests.
        # This fixture exercises transactional ORM storage on SQLite.
        reserve_record = None

        async def get_active_record_for_update(self, **fields):
            case.calls.append("reserve")
            return await super().get_active_record_for_update(**fields)

        async def complete_record(self, **fields):
            case.calls.append("complete")
            if flags.fail_complete:
                raise RuntimeError("Idempotency completion unavailable")
            return await super().complete_record(**fields)

    case.adapter.add = case.session.add
    original_rollback = case.adapter.rollback

    async def commit():
        case.calls.append("commit")
        if flags.fail_commit:
            raise RuntimeError("Commit unavailable")
        case.session.commit()

    async def rollback():
        case.calls.append("rollback")
        await original_rollback()

    case.adapter.commit = commit
    case.adapter.rollback = rollback

    codec = ApiIdempotencyResponseCodec(
        encryption_key=Fernet.generate_key().decode("ascii"),
    )
    idempotency = ApiIdempotencyService(
        repository=SQLiteIdempotencyRepository(case.adapter),
        response_codec=codec,
    )
    workflow = ConstructionInvitationWorkflow(
        session=case.adapter,
        invitation_service=case.service,
        idempotency_service=idempotency,
    )
    return SimpleNamespace(
        case=case, table=table, codec=codec,
        idempotency=idempotency, workflow=workflow, flags=flags,
    )


async def invoke(pipeline):
    case = pipeline.case
    return await pipeline.workflow.revoke_invitation(
        actor=case.actor,
        access_context=case.context,
        invitation_id=case.invitation_id,
        expected_row_version=4,
        idempotency_key="revoke-key",
        trace_id="revoke-workflow",
    )


def ledger_rows(pipeline):
    return [
        dict(row) for row in pipeline.case.session.execute(
            select(pipeline.table)
        ).mappings().all()
    ]


@pytest.mark.asyncio
async def test_revoke_workflow_commits_business_audit_and_saved_response(pipeline):
    case = pipeline.case
    response = await invoke(pipeline)

    assert case.calls == [
        "reserve", "lock", "scopes", "revoke",
        "audit", "scopes", "complete", "commit",
    ]
    assert response["id"] == str(case.invitation_id)
    assert response["status"] == "revoked"
    assert response["row_version"] == 5
    assert current_row(case)["status"] == "revoked"
    assert case.adapter.rollbacks == 0
    case.events.create_event.assert_awaited_once()
    case.token_provider.assert_not_called()

    records = ledger_rows(pipeline)
    assert len(records) == 1
    record = records[0]
    assert record["tenant_id"] == case.actor.tenant_id
    assert record["principal_id"] == case.actor.user_id
    assert record["principal_type"] == "user"
    assert record["operation"] == "construction.invitation.revoke"
    assert record["status"] == "completed"
    assert record["response_status"] == 200
    assert pipeline.codec.decrypt(record["response_ciphertext"]) == response
    assert set(response) == {
        "id", "email", "role", "status", "scopes",
        "expires_at", "grant_expires_at", "row_version",
    }
    assert "token_hash" not in repr(response)
    assert "delivery_metadata" not in repr(response)


@pytest.mark.asyncio
async def test_revoke_replay_returns_saved_response_without_second_audit(pipeline):
    case = pipeline.case
    first = await invoke(pipeline)
    before_invitation = current_row(case)
    before_ledger = ledger_rows(pipeline)
    case.calls.clear()
    case.events.create_event.reset_mock()

    replay = await invoke(pipeline)

    assert replay == first
    assert case.calls == ["reserve", "commit"]
    assert current_row(case) == before_invitation
    assert ledger_rows(pipeline) == before_ledger
    case.events.create_event.assert_not_awaited()
    assert case.adapter.rollbacks == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "complete", "commit"])
async def test_revoke_workflow_failure_rolls_back_both_records(
    pipeline, failure,
):
    case = pipeline.case
    before = current_row(case)
    if failure == "audit":
        case.events.create_event.side_effect = RuntimeError("Audit unavailable")
    elif failure == "complete":
        pipeline.flags.fail_complete = True
    else:
        pipeline.flags.fail_commit = True

    with pytest.raises(ConstructionInvitationOperationError):
        await invoke(pipeline)

    assert "revoke" in case.calls
    assert case.adapter.rollbacks >= 1
    assert case.calls[-1] == "rollback"
    assert current_row(case) == before
    assert ledger_rows(pipeline) == []
    case.events.create_event.assert_awaited_once()
