"""Actual local ORM updates/versioned links and audit rollback; no native DB acceptance."""
import asyncio
import inspect
import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from database.models import ConstructionClient as Client, ConstructionProject as Project
from database.repositories.construction import ConstructionClientRepository
from database.repositories.construction.construction_client_user_links import MAX_CLIENT_ROW_VERSION as MAX_VERSION
from services.construction.construction_clients import (
    ConstructionClientService, ConstructionClientNotFoundError, ConstructionClientValidationError,
    ConstructionClientOperationError,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError as Conflict
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext, CONSTRUCTION_ROLE_PERMISSIONS,
    ConstructionPermissionDeniedError,
)
from test_construction_1a_client_link_model import client_store


@pytest.fixture
def version_case(client_store):
    c=client_store
    Project.__table__.create(c.engine)
    with c.engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE client_version_audit(id TEXT PRIMARY KEY,event_type TEXT,payload TEXT)')
    c.now=datetime(2026,10,21,16,tzinfo=timezone.utc)
    c.actor=NS(user_id=c.target,tenant_id=c.tenant,status='active')
    c.context=ConstructionAccessContext(c.target,c.tenant,('OWNER',),
        tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS['OWNER'])),
        (ConstructionGrantContext('OWNER','tenant',c.tenant),))
    c.sync=Session(c.engine,expire_on_commit=False)
    c.project_id=uuid4()
    c.sync.add(Project(id=c.project_id,tenant_id=c.tenant,client_id=c.client_id,
        name='Keep project',address_raw='Keep address',created_by=c.target))
    c.sync.commit()
    c.statements=[]
    def capture(connection,cursor,statement,parameters,context,executemany):c.statements.append(statement)
    event.listen(c.engine,'before_cursor_execute',capture)
    c.session=NS(add=c.sync.add,no_autoflush=c.sync.no_autoflush,in_transaction=c.sync.in_transaction,
        execute=AsyncMock(side_effect=c.sync.execute),flush=AsyncMock(side_effect=c.sync.flush),
        commit=AsyncMock(side_effect=c.sync.commit),rollback=AsyncMock(side_effect=c.sync.rollback))
    c.repo=ConstructionClientRepository(c.session)
    c.repo.update_client=AsyncMock(wraps=c.repo.update_client)
    c.repo.soft_delete_client=AsyncMock(wraps=c.repo.soft_delete_client)
    async def audit(**values):
        c.sync.execute(text('INSERT INTO client_version_audit VALUES(:id,:event,:payload)'),
            dict(id=str(uuid4()),event=values['event_type'],payload=json.dumps(values['payload'],sort_keys=True)))
        return NS(**values)
    c.events=NS(session=c.session,create_event=AsyncMock(side_effect=audit))
    c.service=ConstructionClientService(session=c.session,repository=c.repo,event_repository=c.events,
        now_provider=lambda:c.now)
    try:
        yield c
    finally:
        c.sync.close();event.remove(c.engine,'before_cursor_execute',capture)


async def change(c, *, expected=1, **fields):
    values=dict(actor=c.actor,access_context=c.context,client_id=c.client_id,expected_row_version=expected,
        display_name='Changed client',client_type='company',phone='+12025550101',email='changed@example.com',notes='Changed note')
    values.update(fields)
    return await c.service.update_client(**values)


async def archive(c, *, expected=1, **fields):
    values=dict(actor=c.actor,access_context=c.context,client_id=c.client_id,expected_row_version=expected)
    values.update(fields)
    return await c.service.archive_client(**values)


def snapshot(c):
    return dict(client=c.sync.execute(select(Client.__table__).where(Client.id==c.client_id)).one(),
        project=c.sync.execute(select(Project.__table__).where(Project.id==c.project_id)).one(),
        audit=c.sync.execute(text('SELECT * FROM client_version_audit ORDER BY id')).all())


def no_sql_update(c):return not any(statement.lstrip().upper().startswith(('UPDATE','INSERT','DELETE')) for statement in c.statements)


