import asyncio
from contextlib import nullcontext
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from uuid import UUID,uuid4

import pytest
from sqlalchemy import insert,select,event
from sqlalchemy.orm.attributes import set_committed_value

from database.models import ConstructionLead
from database.repositories.construction.construction_leads import ConstructionLeadRepository,ConstructionLeadCursor
from services.construction.construction_client_leads_read import (
    ConstructionClientLeadsReadService,ConstructionClientLeadsReadCursor,ConstructionClientLeadsReadPage,
    ConstructionClientLeadsReadOperationError,PUBLIC_CLIENT_LEAD_FIELDS,build_construction_client_leads_read_service,
)
from services.construction.construction_clients import ConstructionClientNotFoundError,ConstructionClientValidationError
from services.construction.construction_permissions import (
    ConstructionAccessContext,ConstructionGrantContext,ConstructionPermissionDeniedError,ConstructionScopeDeniedError,
)
from test_construction_1a_lead_model import lead_db,values,TABLE
from test_construction_1a_lead_repository import AsyncSessionAdapter


def service(c):
    return ConstructionClientLeadsReadService(session=c.session,repository=c.repo,client_repository=c.clients,
        access_repository=c.access,now_provider=lambda:c.now)


@pytest.fixture
def read_case():
    c=SimpleNamespace(tenant=uuid4(),user=uuid4(),client=uuid4(),project=uuid4(),now=datetime(2026,10,13,12,tzinfo=timezone.utc))
    c.actor=SimpleNamespace(tenant_id=c.tenant,user_id=c.user,status='active')
    c.context=ConstructionAccessContext(c.user,c.tenant,('OWNER',),('construction.projects.read',),
        (ConstructionGrantContext('OWNER','tenant',c.tenant),))
    c.session=SimpleNamespace(no_autoflush=nullcontext(),in_transaction=lambda:True,execute=AsyncMock(),add=Mock(),
        flush=AsyncMock(),commit=AsyncMock(),rollback=AsyncMock())
    c.grant=SimpleNamespace(role='OWNER',scope_type='tenant',scope_id=c.tenant,status='active',expires_at=None)
    c.access=SimpleNamespace(session=c.session,has_active_platform_membership=AsyncMock(return_value=True),
        list_active_grants=AsyncMock(return_value=[c.grant]))
    c.client_row=SimpleNamespace(id=c.client,tenant_id=c.tenant,deleted_at=None)
    c.clients=SimpleNamespace(session=c.session,get_active_client=AsyncMock(return_value=c.client_row))
    c.row=SimpleNamespace(id=UUID(int=20),tenant_id=c.tenant,source='customer_portal_v2',name='Existing inquiry',
        work_type_description='Kitchen refit',phone='123',email=None,address_raw='Address',comment='Original comment',
        responsible_user_id=c.user,status='won',converted_client_id=c.client,converted_project_id=c.project,
        created_at=c.now,updated_at=c.now,row_version=4,deleted_at=None,private_internal='Never public')
    c.repo=SimpleNamespace(session=c.session,list_active_leads=AsyncMock(return_value=[c.row]))
    c.service=service(c)
    return c


async def read(c,**changes):
    options=dict(actor=c.actor,access_context=c.context,client_id=c.client)
    options.update(changes)
    return await c.service.list_leads_for_client(**options)


def no_writes(c):
    c.session.add.assert_not_called();c.session.flush.assert_not_called()
    c.session.commit.assert_not_called();c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_canonical_client_link_public_projection_and_current_access(read_case):
    c=read_case;page=await read(c)
    assert isinstance(page,ConstructionClientLeadsReadPage) and page.next_cursor is None
    assert set(page.items[0])==PUBLIC_CLIENT_LEAD_FIELDS and len(page.items[0])==16
    assert page.items[0]['id']==str(c.row.id) and page.items[0]['converted_client_id']==str(c.client)
    assert page.items[0]['converted_project_id']==str(c.project) and page.items[0]['status']=='won'
    assert page.items[0]['source']=='customer_portal_v2' and page.items[0]['work_type_description']=='Kitchen refit'
    assert 'deleted_at' not in page.items[0] and 'private_internal' not in page.items[0] and 'next_action' not in page.items[0]
    assert 'work_type_id' not in page.items[0] and 'master_work_type_id' not in page.items[0]
    c.row.comment='Changed later';assert page.items[0]['comment']=='Original comment'
    c.repo.list_active_leads.assert_awaited_once_with(tenant_id=c.tenant,converted_client_id=c.client,limit=20,after=None)
    assert c.clients.get_active_client.await_count==2 and c.access.has_active_platform_membership.await_count==3
    no_writes(c)


