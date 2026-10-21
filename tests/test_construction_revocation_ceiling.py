"""Existing delegation policy on original grants and atomic bulk-block boundaries."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from services.construction_grants import ConstructionAccessGrantService, ConstructionGrantOperationError
from services.construction_permissions import (
    ConstructionPermissionDeniedError as Denied, ConstructionGrantContext,
)
from test_construction_1a_access_mutation_service import case, context, replace, revoke
from test_construction_1a_access_mutation_workflow import pipeline, call, snapshot as grant_snapshot
from test_construction_1a_team_block_service import block_case, block, snapshot as block_snapshot
from test_construction_1a_team_block_workflow import block_pipeline
from test_construction_1a_team_block_http import block_http, post


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['revoke','replace'])
@pytest.mark.parametrize('scope',['tenant','project','document'])
async def test_admin_cannot_revoke_or_downgrade_original_owner_even_to_a_delegable_new_role(case,operation,scope):
    c=case
    c.access=context(c.actor,ConstructionGrantContext('ADMIN','tenant',c.actor.tenant_id))
    c.grant.role='OWNER';c.grant.scope_type=scope
    c.grant.scope_id=c.actor.tenant_id if scope=='tenant' else c.project_id
    c.original=deepcopy(vars(c.grant))
    with pytest.raises(Denied): await (revoke(c) if operation=='revoke' else replace(c))
    assert vars(c.grant)==c.original and not c.events and not c.created
    c.repo.revoke_if_effective.assert_not_called();c.repo.create_grant.assert_not_called()
    c.event_repo.create_event.assert_not_called();c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['revoke','replace'])
async def test_covering_owner_still_mutates_original_owner_with_existing_audit_and_history(case,operation):
    c=case;c.grant.role='OWNER';c.original=deepcopy(vars(c.grant))
    result=await (revoke(c) if operation=='revoke' else replace(c))
    assert c.grant.status=='revoked' and c.grant.role=='OWNER'
    assert len(c.events)==(1 if operation=='revoke' else 2)
    assert result.status==('revoked' if operation=='revoke' else 'active')
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_revoked_higher_role_is_not_an_authorization_bypass_for_a_noop(case):
    c=case;c.grant.role='OWNER';c.grant.status='revoked';c.original=deepcopy(vars(c.grant))
    c.access=context(c.actor,ConstructionGrantContext('ADMIN','tenant',c.actor.tenant_id))
    with pytest.raises(Denied): await revoke(c)
    c.repo.revoke_if_effective.assert_not_called();c.event_repo.create_event.assert_not_called()


@pytest.mark.asyncio
async def test_project_owner_authority_does_not_cover_original_owner_on_another_project(case):
    c=case;c.grant.role='OWNER';c.original=deepcopy(vars(c.grant))
    c.access=context(c.actor,ConstructionGrantContext('ADMIN','project',c.project_id),
        ConstructionGrantContext('OWNER','project',uuid4()))
    with pytest.raises(Denied): await replace(c)
    c.repo.revoke_if_effective.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('replacing',[False,True])
@pytest.mark.parametrize('key',[None,'revocation-ceiling'])
async def test_real_local_workflow_denies_initial_owner_before_write_and_rolls_back_key_reservation(pipeline,replacing,key):
    c=pipeline;c.original.role='OWNER';c.save('grant',c.original.id,c.original);c.db.commit()
    c.repo.list_active_grants.side_effect=lambda **kw:[NS(role='ADMIN',scope_type='tenant',scope_id=c.actor.tenant_id)]
    before=grant_snapshot(c)
    with pytest.raises(Denied): await call(c,replacing=replacing,idempotency_key=key)
    assert grant_snapshot(c)==before and not c.rows('audit') and not c.rows('ledger')
    c.session.commit.assert_not_called();c.repo.revoke_if_effective.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('replacing',[False,True])
async def test_keyed_replay_rechecks_original_role_after_owner_authority_is_lost(pipeline,replacing):
    c=pipeline;c.original.role='OWNER';c.save('grant',c.original.id,c.original);c.db.commit()
    await call(c,replacing=replacing,idempotency_key='original-owner')
    before=grant_snapshot(c)
    c.repo.list_active_grants.side_effect=lambda **kw:[NS(role='ADMIN',scope_type='tenant',scope_id=c.actor.tenant_id)]
    with pytest.raises(Denied): await call(c,replacing=replacing,idempotency_key='original-owner')
    assert grant_snapshot(c)==before and c.session.commit.await_count==1


@pytest.mark.asyncio
@pytest.mark.parametrize('revoked',[False,True])
async def test_legacy_revoke_uses_the_same_original_role_guard(case,revoked):
    c=case;c.grant.role='OWNER';c.grant.status='revoked' if revoked else 'active'
    c.original=deepcopy(vars(c.grant));c.repo.revoke_grant=AsyncMock()
    legacy=ConstructionAccessGrantService(session=c.session,repository=c.repo,event_repository=c.event_repo,
        now_provider=lambda:c.now)
    authority=context(c.actor,ConstructionGrantContext('ADMIN','tenant',c.actor.tenant_id))
    with pytest.raises(Denied):
        await legacy.revoke_access(actor=c.actor,access_context=authority,grant_id=c.grant.id)
    c.repo.revoke_grant.assert_not_called();c.session.commit.assert_not_called()
    c.event_repo.create_event.assert_not_called()


def change_authority_and_target(c, *, actor='ADMIN', target='OWNER', scope='tenant'):
    c.db.execute('UPDATE grants SET role=? WHERE id=?',(actor,str(c.authority_id)))
    target_id=next(iter(c.expected))
    row=c.db.execute('SELECT * FROM grants WHERE id=?',(str(target_id),)).fetchone()
    c.db.execute('UPDATE grants SET role=?,scope_type=?,scope_id=? WHERE id=?',
        (target,scope,str(c.actor.tenant_id if scope=='tenant' else uuid4()),str(target_id)))
    c.db.commit();c.db.execute('BEGIN')
    return target_id


@pytest.mark.asyncio
@pytest.mark.parametrize('scope',['tenant','project','document'])
async def test_bulk_block_admin_denies_owner_before_first_update_without_partial_lower_revocations(block_case,scope):
    c=block_case;change_authority_and_target(c,scope=scope);before=block_snapshot(c)
    with pytest.raises(Denied): await block(c)
    assert block_snapshot(c)==before
    c.repo.revoke_effective_user_grants.assert_not_called();c.event_repo.create_event.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_http_block_enforces_fresh_original_role_even_if_http_context_claims_owner(block_http):
    c=block_http;change_authority_and_target(c);before=block_snapshot(c)
    response=await post(c)
    assert response.status_code==403,response.text
    assert block_snapshot(c)==before
    c.repo.revoke_effective_user_grants.assert_not_called();c.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_covering_owner_can_block_target_owner_without_changing_platform_membership(block_case):
    c=block_case;change_authority_and_target(c,actor='OWNER');before=block_snapshot(c)
    result=await block(c)
    assert set(result.revoked_grant_ids)==c.expected
    assert block_snapshot(c)['users']==before['users'] and block_snapshot(c)['memberships']==before['memberships']


@pytest.mark.asyncio
async def test_additional_admin_grant_with_lower_uuid_does_not_mask_owner_authority(block_case):
    c=block_case;change_authority_and_target(c,actor='OWNER')
    duplicate=UUID(int=1)
    c.db.execute('INSERT INTO grants SELECT ?,tenant_id,user_id,?,scope_type,scope_id,status,expires_at,'
        'granted_by_user_id,granted_at,revoked_by_user_id,revoked_at FROM grants WHERE id=?',
        (str(duplicate),'ADMIN',str(c.authority_id)))
    c.db.commit();c.db.execute('BEGIN')
    assert set((await block(c)).revoked_grant_ids)==c.expected
    calls=c.repo.get_grant_for_update.await_args_list
    assert [call.kwargs['grant_id'] for call in calls[:2]]==[duplicate,c.authority_id]


@pytest.mark.asyncio
async def test_owner_role_discovered_after_target_lock_is_checked_before_any_mutation(block_case):
    c=block_case
    c.db.execute("UPDATE grants SET role='ADMIN' WHERE id=?",(str(c.authority_id),))
    c.db.commit();c.db.execute('BEGIN');before=block_snapshot(c)
    original=c.repo.get_grant_for_update.side_effect
    changed=False
    async def locked(**values):
        nonlocal changed
        row=await original(**values)
        if row.user_id==c.target and not changed:
            row.role='OWNER';changed=True
        return row
    c.repo.get_grant_for_update.side_effect=locked
    with pytest.raises(Denied): await block(c)
    assert block_snapshot(c)==before
    c.repo.revoke_effective_user_grants.assert_not_called();c.event_repo.create_event.assert_not_called()


@pytest.mark.asyncio
async def test_unreviewed_bulk_target_causes_rollback_before_any_audit(block_case):
    c=block_case;before=block_snapshot(c)
    original=c.repo.revoke_effective_user_grants.side_effect
    async def injected(**values):
        c.db.execute('INSERT INTO grants SELECT ?,tenant_id,user_id,?,scope_type,scope_id,status,expires_at,'
            'granted_by_user_id,granted_at,revoked_by_user_id,revoked_at FROM grants WHERE id=?',
            (str(uuid4()),'OWNER',str(next(iter(c.expected)))))
        return await original(**values)
    c.repo.revoke_effective_user_grants.side_effect=injected
    with pytest.raises(ConstructionGrantOperationError): await block(c)
    assert block_snapshot(c)==before
    c.event_repo.create_event.assert_not_called();c.session.commit.assert_not_called()
