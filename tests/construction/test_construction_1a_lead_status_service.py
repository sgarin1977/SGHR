import json
from contextlib import nullcontext
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.orm.attributes import set_committed_value

from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_lead_status import (
    ConstructionLeadStatusCommand, ConstructionLeadStatusService,
    ConstructionLeadStatusNotFoundError, ConstructionLeadStatusOperationError,
    build_construction_lead_status_service,
)
from services.construction.construction_lead_rules import ConstructionLeadValidationError, ConstructionLeadTransitionError
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from database.repositories.construction.construction_leads import ConstructionLeadRepository
from test_construction_1a_lead_model import TABLE, lead_db, values
from test_construction_1a_lead_repository import AsyncSessionAdapter


@pytest.fixture
def status_case():
    c = SimpleNamespace(tenant=uuid4(), user=uuid4(), now=datetime(2026,10,13,12,tzinfo=timezone.utc))
    c.actor = SimpleNamespace(tenant_id=c.tenant, user_id=c.user, status="active")
    c.context = ConstructionAccessContext(c.user, c.tenant, ("OWNER",),
        tuple(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"]), (ConstructionGrantContext("OWNER","tenant",c.tenant),))
    c.session = SimpleNamespace(in_transaction=lambda: True, no_autoflush=nullcontext(),
        commit=AsyncMock(), rollback=AsyncMock())
    c.lead = SimpleNamespace(id=uuid4(), tenant_id=c.tenant, deleted_at=None, row_version=4,
        status="contacted", name="Private name", source="new_channel", phone="123", email="private@example.com",
        work_type_description="Private work", address_raw="Private address", comment="Private comment",
        responsible_user_id=c.user, converted_client_id=None, converted_project_id=None,
        created_at=c.now, updated_at=c.now)
    c.grant = SimpleNamespace(role="OWNER", scope_type="tenant", scope_id=c.tenant,
        status="active", expires_at=None)
    c.access = SimpleNamespace(session=c.session, has_active_platform_membership=AsyncMock(return_value=True),
        list_active_grants=AsyncMock(return_value=[c.grant]))
    async def update(**kwargs):
        return SimpleNamespace(**{**vars(c.lead), "status": kwargs["next_status"],
            "row_version": kwargs["expected_row_version"]+1, "updated_at": kwargs["as_of"]})
    c.repo = SimpleNamespace(session=c.session, get_active_lead_for_update=AsyncMock(return_value=c.lead),
        transition_status=AsyncMock(side_effect=update))
    c.events = SimpleNamespace(session=c.session, create_event=AsyncMock())
    c.service = ConstructionLeadStatusService(session=c.session, repository=c.repo, access_repository=c.access,
        event_repository=c.events, now_provider=lambda: c.now)
    return c


async def run(c, **changes):
    kwargs = dict(actor=c.actor, access_context=c.context, lead_id=c.lead.id,
        command=ConstructionLeadStatusCommand(4,"measurement_scheduled"), trace_id="status-test")
    kwargs.update(changes)
    return await c.service.transition_status(**kwargs)


def transaction_owned_by_caller(c):
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


def no_writes(c):
    c.repo.transition_status.assert_not_called()
    c.events.create_event.assert_not_called()
    transaction_owned_by_caller(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_shared_permission_lock_fresh_access_version_and_exact_audit(status_case, role):
    c = status_case
    c.grant.role = role
    c.context = ConstructionAccessContext(c.user,c.tenant,(role,),tuple(CONSTRUCTION_ROLE_PERMISSIONS[role]),
        (ConstructionGrantContext(role,"tenant",c.tenant),))
    result = await run(c)
    assert result.changed and result.lead.status == "measurement_scheduled" and result.lead.row_version == 5
    c.repo.get_active_lead_for_update.assert_awaited_once_with(tenant_id=c.tenant,lead_id=c.lead.id)
    assert c.access.has_active_platform_membership.await_count == c.access.list_active_grants.await_count == 2
    c.repo.transition_status.assert_awaited_once_with(tenant_id=c.tenant,lead_id=c.lead.id,
        expected_row_version=4,current_status="contacted",next_status="measurement_scheduled",as_of=c.now)
    c.events.create_event.assert_awaited_once()
    event = c.events.create_event.await_args.kwargs
    assert event == dict(event_type="construction_lead_status_changed",tenant_id=c.tenant,user_id=c.user,
        entity_type="construction_lead",entity_id=c.lead.id,platform="api",trace_id="status-test",
        payload={"operation":"transition_status","reason":None,
            "changes":[{"field":"status","old_value":"contacted","new_value":"measurement_scheduled"}]})
    assert "private" not in json.dumps(event["payload"]).lower()
    transaction_owned_by_caller(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["new","contacted","measurement_scheduled","measurement_completed",
    "estimate_preparation","proposal_sent","negotiation","won","lost"])
async def test_same_status_is_no_op_after_version_and_current_access_checks(status_case, status):
    c = status_case
    c.lead.status = status
    result = await run(c, command=ConstructionLeadStatusCommand(4,status))
    assert result.lead is c.lead and not result.changed and result.lead.row_version == 4
    assert c.access.list_active_grants.await_count == 2
    no_writes(c)


@pytest.mark.asyncio
async def test_no_op_with_stale_version_is_conflict(status_case):
    c = status_case
    with pytest.raises(ConstructionRowVersionConflictError):
        await run(c,command=ConstructionLeadStatusCommand(3,"contacted"))
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("current,next_status", [("won","new"),("lost","new"),("contacted","new"),
    ("new","negotiation"),("new","won")])
async def test_terminal_reverse_and_skipped_transitions_denied(status_case,current,next_status):
    c = status_case
    c.lead.status=current
    with pytest.raises(ConstructionLeadTransitionError):
        await run(c,command=ConstructionLeadStatusCommand(4,next_status))
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("current", ["new","contacted","measurement_scheduled","measurement_completed",
    "estimate_preparation","proposal_sent","negotiation"])
async def test_lost_from_every_nonterminal_state(status_case,current):
    c=status_case
    c.lead.status=current
    assert (await run(c,command=ConstructionLeadStatusCommand(4,"lost"))).lead.status == "lost"


@pytest.mark.asyncio
async def test_ordinary_won_does_not_perform_conversion(status_case):
    c=status_case
    c.lead.status="negotiation"
    result=await run(c,command=ConstructionLeadStatusCommand(4,"won"))
    assert result.lead.converted_client_id is None and result.lead.converted_project_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["actor","tenant","user","permission","role","project_scope","document_scope","illegal_tenant"])
async def test_initial_denial_precedes_business_queries(status_case,failure):
    c=status_case
    if failure == "actor":
        c.actor.status="blocked"
    else:
        role="OBSERVER" if failure == "role" else "PROJECT_MANAGER" if failure == "illegal_tenant" else "OWNER"
        scope="project" if failure == "project_scope" else "document" if failure == "document_scope" else "tenant"
        c.context=ConstructionAccessContext(uuid4() if failure == "user" else c.user,
            uuid4() if failure == "tenant" else c.tenant,(role,),
            () if failure == "permission" else ("construction.projects.edit",),
            (ConstructionGrantContext(role,scope,c.tenant),))
    with pytest.raises((ConstructionPermissionDeniedError,ConstructionScopeDeniedError)):
        await run(c)
    c.repo.get_active_lead_for_update.assert_not_called()
    c.access.has_active_platform_membership.assert_not_called()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership_before","membership_after","grants_before","grants_after","expired","revoked"])
async def test_current_access_loss_before_or_after_lock_denies_write_and_no_op(status_case,failure):
    c=status_case
    if failure.startswith("membership"):
        c.access.has_active_platform_membership.side_effect=[False] if failure.endswith("before") else [True,False]
    elif failure.startswith("grants"):
        c.access.list_active_grants.side_effect=[[]] if failure.endswith("before") else [[c.grant],[]]
    elif failure == "expired":
        c.grant.expires_at=c.now
    else:
        c.grant.status="revoked"
    with pytest.raises(ConstructionPermissionDeniedError):
        await run(c,command=ConstructionLeadStatusCommand(4,"contacted"))
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing","foreign","deleted","wrong_id"])
async def test_unavailable_lead_is_not_found(status_case,failure):
    c=status_case
    if failure == "missing": c.repo.get_active_lead_for_update.return_value=None
    elif failure == "foreign": c.lead.tenant_id=uuid4()
    elif failure == "deleted": c.lead.deleted_at=c.now
    else: c.repo.get_active_lead_for_update.return_value=SimpleNamespace(id=uuid4())
    with pytest.raises(ConstructionLeadStatusNotFoundError):
        await run(c)
    no_writes(c)


@pytest.mark.asyncio
async def test_failed_conditional_write_is_row_version_conflict(status_case):
    c=status_case
    c.repo.transition_status.side_effect=None
    c.repo.transition_status.return_value=None
    with pytest.raises(ConstructionRowVersionConflictError): await run(c)
    c.events.create_event.assert_not_called()
    transaction_owned_by_caller(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("status","lost"),("row_version",6),("comment","changed"),
    ("converted_client_id",uuid4()),("created_at",None),("updated_at",None)])
async def test_malformed_updated_result_cannot_create_audit(status_case,field,value):
    c=status_case
    original=c.repo.transition_status.side_effect
    async def bad(**kwargs):
        result=await original(**kwargs)
        setattr(result,field,value)
        return result
    c.repo.transition_status.side_effect=bad
    with pytest.raises(ConstructionLeadStatusOperationError): await run(c)
    c.events.create_event.assert_not_called()
    transaction_owned_by_caller(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["version","status","missing_field","max_version"])
async def test_bad_stored_state_cannot_write(status_case,failure):
    c=status_case
    if failure == "version": c.lead.row_version=True
    elif failure == "status": c.lead.status="qualified"
    elif failure == "missing_field": del c.lead.comment
    else: c.lead.row_version=2**63-1
    error=ConstructionLeadValidationError if failure == "max_version" else ConstructionLeadStatusOperationError
    with pytest.raises(error):
        await run(c,command=ConstructionLeadStatusCommand(2**63-1 if failure == "max_version" else 4,"measurement_scheduled"))
    no_writes(c)


@pytest.mark.asyncio
async def test_max_version_no_op_is_allowed(status_case):
    c=status_case
    c.lead.row_version=2**63-1
    assert not (await run(c,command=ConstructionLeadStatusCommand(c.lead.row_version,"contacted"))).changed
    no_writes(c)


@pytest.mark.parametrize("version", [None,False,True,0,-1,1.0,"1",2**63])
def test_command_requires_positive_bigint(version):
    with pytest.raises(ConstructionLeadValidationError): ConstructionLeadStatusCommand(version,"new")


def test_command_is_frozen_and_requires_exact_status():
    command=ConstructionLeadStatusCommand(1,"new")
    with pytest.raises(FrozenInstanceError): command.next_status="lost"
    for status in ("qualified","proposal"," new ",None):
        with pytest.raises(ConstructionLeadValidationError): ConstructionLeadStatusCommand(1,status)


@pytest.mark.asyncio
async def test_frozen_command_is_revalidated_before_queries(status_case):
    c=status_case
    command=ConstructionLeadStatusCommand(4,"contacted")
    object.__setattr__(command,"expected_row_version",True)
    with pytest.raises(ConstructionLeadValidationError): await run(c,command=command)
    c.access.has_active_platform_membership.assert_not_called()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["repo","audit","clock","transaction","command","id"])
