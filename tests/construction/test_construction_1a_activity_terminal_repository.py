from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.dialects import postgresql

from database.models import ConstructionActivity
from database.repositories.construction.construction_activities import ConstructionActivityRepository
from test_construction_1a_activity_model import TABLE, activity_db, values
from test_construction_1a_lead_model import lead_db
from test_construction_1a_lead_repository import AsyncSessionAdapter


@pytest.fixture
def statement_case():
    expected=object()
    response=SimpleNamespace(scalar_one_or_none=Mock(return_value=expected))
    session=SimpleNamespace(execute=AsyncMock(return_value=response),add=Mock(),flush=AsyncMock(),
        in_transaction=Mock(return_value=True),commit=AsyncMock(),rollback=AsyncMock())
    return SimpleNamespace(tenant=uuid4(),activity=uuid4(),now=datetime(2026,10,13,12,tzinfo=timezone.utc),
        session=session,repo=ConstructionActivityRepository(session),expected=expected)


async def finish(c,operation="complete",**changes):
    data=dict(tenant_id=c.tenant,activity_id=c.activity,expected_row_version=1,as_of=c.now,result=" Result ")
    data.update(changes)
    return await getattr(c.repo,operation+"_activity")(**data)


def transaction_owned_by_caller(c):
    c.session.commit.assert_not_called(); c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,status",[("complete","completed"),("cancel","cancelled")])
