import asyncio
from dataclasses import replace
from datetime import timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm.attributes import set_committed_value

from database.models import ConstructionActivity, ConstructionLead
from database.repositories.construction_lead_list_read import ConstructionLeadListReadRepository
from services.construction_lead_list_read import (
    ConstructionLeadListReadService, ConstructionLeadListFilters, ConstructionLeadListReadCursor,
    ConstructionLeadListReadPage, ConstructionLeadListReadOperationError, build_construction_lead_list_read_service,
)
from services.construction_lead_rules import ConstructionLeadValidationError
from services.construction_permissions import ConstructionAccessContext, ConstructionGrantContext, ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from test_construction_1a_lead_detail_read import detail_case, detail_sql, no_writes
from test_construction_1a_activity_read_service import read_case, sql_read
from test_construction_1a_activity_terminal_service import terminal_case, sql_terminal
from test_construction_1a_activity_model import activity_db
from test_construction_1a_lead_model import lead_db, values as lead_values


@pytest.fixture
def list_case(detail_case):
    c=detail_case
    c.lead.id=UUID(int=30)
    c.row.lead_id=c.lead.id
    c.list_repo=SimpleNamespace(session=c.session,list_active_leads=AsyncMock(return_value=[c.lead]),
        list_next_actions_for_leads=AsyncMock(return_value=[c.row]))
    c.list_service=ConstructionLeadListReadService(session=c.session,repository=c.list_repo,
        access_repository=c.access,now_provider=lambda:c.now)
    return c


async def listing(c, **changes):
    kwargs=dict(actor=c.actor,access_context=c.context)
    kwargs.update(changes)
    return await c.list_service.list_leads(**kwargs)


def another(c,number,**changes):
    return SimpleNamespace(**{**vars(c.lead),'id':UUID(int=number),**changes})


@pytest.mark.asyncio
async def test_default_page_materializes_leads_and_one_batch_of_next_actions_without_writes(list_case):
    c=list_case
    c.list_repo.list_active_leads.return_value=[c.lead,another(c,20)]
    page=await listing(c)
    assert isinstance(page,ConstructionLeadListReadPage) and page.next_cursor is None
    assert [item['id'] for item in page.items]==[str(UUID(int=30)),str(UUID(int=20))]
    assert page.items[0]['next_action']['id']==str(c.row.id) and page.items[1]['next_action'] is None
    assert 'deleted_at' not in page.items[0] and 'private_internal' not in page.items[0]
    c.list_repo.list_active_leads.assert_awaited_once_with(tenant_id=c.tenant,limit=20,after=None,
        status=None,source=None,responsible_user_id=None,converted_client_id=None,converted_project_id=None)
    c.list_repo.list_next_actions_for_leads.assert_awaited_once_with(tenant_id=c.tenant,lead_ids=(UUID(int=30),UUID(int=20)))
    c.lead.comment='later change';c.row.comment='later action change'
    assert page.items[0]['comment']=='Lead note' and page.items[0]['next_action']['comment']=='Private comment'
    no_writes(c)


@pytest.mark.asyncio
async def test_empty_result_does_not_query_next_actions(list_case):
    c=list_case;c.list_repo.list_active_leads.return_value=[]
    page=await listing(c)
    assert page.items==() and page.next_cursor is None
    c.list_repo.list_next_actions_for_leads.assert_not_called()
    no_writes(c)


@pytest.mark.asyncio
async def test_cursor_is_last_visible_lead_and_lookahead_has_no_next_action_query(list_case):
    c=list_case
    c.list_repo.list_active_leads.return_value=[c.lead,another(c,20),another(c,10)]
    page=await listing(c,limit=2)
    assert page.next_cursor==ConstructionLeadListReadCursor(c.tenant,c.user,ConstructionLeadListFilters(),c.now,UUID(int=20))
    assert c.list_repo.list_next_actions_for_leads.await_args.kwargs['lead_ids']==(UUID(int=30),UUID(int=20))
    c.list_repo.list_active_leads.return_value=[another(c,10)]
    c.list_repo.list_next_actions_for_leads.return_value=[]
    final=await listing(c,limit=2,after=page.next_cursor)
    assert final.items[0]['id']==str(UUID(int=10)) and final.next_cursor is None
    assert c.list_repo.list_active_leads.await_args.kwargs['after'].lead_id==UUID(int=20)
    no_writes(c)


