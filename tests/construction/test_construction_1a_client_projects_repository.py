from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from uuid import UUID,uuid4

import pytest
from sqlalchemy import create_engine,event,insert,select,update
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from database.models import ConstructionProject
from database.repositories.construction.construction_client_projects import ConstructionClientProjectsRepository,ConstructionClientProjectsCursor


TABLE=ConstructionProject.__table__


@pytest.fixture
def repository_case():
    c=SimpleNamespace(tenant=uuid4(),client=uuid4(),project=uuid4(),now=datetime(2026,10,13,12,tzinfo=timezone.utc))
    c.session=SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=Mock(return_value=SimpleNamespace(all=Mock(return_value=[]))))),
        add=Mock(),flush=AsyncMock(),commit=AsyncMock(),rollback=AsyncMock())
    c.repo=ConstructionClientProjectsRepository(c.session)
    return c


async def read(c,**changes):
    options=dict(tenant_id=c.tenant,client_id=c.client)
    options.update(changes)
    return await c.repo.list_projects_for_client(**options)


def no_writes(c):
    c.session.add.assert_not_called();c.session.flush.assert_not_called()
    c.session.commit.assert_not_called();c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_query_uses_direct_tenant_client_link_soft_delete_and_approved_order(repository_case):
    c=repository_case
    await read(c)
    statement=c.session.execute.await_args.args[0];compiled=statement.compile(dialect=postgresql.dialect());sql=str(compiled)
    assert 'construction_projects.tenant_id =' in sql and 'construction_projects.client_id =' in sql
    assert 'construction_projects.deleted_at IS NULL' in sql
    assert 'ORDER BY construction_projects.updated_at DESC, construction_projects.id DESC' in sql
    assert c.tenant in compiled.params.values() and c.client in compiled.params.values()
    assert statement._limit_clause.value==21 and 'FOR UPDATE' not in sql
    assert statement.get_execution_options()==dict(populate_existing=True,autoflush=False)
    assert 'email' not in sql and 'phone' not in sql
    no_writes(c)


@pytest.mark.asyncio
async def test_scope_is_applied_in_sql_before_limit(repository_case):
    c=repository_case
    await read(c,allowed_project_ids=(c.project,),limit=2)
    statement=c.session.execute.await_args.args[0];sql=str(statement.compile(dialect=postgresql.dialect()))
    assert 'construction_projects.id IN' in sql and statement._limit_clause.value==3
    no_writes(c)


@pytest.mark.asyncio
async def test_empty_authorized_scope_returns_empty_without_query(repository_case):
    c=repository_case
    assert await read(c,allowed_project_ids=())==[]
    c.session.execute.assert_not_called();no_writes(c)


@pytest.mark.asyncio
async def test_cursor_uses_descending_keyset_and_normalizes_offset(repository_case):
    c=repository_case
    cursor=ConstructionClientProjectsCursor(c.tenant,c.client,c.now.astimezone(timezone(timedelta(hours=3))),c.project)
    assert cursor.updated_at==c.now and cursor.updated_at.tzinfo is timezone.utc
    await read(c,after=cursor)
    statement=c.session.execute.await_args.args[0];sql=str(statement.compile(dialect=postgresql.dialect()))
    assert 'construction_projects.updated_at <' in sql and 'construction_projects.updated_at =' in sql
    assert 'construction_projects.id <' in sql
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('change',[dict(tenant_id='uuid'),dict(client_id='uuid'),dict(limit=True),dict(limit=0),dict(limit=101),
    dict(limit='20'),dict(after={}),dict(allowed_project_ids=[]),dict(allowed_project_ids=('uuid',))])
async def test_invalid_query_cannot_execute(repository_case,change):
    c=repository_case
    with pytest.raises(ValueError):await read(c,**change)
    c.session.execute.assert_not_called();no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['tenant','client','tampered'])
async def test_cursor_binding_and_frozen_invariants_are_rechecked(repository_case,failure):
    c=repository_case
    cursor=ConstructionClientProjectsCursor(uuid4() if failure=='tenant' else c.tenant,
        uuid4() if failure=='client' else c.client,c.now,c.project)
    if failure=='tampered':object.__setattr__(cursor,'updated_at',c.now.replace(tzinfo=None))
    with pytest.raises(ValueError):await read(c,after=cursor)
    c.session.execute.assert_not_called();no_writes(c)


@pytest.mark.asyncio
async def test_repository_does_not_catch_storage_failure_or_own_rollback(repository_case):
    c=repository_case;failure=RuntimeError('Storage failure');c.session.execute.side_effect=failure
    with pytest.raises(RuntimeError) as caught:await read(c)
    assert caught.value is failure;no_writes(c)


def test_constructor_and_cursor_contract(repository_case):
    c=repository_case
    with pytest.raises(ValueError):ConstructionClientProjectsRepository(object())
    for changes in [dict(tenant_id='uuid'),dict(client_id='uuid'),dict(project_id='uuid'),dict(updated_at=c.now.replace(tzinfo=None))]:
        with pytest.raises(ValueError):ConstructionClientProjectsCursor(**{**dict(tenant_id=c.tenant,client_id=c.client,project_id=c.project,updated_at=c.now),**changes})