async def test_errors_are_sanitized_and_transaction_remains_owned_by_caller(status_case,failure):
    c=status_case
    options={}
    error=ConstructionLeadStatusOperationError
    if failure == "repo": c.repo.get_active_lead_for_update.side_effect=RuntimeError("private-detail")
    elif failure == "audit": c.events.create_event.side_effect=RuntimeError("private-detail")
    elif failure == "clock": c.service.now_provider=lambda: c.now.replace(tzinfo=None)
    elif failure == "transaction": c.session.in_transaction=lambda: False
    elif failure == "command": options["command"]={}; error=ConstructionLeadValidationError
    else: options["lead_id"]="unparsed"; error=ConstructionLeadValidationError
    with pytest.raises(error) as caught: await run(c,**options)
    assert "private-detail" not in str(caught.value)
    transaction_owned_by_caller(c)


def test_factory_uses_shared_session_and_rejects_split_session(status_case):
    c=status_case
    # Real repository factory needs the standard session protocol.
    c.session.execute=AsyncMock(); c.session.add=lambda value: None; c.session.flush=AsyncMock()
    service=build_construction_lead_status_service(session=c.session,now_provider=lambda: c.now)
    assert all(item.session is c.session for item in (service,service.repository,service.access_repository,service.event_repository))
    c.events.session=object()
    with pytest.raises(ValueError): ConstructionLeadStatusService(session=c.session,repository=c.repo,
        access_repository=c.access,event_repository=c.events)


