"""Factory integration and local SQL rollback; no native parallel acceptance."""
import asyncio
import importlib
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from database.repositories.construction import ConstructionAccessRepository
from database.repositories.platform_membership_serialization import PlatformMembershipSerializationReceipt as Receipt
from services.construction import construction_membership_writers as writers
from services.construction.construction_access_errors import ConstructionAccessGrantConflictError
from services.construction.construction_grants import ConstructionGrantOperationError
from services.construction.construction_invitations import ConstructionInvitationOperationError, ConstructionInvitationValidationError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from test_construction_1a_access_mutation_workflow import pipeline as grant_pipeline, snapshot as grant_snapshot
from test_construction_1a_invitation_acceptance_service import acceptance_case, stored
from test_construction_1a_invitation_acceptance_workflow import pipeline as acceptance_pipeline, invoke, snapshot as acceptance_snapshot
from test_construction_1a_team_block_service import block_case, snapshot as block_snapshot


def serialization(c, monkeypatch):
    c.barriers, c.transaction = [], SimpleNamespace(is_active=True)
    c.session.get_transaction = lambda: c.transaction if c.db.in_transaction else None
    c.session.get_nested_transaction = lambda: None
    async def lock(*, tenant_id, user_id):
        c.barriers.append((tenant_id, user_id))
        if hasattr(c, 'calls'): c.calls.append('member:'+str(user_id))
        return Receipt(tenant_id, user_id, c.transaction)
    c.serializer = SimpleNamespace(session=c.session, lock_member=AsyncMock(side_effect=lock))
    monkeypatch.setattr(writers, 'PlatformMembershipSerializationRepository', lambda session: c.serializer)
    return c


@pytest.fixture
def writer_case(grant_pipeline, monkeypatch):
    c = serialization(grant_pipeline, monkeypatch)
    c.member_active = True
    c.repo.has_active_platform_membership.side_effect = lambda **kw: c.member_active
    async def locate(**values):
        c.calls.append('locate')
        row = next((g for g in c.rows('grant') if g.id == values['grant_id'] and g.tenant_id == values['tenant_id']), None)
        return row.user_id if row is not None else None
    c.repo.get_grant_member_id = AsyncMock(side_effect=locate)
    c.mutation = writers.SerializedConstructionAccessMutationService(session=c.session, repository=c.repo,
        project_repository=c.service.project_repository, event_repository=c.service.audit_logger.event_repository,
        now_provider=lambda: c.now)
    c.issuance = writers.SerializedConstructionAccessIssuanceService(session=c.session, repository=c.repo,
        project_repository=c.service.project_repository, event_repository=c.service.audit_logger.event_repository,
        now_provider=lambda: c.now)
    c.issue_flow = writers.SerializedConstructionAccessIssuanceWorkflow(session=c.session,
        issuance_service=c.issuance, idempotency_service=c.idem)
    c.mutation_flow = writers.SerializedConstructionAccessMutationWorkflow(session=c.session,
        mutation_service=c.mutation, access_resolver=c.workflow.resolver, idempotency_service=c.idem)
    c.legacy = writers.SerializedConstructionAccessGrantService(session=c.session, repository=c.repo,
        event_repository=c.service.audit_logger.event_repository, project_repository=c.service.project_repository,
        now_provider=lambda: c.now)
    return c


async def issue(c, *, workflow=True, key=None):
    if not c.db.in_transaction: c.db.execute('BEGIN')
    values = dict(actor=c.actor, target_user_id=c.original.user_id, role='SURVEYOR',
        scope_type='project', scope_id=c.project_id, expires_at=None, trace_id='serialized-issue')
    if workflow: values['idempotency_key'] = key
    return await (c.issue_flow if workflow else c.issuance).grant_access(**values)


async def mutate(c, *, workflow=True, revoke=False, key=None):
    if not c.db.in_transaction: c.db.execute('BEGIN')
    values = dict(actor=c.actor, grant_id=c.original.id, trace_id='serialized-mutation')
    if workflow: values['idempotency_key'] = key
    else: values['access_context'] = await c.workflow.resolver.resolve(actor=c.actor)
    if not revoke: values.update(role='SURVEYOR', scope_type='project', scope_id=c.project_id)
    obj = c.mutation_flow if workflow else c.mutation
    return await (obj.revoke_access if revoke else obj.replace_access)(**values)


def expected_pair(c):
    return [(c.actor.tenant_id, user) for user in sorted({c.actor.user_id,c.original.user_id},key=lambda value:value.int)]


