import json
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm.attributes import set_committed_value

from database.models import ConstructionActivity
from database.repositories.construction_activities import ConstructionActivityRepository
from database.repositories.construction_leads import ConstructionLeadRepository
from services.construction_activity_terminal import (
    ConstructionActivityTerminalCommand, ConstructionActivityTerminalService,
    ConstructionActivityTerminalNotFoundError, ConstructionActivityTerminalOperationError,
    build_construction_activity_terminal_service,
)
from services.construction_activity_rules import ConstructionActivityLinks, ConstructionActivityValidationError, ConstructionActivityTransitionError
from services.construction_foundation import ConstructionRowVersionConflictError
from services.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from test_construction_1a_activity_model import TABLE, activity_db, values
from test_construction_1a_lead_model import lead_db
from test_construction_1a_lead_repository import AsyncSessionAdapter


@pytest.fixture
def terminal_case():
    c=SimpleNamespace(tenant=uuid4(),user=uuid4(),now=datetime(2026,10,13,12,tzinfo=timezone.utc),order=[])
    c.actor=SimpleNamespace(tenant_id=c.tenant,user_id=c.user,status="active")
    c.context=ConstructionAccessContext(c.user,c.tenant,("OWNER",),tuple(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"]),
        (ConstructionGrantContext("OWNER","tenant",c.tenant),))
    c.session=SimpleNamespace(in_transaction=lambda:True,no_autoflush=nullcontext(),commit=AsyncMock(),rollback=AsyncMock(),
        execute=AsyncMock(),add=Mock(),flush=AsyncMock())
    c.grant=SimpleNamespace(role="OWNER",scope_type="tenant",scope_id=c.tenant,status="active",expires_at=None)
    c.access=SimpleNamespace(session=c.session,has_active_platform_membership=AsyncMock(return_value=True),
        list_active_grants=AsyncMock(return_value=[c.grant]))
    for name in ("lead","client","project"):
        row=SimpleNamespace(id=uuid4(),tenant_id=c.tenant,deleted_at=None)
        if name == "lead": row.converted_client_id = None
        if name == "project": row.client_id = None
        setattr(c,name,row)
        async def get(name=name,row=row,**kwargs): c.order.append(name+"_lock"); return row
        setattr(c,name+"_repo",SimpleNamespace(session=c.session,
            **{"get_active_"+name+"_for_update":AsyncMock(side_effect=get)}))
    c.row=SimpleNamespace(id=uuid4(),tenant_id=c.tenant,status="planned",row_version=4,deleted_at=None,
        type="call",lead_id=c.lead.id,client_id=None,project_id=None,due_at=c.now,timezone="Europe/Lisbon",
        responsible_user_id=c.user,comment="Private comment",result=None,completed_at=None,cancelled_at=None,
        created_by_user_id=c.user,created_at=c.now,updated_at=c.now)
    async def find(**kwargs): return {key:getattr(c.row,key) for key in ("id","lead_id","client_id","project_id")}
    async def lock(**kwargs): c.order.append("activity_lock"); return c.row
    async def mutate(operation,**kwargs):
        c.order.append(operation)
        return SimpleNamespace(**{**vars(c.row),"status":"completed" if operation=="complete" else "cancelled",
            "row_version":kwargs["expected_row_version"]+1,"result":kwargs["result"],"updated_at":kwargs["as_of"],
            "completed_at":kwargs["as_of"] if operation=="complete" else None,
            "cancelled_at":kwargs["as_of"] if operation=="cancel" else None})
    async def complete(**kwargs):return await mutate("complete",**kwargs)
    async def cancel(**kwargs):return await mutate("cancel",**kwargs)
    c.repo=SimpleNamespace(session=c.session,find_active_activity_links=AsyncMock(side_effect=find),
        get_active_activity_for_update_by_links=AsyncMock(side_effect=lock),
        complete_activity=AsyncMock(side_effect=complete),cancel_activity=AsyncMock(side_effect=cancel))
    async def audit(**kwargs):c.order.append("audit")
    c.events=SimpleNamespace(session=c.session,create_event=AsyncMock(side_effect=audit))
    c.service=ConstructionActivityTerminalService(session=c.session,repository=c.repo,lead_repository=c.lead_repo,
        client_repository=c.client_repo,project_repository=c.project_repo,access_repository=c.access,event_repository=c.events,
        now_provider=lambda:c.now)
    return c


async def run(c,operation="complete",**changes):
    options=dict(actor=c.actor,access_context=c.context,activity_id=c.row.id,
        command=ConstructionActivityTerminalCommand(4," Private result "),trace_id="terminal-test")
    options.update(changes)
    return await getattr(c.service,operation+"_activity")(**options)


def caller_owns_transaction(c):
    c.session.commit.assert_not_called(); c.session.rollback.assert_not_called()


def no_writes(c):
    c.repo.complete_activity.assert_not_called();c.repo.cancel_activity.assert_not_called();c.events.create_event.assert_not_called()
    caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,status,field",[("complete","completed","completed_at"),("cancel","cancelled","cancelled_at")])
async def test_terminal_change_uses_fixed_lock_order_version_and_one_audit_without_free_text(terminal_case,operation,status,field):
    c=terminal_case
    result=await run(c,operation)
    assert result.status==status and result.row_version==5 and result.result=="Private result"
    assert c.order==["lead_lock","activity_lock",operation,"audit"]
    assert c.access.has_active_platform_membership.await_count==3
    event=c.events.create_event.await_args.kwargs
    assert event==dict(event_type="construction_activity_"+status,tenant_id=c.tenant,user_id=c.user,
        entity_type="construction_activity",entity_id=c.row.id,platform="api",trace_id="terminal-test",
        payload={"operation":operation,"reason":None,"changes":[
            {"field":"status","old_value":"planned","new_value":status},
            {"field":field,"old_value":None,"new_value":c.now.isoformat()}],"result_changed":True})
    assert "Private" not in json.dumps(event["payload"])
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_project_manager_scope_filters_lookup_and_does_not_depend_on_responsible_identity(terminal_case):
    c=terminal_case
    c.row.lead_id=None;c.row.project_id=c.project.id;c.row.responsible_user_id=uuid4()
    c.grant.role,c.grant.scope_type,c.grant.scope_id="PROJECT_MANAGER","project",c.project.id
    c.context=ConstructionAccessContext(c.user,c.tenant,("PROJECT_MANAGER",),tuple(CONSTRUCTION_ROLE_PERMISSIONS["PROJECT_MANAGER"]),
        (ConstructionGrantContext("PROJECT_MANAGER","project",c.project.id),))
    assert (await run(c)).status=="completed"
    assert c.repo.find_active_activity_links.await_args.kwargs["allowed_project_ids"]==(c.project.id,)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["inactive","permission","observer","wrong_tenant","wrong_user","document"])
