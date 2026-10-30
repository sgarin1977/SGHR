from dataclasses import replace
from datetime import timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from database.repositories.construction import (
    ConstructionInvitationRepository,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_invitations import (
    ConstructionInvitationOperationError,
    ConstructionInvitationService,
    ConstructionInvitationTransitionError,
)
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS,
    ConstructionAccessContext,
    ConstructionGrantContext,
    ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError,
)
from test_construction_1a_invitation_repository_accept import (
    Adapter, invitation_store, stored_row,
)


def context_for(actor, role="OWNER", scope_type="tenant", scope_id=None):
    return ConstructionAccessContext(
        user_id=actor.user_id,
        tenant_id=actor.tenant_id,
        roles=(role,),
        permissions=tuple(CONSTRUCTION_ROLE_PERMISSIONS[role]),
        grants=(ConstructionGrantContext(
            role, scope_type,
            actor.tenant_id if scope_id is None else scope_id,
        ),),
    )


@pytest.fixture
def revoke_case(invitation_store):
    engine, table, invitation_id, now = invitation_store
    session = Session(engine)
    baseline = dict(stored_row(session, table, invitation_id))
    actor = SimpleNamespace(
        user_id=uuid4(), tenant_id=baseline["tenant_id"], status="active",
    )
    calls = []

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
            calls.append("lock")
            invitation = await super().get_invitation_for_update(**fields)
            if invitation is not None and invitation.expires_at.tzinfo is None:
                # SQLite fixture не зберігає tzinfo.
                invitation.expires_at = invitation.expires_at.replace(
                    tzinfo=timezone.utc
                )
            return invitation

        async def list_invitation_scopes(self, **fields):
            calls.append("scopes")
            return self.scopes

        async def revoke_invitation(self, **fields):
            calls.append("revoke")
            return await super().revoke_invitation(**fields)

    adapter = SQLAdapter(session)
    repository = Repository(adapter)
    repository.scopes = [SimpleNamespace(
        tenant_id=actor.tenant_id, invitation_id=invitation_id,
        scope_type="tenant", scope_id=actor.tenant_id,
    )]

    async def record_event(**fields):
        calls.append("audit")
        return SimpleNamespace(**fields)

    events = SimpleNamespace(
        session=adapter,
        create_event=AsyncMock(side_effect=record_event),
    )
    token_provider = Mock(side_effect=AssertionError(
        "Revoke must not issue a new token."
    ))
    service = ConstructionInvitationService(
        session=adapter,
        repository=repository,
        event_repository=events,
        now_provider=lambda: now,
        token_provider=token_provider,
    )
    case = SimpleNamespace(
        session=session, table=table, invitation_id=invitation_id,
        now=now, actor=actor, context=context_for(actor),
        adapter=adapter, repository=repository, events=events,
        service=service, token_provider=token_provider, calls=calls,
    )
    try:
        yield case
    finally:
        session.close()


async def revoke(case, version=4, invitation_id=None):
    return await case.service.revoke_invitation(
        actor=case.actor,
        access_context=case.context,
        invitation_id=(
            case.invitation_id if invitation_id is None else invitation_id
        ),
        expected_row_version=version,
        trace_id="revoke-service",
    )


def current_row(case):
    return dict(stored_row(
        case.session, case.table, case.invitation_id,
    ))


def assert_denied_without_write(case, before):
    assert "revoke" not in case.calls
    case.events.create_event.assert_not_awaited()
    case.token_provider.assert_not_called()
    assert case.adapter.rollbacks == 1
    assert current_row(case) == before


