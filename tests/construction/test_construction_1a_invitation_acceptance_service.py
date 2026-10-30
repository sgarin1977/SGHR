import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from database.repositories.user import PlatformInvitationMembershipConflictError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_invitation_acceptance import ConstructionInvitationAcceptanceService
from services.construction.construction_invitation_acceptance_context import ConstructionInvitationAcceptanceContext
from services.construction.construction_invitations import ConstructionInvitationOperationError, ConstructionInvitationRecipientDeniedError, ConstructionInvitationTokenDeniedError, ConstructionInvitationValidationError, hash_construction_invitation_token
from services.construction.construction_permissions import ConstructionScopeDeniedError

@pytest.fixture
def acceptance_case():
    db = sqlite3.connect(':memory:')
    for name in ('memberships', 'grants', 'audits', 'invitations'):
        db.execute(f'CREATE TABLE {name} (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
    now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
    tenant, user, invitation_id, project_id = (uuid4() for _ in range(4))
    invitation = SimpleNamespace(id=invitation_id, tenant_id=tenant, user_id=None, role='ADMIN', email='recipient@example.com', status='pending', row_version=4, expires_at=now + timedelta(days=7), grant_expires_at=now + timedelta(days=30), token_hash=hash_construction_invitation_token('one-time-secret'), token_used_at=None, created_by_user_id=uuid4())
    initial = {'status': 'pending', 'row_version': 4, 'user_id': None}
    db.execute('INSERT INTO invitations VALUES (?, ?)', (str(invitation_id), json.dumps(initial)))
    db.commit()
    db.execute('BEGIN')
    state = SimpleNamespace(db=db, now=now, invitation=invitation, initial=initial, actor=SimpleNamespace(user_id=user, tenant_id=uuid4(), status='active'), fail=None, conflict=False, existing=False, advance=False, grant_count=0, audit_count=0)

    class Session:
        commit = AsyncMock(side_effect=AssertionError('Service must not commit.'))

        def in_transaction(self):
            return db.in_transaction

        async def execute(self, statement, parameters):
            assert 'set_config' in str(statement)
            assert parameters == {'tenant_id': str(tenant), 'recipient_id': str(user), 'invitation_id': str(invitation_id)}

        async def rollback(self):
            db.rollback()
    session = Session()
    session.rollback = AsyncMock(side_effect=session.rollback)
    state.session = session

    def write(table, entity_id, payload):
        db.execute(f'INSERT INTO {table} VALUES (?, ?)', (str(entity_id), json.dumps(payload, default=str)))

    class Invitations:
        def __init__(self):
            self.session = session
            self.rows = [SimpleNamespace(tenant_id=tenant, invitation_id=invitation_id, scope_type='tenant', scope_id=tenant), SimpleNamespace(tenant_id=tenant, invitation_id=invitation_id, scope_type='project', scope_id=project_id)]

        async def get_invitation_for_update(self, **kwargs):
            assert kwargs == {'tenant_id': tenant, 'invitation_id': invitation_id}
            return invitation

        async def list_invitation_scopes(self, **kwargs):
            assert kwargs == {'tenant_id': tenant, 'invitation_id': invitation_id}
            return self.rows

        async def accept_invitation(self, *, invitation, user_id, accepted_at):
            if state.fail == 'accept':
                raise RuntimeError('simulated accept failure')
            invitation.user_id = user_id
            invitation.status = 'accepted'
            invitation.token_used_at = accepted_at
            invitation.row_version += 1
            db.execute('UPDATE invitations SET payload=? WHERE id=?', (json.dumps({'status': 'accepted', 'row_version': 5, 'user_id': str(user_id)}), str(invitation_id)))
            return invitation

    class Memberships:
        def __init__(self):
            self.session = session

        async def ensure_invitation_tenant_membership(self, **kwargs):
            assert kwargs == {'user_id': user, 'tenant_id': tenant}
            if state.conflict:
                raise PlatformInvitationMembershipConflictError()
            if state.fail == 'membership':
                raise RuntimeError('simulated membership failure')
            if not state.existing:
                write('memberships', uuid4(), {**kwargs, 'role': 'client'})
            if state.advance:
                state.now += timedelta(days=8)
            return not state.existing

    class Grants:
        def __init__(self):
            self.session = session

        async def create_grant(self, **kwargs):
            state.grant_count += 1
            if state.fail == 'grant:2' and state.grant_count == 2:
                raise RuntimeError('simulated grant failure')
            result = SimpleNamespace(id=uuid4(), **kwargs)
            write('grants', result.id, kwargs)
            return result

    class Projects:
        def __init__(self):
            self.session = session
            self.project = SimpleNamespace(id=project_id, tenant_id=tenant, deleted_at=None)

        async def get_active_project_for_update(self, **kwargs):
            assert kwargs == {'tenant_id': tenant, 'project_id': project_id}
            return self.project

    class Events:
        def __init__(self):
            self.session = session

        async def create_event(self, **kwargs):
            state.audit_count += 1
            if state.fail == 'audit:3' and state.audit_count == 3:
                raise RuntimeError('simulated audit failure')
            write('audits', uuid4(), kwargs)

    state.repository = Invitations()
    state.memberships = Memberships()
    state.grants = Grants()
    state.projects = Projects()
    state.events = Events()
    state.identity = SimpleNamespace(session=object(), has_verified_email=AsyncMock(return_value=True))
    context = ConstructionInvitationAcceptanceContext(session=session, repository=state.repository, identity_reader=state.identity, now_provider=lambda: state.now)
    state.context = context
    state.service = ConstructionInvitationAcceptanceService(session=session, context=context, membership_repository=state.memberships, grant_repository=state.grants, project_repository=state.projects, event_repository=state.events)
    try:
        yield state
    finally:
        db.close()

def stored(case, name):
    return [json.loads(row[0]) for row in case.db.execute(f'SELECT payload FROM {name} ORDER BY id')]

async def accept(case, **changes):
    arguments = dict(actor=case.actor, tenant_id=case.invitation.tenant_id, invitation_id=case.invitation.id, expected_row_version=4, token='one-time-secret', trace_id='acceptance-trace')
    arguments.update(changes)
    return await case.service.accept_invitation(**arguments)

def unchanged(case):
    assert stored(case, 'invitations') == [case.initial]
    for name in ('memberships', 'grants', 'audits'):
        assert stored(case, name) == []
    case.session.commit.assert_not_awaited()
    case.session.rollback.assert_awaited_once()

@pytest.mark.asyncio
@pytest.mark.parametrize('existing', [False, True])
async def test_accept_creates_scoped_access_and_safe_audit_without_commit(acceptance_case, existing):
    case = acceptance_case
    case.existing = existing
    result = await accept(case)
    assert result.status == 'accepted' and result.row_version == 5
    assert result.user_id == case.actor.user_id and result.token_used_at == case.now
    assert len(stored(case, 'memberships')) == (0 if existing else 1)
    grants = stored(case, 'grants')
    assert len(grants) == 2 and {x['scope_type'] for x in grants} == {'tenant', 'project'}
    for grant in grants:
        assert grant['user_id'] == str(case.actor.user_id)
        assert grant['tenant_id'] == str(case.invitation.tenant_id)
        assert grant['role'] == 'ADMIN'
        assert grant['expires_at'] == str(case.invitation.grant_expires_at)
        assert grant['granted_by_user_id'] == str(case.invitation.created_by_user_id)
    audits = stored(case, 'audits')
    assert len(audits) == 3
    accepted = next(x for x in audits if x['event_type'] == 'construction_invitation_accepted')
    assert accepted['payload']['changes'] == [{'field': 'status', 'old_value': 'pending', 'new_value': 'accepted'}]
    assert len(accepted['payload']['grant_ids']) == 2
    for audit in audits:
        assert audit['tenant_id'] == str(case.invitation.tenant_id)
        assert audit['user_id'] == str(case.actor.user_id)
        assert audit['trace_id'] == 'acceptance-trace'
    assert 'one-time-secret' not in json.dumps(audits)
    assert case.invitation.token_hash not in json.dumps(audits)
    assert case.session.in_transaction()
    case.session.commit.assert_not_awaited()
    case.session.rollback.assert_not_awaited()

@pytest.mark.asyncio
async def test_caller_rollback_removes_all_acceptance_writes(acceptance_case):
    case = acceptance_case
    await accept(case)
    await case.session.rollback()
    unchanged(case)

@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['membership', 'grant:2', 'accept', 'audit:3'])
async def test_failure_rolls_back_membership_grants_invitation_and_audit(acceptance_case, failure):
    case = acceptance_case
    case.fail = failure
    with pytest.raises(ConstructionInvitationOperationError):
        await accept(case)
    unchanged(case)

