import json
from contextlib import nullcontext
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from itertools import product
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from services.construction_activity_create import (
    ConstructionActivityCreateCommand, ConstructionActivityCreateService,
    ConstructionActivityCreateNotFoundError, ConstructionActivityCreateOperationError,
    build_construction_activity_create_service,
)
from services.construction_activity_rules import (
    ConstructionActivityLinks, ConstructionActivitySchedule, ConstructionActivityValidationError,
)
from services.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from database.repositories.construction_activities import ConstructionActivityRepository
from database.repositories.construction_leads import ConstructionLeadRepository
from test_construction_1a_activity_model import TABLE, activity_db
from test_construction_1a_lead_model import lead_db
from test_construction_1a_lead_repository import AsyncSessionAdapter


@pytest.fixture
def activity_create_case():
    c=SimpleNamespace(tenant=uuid4(),user=uuid4(),now=datetime(2026,10,13,12,tzinfo=timezone.utc),order=[])
    c.actor=SimpleNamespace(tenant_id=c.tenant,user_id=c.user,status="active")
    c.context=ConstructionAccessContext(c.user,c.tenant,("OWNER",),tuple(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"]),
        (ConstructionGrantContext("OWNER","tenant",c.tenant),))
    c.session=SimpleNamespace(in_transaction=lambda: True,no_autoflush=nullcontext(),
        commit=AsyncMock(),rollback=AsyncMock(),execute=AsyncMock(),add=Mock(),flush=AsyncMock())
    c.grant=SimpleNamespace(role="OWNER",scope_type="tenant",scope_id=c.tenant,status="active",expires_at=None)
    c.access=SimpleNamespace(session=c.session,has_active_platform_membership=AsyncMock(return_value=True),
        list_active_grants=AsyncMock(return_value=[c.grant]))
    for name,method in [("lead","get_active_lead_for_update"),("client","get_active_client_for_update"),
                        ("project","get_active_project_for_update")]:
        resource=SimpleNamespace(id=uuid4(),tenant_id=c.tenant,deleted_at=None)
        if name == "lead": resource.converted_client_id = None
        if name == "project": resource.client_id = None
        setattr(c,name,resource)
        async def get(name=name,resource=resource,**kwargs):
            c.order.append(name+"_lock")
            return resource
        setattr(c,name+"_repo",SimpleNamespace(session=c.session,**{method:AsyncMock(side_effect=get)}))
    async def create(**kwargs):
        c.order.append("create")
        return SimpleNamespace(id=uuid4(),tenant_id=kwargs["tenant_id"],type=kwargs["activity_type"],status="planned",
            lead_id=kwargs["links"].lead_id,client_id=kwargs["links"].client_id,project_id=kwargs["links"].project_id,
            due_at=kwargs["schedule"].due_at,timezone=kwargs["schedule"].timezone,
            responsible_user_id=kwargs["responsible_user_id"],comment=kwargs["comment"],result=None,
            completed_at=None,cancelled_at=None,created_by_user_id=kwargs["created_by_user_id"],
            created_at=kwargs["as_of"],updated_at=kwargs["as_of"],deleted_at=None,row_version=1)
    c.repo=SimpleNamespace(session=c.session,create_activity=AsyncMock(side_effect=create))
    async def audit(**event): c.order.append("audit")
    c.events=SimpleNamespace(session=c.session,create_event=AsyncMock(side_effect=audit))
    c.service=ConstructionActivityCreateService(session=c.session,repository=c.repo,lead_repository=c.lead_repo,
        client_repository=c.client_repo,project_repository=c.project_repo,access_repository=c.access,
        event_repository=c.events,now_provider=lambda: c.now)
    return c


def command(c,**changes):
    data=dict(activity_type=" CALL ",links=ConstructionActivityLinks(lead_id=c.lead.id),
        schedule=ConstructionActivitySchedule(c.now,"Europe/Lisbon"),responsible_user_id=None,comment=" Private comment ")
    data.update(changes)
    return ConstructionActivityCreateCommand(**data)


async def run(c,**changes):
    options=dict(actor=c.actor,access_context=c.context,command=command(c),trace_id="activity-create-test")
    options.update(changes)
    return await c.service.create_activity(**options)


def transaction_owned_by_caller(c):
    c.session.commit.assert_not_called(); c.session.rollback.assert_not_called()


def no_writes(c):
    c.repo.create_activity.assert_not_called(); c.events.create_event.assert_not_called()
    transaction_owned_by_caller(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("links",[choice for choice in product((False,True),repeat=3) if any(choice)])
async def test_all_approved_link_combinations_use_fixed_lock_order_and_one_private_safe_audit(activity_create_case,links):
    c=activity_create_case
    cmd=command(c,links=ConstructionActivityLinks(*[getattr(c,name).id if enabled else None
        for name,enabled in zip(("lead","client","project"),links)]))
    result=await run(c,command=cmd)
    expected_order=[name+"_lock" for name,enabled in zip(("lead","client","project"),links) if enabled]+["create","audit"]
    assert c.order==expected_order
    assert result.status=="planned" and result.row_version==1 and result.created_by_user_id==c.user
    assert result.responsible_user_id is None and result.comment=="Private comment"
    assert c.access.has_active_platform_membership.await_count==2
    assert c.access.list_active_grants.await_count==2
    c.events.create_event.assert_awaited_once()
    event=c.events.create_event.await_args.kwargs
    assert event==dict(event_type="construction_activity_created",tenant_id=c.tenant,user_id=c.user,
        entity_type="construction_activity",entity_id=result.id,platform="api",trace_id="activity-create-test",
        payload={"operation":"create","changes":[],"reason":None,
            **({"related_client_id": str(c.client.id)} if links[1] else {})})
    assert "private" not in json.dumps(event["payload"]).lower()
    transaction_owned_by_caller(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("role",["OWNER","ADMIN"])
async def test_tenant_role_policy_and_current_responsible_membership(activity_create_case,role):
    c=activity_create_case
    c.grant.role=role
    c.context=ConstructionAccessContext(c.user,c.tenant,(role,),tuple(CONSTRUCTION_ROLE_PERMISSIONS[role]),
        (ConstructionGrantContext(role,"tenant",c.tenant),))
    assigned=uuid4()
    result=await run(c,command=command(c,responsible_user_id=assigned))
    assert result.responsible_user_id==assigned
    assert [item.kwargs["user_id"] for item in c.access.has_active_platform_membership.await_args_list]==[
        c.user,assigned,assigned,c.user]


@pytest.mark.asyncio
async def test_scoped_project_manager_can_create_only_within_assigned_project(activity_create_case):
    c=activity_create_case
    c.grant.role,c.grant.scope_type,c.grant.scope_id="PROJECT_MANAGER","project",c.project.id
    c.context=ConstructionAccessContext(c.user,c.tenant,("PROJECT_MANAGER",),tuple(CONSTRUCTION_ROLE_PERMISSIONS["PROJECT_MANAGER"]),
        (ConstructionGrantContext("PROJECT_MANAGER","project",c.project.id),))
    cmd=command(c,links=ConstructionActivityLinks(project_id=c.project.id))
    result=await run(c,command=cmd)
    assert result.project_id==c.project.id and result.lead_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["other_project","lead","client","mixed"])
async def test_project_scope_cannot_expand_to_tenant_crm_or_another_project(activity_create_case,failure):
    c=activity_create_case
    c.context=ConstructionAccessContext(c.user,c.tenant,("PROJECT_MANAGER",),tuple(CONSTRUCTION_ROLE_PERMISSIONS["PROJECT_MANAGER"]),
        (ConstructionGrantContext("PROJECT_MANAGER","project",c.project.id),))
    links=ConstructionActivityLinks(project_id=uuid4()) if failure=="other_project" else (
        ConstructionActivityLinks(lead_id=c.lead.id,project_id=c.project.id) if failure=="mixed" else
        ConstructionActivityLinks(**{failure+"_id":getattr(c,failure).id}))
    with pytest.raises(ConstructionScopeDeniedError): await run(c,command=command(c,links=links))
    c.access.has_active_platform_membership.assert_not_called(); no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["actor","tenant","user","observer","permission","illegal_tenant"])
async def test_initial_denial_precedes_any_linked_resource_query(activity_create_case,failure):
    c=activity_create_case
    if failure=="actor": c.actor.status="blocked"
    else:
        role="OBSERVER" if failure=="observer" else "PROJECT_MANAGER" if failure=="illegal_tenant" else "OWNER"
        c.context=ConstructionAccessContext(uuid4() if failure=="user" else c.user,
            uuid4() if failure=="tenant" else c.tenant,(role,),
            () if failure=="permission" else ("construction.projects.create",),
            (ConstructionGrantContext(role,"tenant",c.tenant),))
    with pytest.raises((ConstructionPermissionDeniedError,ConstructionScopeDeniedError)): await run(c)
    c.lead_repo.get_active_lead_for_update.assert_not_called()
    c.access.has_active_platform_membership.assert_not_called(); no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["membership_before","membership_after","grants_before","grants_after","expired","revoked"])