async def test_postgresql_update_has_tenant_version_planned_and_terminal_timestamp_guards(statement_case,operation,status):
    c=statement_case
    assert await finish(c,operation,as_of=c.now.astimezone(timezone(timedelta(hours=3)))) is c.expected
    statement=c.session.execute.await_args.args[0]
    compiled=statement.compile(dialect=postgresql.dialect()); sql=str(compiled)
    assert sql.startswith("UPDATE construction_activities SET ")
    for field in ("tenant_id","id","row_version","status"):
        assert "construction_activities."+field+" =" in str(statement.whereclause)
    for field in ("deleted_at","completed_at","cancelled_at"):
        assert "construction_activities."+field+" IS NULL" in sql
    assert "RETURNING construction_activities.id" in sql
    assert {column.key for column in statement._values}=={
        "status","result","completed_at","cancelled_at","updated_at","row_version"}
    assert status in compiled.params.values() and "planned" in compiled.params.values()
    assert c.now in compiled.params.values() and "Result" in compiled.params.values()
    assert statement.get_execution_options()["populate_existing"] is True
    assert statement.get_execution_options()["autoflush"] is False
    c.session.add.assert_not_called(); c.session.flush.assert_not_called()
    transaction_owned_by_caller(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation",["complete","cancel"])
@pytest.mark.parametrize("version",[None,False,True,0,-1,1.0,"1",2**63-1,2**63])
async def test_invalid_versions_do_not_execute_sql(statement_case,operation,version):
    c=statement_case
    with pytest.raises(ValueError): await finish(c,operation,expected_row_version=version)
    c.session.execute.assert_not_called(); transaction_owned_by_caller(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation",["complete","cancel"])
@pytest.mark.parametrize("failure",["tenant","activity","clock","naive","transaction","result"])
async def test_invalid_context_result_and_clock_do_not_execute_sql(statement_case,operation,failure):
    c=statement_case
    changes={}
    if failure in ("tenant","activity"): changes[failure+"_id"]="unparsed UUID"
    elif failure=="clock": changes["as_of"]=None
    elif failure=="naive": changes["as_of"]=c.now.replace(tzinfo=None)
    elif failure=="result": changes["result"]={"unapproved":"structure"}
    else: c.session.in_transaction.return_value=False
    with pytest.raises(ValueError): await finish(c,operation,**changes)
    c.session.execute.assert_not_called(); transaction_owned_by_caller(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation",["complete","cancel"])
async def test_database_failure_propagates_and_rollback_belongs_to_caller(statement_case,operation):
    c=statement_case
    failure=RuntimeError("database failure")
    c.session.execute.side_effect=failure
    with pytest.raises(RuntimeError) as caught: await finish(c,operation)
    assert caught.value is failure
    transaction_owned_by_caller(c)


@pytest.fixture
def sql_terminal(activity_db):
    engine,ids=activity_db
    c=SimpleNamespace(engine=engine,ids=ids,tenant=ids["a"],activity=uuid4(),
        now=datetime(2026,10,13,12,tzinfo=timezone.utc))
    with engine.begin() as connection:
        connection.execute(insert(TABLE).values(**values(ids,id=c.activity,comment="Preserve comment",
            responsible_user_id=ids["user"],created_at=c.now-timedelta(days=1),updated_at=c.now-timedelta(days=1))))
        connection.exec_driver_sql("CREATE TABLE activity_terminal_checkpoint(id TEXT PRIMARY KEY)")
    c.session=AsyncSessionAdapter(engine); c.repo=ConstructionActivityRepository(c.session)
    try: yield c
    finally: c.session.sync.close()


def row(c):
    return dict(c.session.sync.execute(select(TABLE).where(TABLE.c.id==c.activity)).mappings().one())


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,status,terminal",[("complete","completed","completed_at"),("cancel","cancelled","cancelled_at")])
async def test_actual_finalization_refreshes_identity_preserves_business_fields_and_removes_next_action(sql_terminal,operation,status,terminal):
    c=sql_terminal
    before=row(c)
    loaded=c.session.sync.get(ConstructionActivity,c.activity)
    assert (await c.repo.get_next_action_for_lead(tenant_id=c.tenant,lead_id=c.ids["lead_a"])).id==c.activity
    result=await finish(c,operation)
    assert result is loaded and result.status==status and result.row_version==2 and result.result=="Result"
    after=row(c)
    changed={"status","result","completed_at","cancelled_at","row_version","updated_at"}
    assert {key:after[key] for key in before if key not in changed}=={key:before[key] for key in before if key not in changed}
    assert after[terminal].replace(tzinfo=timezone.utc)==c.now
    assert after["cancelled_at" if terminal=="completed_at" else "completed_at"] is None
    assert after["updated_at"].replace(tzinfo=timezone.utc)==c.now
    assert await c.repo.get_next_action_for_lead(tenant_id=c.tenant,lead_id=c.ids["lead_a"]) is None
    transaction_owned_by_caller(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation",["complete","cancel"])
@pytest.mark.parametrize("result",[None,"","   "])
async def test_nullable_result_is_supported_without_required_completion_text_or_cancellation_reason(sql_terminal,operation,result):
    c=sql_terminal
    row(c)
    assert (await finish(c,operation,result=result)).result is None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation",["complete","cancel"])
@pytest.mark.parametrize("failure",["foreign_tenant","missing","stale","deleted","completed","cancelled"])
async def test_failed_preconditions_return_none_and_preserve_every_field(sql_terminal,operation,failure):
    c=sql_terminal
    changes={}; stored={}
    if failure=="foreign_tenant": changes["tenant_id"]=c.ids["b"]
    elif failure=="missing": changes["activity_id"]=uuid4()
    elif failure=="stale": stored["row_version"]=2
    elif failure=="deleted": stored["deleted_at"]=c.now
    else:
        stored.update(status=failure,**{failure+"_at":c.now})
    if stored:
        with c.engine.begin() as connection:
            connection.execute(update(TABLE).where(TABLE.c.id==c.activity).values(**stored))
    before=row(c)
    assert await finish(c,operation,**changes) is None
    assert row(c)==before
    transaction_owned_by_caller(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("first",["complete","cancel"])
@pytest.mark.parametrize("second",["complete","cancel"])
async def test_terminal_row_cannot_be_completed_cancelled_or_incremented_again(sql_terminal,first,second):
    c=sql_terminal
    row(c)
    assert (await finish(c,first)).row_version==2
    before=row(c)
    assert await finish(c,second,expected_row_version=2) is None
    assert row(c)==before


@pytest.mark.asyncio
@pytest.mark.parametrize("operation",["complete","cancel"])
async def test_stale_python_edits_are_not_flushed_and_latest_database_record_is_returned(sql_terminal,operation):
    c=sql_terminal
    stale=c.session.sync.get(ConstructionActivity,c.activity)
    stale.comment="Do not persist this pending edit"
    with c.engine.begin() as connection:
        connection.execute(update(TABLE).where(TABLE.c.id==c.activity).values(row_version=2,comment="Committed elsewhere"))
    result=await finish(c,operation,expected_row_version=2)
    assert result is stale and result.row_version==3 and result.comment=="Committed elsewhere"
    assert not c.session.sync.is_modified(result)
    transaction_owned_by_caller(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation",["complete","cancel"])
async def test_caller_rollback_restores_terminal_state_version_and_later_work(sql_terminal,operation):
    c=sql_terminal
    before=row(c)
    await finish(c,operation)
    c.session.sync.execute(text("INSERT INTO activity_terminal_checkpoint VALUES(:id)"),dict(id=str(c.activity)))
    c.session.sync.rollback()
    assert row(c)==before
    assert c.session.sync.execute(text("SELECT count(*) FROM activity_terminal_checkpoint")).scalar_one()==0
    transaction_owned_by_caller(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation",["complete","cancel"])
async def test_largest_incrementable_bigint_advances_once(sql_terminal,operation):
    c=sql_terminal
    with c.engine.begin() as connection:
        connection.execute(update(TABLE).where(TABLE.c.id==c.activity).values(row_version=2**63-2))
    row(c)
    assert (await finish(c,operation,expected_row_version=2**63-2)).row_version==2**63-1
