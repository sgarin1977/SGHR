from types import SimpleNamespace

import pytest
from sqlalchemy import select,update,text

from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction_activity_rules import ACTIVITY_RESULT_UNSET
from services.construction_activity_terminal import ConstructionActivityTerminalCommand,ConstructionActivityTerminalOperationError
from test_construction_1a_activity_terminal_repository import statement_case,sql_terminal as repository_sql_terminal
from test_construction_1a_activity_terminal_service import terminal_case,sql_terminal
from test_construction_1a_activity_terminal_workflow import workflow_case,sql_workflow
from test_construction_1a_activity_model import activity_db,TABLE
from test_construction_1a_lead_model import lead_db


@pytest.fixture
def result_case(workflow_case):
    c=workflow_case;c.row.result='  Preserve exact stored text  '
    for name in ('complete_activity','cancel_activity'):
        original=getattr(c.repo,name).side_effect
        async def preserve(original=original,**kw):
            if kw['result'] is ACTIVITY_RESULT_UNSET:kw['result']=c.row.result
            return await original(**kw)
        getattr(c.repo,name).side_effect=preserve
    return c


async def run(c,operation='complete',**changes):
    options=dict(actor=c.actor,access_context=c.context,activity_id=c.row.id,expected_row_version=4,idempotency_key='result-presence-key')
    options.update(changes)
    return await getattr(c.workflow,operation+'_activity')(**options)


def test_command_distinguishes_omission_explicit_null_and_text():
    omitted=ConstructionActivityTerminalCommand(4)
    assert omitted.result is ACTIVITY_RESULT_UNSET and omitted.result_provided is False
    for value,expected in [(None,None),('',None),('   ',None),(' Result ','Result')]:
        command=ConstructionActivityTerminalCommand(4,value)
        assert command.result_provided is True and command.result==expected


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['complete','cancel'])
async def test_omitted_result_preserves_value_and_replays_original_snapshot_without_audit_duplication(result_case,operation):
    c=result_case;old=c.row.result
    first=await run(c,operation)
    assert first['result']==old and c.row.result==old and first['row_version']==5
    assert c.events.create_event.await_args.kwargs['payload']['result_changed'] is False
    c.row.result='Changed later';c.row.row_version=6
    assert await run(c,operation)==first
    c.events.create_event.assert_awaited_once()
    getattr(c.repo,operation+'_activity').assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['complete','cancel'])
@pytest.mark.parametrize('value,expected',[(None,None),('','' or None),(' New result ','New result')])
async def test_supplied_result_keeps_existing_nullable_text_contract(result_case,operation,value,expected):
    c=result_case
    response=await run(c,operation,result=value)
    assert response['result']==expected and c.events.create_event.await_args.kwargs['payload']['result_changed'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['complete','cancel'])
@pytest.mark.parametrize('first_omitted',[True,False])
async def test_omission_and_explicit_null_are_different_commands_for_the_same_key(result_case,operation,first_omitted):
    c=result_case
    await run(c,operation,**({} if first_omitted else {'result':None}))
    with pytest.raises(ApiIdempotencyKeyReusedError):await run(c,operation,**({'result':None} if first_omitted else {}))
    assert c.session.commit.await_count==1 and c.events.create_event.await_count==1


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['complete','cancel'])
async def test_repository_omission_excludes_result_from_sql_update(statement_case,operation):
    c=statement_case
    await getattr(c.repo,operation+'_activity')(tenant_id=c.tenant,activity_id=c.activity,expected_row_version=1,as_of=c.now)
    statement=c.session.execute.await_args.args[0]
    assert 'result' not in {column.key for column in statement._values}
    assert {column.key for column in statement._values}=={'status','completed_at','cancelled_at','updated_at','row_version'}
    c.session.commit.assert_not_called();c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['complete','cancel'])
async def test_actual_repository_preserves_untrimmed_prior_result_and_can_roll_back(repository_sql_terminal,operation):
    c=repository_sql_terminal;old='  Keep database value exactly  '
    with c.engine.begin() as connection:connection.execute(update(TABLE).where(TABLE.c.id==c.activity).values(result=old))
    c.session.sync.begin()
    row=await getattr(c.repo,operation+'_activity')(tenant_id=c.tenant,activity_id=c.activity,expected_row_version=1,as_of=c.now)
    assert row.result==old and row.row_version==2
    c.session.sync.rollback()
    with c.engine.connect() as connection:
        stored=connection.execute(select(TABLE).where(TABLE.c.id==c.activity)).mappings().one()
        assert stored['result']==old and stored['status']=='planned' and stored['row_version']==1


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['complete','cancel'])
async def test_actual_workflow_commits_preserved_result_and_single_audit_then_replays(sql_workflow,operation):
    c=sql_workflow;old='  Exact existing result  '
    with c.session.sync.get_bind().begin() as connection:connection.execute(update(TABLE).where(TABLE.c.id==c.row.id).values(result=old))
    first=await run(c,operation)
    assert first['result']==old
    c.session.sync.begin();assert await run(c,operation)==first
    with c.session.sync.get_bind().connect() as connection:
        row=connection.execute(select(TABLE).where(TABLE.c.id==c.row.id)).mappings().one()
        assert row['result']==old and row['row_version']==5
        for table in ('terminal_service_audit','lead_test_idempotency'):
            assert connection.execute(text('SELECT count(*) FROM '+table)).scalar_one()==1


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['complete','cancel'])
@pytest.mark.parametrize('failure',['audit','completion','commit'])
async def test_actual_workflow_rolls_back_all_changes_and_preserves_old_result_on_failure(sql_workflow,operation,failure):
    c=sql_workflow;old='Prior result'
    with c.session.sync.get_bind().begin() as connection:connection.execute(update(TABLE).where(TABLE.c.id==c.row.id).values(result=old))
    if failure=='audit':c.events.fail=True
    elif failure=='completion':c.records.fail_complete=True
    else:c.session.commit.side_effect=RuntimeError('private commit error')
    with pytest.raises(ConstructionActivityTerminalOperationError):await run(c,operation)
    assert c.audit_insert_seen is True
    with c.session.sync.get_bind().connect() as connection:
        row=connection.execute(select(TABLE).where(TABLE.c.id==c.row.id)).mappings().one()
        assert row['result']==old and row['row_version']==4 and row['status']=='planned'
        for table in ('terminal_service_audit','lead_test_idempotency'):
            assert connection.execute(text('SELECT count(*) FROM '+table)).scalar_one()==0