async def test_current_access_is_checked_before_and_after_parent_locks(activity_create_case,failure):
    c=activity_create_case
    if failure.startswith("membership"):
        c.access.has_active_platform_membership.side_effect=[False] if failure.endswith("before") else [True,False]
    elif failure.startswith("grants"):
        c.access.list_active_grants.side_effect=[[]] if failure.endswith("before") else [[c.grant],[]]
    elif failure=="expired": c.grant.expires_at=c.now
    else: c.grant.status="revoked"
    with pytest.raises(ConstructionPermissionDeniedError): await run(c)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage",["before","after"])
async def test_responsible_must_remain_active_member_before_creation(activity_create_case,stage):
    c=activity_create_case
    c.access.has_active_platform_membership.side_effect=[True,False] if stage=="before" else [True,True,False]
    with pytest.raises(ConstructionActivityValidationError):
        await run(c,command=command(c,responsible_user_id=uuid4()))
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("resource",["lead","client","project"])
@pytest.mark.parametrize("failure",["missing","foreign","deleted","wrong_id"])
async def test_every_selected_resource_is_checked_for_identity_tenant_and_availability(activity_create_case,resource,failure):
    c=activity_create_case
    selected=getattr(c,resource).id
    row=getattr(c,resource)
    method=getattr(getattr(c,resource+"_repo"),"get_active_"+resource+"_for_update")
    if failure=="missing": method.side_effect=None; method.return_value=None
    elif failure=="foreign": row.tenant_id=uuid4()
    elif failure=="deleted": row.deleted_at=c.now
    else: row.id=uuid4()
    with pytest.raises(ConstructionActivityCreateNotFoundError):
        await run(c,command=command(c,links=ConstructionActivityLinks(**{resource+"_id":selected})))
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value",[("id","invalid"),("tenant_id",uuid4()),("created_by_user_id",uuid4()),
    ("status","completed"),("row_version",True),("row_version",2),("result","Unexpected"),
    ("due_at",None),("timezone","UTC"),("completed_at",datetime.now(timezone.utc)),
    ("lead_id",uuid4()),("comment","Changed"),("created_at",None)])