@pytest.fixture
def sql_status(status_case,lead_db):
    c=status_case
    engine,ids=lead_db
    c.tenant,c.user=ids["a"],ids["user"]
    c.actor.tenant_id,c.actor.user_id=c.tenant,c.user
    c.grant.scope_id=c.tenant
    c.context=ConstructionAccessContext(c.user,c.tenant,("OWNER",),tuple(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"]),
        (ConstructionGrantContext("OWNER","tenant",c.tenant),))
    with engine.begin() as connection:
        connection.execute(insert(TABLE).values(**values(ids,id=c.lead.id,status="contacted",row_version=4,
            created_at=c.now,updated_at=c.now,comment="Private comment")))
        connection.exec_driver_sql("CREATE TABLE status_audit(id TEXT PRIMARY KEY,payload TEXT)")
        connection.exec_driver_sql("CREATE TABLE status_later_work(id TEXT PRIMARY KEY)")
    class Adapter(AsyncSessionAdapter):
        @property
        def no_autoflush(self): return self.sync.no_autoflush
    c.session=Adapter(engine)
    class SQLiteRepository(ConstructionLeadRepository):
        async def transition_status(self,**kwargs):
            lead=await super().transition_status(**kwargs)
            # SQLite drops timezone information; PostgreSQL does not use this test adapter.
            if lead is not None and lead.updated_at.tzinfo is None:
                set_committed_value(lead,"updated_at",lead.updated_at.replace(tzinfo=timezone.utc))
            return lead
    c.repo=SQLiteRepository(c.session)
    c.access.session=c.session
    class Events:
        session=c.session
        fail=False
        async def create_event(self,**event):
            c.session.sync.execute(text("INSERT INTO status_audit VALUES(:id,:payload)"),
                {"id":str(uuid4()),"payload":json.dumps(event["payload"])})
            if self.fail: raise RuntimeError("private failure after actual audit INSERT")
    c.events=Events()
    c.service=ConstructionLeadStatusService(session=c.session,repository=c.repo,access_repository=c.access,
        event_repository=c.events,now_provider=lambda: c.now)
    try: yield c
    finally: c.session.sync.close()


@pytest.mark.asyncio
async def test_real_commit_status_and_exact_audit_together_then_no_op(sql_status):
    c=sql_status
    with c.session.sync.begin():
        result=await run(c)
        assert result.changed and result.lead.row_version == 5
        transaction_owned_by_caller(c)
    stored=c.session.sync.execute(select(TABLE).where(TABLE.c.id == c.lead.id)).mappings().one()
    assert stored["status"] == "measurement_scheduled" and stored["row_version"] == 5
    audit=c.session.sync.execute(text("SELECT payload FROM status_audit")).scalars().all()
    assert len(audit)==1 and json.loads(audit[0])["changes"]==[
        {"field":"status","old_value":"contacted","new_value":"measurement_scheduled"}]
    c.session.sync.rollback()
    with c.session.sync.begin():
        assert not (await run(c,command=ConstructionLeadStatusCommand(5,"measurement_scheduled"))).changed
    assert c.session.sync.execute(text("SELECT count(*) FROM status_audit")).scalar_one()==1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["audit_after_insert","later_workflow"])
async def test_caller_rolls_back_actual_status_and_audit_writes(sql_status,failure):
    c=sql_status
    c.events.fail=failure=="audit_after_insert"
    with pytest.raises(ConstructionLeadStatusOperationError if c.events.fail else RuntimeError):
        with c.session.sync.begin():
            await run(c)
            c.session.sync.execute(text("INSERT INTO status_later_work VALUES(:id)"),{"id":str(c.lead.id)})
            raise RuntimeError("caller failure")
    stored=c.session.sync.execute(select(TABLE).where(TABLE.c.id == c.lead.id)).mappings().one()
    assert stored["status"] == "contacted" and stored["row_version"]==4
    assert c.session.sync.execute(text("SELECT count(*) FROM status_audit")).scalar_one()==0
    assert c.session.sync.execute(text("SELECT count(*) FROM status_later_work")).scalar_one()==0
    transaction_owned_by_caller(c)