async def test_initial_denial_precedes_scope_lookup(terminal_case,failure):
    c=terminal_case
    if failure=="inactive":c.actor.status="blocked"
    else:
        role="OBSERVER" if failure=="observer" else "OWNER"
        c.context=ConstructionAccessContext(uuid4() if failure=="wrong_user" else c.user,
            uuid4() if failure=="wrong_tenant" else c.tenant,(role,),
            () if failure=="permission" else ("construction.projects.edit",),
            (ConstructionGrantContext(role,"document" if failure=="document" else "tenant",c.tenant),))
    with pytest.raises((ConstructionPermissionDeniedError,ConstructionScopeDeniedError)):await run(c)
    c.repo.find_active_activity_links.assert_not_called();no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["membership","revoked","expired","after_parent","after_activity"])
async def test_current_access_is_required_around_locks(terminal_case,failure):
    c=terminal_case
    if failure=="membership":c.access.has_active_platform_membership.return_value=False
    elif failure=="revoked":c.grant.status="revoked"
    elif failure=="expired":c.grant.expires_at=c.now
    else:c.access.list_active_grants.side_effect=[[c.grant],[]] if failure=="after_parent" else [[c.grant],[c.grant],[]]
    with pytest.raises(ConstructionPermissionDeniedError):await run(c)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["metadata_missing","parent_missing","row_missing","links_changed","version","terminal","invalid_version","invalid_status","timestamps"])
