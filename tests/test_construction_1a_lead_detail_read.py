import asyncio
from datetime import timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.orm.attributes import set_committed_value

from database.models import ConstructionActivity, ConstructionLead
from database.repositories.construction_leads import ConstructionLeadRepository
from services.construction_lead_read import (
    ConstructionLeadReadService, ConstructionLeadReadResponse, ConstructionLeadReadNotFoundError,
    ConstructionLeadReadOperationError, PUBLIC_LEAD_DETAIL_FIELDS, build_construction_lead_read_service,
)
from services.construction_lead_rules import ConstructionLeadValidationError, CONSTRUCTION_LEAD_STATUSES
from services.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext, CONSTRUCTION_ROLE_PERMISSIONS,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from test_construction_1a_activity_read_service import read_case, sql_read
from test_construction_1a_activity_terminal_service import terminal_case, sql_terminal
from test_construction_1a_activity_model import activity_db, values as activity_values
from test_construction_1a_lead_model import lead_db, values as lead_values


@pytest.fixture
def detail_case(read_case):
    c = read_case
    for name, value in dict(source='phone',name='Example lead',work_type_description='Painting',
        phone='12345',email=None,address_raw='Example address',comment='Lead note',responsible_user_id=c.user,
        status='new',converted_client_id=None,converted_project_id=None,created_at=c.now,updated_at=c.now,row_version=1).items():
        setattr(c.lead,name,value)
    c.lead.private_internal = 'Never public'
    c.activity_service = c.service
    c.detail = ConstructionLeadReadService(session=c.session,repository=c.lead_repo,
        activity_service=c.activity_service,access_repository=c.access,now_provider=lambda:c.now)
    return c


async def read(c, **changes):
    kwargs = dict(actor=c.actor,access_context=c.context,lead_id=c.lead.id)
    kwargs.update(changes)
    return await c.detail.get_lead(**kwargs)


def no_writes(c):
    c.session.add.assert_not_called()
    c.session.flush.assert_not_called()
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
async def test_unconverted_lead_includes_existing_computed_next_action_as_detached_projection(detail_case):
    c = detail_case
    c.row.due_at = c.now-timedelta(days=10)
    result = await read(c)
    assert set(result)==PUBLIC_LEAD_DETAIL_FIELDS
    assert result['id']==str(c.lead.id) and result['converted_client_id'] is None
    assert result['next_action']['id']==str(c.row.id) and result['next_action']['status']=='planned'
    assert result['next_action']['lead_id']==str(c.lead.id)
    assert result['next_action']['due_at'].startswith((c.now-timedelta(days=10)).date().isoformat())
    assert 'deleted_at' not in result and 'private_internal' not in result
    assert 'private_internal' not in result['next_action']
    c.lead.comment = 'Later lead change'
    c.row.comment = 'Later activity change'
    assert result['comment']=='Lead note' and result['next_action']['comment']=='Private comment'
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('status',sorted(CONSTRUCTION_LEAD_STATUSES))
async def test_all_approved_lifecycle_states_are_read_without_changing_the_lead(detail_case,status):
    c = detail_case
    c.lead.status = status
    c.repo.get_next_action_for_lead.return_value = None
    result = await read(c)
    assert result['status']==status and result['row_version']==1 and result['next_action'] is None
    no_writes(c)


@pytest.mark.asyncio
async def test_converted_lead_preserves_canonical_client_and_project_links(detail_case):
    c = detail_case
    c.lead.status='won'
    c.lead.converted_client_id,c.lead.converted_project_id=c.client.id,c.project.id
    result=await read(c)
    assert result['converted_client_id']==str(c.client.id) and result['converted_project_id']==str(c.project.id)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('denial',['inactive','permission','foreign_actor_context','project_only'])
