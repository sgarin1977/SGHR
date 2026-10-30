from datetime import timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from database.repositories.construction import ConstructionInvitationRepository
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_invitation_decline import (
    ConstructionInvitationDeclineService,
    build_construction_invitation_decline_service,
)
from services.construction.construction_invitations import (
    ConstructionInvitationOperationError,
    ConstructionInvitationRecipientDeniedError,
    ConstructionInvitationTokenDeniedError,
    hash_construction_invitation_token,
    require_construction_invitation_token,
)
from test_construction_1a_invitation_repository_accept import (
    Adapter, invitation_store, stored_row,
)


@pytest.fixture
def decline_case(invitation_store):
    engine, table, invitation_id, now = invitation_store
    session = Session(engine)
    token = "private-decline-service-token"
    session.execute(
        table.update().where(table.c.id == invitation_id).values(
            token_hash=hash_construction_invitation_token(token),
        )
    )
    session.commit()
    initial = dict(stored_row(session, table, invitation_id))
    actor = SimpleNamespace(
        user_id=uuid4(), status="active", tenant_id=uuid4(),
    )
    state = SimpleNamespace(
        session=session, table=table, invitation_id=invitation_id,
        tenant_id=initial["tenant_id"], actor=actor,
        token=token, now=now, calls=[],
    )
    proof_user = actor.user_id

    class SQLAdapter(Adapter):
        def __init__(self, session):
            super().__init__(session)
            self.rollbacks = 0

        async def execute(self, statement):
            return self.session.execute(statement)

        async def rollback(self):
            self.rollbacks += 1
            self.session.rollback()

    class Repository(ConstructionInvitationRepository):
        async def get_invitation_for_update(self, **fields):
            state.calls.append("lock")
            invitation = await super().get_invitation_for_update(**fields)
            if invitation is not None and invitation.expires_at.tzinfo is None:
                invitation.expires_at = invitation.expires_at.replace(
                    tzinfo=timezone.utc,
                )
            return invitation

        async def decline_invitation(self, **fields):
            state.calls.append("decline")
            return await super().decline_invitation(**fields)

    async def email_proof(*, user_id, email):
        state.calls.append("identity")
        return user_id == proof_user and email == "member@example.com"

    async def record_event(**fields):
        state.calls.append("audit")
        return SimpleNamespace(**fields)

    state.adapter = SQLAdapter(session)
    state.repository = Repository(state.adapter)
    state.identity = SimpleNamespace(
        has_verified_email=AsyncMock(side_effect=email_proof),
    )
    state.events = SimpleNamespace(
        session=state.adapter,
        create_event=AsyncMock(side_effect=record_event),
    )
    state.service = ConstructionInvitationDeclineService(
        session=state.adapter, repository=state.repository,
        event_repository=state.events,
        identity_reader=state.identity, now_provider=lambda: state.now,
    )
    try:
        yield state
    finally:
        session.close()


def row(state):
    return dict(stored_row(
        state.session, state.table, state.invitation_id,
    ))


async def decline(state, **overrides):
    arguments = {
        "actor": state.actor, "tenant_id": state.tenant_id,
        "invitation_id": state.invitation_id,
        "expected_row_version": 4, "token": state.token,
        "trace_id": "decline-service",
    }
    arguments.update(overrides)
    return await state.service.decline_invitation(**arguments)


def unchanged(state, before):
    assert row(state) == before
    assert "decline" not in state.calls
    state.events.create_event.assert_not_awaited()
    assert state.adapter.rollbacks == 1


