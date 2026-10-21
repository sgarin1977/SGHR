from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.dialects import postgresql

from database.models import ConstructionActivity
from database.repositories.construction_activities import ConstructionActivityCursor, ConstructionActivityRepository
from services.construction_activity_rules import ConstructionActivityLinks, ConstructionActivitySchedule
from test_construction_1a_activity_model import TABLE, activity_db, values
from test_construction_1a_lead_model import lead_db
from test_construction_1a_lead_repository import AsyncSessionAdapter


@pytest.fixture
def repo_case():
    c=SimpleNamespace(tenant=uuid4(),activity=uuid4(),lead=uuid4(),user=uuid4(),
        now=datetime(2026,10,13,12,tzinfo=timezone.utc))
    c.result=SimpleNamespace(scalar_one_or_none=Mock(return_value=None),
        scalars=Mock(return_value=SimpleNamespace(all=Mock(return_value=[]))))
    c.session=SimpleNamespace(execute=AsyncMock(return_value=c.result),add=Mock(),flush=AsyncMock(),
        in_transaction=Mock(return_value=True),commit=AsyncMock(),rollback=AsyncMock())
    c.repo=ConstructionActivityRepository(c.session)
    return c


def caller_owns_transaction(c):
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


async def create(c,**changes):
    kwargs=dict(tenant_id=c.tenant,activity_type=" CALL ",links=ConstructionActivityLinks(lead_id=c.lead),
        schedule=ConstructionActivitySchedule(c.now,"Europe/Lisbon"),created_by_user_id=c.user,
        as_of=c.now.astimezone(timezone(timedelta(hours=3))),comment="  Note  ")
    kwargs.update(changes)
    return await c.repo.create_activity(**kwargs)


@pytest.mark.asyncio
async def test_creation_uses_domain_values_server_initial_state_and_one_flush(repo_case):
    c=repo_case
    row=await create(c,responsible_user_id=c.user)
    assert isinstance(row,ConstructionActivity)
    assert row.tenant_id==c.tenant and row.lead_id==c.lead and row.client_id is None and row.project_id is None
    assert row.type=="call" and row.status=="planned" and row.row_version==1
    assert row.due_at==c.now and row.timezone=="Europe/Lisbon" and row.created_by_user_id==c.user
    assert row.created_at==row.updated_at==c.now and row.created_at.tzinfo is timezone.utc
    assert row.result is row.completed_at is row.cancelled_at is row.deleted_at is None
    assert row.comment=="Note" and row.responsible_user_id==c.user
    c.session.add.assert_called_once_with(row)
    c.session.flush.assert_awaited_once()
    c.session.execute.assert_not_called()
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("comment",[None,"","   "])
async def test_empty_comment_and_unassigned_responsible_are_nullable(repo_case,comment):
    c=repo_case
    row=await create(c,comment=comment)
    assert row.comment is None and row.responsible_user_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["tenant","creator","responsible","kind","links","schedule","comment","time","naive","transaction"])
async def test_invalid_creation_cannot_add_or_flush(repo_case,failure):
    c=repo_case
    changes={}
    if failure in ("tenant","creator","responsible"):
        changes[{"tenant":"tenant_id","creator":"created_by_user_id","responsible":"responsible_user_id"}[failure]]="unparsed"
    elif failure=="kind": changes["activity_type"]="email"
    elif failure in ("links","schedule"): changes[failure]={}
    elif failure=="comment": changes["comment"]=123
    elif failure=="time": changes["as_of"]=None
    elif failure=="naive": changes["as_of"]=c.now.replace(tzinfo=None)
    else: c.session.in_transaction.return_value=False
    with pytest.raises(ValueError): await create(c,**changes)
    c.session.add.assert_not_called(); c.session.flush.assert_not_called(); c.session.execute.assert_not_called()
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["links","timezone","utc"])
async def test_frozen_values_are_revalidated_before_write(repo_case,failure):
    c=repo_case
    links=ConstructionActivityLinks(lead_id=c.lead)
    schedule=ConstructionActivitySchedule(c.now,"Europe/Lisbon")
    if failure=="links": object.__setattr__(links,"lead_id",None)
    elif failure=="timezone": object.__setattr__(schedule,"timezone","Europe/Unknown")
    else: object.__setattr__(schedule,"due_at",c.now.replace(tzinfo=None))
    with pytest.raises(ValueError): await create(c,links=links,schedule=schedule)
    c.session.add.assert_not_called(); c.session.flush.assert_not_called()