@pytest.mark.asyncio
async def test_existing_filters_are_normalized_and_bound_to_cursor(list_case):
    c=list_case
    filters=ConstructionLeadListFilters(status='new',source=' PHONE ',responsible_user_id=c.user)
    c.list_repo.list_active_leads.return_value=[c.lead,another(c,20)]
    page=await listing(c,filters=filters,limit=1)
    assert page.next_cursor.filters.source=='phone'
    assert c.list_repo.list_active_leads.await_args.kwargs['responsible_user_id']==c.user
    assert c.list_repo.list_active_leads.await_args.kwargs['source']=='phone'
    with pytest.raises(ConstructionLeadValidationError):
        await listing(c,filters=ConstructionLeadListFilters(source='email'),after=page.next_cursor)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('field',['user_id','tenant_id'])
async def test_cursor_from_another_actor_or_tenant_is_denied_before_query(list_case,field):
    c=list_case
    cursor=ConstructionLeadListReadCursor(c.tenant,c.user,ConstructionLeadListFilters(),c.now,UUID(int=40))
    with pytest.raises(ConstructionLeadValidationError):await listing(c,after=replace(cursor,**{field:uuid4()}))
    c.list_repo.list_active_leads.assert_not_called();no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['membership','revoked','project_scope','inactive','forged_context'])
async def test_current_crm_access_is_required_before_repository_read(list_case,failure):
    c=list_case
    if failure=='membership':c.access.has_active_platform_membership.return_value=False
    elif failure=='revoked':c.grant.status='revoked'
    elif failure=='project_scope':
        c.context=ConstructionAccessContext(c.user,c.tenant,('PROJECT_MANAGER',),('construction.projects.read',),
            (ConstructionGrantContext('PROJECT_MANAGER','project',c.project.id),))
    elif failure=='inactive':c.actor.status='blocked'
    else:
        c.context=ConstructionAccessContext(uuid4(),c.tenant,('OWNER',),('construction.projects.read',),
            (ConstructionGrantContext('OWNER','tenant',c.tenant),))
    with pytest.raises((ConstructionPermissionDeniedError,ConstructionScopeDeniedError)):await listing(c)
    c.list_repo.list_active_leads.assert_not_called();no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('stage',['lead_page','action_batch'])
async def test_permission_revoked_during_read_prevents_returning_a_page(list_case,stage):
    c=list_case
    async def query(**kwargs):
        c.grant.status='revoked'
        return [c.lead] if stage=='lead_page' else [c.row]
    getattr(c.list_repo,'list_active_leads' if stage=='lead_page' else 'list_next_actions_for_leads').side_effect=query
    with pytest.raises(ConstructionPermissionDeniedError):await listing(c)
    if stage=='lead_page':c.list_repo.list_next_actions_for_leads.assert_not_called()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('bad',['foreign','deleted','duplicate','ascending','after_cursor','filter_mismatch','oversized'])
async def test_repository_page_must_match_tenant_lifecycle_filters_and_descending_order(list_case,bad):
    c=list_case;kw={}
    if bad=='foreign':c.lead.tenant_id=uuid4()
    elif bad=='deleted':c.lead.deleted_at=c.now
    elif bad=='duplicate':c.list_repo.list_active_leads.return_value=[c.lead,c.lead]
    elif bad=='ascending':c.list_repo.list_active_leads.return_value=[another(c,20),c.lead]
    elif bad=='after_cursor':kw['after']=ConstructionLeadListReadCursor(c.tenant,c.user,ConstructionLeadListFilters(),c.now,UUID(int=20))
    elif bad=='filter_mismatch':kw['filters']=ConstructionLeadListFilters(status='won')
    else:c.list_repo.list_active_leads.return_value=[c.lead,another(c,20),another(c,10)];kw['limit']=1
    with pytest.raises(ConstructionLeadListReadOperationError):await listing(c,**kw)
    c.list_repo.list_next_actions_for_leads.assert_not_called();no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('bad',['foreign','other_lead','deleted','completed','duplicate','extra_sentinel'])