@pytest.mark.asyncio
async def test_blocked_membership_remains_conflict_without_access(acceptance_case):
    case = acceptance_case
    case.conflict = True
    with pytest.raises(PlatformInvitationMembershipConflictError):
        await accept(case)
    unchanged(case)

@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['token', 'email', 'version', 'bound_user', 'scope', 'project', 'expired_wait'])
async def test_rejected_acceptance_never_leaves_partial_access(acceptance_case, failure):
    case = acceptance_case
    arguments = {}
    error = ConstructionInvitationTokenDeniedError
    if failure == 'token':
        arguments['token'] = 'wrong'
    elif failure == 'email':
        case.identity.has_verified_email.return_value = False
        error = ConstructionInvitationRecipientDeniedError
    elif failure == 'version':
        arguments['expected_row_version'] = 3
        error = ConstructionRowVersionConflictError
    elif failure == 'bound_user':
        case.invitation.user_id = uuid4()
        error = ConstructionInvitationRecipientDeniedError
    elif failure == 'scope':
        case.repository.rows[0].scope_type = 'document'
        error = ConstructionScopeDeniedError
    elif failure == 'project':
        case.projects.project = None
        error = ConstructionScopeDeniedError
    elif failure == 'expired_wait':
        case.advance = True
    with pytest.raises(error):
        await accept(case, **arguments)
    unchanged(case)