@pytest.mark.asyncio
async def test_no_related_leads_is_an_empty_page_for_existing_client(read_case):
    c=read_case;c.repo.list_active_leads.return_value=[]
    page=await read(c);assert page.items==() and page.next_cursor is None
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['inactive','wrong_tenant','wrong_user','permission','project','document','no_grants'])
async def test_initial_denial_prevents_client_and_lead_queries(read_case,failure):
    c=read_case
    if failure=='inactive':c.actor.status='blocked'
    else:
        scope='project' if failure=='project' else 'document' if failure=='document' else 'tenant'
        role='PROJECT_MANAGER' if scope=='project' else 'OBSERVER' if scope=='document' else 'OWNER'
        c.context=ConstructionAccessContext(uuid4() if failure=='wrong_user' else c.user,
            uuid4() if failure=='wrong_tenant' else c.tenant,(role,),() if failure=='permission' else ('construction.projects.read',),
            () if failure=='no_grants' else (ConstructionGrantContext(role,scope,c.project if scope!='tenant' else c.tenant),))
    with pytest.raises((ConstructionPermissionDeniedError,ConstructionScopeDeniedError)):await read(c)
    c.clients.get_active_client.assert_not_called();c.repo.list_active_leads.assert_not_called();no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['membership','revoked','expired','different_tenant_grant'])
async def test_current_membership_and_grants_are_required(read_case,failure):
    c=read_case
    if failure=='membership':c.access.has_active_platform_membership.return_value=False
    elif failure=='revoked':c.grant.status='revoked'
    elif failure=='expired':c.grant.expires_at=c.now
    else:c.grant.scope_id=uuid4()
    with pytest.raises((ConstructionPermissionDeniedError,ConstructionScopeDeniedError)):await read(c)
    c.clients.get_active_client.assert_not_called();c.repo.list_active_leads.assert_not_called();no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['missing','foreign','deleted','wrong_id'])
async def test_unavailable_client_precedes_lead_query(read_case,failure):
    c=read_case
    if failure=='missing':c.clients.get_active_client.return_value=None
    elif failure=='foreign':c.client_row.tenant_id=uuid4()
    elif failure=='deleted':c.client_row.deleted_at=c.now
    else:c.client_row.id=uuid4()
    with pytest.raises(ConstructionClientNotFoundError):await read(c)
    c.repo.list_active_leads.assert_not_called();no_writes(c)


@pytest.mark.asyncio
async def test_grant_revoked_during_client_lookup_cannot_query_leads(read_case):
    c=read_case
    async def revoke(**kw):c.grant.status='revoked';return c.client_row
    c.clients.get_active_client.side_effect=revoke
    with pytest.raises(ConstructionPermissionDeniedError):await read(c)
    c.repo.list_active_leads.assert_not_called();no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['grant','membership','archived_client','final_client_lookup'])
async def test_access_or_client_change_during_read_prevents_response(read_case,failure):
    c=read_case
    async def query(**kw):
        if failure=='grant':c.grant.status='revoked'
        elif failure=='membership':c.access.has_active_platform_membership.return_value=False
        elif failure=='archived_client':c.client_row.deleted_at=c.now
        return [c.row]
    c.repo.list_active_leads.side_effect=query
    if failure=='final_client_lookup':
        async def client(**kw):
            if c.clients.get_active_client.await_count==2:c.grant.status='revoked'
            return c.client_row
        c.clients.get_active_client.side_effect=client
    with pytest.raises(ConstructionClientNotFoundError if failure=='archived_client' else ConstructionPermissionDeniedError):await read(c)
    no_writes(c)


@pytest.mark.asyncio
async def test_loss_of_tenant_scope_prevents_empty_page_and_cursor_publication(read_case):
    c=read_case
    async def query(**kw):
        c.grant.role,c.grant.scope_type,c.grant.scope_id='PROJECT_MANAGER','project',c.project
        return []
    c.repo.list_active_leads.side_effect=query
    with pytest.raises(ConstructionScopeDeniedError):await read(c)
    no_writes(c)