@pytest.mark.asyncio
async def test_actual_update_changes_version_once_and_commits_correlated_audit(version_case):
    c=version_case;before=snapshot(c)
    row=await change(c)
    after=snapshot(c)
    assert row.row_version==2 and row.display_name=='Changed client' and row.client_type=='company'
    assert after['project']==before['project']
    assert len(after['audit'])==1
    payload=json.loads(after['audit'][0].payload)
    assert payload['related_client_id']==str(c.client_id) and payload['operation']=='update'
    assert {item['field'] for item in payload['changes']}=={'display_name','client_type','phone','email','notes'}
    c.session.commit.assert_awaited_once();c.session.rollback.assert_not_called()
    with c.engine.connect() as connection:
        assert connection.execute(select(Client.row_version).where(Client.id==c.client_id)).scalar_one()==2


@pytest.mark.asyncio
async def test_normalized_noop_has_no_repository_write_version_timestamp_change_or_audit(version_case):
    c=version_case;before=snapshot(c);c.statements.clear()
    row=await change(c,display_name='  Original client  ',client_type=' PERSON ',phone=None,email=None,notes=None)
    assert row.row_version==1 and snapshot(c)==before
    assert no_sql_update(c)
    c.repo.update_client.assert_not_called();c.events.create_event.assert_not_called()
    c.session.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['update','archive','noop'])
@pytest.mark.parametrize('bad_version',[None,True,False,0,-1,'1',1.0,2,MAX_VERSION+1])
async def test_invalid_or_stale_expected_version_never_writes_or_audits(version_case,operation,bad_version):
    c=version_case;before=snapshot(c);c.statements.clear()
    with pytest.raises(Conflict) as caught:
        if operation=='archive':await archive(c,expected=bad_version)
        else:await change(c,expected=bad_version,**(dict(display_name='Original client',client_type='person',
            phone=None,email=None,notes=None) if operation=='noop' else {}))
    assert caught.value.status_code==409 and caught.value.code=='ROW_VERSION_CONFLICT'
    assert snapshot(c)==before and no_sql_update(c)
    c.repo.update_client.assert_not_called();c.repo.soft_delete_client.assert_not_called()
    c.events.create_event.assert_not_called();c.session.commit.assert_not_called()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['update','archive'])
async def test_expected_version_is_required_in_the_internal_service_contract(version_case,operation):
    c=version_case
    method=c.service.update_client if operation=='update' else c.service.archive_client
    assert inspect.signature(method).parameters['expected_row_version'].default is inspect.Parameter.empty
    with pytest.raises(TypeError):await method(actor=c.actor,access_context=c.context,client_id=c.client_id)
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
async def test_stale_profile_edit_after_identity_link_change_is_rejected_and_link_preserved(version_case):
    c=version_case
    cached=c.sync.get(Client,c.client_id)
    c.sync.execute(update(Client.__table__).where(Client.id==c.client_id).values(linked_user_id=c.target,row_version=2))
    c.sync.commit()
    assert cached.row_version==1
    before=snapshot(c)
    with pytest.raises(Conflict):await change(c,expected=1)
    assert snapshot(c)==before
    result=await change(c,expected=2)
    assert result.row_version==3 and result.linked_user_id==c.target


@pytest.mark.asyncio
async def test_second_editor_with_cached_old_orm_version_cannot_overwrite_first_editor(version_case):
    c=version_case
    with Session(c.engine,expire_on_commit=False) as second:
        cached=second.get(Client,c.client_id)
        await change(c)
        assert cached.row_version==1
        bridge=NS(execute=AsyncMock(side_effect=second.execute),flush=AsyncMock(side_effect=second.flush),
            commit=AsyncMock(side_effect=second.commit),rollback=AsyncMock(side_effect=second.rollback))
        service=ConstructionClientService(session=bridge,repository=ConstructionClientRepository(bridge),event_repository=c.events)
        with pytest.raises(Conflict):
            await service.update_client(actor=c.actor,access_context=c.context,client_id=c.client_id,expected_row_version=1,
                display_name='Lost update',client_type='person',phone=None,email=None,notes=None)
        assert c.sync.get(Client,c.client_id).display_name=='Changed client'
        bridge.flush.assert_not_called();bridge.commit.assert_not_called()