async def test_unavailable_stale_terminal_and_malformed_states_never_mutate(terminal_case,failure):
    c=terminal_case
    error=ConstructionActivityTerminalOperationError
    if failure=="metadata_missing":c.repo.find_active_activity_links.side_effect=None;c.repo.find_active_activity_links.return_value=None;error=ConstructionActivityTerminalNotFoundError
    elif failure=="parent_missing":c.lead_repo.get_active_lead_for_update.side_effect=None;c.lead_repo.get_active_lead_for_update.return_value=None;error=ConstructionActivityTerminalNotFoundError
    elif failure=="row_missing":c.repo.get_active_activity_for_update_by_links.side_effect=None;c.repo.get_active_activity_for_update_by_links.return_value=None;error=ConstructionRowVersionConflictError
    elif failure=="links_changed":
        async def changed(**kwargs):c.row.lead_id=uuid4();return c.row
        c.repo.get_active_activity_for_update_by_links.side_effect=changed;error=ConstructionRowVersionConflictError
    elif failure=="version":c.row.row_version=5;error=ConstructionRowVersionConflictError
    elif failure=="terminal":c.row.status="completed";c.row.completed_at=c.now;error=ConstructionActivityTransitionError
    elif failure=="invalid_version":c.row.row_version=True
    elif failure=="invalid_status":c.row.status="new"
    else:c.row.cancelled_at=c.now
    with pytest.raises(error):await run(c)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation",["complete","cancel"])
async def test_failed_conditional_update_is_version_conflict(terminal_case,operation):
    c=terminal_case
    method=getattr(c.repo,operation+"_activity");method.side_effect=None;method.return_value=None
    with pytest.raises(ConstructionRowVersionConflictError):await run(c,operation)
    c.events.create_event.assert_not_called();caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value",[("row_version",6),("status","planned"),("result","wrong"),
    ("completed_at",None),("cancelled_at",datetime.now(timezone.utc)),("comment","Changed"),("lead_id",uuid4())])
async def test_malformed_update_is_rejected_before_audit(terminal_case,field,value):
    c=terminal_case
    original=c.repo.complete_activity.side_effect
    async def bad(**kwargs):
        row=await original(**kwargs);setattr(row,field,value);return row
    c.repo.complete_activity.side_effect=bad
    with pytest.raises(ConstructionActivityTerminalOperationError):await run(c)
    c.events.create_event.assert_not_called();caller_owns_transaction(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["audit","repository","clock","transaction","command"])
async def test_failure_is_sanitized_and_caller_retains_transaction_ownership(terminal_case,failure):
    c=terminal_case
    options={};error=ConstructionActivityTerminalOperationError
    if failure=="audit":c.events.create_event.side_effect=RuntimeError("private audit failure")
    elif failure=="repository":c.repo.complete_activity.side_effect=RuntimeError("private repository failure")
    elif failure=="clock":c.service.now_provider=lambda:c.now.replace(tzinfo=None)
    elif failure=="transaction":c.session.in_transaction=lambda:False
    else:options["command"]={};error=ConstructionActivityValidationError
    with pytest.raises(error) as caught:await run(c,**options)
    assert "private" not in str(caught.value);caller_owns_transaction(c)


def test_command_and_factory_contract(terminal_case):
    c=terminal_case
    assert ConstructionActivityTerminalCommand(4,"   ").result is None
    for version in [False,0,-1,"4",2**63]:
        with pytest.raises(ConstructionActivityValidationError):ConstructionActivityTerminalCommand(version)
    with pytest.raises(ConstructionActivityValidationError):ConstructionActivityTerminalCommand(4,{})
    service=build_construction_activity_terminal_service(session=c.session,now_provider=lambda:c.now)
    assert all(item.session is c.session for item in (service,service.repository,service.lead_repository,
        service.client_repository,service.project_repository,service.access_repository,service.event_repository))