@pytest.mark.asyncio
async def test_descending_cursor_binds_actor_client_tenant_and_last_visible_lead(read_case):
    c=read_case;second=SimpleNamespace(**{**vars(c.row),'id':UUID(int=10)})
    c.repo.list_active_leads.return_value=[c.row,second]
    page=await read(c,limit=1);expected=ConstructionClientLeadsReadCursor(c.tenant,c.user,c.client,c.now,c.row.id)
    assert page.next_cursor==expected and len(page.items)==1
    c.repo.list_active_leads.return_value=[second]
    last=await read(c,limit=1,after=expected);assert last.next_cursor is None and last.items[0]['id']==str(second.id)
    assert c.repo.list_active_leads.await_args.kwargs['after']==ConstructionLeadCursor(c.tenant,c.now,c.row.id)
    for changed in [dict(user_id=uuid4()),dict(client_id=uuid4()),dict(tenant_id=uuid4())]:
        with pytest.raises(ConstructionClientValidationError):await read(c,after=ConstructionClientLeadsReadCursor(**{**vars(expected),**changed}))
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['duplicate','wrong_order','oversize','before_cursor','wrong_result_type'])
async def test_invalid_page_or_lookahead_is_rejected(read_case,failure):
    c=read_case;options={};second=SimpleNamespace(**{**vars(c.row),'id':UUID(int=10)})
    if failure=='duplicate':rows=[c.row,c.row]
    elif failure=='wrong_order':rows=[second,c.row]
    elif failure=='oversize':rows=[c.row,second,second];options['limit']=1
    elif failure=='wrong_result_type':rows=()
    else:rows=[c.row];options['after']=ConstructionClientLeadsReadCursor(c.tenant,c.user,c.client,c.now,c.row.id)
    c.repo.list_active_leads.return_value=rows
    with pytest.raises(ConstructionClientLeadsReadOperationError):await read(c,**options)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value',[('id','uuid'),('tenant_id',uuid4()),('converted_client_id',uuid4()),('converted_client_id',None),
    ('deleted_at',True),('updated_at',datetime(2026,10,13)),('created_at',datetime(2026,10,13)),('row_version',True),
    ('row_version',2**63),('status','qualified'),('status','proposal'),('source','  '),('name',''),('work_type_description',' '),
    ('converted_project_id','uuid')])
async def test_wrong_client_foreign_deleted_or_malformed_lead_is_not_published(read_case,field,value):
    c=read_case;setattr(c.row,field,value)
    with pytest.raises(ConstructionClientLeadsReadOperationError):await read(c)
    no_writes(c)


@pytest.mark.asyncio
async def test_malformed_stored_lead_without_contact_is_not_published(read_case):
    c=read_case;c.row.phone=None;c.row.email=None
    with pytest.raises(ConstructionClientLeadsReadOperationError):await read(c)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('changes',[dict(client_id='uuid'),dict(limit=True),dict(limit=0),dict(limit=101),dict(after={})])
async def test_invalid_query_precedes_reads(read_case,changes):
    c=read_case
    with pytest.raises(ConstructionClientValidationError):await read(c,**changes)
    c.clients.get_active_client.assert_not_called();c.repo.list_active_leads.assert_not_called();no_writes(c)


@pytest.mark.asyncio
async def test_frozen_cursor_values_are_revalidated(read_case):
    c=read_case;cursor=ConstructionClientLeadsReadCursor(c.tenant,c.user,c.client,c.now,c.row.id)
    object.__setattr__(cursor,'updated_at',c.now.replace(tzinfo=None))
    with pytest.raises(ConstructionClientValidationError):await read(c,after=cursor)
    c.repo.list_active_leads.assert_not_called();no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['leads','client','clock','transaction','cancelled'])
async def test_failures_are_sanitized_and_transaction_remains_with_caller(read_case,failure):
    c=read_case
    if failure=='leads':c.repo.list_active_leads.side_effect=RuntimeError('private-database-detail')
    elif failure=='client':c.clients.get_active_client.side_effect=RuntimeError('private-client-detail')
    elif failure=='clock':c.service.now_provider=lambda:c.now.replace(tzinfo=None)
    elif failure=='transaction':c.session.in_transaction=lambda:False
    else:c.repo.list_active_leads.side_effect=asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError if failure=='cancelled' else ConstructionClientLeadsReadOperationError) as caught:await read(c)
    assert 'private-' not in str(caught.value);no_writes(c)