@pytest.mark.asyncio
async def test_archive_increments_version_once_and_preserves_project_reference_and_identity_link(version_case):
    c=version_case
    c.sync.execute(update(Client.__table__).where(Client.id==c.client_id).values(linked_user_id=c.target));c.sync.commit()
    before=snapshot(c)
    row=await archive(c)
    assert row.row_version==2 and row.deleted_at==c.now and row.linked_user_id==c.target
    assert snapshot(c)['project']==before['project']
    payload=json.loads(snapshot(c)['audit'][0].payload)
    assert payload['related_client_id']==str(c.client_id) and payload['changes']==[
        dict(field='deleted_at',old_value=None,new_value=c.now.isoformat())]
    with pytest.raises(ConstructionClientNotFoundError):await archive(c,expected=2)
    assert len(snapshot(c)['audit'])==1


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['update','archive'])
@pytest.mark.parametrize('failure',['audit','commit','stale_flush','cancel'])
async def test_real_flushed_client_version_and_audit_roll_back_together(version_case,operation,failure):
    c=version_case;before=snapshot(c)
    if failure=='audit':
        original=c.events.create_event.side_effect
        async def failed(**values):
            await original(**values)
            raise RuntimeError('private audit failure after real SQL')
        c.events.create_event.side_effect=failed
    elif failure=='commit':c.session.commit.side_effect=RuntimeError('private commit failure')
    elif failure=='stale_flush':
        async def stale():
            c.sync.flush();raise StaleDataError('private stale write')
        c.session.flush.side_effect=stale
    else:
        original=c.events.create_event.side_effect
        async def cancelled(**values):
            await original(**values);raise asyncio.CancelledError()
        c.events.create_event.side_effect=cancelled
    expected=Conflict if failure=='stale_flush' else asyncio.CancelledError if failure=='cancel' else ConstructionClientOperationError
    with pytest.raises(expected):await (archive(c) if operation=='archive' else change(c))
    assert snapshot(c)==before
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['update','archive'])
@pytest.mark.parametrize('identity',['missing','foreign_tenant'])
async def test_unavailable_client_remains_safe_notfound_before_version_or_mutation(version_case,operation,identity):
    c=version_case
    if identity=='missing':c.client_id=uuid4()
    else:c.actor.tenant_id=c.other_tenant;c.context=ConstructionAccessContext(c.target,c.other_tenant,('OWNER',),
        tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS['OWNER'])),(ConstructionGrantContext('OWNER','tenant',c.other_tenant),))
    with pytest.raises(ConstructionClientNotFoundError):await (archive(c) if operation=='archive' else change(c))
    c.session.flush.assert_not_called();c.events.create_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['update','archive'])
async def test_current_client_edit_permission_is_still_required(version_case,operation):
    c=version_case;c.context=ConstructionAccessContext(c.target,c.tenant,(),(),())
    with pytest.raises(ConstructionPermissionDeniedError):await (archive(c) if operation=='archive' else change(c))
    c.session.execute.assert_not_called();c.events.create_event.assert_not_called()


@pytest.mark.asyncio
async def test_maximum_bigint_version_allows_noop_but_not_overflowing_mutation(version_case):
    c=version_case
    c.sync.execute(update(Client.__table__).where(Client.id==c.client_id).values(row_version=MAX_VERSION));c.sync.commit()
    row=await change(c,expected=MAX_VERSION,display_name='Original client',client_type='person',phone=None,email=None,notes=None)
    assert row.row_version==MAX_VERSION
    with pytest.raises(ConstructionClientValidationError):await change(c,expected=MAX_VERSION)
    with pytest.raises(ConstructionClientValidationError):await archive(c,expected=MAX_VERSION)
    c.session.flush.assert_not_called();c.events.create_event.assert_not_called()
