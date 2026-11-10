import json
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from services.construction.construction_clients import ConstructionClientService,ConstructionClientOperationError
from services.construction.construction_projects import ConstructionProjectService,ConstructionProjectOperationError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionAccessContext,ConstructionGrantContext,CONSTRUCTION_ROLE_PERMISSIONS
from test_construction_01_client_create_transaction import client_create_case,sql_client_create
from test_construction_01_project_create_transaction import create_case,real_create



def _with_project_currency_fixture(value):
    """Explicit organization currency for pre-existing isolated service doubles."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    target = getattr(value, "service", value)
    target.currency_reader = SimpleNamespace(
        session=target.session, get_for_tenant=AsyncMock(return_value="EUR"),
    )
    return value

@pytest.fixture
def correlation_case():
    c=SimpleNamespace(tenant=uuid4(),user=uuid4(),client_id=uuid4(),project_id=uuid4(),now=datetime(2026,10,14,tzinfo=timezone.utc),events=[],order=[])
    c.actor=SimpleNamespace(user_id=c.user,tenant_id=c.tenant,status='active')
    c.context=ConstructionAccessContext(c.user,c.tenant,('OWNER',),tuple(CONSTRUCTION_ROLE_PERMISSIONS['OWNER']),
        (ConstructionGrantContext('OWNER','tenant',c.tenant),))
    c.session=SimpleNamespace(in_transaction=lambda:True,commit=AsyncMock(side_effect=lambda:c.order.append('commit')),
        rollback=AsyncMock(side_effect=lambda:c.order.append('rollback')))
    c.client=SimpleNamespace(id=c.client_id,tenant_id=c.tenant,deleted_at=None,display_name='Client',client_type='person',phone=None,email=None,notes=None,row_version=1)
    c.project=SimpleNamespace(id=c.project_id,tenant_id=c.tenant,client_id=c.client_id,row_version=1,status='draft',
        name='Project',address_raw='Address',responsible_user_id=None,comment=None,deleted_at=None,
        address_formatted=None,country_code=None,region=None,city=None,postal_code=None,latitude=None,longitude=None,
        address_provider=None,provider_place_id=None,address_verification_status='pending')
    async def created_client(**kwargs): c.order.append('client_write');return c.client
    async def updated_client(**kwargs):
        c.order.append('client_write')
        for name,value in kwargs.items():
            if name!='client':setattr(c.client,name,value)
        c.client.row_version+=1
        return c.client
    async def created_project(**kwargs):
        c.order.append('project_write')
        for name,value in kwargs.items():
            if name!='tenant_id':setattr(c.project,name,value)
        return c.project
    async def updated_project(**kwargs):
        c.order.append('project_write')
        for name,value in kwargs.items():
            if name not in ('project','reset_address_verification'):setattr(c.project,name,value)
        c.project.row_version+=1
        return c.project
    async def event(**kwargs):c.order.append('audit');c.events.append(kwargs)
    c.event_repo=SimpleNamespace(session=c.session,create_event=AsyncMock(side_effect=event))
    c.client_repo=SimpleNamespace(session=c.session,create_client=AsyncMock(side_effect=created_client),
        get_active_client_for_update=AsyncMock(return_value=c.client),update_client=AsyncMock(side_effect=updated_client),
        soft_delete_client=AsyncMock(side_effect=updated_client),get_active_client=AsyncMock(return_value=c.client))
    c.project_repo=SimpleNamespace(session=c.session,create_project=AsyncMock(side_effect=created_project),
        get_active_project_for_update=AsyncMock(return_value=c.project),update_project=AsyncMock(side_effect=updated_project),
        set_project_status=AsyncMock(side_effect=updated_project))
    c.clients=ConstructionClientService(session=c.session,repository=c.client_repo,event_repository=c.event_repo,now_provider=lambda:c.now)
    c.projects=_with_project_currency_fixture(ConstructionProjectService(session=c.session,repository=c.project_repo,client_repository=c.client_repo,event_repository=c.event_repo))
    return c


async def create_client(c,**extra):
    return await c.clients.create_client(actor=c.actor,access_context=c.context,display_name='Client',client_type='person',
        phone=None,email=None,notes=None,trace_id='correlation-client',**extra)


async def update_client(c):
    return await c.clients.update_client(actor=c.actor,access_context=c.context,client_id=c.client.id,expected_row_version=c.client.row_version,display_name='Changed client',
        client_type='person',phone=None,email=None,notes=None,trace_id='correlation-client-update')


async def create_project(c,client_id='current',**extra):
    return await c.projects.create_project(actor=c.actor,access_context=c.context,name='Project',address_raw='Address',
        client_id=c.client_id if client_id=='current' else client_id,responsible_user_id=None,comment=None,
        trace_id='correlation-project',**extra)


async def update_project(c,**changes):
    data=dict(project_id=c.project.id,expected_row_version=c.project.row_version,name='Changed project',address_raw=c.project.address_raw,
        client_id=c.project.client_id,responsible_user_id=None,comment=None)
    data.update(changes)
    return await c.projects.update_project(actor=c.actor,access_context=c.context,**data,trace_id='correlation-project-update')


@pytest.mark.asyncio
async def test_client_create_update_archive_events_have_stable_identity(correlation_case):
    c=correlation_case
    await create_client(c);await update_client(c)
    await c.clients.archive_client(actor=c.actor,access_context=c.context,client_id=c.client.id,expected_row_version=c.client.row_version,trace_id='archive-client')
    assert [e['event_type'] for e in c.events]==['construction_client_created','construction_client_updated','construction_client_archived']
    assert all(e['payload']['related_client_id']==str(c.client_id) for e in c.events)
    assert c.client.deleted_at==c.now
    assert all(e['tenant_id']==c.tenant and e['user_id']==c.user and e['entity_id']==c.client_id for e in c.events)
    assert c.order==['client_write','audit','commit']*3


@pytest.mark.asyncio
async def test_project_events_use_persisted_client_and_reassignment_preserves_past_events(correlation_case):
    c=correlation_case
    await create_project(c)
    original_payload=dict(c.events[0]['payload'])
    new_client=uuid4()
    await update_project(c,client_id=new_client)
    await c.projects.transition_project_status(actor=c.actor,access_context=c.context,project_id=c.project.id,
        expected_row_version=2,next_status='planning',trace_id='project-planning')
    assert [e['payload']['related_client_id'] for e in c.events]==[str(c.client_id),str(new_client),str(new_client)]
    assert c.events[0]['payload']==original_payload
    assert c.events[1]['payload']['changes'][0]['field']=='name'
    assert any(x['field']=='client_id' and x['old_value']==str(c.client_id) and x['new_value']==str(new_client)
        for x in c.events[1]['payload']['changes'])
    assert c.events[2]['event_type']=='construction_project_status_changed'
    assert c.order==['project_write','audit','commit']*3


@pytest.mark.asyncio
async def test_unlinked_project_events_omit_marker_until_link_is_defined(correlation_case):
    c=correlation_case
    await create_project(c,client_id=None)
    await update_project(c)
    await c.projects.transition_project_status(actor=c.actor,access_context=c.context,project_id=c.project.id,
        expected_row_version=2,next_status='planning')
    assert len(c.events)==3 and all('related_client_id' not in e['payload'] for e in c.events)
    await update_project(c,client_id=c.client_id)
    assert c.events[-1]['payload']['related_client_id']==str(c.client_id)
    assert all('related_client_id' not in e['payload'] for e in c.events[:-1])


@pytest.mark.asyncio
async def test_unlinking_project_does_not_rewrite_original_correlation(correlation_case):
    c=correlation_case
    await create_project(c)
    await update_project(c,client_id=None)
    assert c.events[0]['payload']['related_client_id']==str(c.client_id)
    assert 'related_client_id' not in c.events[1]['payload']
    assert any(x['field']=='client_id' and x['old_value']==str(c.client_id) and x['new_value'] is None for x in c.events[1]['payload']['changes'])


@pytest.mark.asyncio
async def test_project_no_op_and_stale_paths_have_no_extra_audit(correlation_case):
    c=correlation_case
    await update_project(c,name=c.project.name)
    assert c.events==[]
    with pytest.raises(ConstructionRowVersionConflictError):await update_project(c,expected_row_version=99)
    await c.projects.transition_project_status(actor=c.actor,access_context=c.context,project_id=c.project.id,
        expected_row_version=1,next_status='draft')
    assert c.events==[]
    c.project_repo.update_project.assert_not_called();c.project_repo.set_project_status.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['client','project'])
@pytest.mark.parametrize('mode',[False,True])
async def test_caller_transaction_ownership_is_preserved(correlation_case,kind,mode):
    c=correlation_case
    await (create_client(c,manage_transaction=mode) if kind=='client' else create_project(c,manage_transaction=mode))
    assert c.events[0]['payload']['related_client_id']==str(c.client_id)
    assert c.session.commit.await_count==int(mode)
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['client','project'])
@pytest.mark.parametrize('failure',['tenant','id_type','missing_tenant'])
async def test_bad_persisted_resource_is_not_correlated_or_committed(correlation_case,kind,failure):
    c=correlation_case;resource=getattr(c,kind)
    if failure=='tenant':resource.tenant_id=uuid4()
    elif failure=='id_type':resource.id=str(resource.id)
    else:del resource.tenant_id
    with pytest.raises(ConstructionClientOperationError if kind=='client' else ConstructionProjectOperationError):
        await (create_client(c) if kind=='client' else create_project(c))
    assert c.events==[]
    c.session.commit.assert_not_called();c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['client','project'])
async def test_audit_failure_with_correlation_keeps_existing_rollback(correlation_case,kind):
    c=correlation_case
    c.event_repo.create_event.side_effect=RuntimeError('private-audit-error')
    with pytest.raises(ConstructionClientOperationError if kind=='client' else ConstructionProjectOperationError):
        await (create_client(c) if kind=='client' else create_project(c))
    assert c.event_repo.create_event.await_args.kwargs['payload']['related_client_id']==str(c.client_id)
    c.session.commit.assert_not_called();c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_sql_client_correlation_is_committed_with_domain_row(sql_client_create):
    c=sql_client_create
    c.sync.begin()
    client=await c.service.create_client(actor=c.actor,access_context=c.context,display_name='Client',client_type='person',
        phone=None,email=None,notes=None,manage_transaction=False)
    await c.session.commit()
    payload=json.loads(c.sync.execute(text('SELECT payload FROM client_test_audit')).scalar_one())
    assert payload['related_client_id']==str(client.id)
    assert c.sync.execute(text('SELECT count(*) FROM construction_clients')).scalar_one()==1


@pytest.mark.asyncio
async def test_sql_project_correlation_and_existing_events_are_not_backfilled(real_create):
    c=real_create
    linked_id=c.client_id
    c.service.client_repository=SimpleNamespace(get_active_client=AsyncMock(return_value=SimpleNamespace(id=linked_id,tenant_id=c.tenant)))
    with c.sql_session.begin():
        c.sql_session.execute(text('INSERT INTO construction_clients(id,tenant_id) VALUES(:id,:tenant)'),dict(id=linked_id.hex,tenant=c.tenant.hex))
        c.sql_session.execute(text('INSERT INTO tx_test_audit VALUES(:id,:event,:entity,:payload)'),
            dict(id='old-event',event='construction_project_created',entity='old-project',payload='{"operation":"create","changes":[],"reason":null}'))
        project=await c.service.create_project(actor=c.actor,access_context=c.context,name='Project',address_raw='Address',
            client_id=linked_id,responsible_user_id=None,comment=None,manage_transaction=False)
        expected_id=project.id
    saved=c.sql_session.execute(text('SELECT id,payload FROM tx_test_audit')).all()
    assert len(saved)==2
    old=next(p for i,p in saved if i=='old-event')
    new=next(p for i,p in saved if i!='old-event')
    assert 'related_client_id' not in json.loads(old)
    assert json.loads(new)['related_client_id']==str(linked_id)
    assert c.sql_session.execute(text('SELECT count(*) FROM construction_projects')).scalar_one()==1
    assert expected_id is not None


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['client','project'])
async def test_sql_flushed_correlated_audit_rolls_back_with_domain(kind,sql_client_create,real_create):
    c=sql_client_create if kind=='client' else real_create
    sync=c.sync if kind=='client' else c.sql_session
    c.events.fail_after_insert=True
    with pytest.raises(ConstructionClientOperationError if kind=='client' else ConstructionProjectOperationError):
        with sync.begin():
            if kind=='client':
                await c.service.create_client(actor=c.actor,access_context=c.context,display_name='Client',client_type='person',
                    phone=None,email=None,notes=None,manage_transaction=False)
            else:
                c.service.client_repository=SimpleNamespace(get_active_client=AsyncMock(return_value=SimpleNamespace(id=c.client_id,tenant_id=c.tenant)))
                sync.execute(text('INSERT INTO construction_clients(id,tenant_id) VALUES(:id,:tenant)'),dict(id=c.client_id.hex,tenant=c.tenant.hex))
                await c.service.create_project(actor=c.actor,access_context=c.context,name='Project',address_raw='Address',
                    client_id=c.client_id,responsible_user_id=None,comment=None,manage_transaction=False)
    assert sync.execute(text('SELECT count(*) FROM '+('construction_clients' if kind=='client' else 'construction_projects'))).scalar_one()==0
    assert sync.execute(text('SELECT count(*) FROM '+('client_test_audit' if kind=='client' else 'tx_test_audit'))).scalar_one()==0


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['client','project'])
@pytest.mark.parametrize('marker',['missing','foreign','noncanonical'])
async def test_conversion_collector_requires_canonical_selected_client_marker(correlation_case,kind,marker):
    from services.construction.construction_lead_conversion import _ConversionCreationAudit,ConstructionLeadConversionOperationError
    c=correlation_case
    collector=_ConversionCreationAudit(session=c.session,actor=c.actor,trace_id='convert',client_id=c.client_id)
    payload=dict(operation='create',changes=[],reason=None)
    if kind == 'project':
        payload.update(currency='EUR', currency_source='organization_default')
    if marker!='missing':payload['related_client_id']=str(uuid4()) if marker=='foreign' else c.client_id.hex
    with pytest.raises(ConstructionLeadConversionOperationError):
        await collector.create_event(event_type='construction_'+kind+'_created',tenant_id=c.tenant,user_id=c.user,
            entity_type='construction_'+kind,entity_id=c.client_id if kind=='client' else c.project_id,
            payload=payload,platform='api',trace_id='convert')
    assert collector.events==[]


@pytest.mark.asyncio
async def test_conversion_collector_binds_project_to_created_client_and_preserves_single_final_audit_contract(correlation_case):
    from services.construction.construction_lead_conversion import _ConversionCreationAudit,ConstructionLeadConversionOperationError
    c=correlation_case
    collector=_ConversionCreationAudit(session=c.session,actor=c.actor,trace_id='convert')
    for kind,identifier in [('client',c.client_id),('project',c.project_id)]:
        await collector.create_event(event_type='construction_'+kind+'_created',tenant_id=c.tenant,user_id=c.user,
            entity_type='construction_'+kind,entity_id=identifier,platform='api',trace_id='convert',
            payload=dict(operation='create',changes=[],reason=None,related_client_id=str(c.client_id),
                **({'currency':'EUR','currency_source':'organization_default'} if kind=='project' else {})))
    assert len(collector.events)==2 and c.events==[]
    assert collector.related_client_id==c.client_id
    with pytest.raises(ConstructionLeadConversionOperationError):await collector.create_event(**collector.events[0])
