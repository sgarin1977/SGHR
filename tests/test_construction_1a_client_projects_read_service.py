import asyncio
from contextlib import nullcontext
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from uuid import UUID,uuid4

import pytest
from sqlalchemy import insert,select
from sqlalchemy.orm.attributes import set_committed_value

from database.models import ConstructionProject
from database.repositories.construction_client_projects import ConstructionClientProjectsRepository,ConstructionClientProjectsCursor
from services.construction_client_projects_read import (
    ConstructionClientProjectsReadService,ConstructionClientProjectsReadCursor,ConstructionClientProjectsReadPage,
    ConstructionClientProjectsReadOperationError,PUBLIC_CLIENT_PROJECT_FIELDS,build_construction_client_projects_read_service,
)
from services.construction_clients import ConstructionClientNotFoundError,ConstructionClientValidationError
from services.construction_permissions import (
    ConstructionAccessContext,ConstructionGrantContext,CONSTRUCTION_ROLE_PERMISSIONS,
    ConstructionPermissionDeniedError,ConstructionScopeDeniedError,
)
from test_construction_1a_client_projects_repository import repository_case,real_case,project,TABLE


def create_service(c):
    return ConstructionClientProjectsReadService(session=c.session,repository=c.repo,client_repository=c.clients,
        access_repository=c.access,now_provider=lambda:c.now)


@pytest.fixture
def service_case():
    c=SimpleNamespace(tenant=uuid4(),user=uuid4(),client=uuid4(),now=datetime(2026,10,13,12,tzinfo=timezone.utc))
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
    c.row=SimpleNamespace(id=UUID(int=20),tenant_id=c.tenant,client_id=c.client,name='Project',status='draft',
        responsible_user_id=c.user,comment='Comment',address_raw='Address',address_formatted=None,country_code=None,
        region=None,city=None,postal_code=None,latitude=Decimal('38.72'),longitude=Decimal('-9.13'),address_provider=None,
        provider_place_id=None,address_verification_status='pending',created_by=c.user,created_at=c.now,updated_at=c.now,
        row_version=1,deleted_at=None,private_internal='Never public')
    c.repo=SimpleNamespace(session=c.session,list_projects_for_client=AsyncMock(return_value=[c.row]))
    c.service=create_service(c)
    return c


async def read(c,**changes):
    options=dict(actor=c.actor,access_context=c.context,client_id=c.client)
    options.update(changes)
    return await c.service.list_projects_for_client(**options)


def no_writes(c):
    c.session.add.assert_not_called();c.session.flush.assert_not_called()
    c.session.commit.assert_not_called();c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_current_access_client_and_explicit_project_projection(service_case):
    c=service_case;page=await read(c)
    assert isinstance(page,ConstructionClientProjectsReadPage) and page.next_cursor is None
    assert set(page.items[0])==PUBLIC_CLIENT_PROJECT_FIELDS and len(page.items[0])==22
    assert page.items[0]['id']==str(c.row.id) and page.items[0]['client_id']==str(c.client)
    assert page.items[0]['latitude']=='38.72' and page.items[0]['longitude']=='-9.13'
    assert 'deleted_at' not in page.items[0] and 'private_internal' not in page.items[0]
    c.row.name='Changed later';assert page.items[0]['name']=='Project'
    c.repo.list_projects_for_client.assert_awaited_once_with(tenant_id=c.tenant,client_id=c.client,limit=20,after=None,allowed_project_ids=None)
    assert c.clients.get_active_client.await_count==2 and c.access.has_active_platform_membership.await_count==3
    no_writes(c)