@pytest.mark.asyncio
@pytest.mark.parametrize('key', [None, 'issue-key'])
async def test_issuance_barriers_precede_authorization_reservation_and_insert(writer_case, key):
    c = writer_case
    result = await issue(c, key=key)
    assert c.barriers[:2] == expected_pair(c)
    assert c.calls[:2] == ['member:'+str(user) for _,user in expected_pair(c)]
    assert len(c.rows('grant')) == 2 and len(c.rows('audit')) == 1
    assert result['user_id'] == str(c.original.user_id) and c.session.commit.await_count == 1
    if key:
        before = grant_snapshot(c)
        assert await issue(c, key=key) == result
        assert grant_snapshot(c) == before and c.repo.create_grant.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('workflow', [False, True])
@pytest.mark.parametrize('kind', ['issue', 'replace'])
async def test_membership_lost_while_waiting_is_rechecked_before_grant_write(writer_case, workflow, kind):
    c = writer_case
    before = grant_snapshot(c)
    real = c.serializer.lock_member.side_effect
    async def waited(**values):
        receipt = await real(**values)
        if values['user_id'] == c.original.user_id: c.member_active = False
        return receipt
    c.serializer.lock_member.side_effect = waited
    with pytest.raises(ConstructionPermissionDeniedError):
        if kind == 'issue': await issue(c, workflow=workflow, key='wait-key')
        else: await mutate(c, workflow=workflow, key='wait-key')
    assert grant_snapshot(c) == before and not c.rows('ledger')
    c.repo.create_grant.assert_not_called(); c.repo.revoke_if_effective.assert_not_called()
    c.session.commit.assert_not_called(); c.session.rollback.assert_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('key', [None, 'replace-key'])
async def test_replace_locator_does_not_lock_and_barrier_precedes_workflow_row_locks(writer_case, key):
    c = writer_case
    result = await mutate(c, key=key)
    assert c.calls[0] == 'locate' and c.barriers[:2] == expected_pair(c)
    first_row_lock = c.calls.index('lock')
    assert all(c.calls.index('member:'+str(user)) < first_row_lock for _,user in expected_pair(c))
    if key: assert c.calls.index('member:'+str(c.original.user_id)) < c.calls.index('reserve')
    assert result['id'] != str(c.original.id) and len(c.rows('audit')) == 2
    assert next(g for g in c.rows('grant') if g.id==c.original.id).status == 'revoked'
    if key:
        before = grant_snapshot(c)
        assert await mutate(c, key=key) == result and grant_snapshot(c) == before


@pytest.mark.asyncio
async def test_revoke_repeat_keeps_existing_noop_semantics_with_same_lock_order(writer_case):
    c = writer_case
    result = await mutate(c, revoke=True)
    before = grant_snapshot(c)
    assert await mutate(c, revoke=True) == result and grant_snapshot(c)==before
    assert len(c.rows('audit'))==1 and c.repo.revoke_if_effective.await_count==1


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['issue','replace','revoke'])
async def test_authority_lost_during_barrier_wait_denies_without_new_audit(writer_case, kind):
    c = writer_case
    before = grant_snapshot(c)
    real = c.serializer.lock_member.side_effect
    async def waited(**values):
        receipt=await real(**values); c.authorized=False; return receipt
    c.serializer.lock_member.side_effect=waited
    with pytest.raises((ConstructionPermissionDeniedError,ConstructionScopeDeniedError)):
        if kind=='issue': await issue(c)
        else: await mutate(c,revoke=kind=='revoke')
    assert grant_snapshot(c)==before
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['driver','wrong_scope','wrong_transaction','missing_receipt','nested','cancel'])
async def test_serialization_failure_or_cancel_cannot_reach_business_writes(writer_case, failure):
    c = writer_case
    before=grant_snapshot(c)
    if failure=='driver': c.serializer.lock_member.side_effect=RuntimeError('private DB diagnostic')
    elif failure=='cancel': c.serializer.lock_member.side_effect=asyncio.CancelledError()
    elif failure=='nested': c.session.get_nested_transaction=lambda:object()
    else:
        def receipt(**values):
            if failure=='missing_receipt': return True
            return Receipt(uuid4() if failure=='wrong_scope' else values['tenant_id'],values['user_id'],
                object() if failure=='wrong_transaction' else c.transaction)
        c.serializer.lock_member.side_effect=receipt
    with pytest.raises(asyncio.CancelledError if failure=='cancel' else ConstructionGrantOperationError) as caught:
        await issue(c,key='failed-key')
    assert 'private' not in str(caught.value) and grant_snapshot(c)==before
    c.repo.create_grant.assert_not_called(); c.session.commit.assert_not_called()
    c.session.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_changed_grant_locator_conflicts_before_any_row_lock_or_audit(writer_case):
    c=writer_case
    before=grant_snapshot(c)
    c.repo.get_grant_member_id.side_effect=[c.original.user_id,uuid4()]
    with pytest.raises(ConstructionAccessGrantConflictError): await mutate(c,key='locator-key')
    assert grant_snapshot(c)==before
    c.repo.get_grant_for_update.assert_not_called(); c.repo.create_grant.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['issue','replace'])