async def test_next_action_batch_cannot_publish_foreign_unrequested_or_terminal_data(list_case,bad):
    c=list_case
    if bad=='foreign':c.row.tenant_id=uuid4()
    elif bad in ('other_lead','extra_sentinel'):
        c.row.lead_id=UUID(int=10)
        if bad=='extra_sentinel':c.list_repo.list_active_leads.return_value=[c.lead,another(c,10)]
    elif bad=='deleted':c.row.deleted_at=c.now
    elif bad=='completed':c.row.status='completed'
    else:
        c.list_repo.list_active_leads.return_value=[c.lead,another(c,20)]
        c.list_repo.list_next_actions_for_leads.return_value=[c.row,SimpleNamespace(**{**vars(c.row),'id':uuid4()})]
    with pytest.raises(ConstructionLeadListReadOperationError):await listing(c,**({'limit':1} if bad=='extra_sentinel' else {}))
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('bad',[dict(limit=True),dict(limit=0),dict(limit=101),dict(filters={}),dict(after={})])
async def test_invalid_page_input_is_rejected_without_queries(list_case,bad):
    c=list_case
    with pytest.raises(ConstructionLeadValidationError):await listing(c,**bad)
    c.list_repo.list_active_leads.assert_not_called();no_writes(c)


@pytest.mark.asyncio
async def test_errors_are_sanitized_and_cancellation_does_not_change_transaction(list_case):
    c=list_case
    c.list_repo.list_next_actions_for_leads.side_effect=RuntimeError('private db detail')
    with pytest.raises(ConstructionLeadListReadOperationError) as error:await listing(c)
    assert 'private' not in str(error.value)
    c.list_repo.list_next_actions_for_leads.side_effect=asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):await listing(c)
    no_writes(c)


@pytest.mark.asyncio
async def test_batch_repository_bounds_and_sql_partition_before_selection(list_case):
    c=list_case
    repo=ConstructionLeadListReadRepository(c.session)
    for bad in ([c.lead.id],(c.lead.id,c.lead.id),('not-uuid',),tuple(uuid4() for _ in range(101))):
        with pytest.raises(ValueError):await repo.list_next_actions_for_leads(tenant_id=c.tenant,lead_ids=bad)
    assert await repo.list_next_actions_for_leads(tenant_id=c.tenant,lead_ids=())==[]
    c.session.execute.assert_not_called()
    c.session.execute.return_value=SimpleNamespace(scalars=lambda:SimpleNamespace(all=lambda:[]))
    assert await repo.list_next_actions_for_leads(tenant_id=c.tenant,lead_ids=(c.lead.id,UUID(int=20)))==[]
    statement=c.session.execute.await_args.args[0]
    sql=str(statement.compile(dialect=postgresql.dialect()))
    assert 'row_number() OVER (PARTITION BY construction_activities.lead_id ORDER BY construction_activities.due_at ASC, construction_activities.id ASC)' in sql
    assert 'construction_activities.tenant_id =' in sql and 'construction_activities.deleted_at IS NULL' in sql
    assert 'construction_activities.status =' in sql and 'construction_activities.lead_id IN' in sql
    assert 'LIMIT' not in sql and 'FOR UPDATE' not in sql
    assert statement.get_execution_options()['autoflush'] is False
    no_writes(c)


def test_factory_and_filter_contracts_reuse_existing_storage(list_case):
    c=list_case
    service=build_construction_lead_list_read_service(session=c.session)
    assert service.repository.session is service.access.session is c.session
    assert ConstructionLeadListFilters(source=' New_Partner ').source=='new_partner'
    for fields in ({'status':'qualified'},{'source':' '},{'responsible_user_id':'uuid'}):
        with pytest.raises(ConstructionLeadValidationError):ConstructionLeadListFilters(**fields)