async def test_access_denial_precedes_lead_or_activity_reads(detail_case,denial):
    c=detail_case
    if denial=='inactive':c.actor.status='blocked'
    elif denial=='permission':
        c.context=ConstructionAccessContext(c.user,c.tenant,('OWNER',),(),(ConstructionGrantContext('OWNER','tenant',c.tenant),))
    elif denial=='foreign_actor_context':
        c.context=ConstructionAccessContext(uuid4(),c.tenant,('OWNER',),('construction.projects.read',),(ConstructionGrantContext('OWNER','tenant',c.tenant),))
    else:
        c.context=ConstructionAccessContext(c.user,c.tenant,('PROJECT_MANAGER',),tuple(CONSTRUCTION_ROLE_PERMISSIONS['PROJECT_MANAGER']),
            (ConstructionGrantContext('PROJECT_MANAGER','project',c.project.id),))
    with pytest.raises((ConstructionPermissionDeniedError,ConstructionScopeDeniedError)):
        await read(c)
    c.lead_repo.get_active_lead.assert_not_called()
    c.repo.get_next_action_for_lead.assert_not_called()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('denial',['membership','revoked','expired'])
async def test_fresh_membership_and_effective_grant_are_required_before_read(detail_case,denial):
    c=detail_case
    if denial=='membership':c.access.has_active_platform_membership.return_value=False
    elif denial=='revoked':c.grant.status='revoked'
    else:c.grant.expires_at=c.now
    with pytest.raises(ConstructionPermissionDeniedError):await read(c)
    c.lead_repo.get_active_lead.assert_not_called()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('missing',['absent','foreign','archived'])
async def test_unavailable_lead_is_not_published_and_does_not_query_activities(detail_case,missing):
    c=detail_case
    if missing=='absent':c.lead_repo.get_active_lead.return_value=None
    elif missing=='foreign':c.lead.tenant_id=uuid4()
    else:c.lead.deleted_at=c.now
    with pytest.raises(ConstructionLeadReadNotFoundError):await read(c)
    c.repo.get_next_action_for_lead.assert_not_called()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('later',['revoked','archived','changed_lead'])
async def test_read_does_not_publish_data_after_access_loss_or_parent_change(detail_case,later):
    c=detail_case
    async def query(**kwargs):
        if later=='revoked':c.grant.status='revoked'
        elif later=='archived':c.lead.deleted_at=c.now
        else:c.lead.row_version+=1;c.lead.comment='Changed while reading'
        return c.row
    c.repo.get_next_action_for_lead.side_effect=query
    expected=ConstructionPermissionDeniedError if later=='revoked' else ConstructionLeadReadNotFoundError if later=='archived' else ConstructionLeadReadOperationError
    with pytest.raises(expected):await read(c)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value',[('tenant_id','foreign'),('lead_id','foreign'),('status','completed')])
async def test_next_action_cannot_escape_the_lead_or_approved_planned_state(detail_case,field,value):
    c=detail_case
    setattr(c.row,field,uuid4() if value=='foreign' else value)
    with pytest.raises(ConstructionLeadReadOperationError):await read(c)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('problem',['blank_name','no_contact','old_status','naive_date'])
async def test_malformed_stored_lead_cannot_become_a_public_response(detail_case,problem):
    c=detail_case
    if problem=='blank_name':c.lead.name=' '
    elif problem=='no_contact':c.lead.phone=None
    elif problem=='old_status':c.lead.status='qualified'
    else:c.lead.created_at=c.now.replace(tzinfo=None)
    with pytest.raises(ConstructionLeadReadOperationError):await read(c)
    no_writes(c)


@pytest.mark.asyncio
async def test_database_failure_is_sanitized_and_cancellation_propagates_without_transaction_changes(detail_case):
    c=detail_case
    c.lead_repo.get_active_lead.side_effect=RuntimeError('private query data')
    with pytest.raises(ConstructionLeadReadOperationError) as caught:await read(c)
    assert 'private' not in str(caught.value)
    c.lead_repo.get_active_lead.side_effect=asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):await read(c)
    no_writes(c)


@pytest.mark.asyncio
async def test_invalid_identifier_or_missing_transaction_cannot_trigger_a_query(detail_case):
    c=detail_case
    with pytest.raises(ConstructionLeadValidationError):await read(c,lead_id='not-a-uuid')
    c.session.in_transaction=lambda:False
    with pytest.raises(ConstructionLeadReadOperationError):await read(c)
    c.lead_repo.get_active_lead.assert_not_called()
    no_writes(c)