def test_acceptance_rejects_split_write_sessions(acceptance_case):
    case = acceptance_case
    case.grants.session = object()
    with pytest.raises(ValueError, match='shared'):
        ConstructionInvitationAcceptanceService(session=case.session, context=case.context, membership_repository=case.memberships, grant_repository=case.grants, project_repository=case.projects, event_repository=case.events)

@pytest.mark.asyncio
async def test_expired_grant_deadline_rejects_before_membership(acceptance_case):
    case = acceptance_case
    case.invitation.grant_expires_at = case.now
    with pytest.raises(ConstructionInvitationValidationError):
        await accept(case)
    unchanged(case)

@pytest.mark.asyncio
async def test_foreign_scope_rejects_before_membership(acceptance_case):
    case = acceptance_case
    case.repository.rows[0].tenant_id = uuid4()
    with pytest.raises(ConstructionScopeDeniedError):
        await accept(case)
    unchanged(case)

def test_factory_keeps_writes_in_context_session(acceptance_case):
    from services.construction.construction_invitation_acceptance import build_construction_invitation_acceptance_service
    case = acceptance_case
    service = build_construction_invitation_acceptance_service(session=case.session, identity_reader=case.identity, now_provider=lambda: case.now)
    assert service.context.identity_reader is case.identity
    for item in (service.context, service.context.repository, service.membership_repository, service.grant_repository, service.project_repository, service.audit_logger.event_repository):
        assert item.session is case.session