def test_factory_preserves_shared_wiring_without_new_permission_or_idempotency(read_case):
    c=read_case;built=build_construction_client_leads_read_service(session=c.session,now_provider=lambda:c.now)
    assert all(repo.session is c.session for repo in (built.repository,built.client_repository,built.access_repository))
    c.clients.session=object()
    with pytest.raises(ValueError):service(c)


@pytest.fixture
def sql_read(lead_db,read_case):
    engine,ids=lead_db;c=read_case
    c.tenant,c.user,c.client,c.project=ids['a'],ids['user'],ids['client_a'],ids['project_a']
    c.actor.tenant_id,c.actor.user_id=c.tenant,c.user;c.client_row.id,c.client_row.tenant_id=c.client,c.tenant
    c.grant.scope_id=c.tenant
    c.context=ConstructionAccessContext(c.user,c.tenant,('OWNER',),('construction.projects.read',),
        (ConstructionGrantContext('OWNER','tenant',c.tenant),))
    class Adapter(AsyncSessionAdapter):
        @property
        def no_autoflush(self):return self.sync.no_autoflush
    c.session=Adapter(engine);c.engine=engine;c.ids=ids
    for collaborator in (c.clients,c.access):collaborator.session=c.session
    class SQLiteRepository(ConstructionLeadRepository):
        async def list_active_leads(self,**kw):
            rows=await super().list_active_leads(**kw)
            for row in rows:
                for field in ('created_at','updated_at'):
                    value=getattr(row,field)
                    if value.tzinfo is None:set_committed_value(row,field,value.replace(tzinfo=timezone.utc))
            return rows
    c.repo=SQLiteRepository(c.session);c.service=service(c);c.flushes=[]
    event.listen(c.session.sync,'before_flush',lambda *args:c.flushes.append(True))
    c.session.sync.begin()
    try:yield c
    finally:c.session.sync.close()


@pytest.mark.asyncio
async def test_real_client_link_pages_exclude_other_tenants_deleted_and_matching_contact_unconverted_leads(sql_read):
    c=sql_read;ids=[UUID(int=value) for value in range(1,6)]
    c.client_row.phone='123';c.client_row.email='same@example.com';c.client_row.display_name='Existing inquiry'
    with c.engine.begin() as connection:
        for id in ids:connection.execute(insert(TABLE).values(**values(c.ids,id=id,status='won',source='new-channel-v3',
            name='Existing inquiry',phone='123',email='same@example.com',updated_at=c.now,created_at=c.now,
            converted_client_id=c.client,converted_project_id=c.project)))
        for data in [values(c.ids,id=uuid4(),phone='123',name='Existing inquiry',email='same@example.com',converted_client_id=None),
            values(c.ids,id=uuid4(),tenant_id=c.ids['b'],converted_client_id=c.ids['client_b'],converted_project_id=c.ids['project_b'],status='won'),
            values(c.ids,id=uuid4(),converted_client_id=c.client,converted_project_id=c.project,status='won',deleted_at=c.now)]:
            connection.execute(insert(TABLE).values(**data))
    first=await read(c,limit=2);second=await read(c,limit=2,after=first.next_cursor);third=await read(c,limit=2,after=second.next_cursor)
    assert [item['id'] for item in first.items+second.items+third.items]==[str(id) for id in reversed(ids)]
    assert third.next_cursor is None and all(item['source']=='new-channel-v3' for item in first.items+second.items+third.items)
    assert c.session.commit.await_count==c.session.rollback.await_count==0 and c.flushes==[]
    with c.engine.connect() as connection:assert len(connection.execute(select(TABLE.c.id)).all())==8


@pytest.mark.asyncio
async def test_real_read_refreshes_without_flushing_pending_lead_mutation(sql_read):
    c=sql_read;id=uuid4()
    with c.engine.begin() as connection:connection.execute(insert(TABLE).values(**values(c.ids,id=id,status='won',
        converted_client_id=c.client,converted_project_id=c.project,updated_at=c.now,created_at=c.now)))
    initial=await read(c);cached=c.session.sync.get(ConstructionLead,id);cached.name='Unsaved mutation'
    again=await read(c)
    assert again.items[0]['name']==initial.items[0]['name'] and again.items[0]['name']!='Unsaved mutation'
    assert c.flushes==[] and c.session.commit.await_count==c.session.rollback.await_count==0