@pytest.mark.asyncio
async def test_verified_recipient_declines_without_existing_tenant_access(decline_case):
    state = decline_case
    before = row(state)
    assert state.actor.tenant_id != state.tenant_id
    result = await decline(state)
    after = row(state)
    assert result.id == state.invitation_id
    assert state.calls == ["lock", "identity", "decline", "audit"]
    assert after["status"] == "declined"
    assert after["row_version"] == before["row_version"] + 1
    for name in set(before) - {"status", "row_version", "updated_at"}:
        assert after[name] == before[name]
    assert after["user_id"] is None
    assert state.adapter.rollbacks == 0
    state.identity.has_verified_email.assert_awaited_once_with(
        user_id=state.actor.user_id, email="member@example.com",
    )
    event = state.events.create_event.await_args.kwargs
    assert event["event_type"] == "construction_invitation_declined"
    assert event["tenant_id"] == state.tenant_id
    assert event["user_id"] == state.actor.user_id
    assert event["entity_id"] == state.invitation_id
    assert event["payload"] == {
        "operation": "decline",
        "changes": [{
            "field": "status", "old_value": "pending",
            "new_value": "declined",
        }],
        "reason": None,
    }
    for secret in (state.token, before["token_hash"], "delivery_metadata"):
        assert secret not in repr(event)
    with pytest.raises(ConstructionInvitationTokenDeniedError):
        require_construction_invitation_token(
            invitation=result, token=state.token, as_of=state.now,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [
    "anonymous", "inactive", "foreign_user", "unverified", "truthy_proof",
])
async def test_decline_requires_authenticated_verified_target_email(
    decline_case, scenario,
):
    state = decline_case
    before = row(state)
    actor = state.actor
    if scenario == "anonymous":
        actor = None
    elif scenario == "inactive":
        actor.status = "suspended"
    elif scenario == "foreign_user":
        actor.user_id = uuid4()
    else:
        state.identity.has_verified_email.side_effect = None
        state.identity.has_verified_email.return_value = (
            1 if scenario == "truthy_proof" else False
        )
    with pytest.raises(ConstructionInvitationRecipientDeniedError):
        await decline(state, actor=actor)
    unchanged(state, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "", "wrong-private-token"])
async def test_invalid_token_does_not_query_identity(decline_case, token):
    state = decline_case
    before = row(state)
    with pytest.raises(ConstructionInvitationTokenDeniedError):
        await decline(state, token=token)
    state.identity.has_verified_email.assert_not_awaited()
    unchanged(state, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["accepted", "declined", "expired", "revoked"])
async def test_terminal_invitation_is_unchanged(decline_case, status):
    state = decline_case
    state.session.execute(
        state.table.update().where(
            state.table.c.id == state.invitation_id,
        ).values(status=status)
    )
    state.session.commit()
    before = row(state)
    with pytest.raises(ConstructionInvitationTokenDeniedError):
        await decline(state)
    unchanged(state, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [3, 0, True, None, "4"])
async def test_decline_version_conflict_is_409(decline_case, version):
    state = decline_case
    before = row(state)
    with pytest.raises(ConstructionRowVersionConflictError) as caught:
        await decline(state, expected_row_version=version)
    assert caught.value.status_code == 409
    assert caught.value.code == "ROW_VERSION_CONFLICT"
    unchanged(state, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["tenant_id", "invitation_id"])
async def test_decline_does_not_cross_tenant_or_invitation(decline_case, field):
    state = decline_case
    before = row(state)
    with pytest.raises(ConstructionInvitationTokenDeniedError):
        await decline(state, **{field: uuid4()})
    state.identity.has_verified_email.assert_not_awaited()
    unchanged(state, before)


@pytest.mark.asyncio
async def test_invitation_expiring_during_identity_check_is_not_declined(decline_case):
    state = decline_case
    before = row(state)

    async def slow_proof(**fields):
        state.now += timedelta(days=8)
        return True

    state.identity.has_verified_email.side_effect = slow_proof
    with pytest.raises(ConstructionInvitationTokenDeniedError):
        await decline(state)
    unchanged(state, before)


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_decline_without_exposing_secret(decline_case):
    state = decline_case
    before = row(state)
    state.events.create_event.side_effect = RuntimeError(state.token)
    with pytest.raises(ConstructionInvitationOperationError) as caught:
        await decline(state)
    assert "decline" in state.calls
    assert row(state) == before
    assert state.adapter.rollbacks == 1
    assert state.token not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.asyncio
async def test_orm_conflict_is_mapped_to_409(decline_case):
    state = decline_case
    before = row(state)
    state.repository.decline_invitation = AsyncMock(
        side_effect=StaleDataError("Stale invitation"),
    )
    with pytest.raises(ConstructionRowVersionConflictError) as caught:
        await decline(state)
    assert caught.value.code == "ROW_VERSION_CONFLICT"
    assert row(state) == before
    assert state.adapter.rollbacks == 1
    state.events.create_event.assert_not_awaited()


def test_decline_factory_keeps_business_writes_in_one_session(decline_case):
    state = decline_case
    service = build_construction_invitation_decline_service(
        session=state.adapter, identity_reader=state.identity,
        now_provider=lambda: state.now,
    )
    assert service.repository.session is state.adapter
    assert service.audit_logger.event_repository.session is state.adapter
    assert service.identity_reader is state.identity