@pytest.fixture
def sql_list(detail_sql):
    c=detail_sql
    class UTCRepository(ConstructionLeadListReadRepository):
        @staticmethod
        def dates(rows):
            for row in rows:
                for name in ('created_at','updated_at','due_at'):
                    value=getattr(row,name,None)
                    if value is not None and value.tzinfo is None:set_committed_value(row,name,value.replace(tzinfo=timezone.utc))
            return rows
        async def list_active_leads(self,**kw):
            return self.dates(await super().list_active_leads(**kw))
        async def list_next_actions_for_leads(self,**kw):
            return self.dates(await super().list_next_actions_for_leads(**kw))
    c.list_service=ConstructionLeadListReadService(session=c.session,repository=UTCRepository(c.session),
        access_repository=c.access,now_provider=lambda:c.now)
    return c


@pytest.mark.asyncio
async def test_actual_keyset_pages_and_per_lead_ranking_have_no_global_cutoff_or_cross_tenant_data(sql_list):
    c=sql_list
    ids={'a':c.tenant,'user':c.user}
    a,b,d=UUID(int=30),UUID(int=20),UUID(int=10)
    with c.session.sync.get_bind().begin() as connection:
        connection.execute(update(ConstructionLead.__table__).where(ConstructionLead.id==c.lead.id).values(
            created_at=c.now,updated_at=c.now))
        for identifier,status in ((a,'won'),(b,'lost'),(d,'new')):
            connection.execute(insert(ConstructionLead.__table__).values(**lead_values(ids,id=identifier,
                updated_at=c.now+timedelta(days=1),created_at=c.now,status=status)))
        connection.execute(insert(ConstructionLead.__table__).values(**lead_values(ids,id=UUID(int=40),
            updated_at=c.now+timedelta(days=2),deleted_at=c.now)))
        base=dict(tenant_id=c.tenant,type='call',status='planned',timezone='UTC',created_by_user_id=c.user,
            created_at=c.now,updated_at=c.now,due_at=c.now-timedelta(days=5))
        # Many earlier actions for A must not crowd B out of a global top-N.
        for number in range(1,6):
            connection.execute(insert(ConstructionActivity.__table__).values(**base,id=UUID(int=100+number),lead_id=a))
        connection.execute(insert(ConstructionActivity.__table__).values(**{**base,'id':UUID(int=200),'lead_id':b,'due_at':c.now}))
        connection.execute(insert(ConstructionActivity.__table__).values(**{**base,'id':UUID(int=90),'lead_id':a,
            'due_at':c.now-timedelta(days=30),'status':'completed','completed_at':c.now}))
        connection.execute(insert(ConstructionActivity.__table__).values(**{**base,'id':UUID(int=91),'lead_id':b,
            'due_at':c.now-timedelta(days=30),'deleted_at':c.now}))
    first=await listing(c,limit=2)
    assert [item['id'] for item in first.items]==[str(a),str(b)]
    assert [item['next_action']['id'] for item in first.items]==[str(UUID(int=101)),str(UUID(int=200))]
    second=await listing(c,limit=2,after=first.next_cursor)
    assert second.items[0]['id']==str(d) and second.items[0]['next_action'] is None
    assert len(second.items)==2 and second.next_cursor is None
    assert all(item['tenant_id']==str(c.tenant) for item in (*first.items,*second.items))
    filtered=await listing(c,filters=ConstructionLeadListFilters(status='lost'))
    assert [item['id'] for item in filtered.items]==[str(b)]
    c.session.commit.assert_not_called();c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_actual_read_rejects_autoflush_and_empty_filters_do_not_write(sql_list):
    c=sql_list
    row=await c.detail.repository.get_active_lead(tenant_id=c.tenant,lead_id=c.lead.id)
    original=row.name
    row.name='Pending local modification'
    page=await listing(c)
    assert page.items[0]['name']==original
    assert not (await listing(c,filters=ConstructionLeadListFilters(source='nonexistent_source'))).items
    c.session.commit.assert_not_called();c.session.rollback.assert_not_called()