@pytest.mark.asyncio
async def test_flush_failure_propagates_for_caller_rollback(repo_case):
    c=repo_case
    failure=RuntimeError("flush failed")
    c.session.flush.side_effect=failure
    with pytest.raises(RuntimeError) as caught: await create(c)
    assert caught.value is failure
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("locked",[False,True])
async def test_read_filters_tenant_deleted_and_refreshes_identity_without_autoflush(repo_case,locked):
    c=repo_case
    method=c.repo.get_active_activity_for_update if locked else c.repo.get_active_activity
    await method(tenant_id=c.tenant,activity_id=c.activity)
    statement=c.session.execute.await_args.args[0]
    sql=str(statement.compile(dialect=postgresql.dialect()))
    assert "construction_activities.tenant_id =" in sql and "construction_activities.id =" in sql
    assert "construction_activities.deleted_at IS NULL" in sql
    assert ("FOR UPDATE" in sql)==locked
    assert statement.get_execution_options()["populate_existing"] is True
    assert statement.get_execution_options()["autoflush"] is False
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_next_action_filters_planned_non_deleted_and_has_no_future_only_cutoff(repo_case):
    c=repo_case
    await c.repo.get_next_action_for_lead(tenant_id=c.tenant,lead_id=c.lead)
    statement=c.session.execute.await_args.args[0]
    sql=str(statement.compile(dialect=postgresql.dialect()))
    assert "construction_activities.tenant_id =" in sql and "construction_activities.lead_id =" in sql
    assert "construction_activities.status =" in sql and "construction_activities.deleted_at IS NULL" in sql
    assert "ORDER BY construction_activities.due_at ASC, construction_activities.id ASC" in sql
    assert statement._limit_clause.value==1
    assert "due_at >" not in sql and "due_at <" not in sql
    c.session.add.assert_not_called(); c.session.flush.assert_not_called()
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_planned_list_has_conjunctive_parent_filters_ascending_cursor_and_lookahead(repo_case):
    c=repo_case
    client,project=uuid4(),uuid4()
    cursor=ConstructionActivityCursor(c.tenant,c.now,c.activity)
    await c.repo.list_planned_activities(tenant_id=c.tenant,after=cursor,lead_id=c.lead,
        client_id=client,project_id=project,responsible_user_id=c.user)
    statement=c.session.execute.await_args.args[0]
    compiled=statement.compile(dialect=postgresql.dialect())
    sql=str(compiled)
    assert all(value in compiled.params.values() for value in (c.tenant,c.lead,client,project,c.user,c.now,c.activity,"planned"))
    assert "construction_activities.due_at >" in sql and "construction_activities.id >" in sql
    assert "ORDER BY construction_activities.due_at ASC, construction_activities.id ASC" in sql
    assert statement._limit_clause.value==21 and "OFFSET" not in sql
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit,lookahead",[(1,2),(20,21),(100,101)])
async def test_page_limit_boundaries(repo_case,limit,lookahead):
    c=repo_case
    assert await c.repo.list_planned_activities(tenant_id=c.tenant,limit=limit)==[]
    assert c.session.execute.await_args.args[0]._limit_clause.value==lookahead


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["limit_zero","limit_max","limit_bool","limit_text","cursor_shape","cursor_tenant",
    "cursor_tamper","lead_id","client_id","project_id","responsible_user_id","tenant_id"])