async def test_malformed_creation_cannot_produce_audit(activity_create_case,field,value):
    c=activity_create_case
    original=c.repo.create_activity.side_effect
    async def bad(**kwargs):
        row=await original(**kwargs); setattr(row,field,value); return row
    c.repo.create_activity.side_effect=bad
    with pytest.raises(ConstructionActivityCreateOperationError): await run(c)
    c.events.create_event.assert_not_called(); transaction_owned_by_caller(c)


def test_command_normalizes_and_is_frozen(activity_create_case):
    c=activity_create_case
    cmd=command(c)
    assert cmd.activity_type=="call" and cmd.comment=="Private comment"
    with pytest.raises(FrozenInstanceError): cmd.comment="new"
    assert command(c,comment="   ").comment is None
    for kwargs in [dict(activity_type="email"),dict(links={}),dict(schedule={}),
        dict(responsible_user_id="uuid-text"),dict(comment=1)]:
        with pytest.raises(ConstructionActivityValidationError): command(c,**kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["links","schedule","responsible","type"])
async def test_frozen_command_and_nested_values_are_revalidated_before_queries(activity_create_case,failure):
    c=activity_create_case
    cmd=command(c)
    if failure=="links": object.__setattr__(cmd.links,"lead_id",None)
    elif failure=="schedule": object.__setattr__(cmd.schedule,"due_at",c.now.replace(tzinfo=None))
    elif failure=="responsible": object.__setattr__(cmd,"responsible_user_id","invalid")
    else: object.__setattr__(cmd,"activity_type","email")
    with pytest.raises(ConstructionActivityValidationError): await run(c,command=cmd)
    c.access.has_active_platform_membership.assert_not_called(); no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["repository","audit","clock","transaction","command"])