@pytest.mark.asyncio
async def test_existing_client_without_projects_returns_empty_page_not_not_found(service_case):
    c=service_case;c.repo.list_projects_for_client.return_value=[]
    page=await read(c);assert page.items==() and page.next_cursor is None
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('status',['completed','cancelled'])
async def test_terminal_project_status_is_readable(service_case,status):
    c=service_case;c.row.status=status
    assert (await read(c)).items[0]['status']==status
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['inactive','wrong_tenant','wrong_user','permission','project','document','no_grants'])
async def test_initial_denial_cannot_read_client_or_projects(service_case,failure):
    c=service_case
    if failure=='inactive':c.actor.status='blocked'
    else:
        scope='project' if failure=='project' else 'document' if failure=='document' else 'tenant'
        role='PROJECT_MANAGER' if scope=='project' else 'OBSERVER' if scope=='document' else 'OWNER'
        c.context=ConstructionAccessContext(uuid4() if failure=='wrong_user' else c.user,
            uuid4() if failure=='wrong_tenant' else c.tenant,(role,),() if failure=='permission' else ('construction.projects.read',),
            () if failure=='no_grants' else (ConstructionGrantContext(role,scope,c.row.id if scope!='tenant' else c.tenant),))
    with pytest.raises((ConstructionPermissionDeniedError,ConstructionScopeDeniedError)):await read(c)
    c.clients.get_active_client.assert_not_called();c.repo.list_projects_for_client.assert_not_called()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['membership','revoked','expired','different_tenant_grant'])
async def test_fresh_membership_and_grants_are_required_not_just_supplied_context(service_case,failure):
    c=service_case
    if failure=='membership':c.access.has_active_platform_membership.return_value=False
    elif failure=='revoked':c.grant.status='revoked'
    elif failure=='expired':c.grant.expires_at=c.now
    else:c.grant.scope_id=uuid4()
    with pytest.raises((ConstructionPermissionDeniedError,ConstructionScopeDeniedError)):await read(c)
    c.clients.get_active_client.assert_not_called();c.repo.list_projects_for_client.assert_not_called()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['missing','foreign','deleted','wrong_id'])
async def test_client_availability_is_checked_before_project_query(service_case,failure):
    c=service_case
    if failure=='missing':c.clients.get_active_client.return_value=None
    elif failure=='foreign':c.client_row.tenant_id=uuid4()
    elif failure=='deleted':c.client_row.deleted_at=c.now
    else:c.client_row.id=uuid4()
    with pytest.raises(ConstructionClientNotFoundError):await read(c)
    c.repo.list_projects_for_client.assert_not_called();no_writes(c)


@pytest.mark.asyncio
async def test_access_loss_while_client_lookup_is_awaited_prevents_project_query(service_case):
    c=service_case
    async def revoke(**kw):c.grant.status='revoked';return c.client_row
    c.clients.get_active_client.side_effect=revoke
    with pytest.raises(ConstructionPermissionDeniedError):await read(c)
    c.repo.list_projects_for_client.assert_not_called();no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['grant','membership','archived_client','final_client_lookup'])
async def test_changed_access_or_client_after_query_prevents_publication(service_case,failure):
    c=service_case
    async def query(**kw):
        if failure=='grant':c.grant.status='revoked'
        elif failure=='membership':c.access.has_active_platform_membership.return_value=False
        elif failure=='archived_client':c.client_row.deleted_at=c.now
        return [c.row]
    c.repo.list_projects_for_client.side_effect=query
    if failure=='final_client_lookup':
        async def client(**kw):
            if c.clients.get_active_client.await_count==2:c.grant.status='revoked'
            return c.client_row
        c.clients.get_active_client.side_effect=client
    with pytest.raises(ConstructionClientNotFoundError if failure=='archived_client' else ConstructionPermissionDeniedError):await read(c)
    no_writes(c)


@pytest.mark.asyncio
async def test_loss_of_tenant_scope_cannot_publish_even_empty_page(service_case):
    c=service_case
    async def query(**kw):
        c.grant.role,c.grant.scope_type,c.grant.scope_id='PROJECT_MANAGER','project',c.row.id
        return []
    c.repo.list_projects_for_client.side_effect=query
    with pytest.raises(ConstructionScopeDeniedError):await read(c)
    no_writes(c)