async def test_cancel_after_real_audit_insert_rolls_back_grants_audit_and_key_reservation(writer_case, kind):
    c=writer_case
    before=grant_snapshot(c)
    event=c.service.audit_logger.event_repository.create_event
    real=event.side_effect
    async def cancelled(**values):
        await real(**values)
        assert c.rows('audit') and c.rows('ledger')
        raise asyncio.CancelledError()
    event.side_effect=cancelled
    with pytest.raises(asyncio.CancelledError):
        if kind=='issue': await issue(c,key='cancel-key')
        else: await mutate(c,key='cancel-key')
    assert grant_snapshot(c)==before
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_issuance_path_uses_barrier_and_fresh_authority(writer_case):
    c=writer_case
    c.db.execute('BEGIN')
    # Tenant scope exercises the legacy path without inventing a new project API.
    result=await c.legacy.grant_access(actor=c.actor,access_context=None,target_user_id=c.original.user_id,
        role='ADMIN',scope_type='tenant',scope_id=c.actor.tenant_id)
    assert c.barriers[:2]==expected_pair(c)
    assert result.user_id==c.original.user_id and len(c.rows('audit'))==1
    assert c.repo.list_active_grants.await_count>=1
    c.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_serializer_runtime_contract_fails_closed_without_sqlite_fallback(writer_case, monkeypatch):
    c=writer_case
    monkeypatch.undo()
    before=grant_snapshot(c)
    with pytest.raises(ConstructionGrantOperationError): await issue(c)
    assert grant_snapshot(c)==before and not c.rows('audit')
    c.repo.create_grant.assert_not_called()


@pytest.mark.asyncio
async def test_nonlocking_grant_locator_executes_exact_tenant_filtered_uuid_projection():
    engine=create_engine('sqlite+pysqlite:///:memory:')
    tenant,foreign,grant,member=(uuid4() for _ in range(4))
    with Session(engine) as session:
        session.execute(text('CREATE TABLE construction_access_grants(id TEXT PRIMARY KEY,tenant_id TEXT,user_id TEXT)'))
        session.execute(text('INSERT INTO construction_access_grants VALUES(:id,:tenant,:member)'),
            {'id':grant.hex,'tenant':tenant.hex,'member':member.hex})
        session.commit();session.begin()
        statements=[]
        async def execute(statement):
            statements.append(statement); return session.execute(statement)
        class Bridge:
            @property
            def no_autoflush(self): return session.no_autoflush
        bridge=Bridge()
        bridge.execute=execute;bridge.in_transaction=session.in_transaction
        repository=ConstructionAccessRepository(bridge)
        assert await repository.get_grant_member_id(tenant_id=tenant,grant_id=grant)==member
        assert await repository.get_grant_member_id(tenant_id=foreign,grant_id=grant) is None
        assert await repository.get_grant_member_id(tenant_id=tenant,grant_id=uuid4()) is None
        sql=str(statements[0].compile(dialect=postgresql.dialect()))
        assert sql.startswith('SELECT construction_access_grants.user_id \nFROM construction_access_grants')
        assert 'tenant_id =' in sql and 'id =' in sql and 'FOR UPDATE' not in sql
        assert len(list(statements[0].selected_columns))==1
        with pytest.raises(ValueError): await repository.get_grant_member_id(tenant_id='untrusted',grant_id=grant)
        session.rollback()
    engine.dispose()


@pytest.fixture
def serialized_acceptance(acceptance_pipeline,monkeypatch):
    p=acceptance_pipeline;c=serialization(p.case,monkeypatch)
    c.service=writers.SerializedConstructionInvitationAcceptanceService(session=c.session,context=c.context,
        membership_repository=c.memberships,grant_repository=c.grants,project_repository=c.projects,event_repository=c.events)
    p.workflow=writers.SerializedConstructionInvitationAcceptanceWorkflow(session=c.session,
        acceptance_service=c.service,idempotency_service=p.idempotency)
    return p