@pytest.fixture
def real_case(repository_case):
    c=repository_case;c.foreign=uuid4();c.other_client=uuid4();c.foreign_client=uuid4();c.user=uuid4()
    engine=create_engine('sqlite://')
    @event.listens_for(engine,'connect')
    def enforce(connection,_record):connection.execute('PRAGMA foreign_keys=ON')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE tenants(id CHAR(32) PRIMARY KEY)')
        connection.exec_driver_sql('CREATE TABLE users(id CHAR(32) PRIMARY KEY)')
        connection.exec_driver_sql('CREATE TABLE construction_clients(id CHAR(32) PRIMARY KEY,tenant_id CHAR(32),UNIQUE(tenant_id,id))')
        for tenant in (c.tenant,c.foreign):connection.exec_driver_sql('INSERT INTO tenants VALUES(?)',(tenant.hex,))
        connection.exec_driver_sql('INSERT INTO users VALUES(?)',(c.user.hex,))
        for client,tenant in [(c.client,c.tenant),(c.other_client,c.tenant),(c.foreign_client,c.foreign)]:
            connection.exec_driver_sql('INSERT INTO construction_clients VALUES(?,?)',(client.hex,tenant.hex))
        TABLE.create(connection)
    c.engine=engine;c.sync=Session(engine)
    async def execute(statement):return c.sync.execute(statement)
    c.session.execute.side_effect=execute;c.repo=ConstructionClientProjectsRepository(c.session)
    c.flushes=[];event.listen(c.sync,'before_flush',lambda *args:c.flushes.append(True))
    try:yield c
    finally:c.sync.close();engine.dispose()


def project(c,id,**changes):
    data=dict(id=id,tenant_id=c.tenant,client_id=c.client,name='Project '+str(id),address_raw='Address',created_by=c.user,
        created_at=c.now,updated_at=c.now,status='draft',row_version=1)
    data.update(changes)
    return data


@pytest.mark.asyncio
async def test_real_pagination_keeps_tied_timestamps_without_gaps_and_excludes_unrelated_rows(real_case):
    c=real_case;ids=[UUID(int=value) for value in range(1,26)]
    with c.engine.begin() as connection:
        for id in ids:connection.execute(insert(TABLE).values(**project(c,id)))
        for data in [project(c,uuid4(),client_id=c.other_client),project(c,uuid4(),client_id=None),
            project(c,uuid4(),tenant_id=c.foreign,client_id=c.foreign_client),project(c,uuid4(),deleted_at=c.now)]:
            connection.execute(insert(TABLE).values(**data))
    first=await read(c)
    assert [row.id for row in first]==list(reversed(ids))[:21]
    cursor=ConstructionClientProjectsCursor(c.tenant,c.client,c.now,first[19].id)
    second=await read(c,after=cursor)
    assert [row.id for row in first[:20]]+[row.id for row in second]==list(reversed(ids))
    assert len(await read(c,limit=100))==25
    assert await read(c,client_id=uuid4())==[]
    assert await read(c,tenant_id=c.foreign,client_id=c.client)==[]
    no_writes(c);assert c.flushes==[]


@pytest.mark.asyncio
async def test_real_scope_restriction_applies_before_lookahead_and_timestamp_order(real_case):
    c=real_case;ids=[UUID(int=value) for value in range(1,5)]
    with c.engine.begin() as connection:
        for id in ids:connection.execute(insert(TABLE).values(**project(c,id,updated_at=c.now+timedelta(days=id.int))))
    rows=await read(c,limit=1,allowed_project_ids=(ids[0],ids[1]))
    assert [row.id for row in rows]==[ids[1],ids[0]]
    cursor=ConstructionClientProjectsCursor(c.tenant,c.client,c.now+timedelta(days=2),ids[1])
    assert [row.id for row in await read(c,limit=1,allowed_project_ids=(ids[0],ids[1]),after=cursor)]==[ids[0]]
    no_writes(c);assert c.flushes==[]


@pytest.mark.asyncio
async def test_completed_and_cancelled_projects_remain_readable_in_client_history(real_case):
    c=real_case
    with c.engine.begin() as connection:
        for number,status in enumerate(('draft','planning','active','completed','cancelled'),1):
            connection.execute(insert(TABLE).values(**project(c,UUID(int=number),status=status)))
    assert {row.status for row in await read(c)}=={'draft','planning','active','completed','cancelled'}
    no_writes(c)


@pytest.mark.asyncio
async def test_cached_row_is_refreshed_and_dirty_unrelated_data_is_not_flushed(real_case):
    c=real_case;id=uuid4()
    with c.engine.begin() as connection:connection.execute(insert(TABLE).values(**project(c,id)))
    cached=(await read(c))[0];cached.name='Unsaved value'
    with c.engine.begin() as connection:connection.execute(update(TABLE).where(TABLE.c.id==id).values(name='Database current value'))
    rows=await read(c)
    assert rows[0] is cached and cached.name=='Database current value'
    with c.engine.connect() as connection:assert connection.execute(select(TABLE.c.name).where(TABLE.c.id==id)).scalar_one()=='Database current value'
    assert c.flushes==[];no_writes(c)