@pytest.mark.asyncio
async def test_revoke_records_one_audit_without_commit(revoke_case):
    case = revoke_case
    before = current_row(case)
    result = await revoke(case)
    after = current_row(case)

    assert case.calls == ["lock", "scopes", "revoke", "audit"]
    assert result.id == case.invitation_id
    assert after["status"] == "revoked"
    assert after["row_version"] == before["row_version"] + 1
    for field in set(before) - {"status", "row_version", "updated_at"}:
        assert after[field] == before[field]
    assert case.adapter.rollbacks == 0
    case.token_provider.assert_not_called()
    case.events.create_event.assert_awaited_once()

    event = case.events.create_event.call_args.kwargs
    assert event["event_type"] == "construction_invitation_revoked"
    assert event["tenant_id"] == case.actor.tenant_id
    assert event["user_id"] == case.actor.user_id
    assert event["entity_id"] == case.invitation_id
    assert event["platform"] == "api"
    assert event["trace_id"] == "revoke-service"
    assert event["payload"] == {
        "operation": "revoke",
        "changes": [{
            "field": "status",
            "old_value": "pending",
            "new_value": "revoked",
        }],
        "reason": None,
    }
    assert "token_hash" not in repr(event)
    assert "delivery_metadata" not in repr(event)
    assert before["token_hash"] not in repr(event)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,days,used", [
    ("accepted", 1, False),
    ("declined", 1, False),
    ("expired", 1, False),
    ("revoked", 1, False),
    ("pending", 0, False),
    ("pending", -1, False),
    ("pending", 1, True),
])
async def test_revoke_rejects_terminal_expired_or_used(
    revoke_case, status, days, used,
):
    case = revoke_case
    case.session.execute(
        case.table.update().where(
            case.table.c.id == case.invitation_id
        ).values(
            status=status,
            expires_at=case.now + timedelta(days=days),
            token_used_at=case.now if used else None,
        )
    )
    case.session.commit()
    before = current_row(case)

    with pytest.raises(ConstructionInvitationTransitionError):
        await revoke(case)
    assert_denied_without_write(case, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [3, 0, True, None, "4"])
async def test_revoke_rejects_wrong_or_invalid_version(revoke_case, version):
    case = revoke_case
    before = current_row(case)

    with pytest.raises(ConstructionRowVersionConflictError) as caught:
        await revoke(case, version=version)
    assert caught.value.status_code == 409
    assert caught.value.code == "ROW_VERSION_CONFLICT"
    assert_denied_without_write(case, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [
    "observer", "admin_owner", "foreign_context", "inactive_actor",
    "foreign_scope", "empty_scopes", "document",
    "wrong_project", "missing_invitation",
])
async def test_revoke_rejects_permission_and_scope_violations(
    revoke_case, scenario,
):
    case = revoke_case
    invitation_id = case.invitation_id

    if scenario == "observer":
        case.context = context_for(case.actor, "OBSERVER")
    elif scenario == "admin_owner":
        case.context = context_for(case.actor, "ADMIN")
        case.session.execute(
            case.table.update().where(
                case.table.c.id == case.invitation_id
            ).values(role="OWNER")
        )
        case.session.commit()
    elif scenario == "foreign_context":
        case.context = replace(case.context, user_id=uuid4())
    elif scenario == "inactive_actor":
        case.actor.status = "suspended"
    elif scenario == "foreign_scope":
        case.repository.scopes[0].tenant_id = uuid4()
    elif scenario == "empty_scopes":
        case.repository.scopes = []
    elif scenario == "document":
        case.repository.scopes[0].scope_type = "document"
    elif scenario == "wrong_project":
        case.repository.scopes[0].scope_type = "project"
        case.repository.scopes[0].scope_id = uuid4()
        case.context = context_for(
            case.actor, "OWNER", "project", uuid4(),
        )
    elif scenario == "missing_invitation":
        invitation_id = uuid4()

    before = current_row(case)
    with pytest.raises((
        ConstructionPermissionDeniedError,
        ConstructionScopeDeniedError,
    )):
        await revoke(case, invitation_id=invitation_id)
    assert_denied_without_write(case, before)


@pytest.mark.asyncio
async def test_revoke_audit_failure_rolls_back_database_change(revoke_case):
    case = revoke_case
    before = current_row(case)
    failure = RuntimeError("Audit unavailable")
    case.events.create_event.side_effect = failure

    with pytest.raises(ConstructionInvitationOperationError) as caught:
        await revoke(case)

    assert caught.value.__cause__ is failure
    assert "revoke" in case.calls
    case.events.create_event.assert_awaited_once()
    assert case.adapter.rollbacks == 1
    assert current_row(case) == before


@pytest.mark.asyncio
async def test_revoke_maps_orm_conflict_to_409(revoke_case):
    case = revoke_case
    before = current_row(case)
    case.repository.revoke_invitation = AsyncMock(
        side_effect=StaleDataError("Stale version")
    )

    with pytest.raises(ConstructionRowVersionConflictError) as caught:
        await revoke(case)

    assert caught.value.status_code == 409
    assert caught.value.code == "ROW_VERSION_CONFLICT"
    case.events.create_event.assert_not_awaited()
    assert case.adapter.rollbacks == 1
    assert current_row(case) == before