@pytest.mark.asyncio
async def test_cursor_binds_actor_client_tenant_and_uses_last_returned_row(service_case):
    c=service_case;second=SimpleNamespace(**{**vars(c.row),'id':UUID(int=10)})
    c.repo.list_projects_for_client.return_value=[c.row,second]
    page=await read(c,limit=1)
    expected=ConstructionClientProjectsReadCursor(c.tenant,c.user,c.client,c.now,c.row.id)
    assert page.next_cursor==expected and len(page.items)==1
    c.repo.list_projects_for_client.return_value=[second]
    last=await read(c,limit=1,after=page.next_cursor)
    assert last.next_cursor is None and last.items[0]['id']==str(second.id)
    assert c.repo.list_projects_for_client.await_args.kwargs['after']==ConstructionClientProjectsCursor(c.tenant,c.client,c.now,c.row.id)
    for changed in [dict(user_id=uuid4()),dict(client_id=uuid4()),dict(tenant_id=uuid4())]:
        cursor=ConstructionClientProjectsReadCursor(**{**vars(expected),**changed})
        with pytest.raises(ConstructionClientValidationError):await read(c,after=cursor)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['duplicate','wrong_order','oversize','before_cursor','wrong_result_type'])
async def test_malformed_page_order_or_lookahead_is_rejected(service_case,failure):
    c=service_case;options={};second=SimpleNamespace(**{**vars(c.row),'id':UUID(int=10)})
    if failure=='duplicate':rows=[c.row,c.row]
    elif failure=='wrong_order':rows=[second,c.row]
    elif failure=='oversize':rows=[c.row,second,second];options['limit']=1
    elif failure=='wrong_result_type':rows=()
    else:
        rows=[c.row];options['after']=ConstructionClientProjectsReadCursor(c.tenant,c.user,c.client,c.now,c.row.id)
    c.repo.list_projects_for_client.return_value=rows
    with pytest.raises(ConstructionClientProjectsReadOperationError):await read(c,**options)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value',[('id','uuid'),('tenant_id',uuid4()),('client_id',uuid4()),('deleted_at',True),
    ('updated_at',datetime(2026,10,13)),('created_at',datetime(2026,10,13)),('row_version',True),('status','invalid'),
    ('latitude',Decimal('NaN')),('longitude',Decimal('Infinity'))])
async def test_foreign_deleted_or_malformed_project_is_not_published(service_case,field,value):
    c=service_case;setattr(c.row,field,value)
    with pytest.raises(ConstructionClientProjectsReadOperationError):await read(c)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('changes',[dict(client_id='uuid'),dict(limit=True),dict(limit=0),dict(limit=101),dict(after={})])
async def test_bad_query_precedes_any_repository_read(service_case,changes):
    c=service_case
    with pytest.raises(ConstructionClientValidationError):await read(c,**changes)
    c.clients.get_active_client.assert_not_called();c.repo.list_projects_for_client.assert_not_called();no_writes(c)


@pytest.mark.asyncio
async def test_frozen_cursor_is_revalidated(service_case):
    c=service_case;cursor=ConstructionClientProjectsReadCursor(c.tenant,c.user,c.client,c.now,c.row.id)
    object.__setattr__(cursor,'updated_at',c.now.replace(tzinfo=None))
    with pytest.raises(ConstructionClientValidationError):await read(c,after=cursor)
    c.repo.list_projects_for_client.assert_not_called();no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['projects','client','clock','transaction','cancelled'])
async def test_read_failure_is_sanitized_and_caller_retains_transaction(service_case,failure):
    c=service_case
    if failure=='projects':c.repo.list_projects_for_client.side_effect=RuntimeError('private-database-detail')
    elif failure=='client':c.clients.get_active_client.side_effect=RuntimeError('private-client-detail')
    elif failure=='clock':c.service.now_provider=lambda:c.now.replace(tzinfo=None)
    elif failure=='transaction':c.session.in_transaction=lambda:False
    else:c.repo.list_projects_for_client.side_effect=asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError if failure=='cancelled' else ConstructionClientProjectsReadOperationError) as caught:await read(c)
    assert 'private-' not in str(caught.value);no_writes(c)