async def test_failures_are_sanitized_and_service_does_not_own_commit_or_rollback(activity_create_case,failure):
    c=activity_create_case
    error=ConstructionActivityCreateOperationError
    options={}
    if failure=="repository": c.repo.create_activity.side_effect=RuntimeError("private storage diagnostic")
    elif failure=="audit": c.events.create_event.side_effect=RuntimeError("private audit diagnostic")
    elif failure=="clock": c.service.now_provider=lambda: c.now.replace(tzinfo=None)
    elif failure=="transaction": c.session.in_transaction=lambda: False
    else: options["command"]={}; error=ConstructionActivityValidationError
    with pytest.raises(error) as caught: await run(c,**options)
    assert "private" not in str(caught.value); transaction_owned_by_caller(c)


def test_factory_has_one_session_and_rejects_mixed_repository_sessions(activity_create_case):
    c=activity_create_case
    service=build_construction_activity_create_service(session=c.session,now_provider=lambda: c.now)
    assert all(item.session is c.session for item in (service,service.repository,service.lead_repository,
        service.client_repository,service.project_repository,service.access_repository,service.event_repository))
    c.events.session=object()
    with pytest.raises(ValueError):
        ConstructionActivityCreateService(session=c.session,repository=c.repo,lead_repository=c.lead_repo,
            client_repository=c.client_repo,project_repository=c.project_repo,access_repository=c.access,
            event_repository=c.events)


@pytest.fixture
def sql_activity_create(activity_create_case,activity_db):
    c=activity_create_case
    engine,ids=activity_db
    c.tenant,c.user=ids["a"],ids["user"]
    c.actor.tenant_id,c.actor.user_id=c.tenant,c.user
    c.lead.id,c.lead.tenant_id=ids["lead_a"],c.tenant
    c.grant.scope_id=c.tenant
    c.context=ConstructionAccessContext(c.user,c.tenant,("OWNER",),tuple(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"]),
        (ConstructionGrantContext("OWNER","tenant",c.tenant),))
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE activity_creation_audit(id TEXT PRIMARY KEY,payload TEXT)")
    class Adapter(AsyncSessionAdapter):
        @property
        def no_autoflush(self): return self.sync.no_autoflush
    c.session=Adapter(engine)
    c.repo=ConstructionActivityRepository(c.session)
    c.lead_repo=ConstructionLeadRepository(c.session)
    for item in (c.client_repo,c.project_repo,c.access): item.session=c.session
    class Events:
        session=c.session
        fail=False
        async def create_event(self,**event):
            c.session.sync.execute(text("INSERT INTO activity_creation_audit VALUES(:id,:payload)"),
                dict(id=str(uuid4()),payload=json.dumps(event["payload"])))
            if self.fail: raise RuntimeError("private failure after actual audit insert")
    c.events=Events()
    c.service=ConstructionActivityCreateService(session=c.session,repository=c.repo,lead_repository=c.lead_repo,
        client_repository=c.client_repo,project_repository=c.project_repo,access_repository=c.access,
        event_repository=c.events,now_provider=lambda: c.now)
    try: yield c
    finally: c.session.sync.close()


@pytest.mark.asyncio
async def test_real_caller_commits_activity_and_one_audit_and_next_action_is_derived(sql_activity_create):
    c=sql_activity_create
    with c.session.sync.begin():
        created=await run(c)
        identifier=created.id
        transaction_owned_by_caller(c)
    with c.session.sync.get_bind().connect() as connection:
        stored=connection.execute(select(TABLE).where(TABLE.c.id==identifier)).mappings().one()
        assert stored["status"]=="planned" and stored["row_version"]==1
        assert connection.execute(text("SELECT count(*) FROM activity_creation_audit")).scalar_one()==1
    assert (await c.repo.get_next_action_for_lead(tenant_id=c.tenant,lead_id=c.lead.id)).id==identifier


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["audit_after_insert","later_workflow"])
async def test_real_caller_rolls_back_activity_and_flushed_audit_together(sql_activity_create,failure):
    c=sql_activity_create
    c.events.fail=failure=="audit_after_insert"
    with pytest.raises(ConstructionActivityCreateOperationError if c.events.fail else RuntimeError):
        with c.session.sync.begin():
            await run(c)
            raise RuntimeError("later workflow failure")
    assert c.session.sync.execute(select(TABLE.c.id)).all()==[]
    assert c.session.sync.execute(text("SELECT count(*) FROM activity_creation_audit")).scalar_one()==0
    transaction_owned_by_caller(c)