@pytest.mark.asyncio
async def test_scoped_lookup_sql_selects_only_metadata_and_excludes_crm_rows(terminal_case):
    c=terminal_case
    mappings=SimpleNamespace(one_or_none=Mock(return_value=None))
    c.session.execute.return_value=SimpleNamespace(mappings=Mock(return_value=mappings))
    repo=ConstructionActivityRepository(c.session)
    await repo.find_active_activity_links(tenant_id=c.tenant,activity_id=c.row.id,allowed_project_ids=(c.project.id,))
    statement=c.session.execute.await_args.args[0];sql=str(statement.compile(dialect=postgresql.dialect()))
    assert {column.key for column in statement.selected_columns}=={"id","lead_id","client_id","project_id"}
    assert "construction_activities.project_id IN" in sql
    assert "construction_activities.lead_id IS NULL" in sql and "construction_activities.client_id IS NULL" in sql
    assert "construction_activities.tenant_id =" in sql and "construction_activities.deleted_at IS NULL" in sql
    assert statement.get_execution_options()["autoflush"] is False


@pytest.mark.asyncio
async def test_authorized_link_lock_uses_exact_links_and_does_not_autoflush(terminal_case):
    c=terminal_case
    c.session.execute.return_value=SimpleNamespace(scalar_one_or_none=Mock(return_value=None))
    repo=ConstructionActivityRepository(c.session)
    await repo.get_active_activity_for_update_by_links(tenant_id=c.tenant,activity_id=c.row.id,
        links=ConstructionActivityLinks(lead_id=c.lead.id))
    statement=c.session.execute.await_args.args[0];sql=str(statement.compile(dialect=postgresql.dialect()))
    assert "construction_activities.lead_id =" in sql and "construction_activities.client_id IS NULL" in sql
    assert "construction_activities.project_id IS NULL" in sql and "FOR UPDATE" in sql
    assert statement.get_execution_options()["populate_existing"] is True and statement.get_execution_options()["autoflush"] is False