def test_factory_requires_shared_session_without_audit_or_idempotency(service_case):
    c=service_case;built=build_construction_client_projects_read_service(session=c.session,now_provider=lambda:c.now)
    assert all(repo.session is c.session for repo in (built.repository,built.client_repository,built.access_repository))
    c.clients.session=object()
    with pytest.raises(ValueError):create_service(c)


@pytest.fixture
def sql_service(real_case):
    c=real_case
    class Adapter:
        sync=c.sync
        @property
        def no_autoflush(self):return self.sync.no_autoflush
        def in_transaction(self):return self.sync.in_transaction()
        async def execute(self,statement):return self.sync.execute(statement)
        add=Mock();flush=AsyncMock();commit=AsyncMock();rollback=AsyncMock()
    c.session=Adapter()
    c.actor=SimpleNamespace(tenant_id=c.tenant,user_id=c.user,status='active')
    c.context=ConstructionAccessContext(c.user,c.tenant,('OWNER',),('construction.projects.read',),
        (ConstructionGrantContext('OWNER','tenant',c.tenant),))
    c.grant=SimpleNamespace(role='OWNER',scope_type='tenant',scope_id=c.tenant,status='active',expires_at=None)
    c.access=SimpleNamespace(session=c.session,has_active_platform_membership=AsyncMock(return_value=True),
        list_active_grants=AsyncMock(return_value=[c.grant]))
    c.client_row=SimpleNamespace(id=c.client,tenant_id=c.tenant,deleted_at=None)
    c.clients=SimpleNamespace(session=c.session,get_active_client=AsyncMock(return_value=c.client_row))
    class SQLiteRepository(ConstructionClientProjectsRepository):
        async def list_projects_for_client(self,**kw):
            rows=await super().list_projects_for_client(**kw)
            for row in rows:
                for field in ('created_at','updated_at'):
                    value=getattr(row,field)
                    if value.tzinfo is None:set_committed_value(row,field,value.replace(tzinfo=timezone.utc))
            return rows
    c.repo=SQLiteRepository(c.session);c.service=create_service(c);c.sync.begin()
    return c


@pytest.mark.asyncio
async def test_real_service_materializes_equal_time_pages_and_excludes_other_clients_tenants_deleted(sql_service):
    c=sql_service;ids=[UUID(int=value) for value in range(1,6)]
    with c.engine.begin() as connection:
        for id in ids:connection.execute(insert(TABLE).values(**project(c,id)))
        for data in [project(c,uuid4(),client_id=c.other_client),project(c,uuid4(),tenant_id=c.foreign,client_id=c.foreign_client),
                     project(c,uuid4(),deleted_at=c.now)]:connection.execute(insert(TABLE).values(**data))
    first=await read(c,limit=2);second=await read(c,limit=2,after=first.next_cursor);third=await read(c,limit=2,after=second.next_cursor)
    assert [item['id'] for item in first.items+second.items+third.items]==[str(id) for id in reversed(ids)]
    assert third.next_cursor is None
    assert first.items[0]['client_id']==str(c.client)
    no_writes(c);assert c.flushes==[]
    with c.engine.connect() as connection:assert len(connection.execute(select(TABLE.c.id)).all())==8


@pytest.mark.asyncio
async def test_real_service_does_not_flush_pending_project_mutation(sql_service):
    c=sql_service;id=uuid4()
    with c.engine.begin() as connection:connection.execute(insert(TABLE).values(**project(c,id)))
    initial=await read(c);cached=c.sync.get(ConstructionProject,id)
    cached.name='Unsaved mutation'
    again=await read(c)
    assert initial.items[0]['name']==again.items[0]['name'] and again.items[0]['name']!='Unsaved mutation'
    assert c.flushes==[];no_writes(c)
