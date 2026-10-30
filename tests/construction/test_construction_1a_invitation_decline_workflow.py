from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import Column, JSON, MetaData, Table, select
from sqlalchemy.dialects.postgresql import JSONB

from database.models import EventLog
from database.repositories.event import EventRepository
from services.construction.construction_audit import ConstructionAuditLogger
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_invitation_decline_workflow import (
    ConstructionInvitationDeclineWorkflow,
    build_construction_invitation_decline_workflow,
)
from services.construction.construction_invitations import (
    ConstructionInvitationOperationError,
    ConstructionInvitationRecipientDeniedError,
    ConstructionInvitationTokenDeniedError,
)
from test_construction_1a_invitation_decline_service import (
    decline_case, invitation_store, row,
)


@pytest.fixture
def pipeline(decline_case):
    state = decline_case
    metadata = MetaData()
    original = EventLog.__table__
    audit_table = Table(
        original.name, metadata,
        *[
            Column(
                column.name,
                JSON() if isinstance(column.type, JSONB) else column.type,
                primary_key=column.primary_key,
                nullable=column.nullable,
            )
            for column in original.columns
        ],
        schema=original.schema,
    )
    metadata.create_all(state.session.connection())
    state.session.commit()
    flags = SimpleNamespace(fail_audit=False, fail_commit=False, commits=0)
    state.adapter.add = state.session.add

    class AuditRepository(EventRepository):
        async def create_event(self, **fields):
            state.calls.append("stored_audit")
            event = await super().create_event(**fields)
            if flags.fail_audit:
                raise RuntimeError(state.token)
            return event

    async def commit():
        state.calls.append("commit")
        flags.commits += 1
        if flags.fail_commit:
            raise RuntimeError(state.token)
        state.session.commit()

    rollback = state.adapter.rollback

    async def counted_rollback():
        state.calls.append("rollback")
        await rollback()

    state.adapter.commit = commit
    state.adapter.rollback = counted_rollback
    state.service.audit_logger = ConstructionAuditLogger(
        event_repository=AuditRepository(state.adapter),
    )
    workflow = ConstructionInvitationDeclineWorkflow(
        session=state.adapter, decline_service=state.service,
    )
    return SimpleNamespace(
        state=state, table=audit_table,
        workflow=workflow, flags=flags,
    )


def audits(pipeline):
    return [
        dict(record) for record in pipeline.state.session.execute(
            select(pipeline.table)
        ).mappings().all()
    ]


async def invoke(pipeline, **overrides):
    state = pipeline.state
    arguments = {
        "actor": state.actor, "tenant_id": state.tenant_id,
        "invitation_id": state.invitation_id,
        "expected_row_version": 4, "token": state.token,
        "trace_id": "decline-workflow",
    }
    arguments.update(overrides)
    return await pipeline.workflow.decline_invitation(**arguments)


@pytest.mark.asyncio
async def test_decline_commits_status_and_real_audit_with_minimal_response(pipeline):
    state = pipeline.state
    response = await invoke(pipeline)
    assert state.calls == [
        "lock", "identity", "decline", "stored_audit", "commit",
    ]
    assert pipeline.flags.commits == 1
    assert state.adapter.rollbacks == 0
    assert row(state)["status"] == "declined"
    assert response == {
        "id": str(state.invitation_id),
        "status": "declined", "row_version": 5,
    }
    records = audits(pipeline)
    assert len(records) == 1
    event = records[0]
    assert event["event_type"] == "construction_invitation_declined"
    assert event["tenant_id"] == state.tenant_id
    assert event["user_id"] == state.actor.user_id
    assert event["entity_id"] == state.invitation_id
    assert event["trace_id"] == "decline-workflow"
    assert event["payload"] == {
        "operation": "decline",
        "changes": [{
            "field": "status", "old_value": "pending",
            "new_value": "declined",
        }],
        "reason": None,
    }
    for secret in (
        state.token, row(state)["token_hash"], "delivery_metadata",
    ):
        assert secret not in repr(response)
        assert secret not in repr(event)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit"])
async def test_decline_failure_rolls_back_invitation_and_inserted_audit(
    pipeline, failure,
):
    state = pipeline.state
    before = row(state)
    setattr(pipeline.flags, f"fail_{failure}", True)
    with pytest.raises(ConstructionInvitationOperationError) as caught:
        await invoke(pipeline)
    assert "decline" in state.calls
    assert "stored_audit" in state.calls
    assert state.calls[-1] == "rollback"
    assert row(state) == before
    assert audits(pipeline) == []
    assert state.adapter.rollbacks >= 1
    assert state.token not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["id", "tenant_id", "status", "row_version"])
async def test_invalid_result_after_audit_is_not_committed(pipeline, field):
    state = pipeline.state
    before = row(state)
    original = state.service.decline_invitation

    async def invalid_result(**arguments):
        invitation = await original(**arguments)
        values = {
            "id": invitation.id, "tenant_id": invitation.tenant_id,
            "status": invitation.status,
            "row_version": invitation.row_version,
        }
        values[field] = (
            uuid4() if field in {"id", "tenant_id"}
            else "pending" if field == "status" else True
        )
        return SimpleNamespace(**values)

    state.service.decline_invitation = AsyncMock(side_effect=invalid_result)
    with pytest.raises(ConstructionInvitationOperationError):
        await invoke(pipeline)
    assert pipeline.flags.commits == 0
    assert row(state) == before
    assert audits(pipeline) == []


@pytest.mark.asyncio
async def test_repeated_terminal_decline_does_not_add_audit_or_change_version(pipeline):
    state = pipeline.state
    await invoke(pipeline)
    before, before_audit = row(state), audits(pipeline)
    with pytest.raises(ConstructionInvitationTokenDeniedError):
        await invoke(pipeline)
    assert row(state) == before
    assert audits(pipeline) == before_audit
    assert pipeline.flags.commits == 1


@pytest.mark.asyncio
async def test_decline_workflow_preserves_recipient_denial(pipeline):
    state = pipeline.state
    before = row(state)
    state.identity.has_verified_email.side_effect = None
    state.identity.has_verified_email.return_value = False
    with pytest.raises(ConstructionInvitationRecipientDeniedError):
        await invoke(pipeline)
    assert pipeline.flags.commits == 0
    assert row(state) == before
    assert audits(pipeline) == []


@pytest.mark.asyncio
async def test_decline_workflow_preserves_409_version_conflict(pipeline):
    before = row(pipeline.state)
    with pytest.raises(ConstructionRowVersionConflictError) as caught:
        await invoke(pipeline, expected_row_version=3)
    assert caught.value.status_code == 409
    assert caught.value.code == "ROW_VERSION_CONFLICT"
    assert pipeline.flags.commits == 0
    assert row(pipeline.state) == before
    assert audits(pipeline) == []


def test_factory_uses_shared_business_session_and_platform_identity_reader(pipeline):
    state = pipeline.state
    workflow = build_construction_invitation_decline_workflow(
        session=state.adapter, identity_reader=state.identity,
        now_provider=lambda: state.now,
    )
    service = workflow.decline_service
    assert workflow.session is state.adapter
    assert service.session is state.adapter
    assert service.repository.session is state.adapter
    assert service.audit_logger.event_repository.session is state.adapter
    assert service.identity_reader is state.identity


def test_workflow_rejects_audit_repository_from_another_session(pipeline):
    state = pipeline.state
    state.service.audit_logger.event_repository.session = object()
    with pytest.raises(ValueError, match="one shared session"):
        ConstructionInvitationDeclineWorkflow(
            session=state.adapter, decline_service=state.service,
        )