async def test_invalid_list_and_cursor_cannot_execute(repo_case,failure):
    c=repo_case
    kwargs=dict(tenant_id=c.tenant)
    if failure.startswith("limit_"): kwargs["limit"]={"limit_zero":0,"limit_max":101,"limit_bool":True,"limit_text":"20"}[failure]
    elif failure=="cursor_shape": kwargs["after"]={}
    elif failure=="cursor_tenant": kwargs["after"]=ConstructionActivityCursor(uuid4(),c.now,c.activity)
    elif failure=="cursor_tamper":
        cursor=ConstructionActivityCursor(c.tenant,c.now,c.activity)
        object.__setattr__(cursor,"due_at",c.now.replace(tzinfo=None)); kwargs["after"]=cursor
    else: kwargs[failure]="unparsed"
    with pytest.raises(ValueError): await c.repo.list_planned_activities(**kwargs)
    c.session.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("method,field",[("get_active_activity","activity_id"),("get_active_activity","tenant_id"),
    ("get_active_activity_for_update","activity_id"),("get_next_action_for_lead","lead_id"),
    ("get_next_action_for_lead","tenant_id")])
async def test_read_identifiers_must_be_uuid(repo_case,method,field):
    c=repo_case
    kwargs=dict(tenant_id=c.tenant,**({"lead_id":c.lead} if "next" in method else {"activity_id":c.activity}))
    kwargs[field]="invalid"
    with pytest.raises(ValueError): await getattr(c.repo,method)(**kwargs)
    c.session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_lock_requires_active_transaction_but_plain_read_does_not_own_one(repo_case):
    c=repo_case
    c.session.in_transaction.return_value=False
    with pytest.raises(ValueError): await c.repo.get_active_activity_for_update(tenant_id=c.tenant,activity_id=c.activity)
    c.session.execute.assert_not_called()
    assert await c.repo.get_active_activity(tenant_id=c.tenant,activity_id=c.activity) is None
    caller_owns_transaction(c)


def test_cursor_is_frozen_and_converts_explicit_offset_to_utc(repo_case):
    c=repo_case
    cursor=ConstructionActivityCursor(c.tenant,c.now.astimezone(timezone(timedelta(hours=3))),c.activity)
    assert cursor.due_at==c.now and cursor.due_at.tzinfo is timezone.utc
    with pytest.raises(FrozenInstanceError): cursor.activity_id=uuid4()


@pytest.fixture
def sql_repo(activity_db):
    engine,ids=activity_db
    c=SimpleNamespace(engine=engine,ids=ids,tenant=ids["a"],lead=ids["lead_a"],user=ids["user"],
        now=datetime(2026,10,13,12,tzinfo=timezone.utc))
    c.session=AsyncSessionAdapter(engine)
    c.repo=ConstructionActivityRepository(c.session)
    try: yield c
    finally: c.session.sync.close()


@pytest.mark.asyncio
async def test_actual_creation_is_tenant_scoped_and_caller_can_rollback(sql_repo):
    c=sql_repo
    c.session.sync.begin()
    activity=await create(c)
    assert isinstance(activity.id,UUID) and activity.row_version==1
    assert await c.repo.get_active_activity(tenant_id=c.tenant,activity_id=activity.id) is activity
    assert await c.repo.get_active_activity(tenant_id=c.ids["b"],activity_id=activity.id) is None
    caller_owns_transaction(c)
    c.session.sync.rollback()
    assert c.session.sync.execute(select(TABLE.c.id)).all()==[]