@pytest.fixture
def sql_terminal(terminal_case,activity_db):
    c=terminal_case
    engine,ids=activity_db
    c.tenant,c.user=ids["a"],ids["user"]
    c.actor.tenant_id,c.actor.user_id=c.tenant,c.user
    c.lead.id,c.row.lead_id=ids["lead_a"],ids["lead_a"]
    c.row.tenant_id=c.tenant;c.row.created_by_user_id=c.user;c.row.responsible_user_id=c.user
    c.grant.scope_id=c.tenant
    c.context=ConstructionAccessContext(c.user,c.tenant,("OWNER",),tuple(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"]),
        (ConstructionGrantContext("OWNER","tenant",c.tenant),))
    with engine.begin() as connection:
        connection.execute(insert(TABLE).values(**values(ids,id=c.row.id,row_version=4,created_at=c.now,updated_at=c.now,
            comment="Private comment",responsible_user_id=c.user)))
        connection.exec_driver_sql("CREATE TABLE terminal_service_audit(id TEXT PRIMARY KEY,payload TEXT)")
    class Adapter(AsyncSessionAdapter):
        @property
        def no_autoflush(self):return self.sync.no_autoflush
    c.session=Adapter(engine)
    class SQLiteRepository(ConstructionActivityRepository):
        async def _finalize_activity(self,**kwargs):
            row=await super()._finalize_activity(**kwargs)
            if row is not None:
                for field in ("updated_at","completed_at","cancelled_at"):
                    value=getattr(row,field)
                    if value is not None and value.tzinfo is None:set_committed_value(row,field,value.replace(tzinfo=timezone.utc))
            return row
    c.repo=SQLiteRepository(c.session);c.lead_repo=ConstructionLeadRepository(c.session)
    for repo in (c.client_repo,c.project_repo,c.access):repo.session=c.session
    class Events:
        session=c.session
        fail=False
        async def create_event(self,**kwargs):
            c.session.sync.execute(text("INSERT INTO terminal_service_audit VALUES(:id,:payload)"),
                dict(id=str(uuid4()),payload=json.dumps(kwargs["payload"])))
            if self.fail:raise RuntimeError("private failure after real audit INSERT")
    c.events=Events()
    c.service=ConstructionActivityTerminalService(session=c.session,repository=c.repo,lead_repository=c.lead_repo,
        client_repository=c.client_repo,project_repository=c.project_repo,access_repository=c.access,
        event_repository=c.events,now_provider=lambda:c.now)
    try:yield c
    finally:c.session.sync.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,status",[("complete","completed"),("cancel","cancelled")])
async def test_actual_mutation_and_audit_commit_together_and_next_action_disappears(sql_terminal,operation,status):
    c=sql_terminal
    with c.session.sync.begin():
        row=await run(c,operation)
        assert row.status==status and row.row_version==5;caller_owns_transaction(c)
    with c.session.sync.get_bind().connect() as connection:
        saved=connection.execute(select(TABLE).where(TABLE.c.id==c.row.id)).mappings().one()
        assert saved["status"]==status and saved["row_version"]==5
        assert connection.execute(text("SELECT count(*) FROM terminal_service_audit")).scalar_one()==1
    assert await c.repo.get_next_action_for_lead(tenant_id=c.tenant,lead_id=c.lead.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation",["complete","cancel"])
@pytest.mark.parametrize("failure",["audit","later_caller"])
async def test_caller_rolls_back_actual_terminal_mutation_and_flushed_audit(sql_terminal,operation,failure):
    c=sql_terminal;c.events.fail=failure=="audit"
    with pytest.raises(ConstructionActivityTerminalOperationError if c.events.fail else RuntimeError):
        with c.session.sync.begin():
            await run(c,operation)
            raise RuntimeError("later caller failure")
    saved=c.session.sync.execute(select(TABLE).where(TABLE.c.id==c.row.id)).mappings().one()
    assert saved["status"]=="planned" and saved["row_version"]==4 and saved["completed_at"] is None and saved["cancelled_at"] is None
    assert c.session.sync.execute(text("SELECT count(*) FROM terminal_service_audit")).scalar_one()==0
    caller_owns_transaction(c)


@pytest.mark.asyncio
async def test_real_scoped_lookup_hides_crm_and_foreign_project_rows_and_lock_requires_unchanged_links(activity_db):
    engine,ids=activity_db
    project_only,crm_linked,foreign=uuid4(),uuid4(),uuid4()
    with engine.begin() as connection:
        connection.execute(insert(TABLE).values(**values(ids,id=project_only,lead_id=None,project_id=ids["project_a"])))
        connection.execute(insert(TABLE).values(**values(ids,id=crm_linked,lead_id=ids["lead_a"],project_id=ids["project_a"])))
        connection.execute(insert(TABLE).values(**values(ids,id=foreign,tenant_id=ids["b"],lead_id=None,project_id=ids["project_b"])))
    session=AsyncSessionAdapter(engine);repo=ConstructionActivityRepository(session)
    try:
        visible=await repo.find_active_activity_links(tenant_id=ids["a"],activity_id=project_only,
            allowed_project_ids=(ids["project_a"],))
        assert visible==dict(id=project_only,lead_id=None,client_id=None,project_id=ids["project_a"])
        assert await repo.find_active_activity_links(tenant_id=ids["a"],activity_id=crm_linked,
            allowed_project_ids=(ids["project_a"],)) is None
        assert await repo.find_active_activity_links(tenant_id=ids["a"],activity_id=foreign,
            allowed_project_ids=(ids["project_b"],)) is None
        assert await repo.find_active_activity_links(tenant_id=ids["a"],activity_id=project_only,
            allowed_project_ids=(uuid4(),)) is None
        expected=ConstructionActivityLinks(project_id=ids["project_a"])
        assert (await repo.get_active_activity_for_update_by_links(tenant_id=ids["a"],activity_id=project_only,links=expected)).id==project_only
        from sqlalchemy import update
        with engine.begin() as connection:
            connection.execute(update(TABLE).where(TABLE.c.id==project_only).values(project_id=None,lead_id=ids["lead_a"]))
        assert await repo.get_active_activity_for_update_by_links(tenant_id=ids["a"],activity_id=project_only,links=expected) is None
    finally:session.sync.close()