@pytest.mark.asyncio
async def test_acceptance_locks_saved_tenant_recipient_before_invitation_and_preserves_replay(serialized_acceptance):
    p=serialized_acceptance;c=p.case
    original=c.context.prepare_pending_acceptance
    async def prepare(**values):
        assert c.barriers and all(pair==(c.invitation.tenant_id,c.actor.user_id) for pair in c.barriers)
        assert c.invitation.tenant_id != c.actor.tenant_id
        return await original(**values)
    c.context.prepare_pending_acceptance=prepare
    first=await invoke(p)
    assert first['status']=='accepted' and len(stored(c,'memberships'))==1 and len(stored(c,'grants'))==2
    before=acceptance_snapshot(p)
    assert await invoke(p)==first and acceptance_snapshot(p)==before
    assert len(c.barriers)==3 and c.session.commit.await_count==2


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['lock','expiry_after_wait','cancel_after_membership','commit'])
async def test_acceptance_failure_restores_membership_grants_audit_and_idempotency(serialized_acceptance,phase):
    p=serialized_acceptance;c=p.case
    before=acceptance_snapshot(p)
    expected=ConstructionInvitationOperationError
    if phase=='lock': c.serializer.lock_member.side_effect=RuntimeError('private lock diagnostic')
    elif phase=='expiry_after_wait':
        async def waited(**values):
            c.now=c.invitation.expires_at
            return Receipt(values['tenant_id'],values['user_id'],c.transaction)
        c.serializer.lock_member.side_effect=waited
        expected=Exception
    elif phase=='commit': p.flags.fail_commit=True
    else:
        real=c.memberships.ensure_invitation_tenant_membership
        async def cancel(**values):
            await real(**values); assert stored(c,'memberships'); raise asyncio.CancelledError()
        c.memberships.ensure_invitation_tenant_membership=cancel
        expected=asyncio.CancelledError
    with pytest.raises(expected): await invoke(p)
    assert acceptance_snapshot(p)==before
    c.session.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_acceptance_missing_trusted_tenant_never_uses_actor_tenant(serialized_acceptance):
    p=serialized_acceptance;c=p.case
    before=acceptance_snapshot(p)
    with pytest.raises(ConstructionInvitationValidationError): await invoke(p,tenant_id=None)
    c.serializer.lock_member.assert_not_called()
    assert acceptance_snapshot(p)==before


@pytest.mark.asyncio
async def test_team_block_uses_same_actor_target_order_before_authority_grant_lock(block_case,monkeypatch):
    c=serialization(block_case,monkeypatch)
    old=c.repo.get_grant_for_update.side_effect
    async def grant_locked(**values):
        expected=[(c.actor.tenant_id,u) for u in sorted({c.actor.user_id,c.target},key=lambda v:v.int)]
        assert c.barriers[:2]==expected
        return await old(**values)
    c.repo.get_grant_for_update.side_effect=grant_locked
    service=writers.SerializedConstructionTeamBlockService(session=c.session,repository=c.repo,
        event_repository=c.event_repo,now_provider=lambda:c.now)
    flow=writers.SerializedConstructionTeamBlockWorkflow(session=c.session,block_service=service)
    c.session.commit.side_effect=c.db.commit
    before=block_snapshot(c)
    result=await flow.block_access(actor=c.actor,user_id=c.target)
    assert len(result['revoked_grant_ids'])==3
    after=block_snapshot(c)
    assert after['users']==before['users'] and after['memberships']==before['memberships']
    c.session.commit.assert_awaited_once()


@pytest.mark.parametrize('module,factory,cls', [
    ('construction_access_issuance','build_construction_access_issuance_service','SerializedConstructionAccessIssuanceService'),
    ('construction_access_mutations','build_construction_access_mutation_service','SerializedConstructionAccessMutationService'),
    ('construction_grants','build_construction_access_grant_service','SerializedConstructionAccessGrantService'),
    ('construction_access_issuance_workflow','build_construction_access_issuance_workflow','SerializedConstructionAccessIssuanceWorkflow'),
    ('construction_access_workflows','build_construction_access_mutation_workflow','SerializedConstructionAccessMutationWorkflow'),
    ('construction_invitation_acceptance','build_construction_invitation_acceptance_service','SerializedConstructionInvitationAcceptanceService'),
    ('construction_invitation_acceptance_workflow','build_construction_invitation_acceptance_workflow','SerializedConstructionInvitationAcceptanceWorkflow'),
    ('construction_team_block','build_construction_team_block_service','SerializedConstructionTeamBlockService'),
    ('construction_team_block_workflow','build_construction_team_block_workflow','SerializedConstructionTeamBlockWorkflow'),
])
def test_all_runtime_factories_select_serialized_adapters_without_connecting(writer_case,module,factory,cls):
    c=writer_case
    values={'session':c.session}
    if 'invitation' in module:
        c.session.execute=AsyncMock(side_effect=AssertionError('Factory must not execute SQL.'))
        values['identity_reader']=SimpleNamespace(session=object(),has_verified_email=AsyncMock(return_value=True))
        if 'workflow' in module: values['idempotency_encryption_key']=Fernet.generate_key().decode()
    result=getattr(importlib.import_module('services.construction.'+module),factory)(**values)
    assert isinstance(result,getattr(writers,cls)) and result.session is c.session
    c.serializer.lock_member.assert_not_called()
    c.session.commit.assert_not_called()