@pytest.mark.asyncio
async def test_real_next_action_includes_overdue_uses_id_tie_breaker_and_ignores_other_states_and_tenants(sql_repo):
    c=sql_repo
    with c.engine.begin() as connection:
        for number,delta in [(7,1),(4,0),(3,0),(2,-1)]:
            connection.execute(insert(TABLE).values(**values(c.ids,id=UUID(int=number),due_at=c.now+timedelta(days=delta))))
        for number,status,deleted in [(8,"completed",False),(9,"cancelled",False),(10,"planned",True)]:
            connection.execute(insert(TABLE).values(**values(c.ids,id=UUID(int=number),status=status,
                due_at=c.now-timedelta(days=4),completed_at=c.now if status=="completed" else None,
                cancelled_at=c.now if status=="cancelled" else None,deleted_at=c.now if deleted else None)))
        connection.execute(insert(TABLE).values(**values(c.ids,id=UUID(int=11),tenant_id=c.ids["b"],
            lead_id=c.ids["lead_b"],due_at=c.now-timedelta(days=5))))
    assert (await c.repo.get_next_action_for_lead(tenant_id=c.tenant,lead_id=c.lead)).id==UUID(int=2)
    first=await c.repo.list_planned_activities(tenant_id=c.tenant,limit=2,lead_id=c.lead)
    assert [item.id.int for item in first]==[2,3,4]
    second=await c.repo.list_planned_activities(tenant_id=c.tenant,limit=2,lead_id=c.lead,
        after=ConstructionActivityCursor(c.tenant,c.now,UUID(int=3)))
    assert [item.id.int for item in second]==[4,7]
    assert await c.repo.get_next_action_for_lead(tenant_id=c.tenant,lead_id=c.ids["lead_b"]) is None
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_next_action_moves_on_after_completed_cancelled_or_archived_and_finally_becomes_none(sql_repo):
    c=sql_repo
    with c.engine.begin() as connection:
        for number in (1,2,3): connection.execute(insert(TABLE).values(**values(c.ids,id=UUID(int=number),due_at=c.now)))
    assert (await c.repo.get_next_action_for_lead(tenant_id=c.tenant,lead_id=c.lead)).id.int==1
    for number,fields in [(1,dict(status="completed",completed_at=c.now)),
        (2,dict(status="cancelled",cancelled_at=c.now)),(3,dict(deleted_at=c.now))]:
        with c.engine.begin() as connection: connection.execute(update(TABLE).where(TABLE.c.id==UUID(int=number)).values(**fields))
        result=await c.repo.get_next_action_for_lead(tenant_id=c.tenant,lead_id=c.lead)
        assert (result.id.int if result else None)==(number+1 if number<3 else None)


@pytest.mark.asyncio
async def test_actual_locked_read_refreshes_stale_object_without_flushing_pending_python_edits(sql_repo):
    c=sql_repo
    identifier=uuid4()
    with c.engine.begin() as connection: connection.execute(insert(TABLE).values(**values(c.ids,id=identifier,comment="Initial")))
    stale=c.session.sync.get(ConstructionActivity,identifier)
    stale.comment="Must not flush"
    with c.engine.begin() as connection:
        connection.execute(update(TABLE).where(TABLE.c.id==identifier).values(comment="Committed elsewhere",row_version=2))
    current=await c.repo.get_active_activity_for_update(tenant_id=c.tenant,activity_id=identifier)
    assert current is stale and current.comment=="Committed elsewhere" and current.row_version==2
    assert not c.session.sync.is_modified(current)
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_terminal_activities_are_available_by_id_but_archived_ones_are_hidden(sql_repo):
    c=sql_repo
    with c.engine.begin() as connection:
        connection.execute(insert(TABLE).values(**values(c.ids,id=UUID(int=1),status="completed",completed_at=c.now)))
        connection.execute(insert(TABLE).values(**values(c.ids,id=UUID(int=2),deleted_at=c.now)))
    assert (await c.repo.get_active_activity(tenant_id=c.tenant,activity_id=UUID(int=1))).status=="completed"
    assert await c.repo.get_active_activity(tenant_id=c.tenant,activity_id=UUID(int=2)) is None