def test_factory_uses_existing_repositories_and_shared_activity_read_session(detail_case):
    c=detail_case
    result=build_construction_lead_read_service(session=c.session,now_provider=lambda:c.now)
    assert result.repository.session is result.activities.session is result.access.session is c.session
    assert result.activities.repository.session is c.session
    c.activity_service.session=object()
    with pytest.raises(ValueError):
        ConstructionLeadReadService(session=c.session,repository=c.lead_repo,
            activity_service=c.activity_service,access_repository=c.access)


@pytest.fixture
def detail_sql(sql_read):
    c=sql_read
    class UTCLeadRepository(ConstructionLeadRepository):
        async def get_active_lead(self,**kw):
            row=await super().get_active_lead(**kw)
            if row is not None:
                for name in ('created_at','updated_at'):
                    instant=getattr(row,name)
                    if instant.tzinfo is None:set_committed_value(row,name,instant.replace(tzinfo=timezone.utc))
            return row
    c.detail=ConstructionLeadReadService(session=c.session,repository=UTCLeadRepository(c.session),
        activity_service=c.service,access_repository=c.access,now_provider=lambda:c.now)
    return c


@pytest.mark.asyncio
async def test_real_repositories_select_earliest_overdue_tie_by_uuid_and_hide_deleted_terminal_foreign_and_other_lead_rows(detail_sql):
    c=detail_sql
    first,second=UUID(int=1),UUID(int=2)
    engine=c.session.sync.get_bind()
    ids={'a':c.tenant,'user':c.user}
    with engine.begin() as connection:
        foreign=connection.execute(select(ConstructionLead.id,ConstructionLead.tenant_id).where(ConstructionLead.tenant_id!=c.tenant)).first()
        other_lead=uuid4()
        connection.execute(insert(ConstructionLead.__table__).values(**lead_values(ids,id=other_lead)))
        base=dict(tenant_id=c.tenant,lead_id=c.lead.id,type='call',status='planned',timezone='UTC',
            created_by_user_id=c.user,created_at=c.now,updated_at=c.now,due_at=c.now-timedelta(days=5))
        for identifier in (second,first):
            connection.execute(insert(ConstructionActivity.__table__).values(**base,id=identifier))
        for changes in (
            dict(status='completed',completed_at=c.now),dict(status='cancelled',cancelled_at=c.now),
            dict(deleted_at=c.now),dict(tenant_id=foreign.tenant_id,lead_id=foreign.id),dict(lead_id=other_lead),
        ):
            data={**base,'id':uuid4(),'due_at':c.now-timedelta(days=30),**changes}
            connection.execute(insert(ConstructionActivity.__table__).values(**data))
    result=await read(c)
    assert result['next_action']['id']==str(first)
    c.session.sync.execute(update(ConstructionActivity.__table__).where(ConstructionActivity.id==first).values(status='completed',completed_at=c.now))
    c.session.sync.commit()
    c.session.sync.begin()
    assert (await read(c))['next_action']['id']==str(second)
    c.session.sync.execute(update(ConstructionActivity.__table__).where(ConstructionActivity.id==second).values(status='cancelled',cancelled_at=c.now))
    c.session.sync.commit()
    c.session.sync.begin()
    assert (await read(c))['next_action']['id']==str(c.row.id)
    c.session.commit.assert_not_called();c.session.rollback.assert_not_called()
    assert c.session.sync.execute(text('SELECT count(*) FROM terminal_service_audit')).scalar_one()==0


@pytest.mark.asyncio
async def test_real_read_never_flushes_pending_local_mutation_and_hides_foreign_or_archived_lead(detail_sql):
    c=detail_sql
    current=await c.detail.repository.get_active_lead(tenant_id=c.tenant,lead_id=c.lead.id)
    original=current.name
    current.name='Pending local edit must not flush'
    assert (await read(c))['name']==original
    foreign=c.session.sync.execute(select(ConstructionLead.id).where(ConstructionLead.tenant_id!=c.tenant)).scalar_one()
    with pytest.raises(ConstructionLeadReadNotFoundError):await read(c,lead_id=foreign)
    c.session.sync.execute(update(ConstructionLead.__table__).where(ConstructionLead.id==c.lead.id).values(deleted_at=c.now))
    c.session.sync.commit();c.session.sync.begin()
    with pytest.raises(ConstructionLeadReadNotFoundError):await read(c)
    c.session.commit.assert_not_called();c.session.rollback.assert_not_called()
