import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from services.construction_foundation import ConstructionRowVersionConflictError
from services.construction_lead_rules import ConstructionLeadValidationError, ConstructionLeadTransitionError
from services.construction_lead_status import (
    ConstructionLeadStatusNotFoundError, ConstructionLeadStatusOperationError, ConstructionLeadStatusResult,
)
from services.construction_lead_status_workflow import (
    PUBLIC_LEAD_STATUS_FIELDS, ConstructionLeadStatusWorkflow, build_construction_lead_status_workflow,
)
from services.construction_permissions import ConstructionPermissionDeniedError
from test_construction_1a_lead_status_service import status_case, sql_status
from test_construction_1a_lead_model import TABLE, lead_db


@pytest.fixture
def workflow_case(status_case):
    c=status_case
    c.order=[]
    original=c.repo.transition_status.side_effect
    async def update(**kwargs):
        c.order.append("update")
        return await original(**kwargs)
    async def audit(**kwargs): c.order.append("audit")
    async def commit(): c.order.append("commit")
    async def rollback(): c.order.append("rollback")
    c.repo.transition_status.side_effect=update
    c.events.create_event.side_effect=audit
    c.session.commit.side_effect=commit
    c.session.rollback.side_effect=rollback
    c.workflow=ConstructionLeadStatusWorkflow(session=c.session,status_service=c.service)
    return c


async def run(c,**changes):
    kwargs=dict(actor=c.actor,access_context=c.context,lead_id=c.lead.id,
        expected_row_version=4,next_status="measurement_scheduled",trace_id="workflow-status-test")
    kwargs.update(changes)
    return await c.workflow.transition_status(**kwargs)


@pytest.mark.asyncio
async def test_update_and_exact_audit_commit_once_in_order(workflow_case):
    c=workflow_case
    response=await run(c)
    assert response==dict(id=str(c.lead.id),tenant_id=str(c.tenant),status="measurement_scheduled",row_version=5)
    assert set(response)==PUBLIC_LEAD_STATUS_FIELDS
    assert c.order==["update","audit","commit"]
    c.session.commit.assert_awaited_once()
    c.session.rollback.assert_not_called()
    c.events.create_event.assert_awaited_once()
    assert c.events.create_event.await_args.kwargs["trace_id"]=="workflow-status-test"


@pytest.mark.asyncio
@pytest.mark.parametrize("status",["contacted","won","lost"])
async def test_no_op_commits_only_to_release_lock(workflow_case,status):
    c=workflow_case
    c.lead.status=status
    response=await run(c,next_status=status)
    assert response["row_version"]==4 and response["status"]==status
    assert c.order==["commit"]
    c.repo.transition_status.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["stale","stale_no_op","reverse","skipped","terminal","not_found",
    "permission","membership","commit","audit","repo","no_transaction"])
async def test_failures_roll_back_without_successful_commit(workflow_case,failure):
    c=workflow_case
    changes={}
    error=ConstructionLeadStatusOperationError
    if failure.startswith("stale"):
        changes["expected_row_version"]=3
        if failure=="stale_no_op": changes["next_status"]="contacted"
        error=ConstructionRowVersionConflictError
    elif failure in ("reverse","skipped","terminal"):
        changes["next_status"]="new" if failure=="reverse" else "won" if failure=="skipped" else "contacted"
        if failure=="terminal": c.lead.status="won"
        error=ConstructionLeadTransitionError
    elif failure=="not_found":
        c.repo.get_active_lead_for_update.return_value=None
        error=ConstructionLeadStatusNotFoundError
    elif failure=="permission":
        c.actor.status="blocked"; error=ConstructionPermissionDeniedError
    elif failure=="membership":
        c.access.has_active_platform_membership.return_value=False; error=ConstructionPermissionDeniedError
    elif failure=="commit":
        async def bad_commit():
            c.order.append("commit_failure")
            raise RuntimeError("private connection detail")
        c.session.commit.side_effect=bad_commit
    elif failure=="audit":
        async def bad_audit(**kwargs):
            c.order.append("audit_failure")
            raise RuntimeError("private audit detail")
        c.events.create_event.side_effect=bad_audit
    elif failure=="repo": c.repo.transition_status.side_effect=RuntimeError("private repository detail")
    else: c.session.in_transaction=lambda: False
    with pytest.raises(error) as caught: await run(c,**changes)
    assert "private" not in str(caught.value)
    c.session.rollback.assert_awaited_once()
    assert c.order[-1]=="rollback"
    assert c.session.commit.await_count==(failure=="commit")


@pytest.mark.asyncio
@pytest.mark.parametrize("changes",[dict(lead_id="invalid"),dict(expected_row_version=True),
    dict(expected_row_version=0),dict(expected_row_version=2**63),dict(next_status="qualified"),dict(next_status=None)])
async def test_invalid_command_rolls_back_before_service_business_reads(workflow_case,changes):
    c=workflow_case
    with pytest.raises(ConstructionLeadValidationError): await run(c,**changes)
    c.repo.get_active_lead_for_update.assert_not_called()
    assert c.order==["rollback"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode",["changed","no_op"])
async def test_response_is_materialized_before_commit_expires_model(workflow_case,mode):
    c=workflow_case
    original=c.service.transition_status
    captured={}
    async def service(**kwargs):
        result=await original(**kwargs)
        captured["lead"]=result.lead
        return result
    c.service.transition_status=service
    async def commit():
        c.order.append("commit")
        row=captured["lead"]
        row.id=row.tenant_id=row.status=row.row_version=None
    c.session.commit.side_effect=commit
    lead_id=c.lead.id
    response=await run(c,**({"next_status":"contacted"} if mode=="no_op" else {}))
    assert response==dict(id=str(lead_id),tenant_id=str(c.tenant),
        status="contacted" if mode=="no_op" else "measurement_scheduled",row_version=4 if mode=="no_op" else 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["result_type","changed_type","version","status","foreign","wrong_id","deleted"])
async def test_invalid_projection_rolls_back_before_commit(workflow_case,failure):
    c=workflow_case
    lead=SimpleNamespace(**{**vars(c.lead),"status":"measurement_scheduled","row_version":5})
    result=ConstructionLeadStatusResult(lead,True)
    error=ConstructionLeadStatusOperationError
    if failure=="result_type": result=SimpleNamespace(lead=lead,changed=True)
    elif failure=="changed_type": result=ConstructionLeadStatusResult(lead,1)
    elif failure=="version": lead.row_version=4
    elif failure=="status": lead.status="lost"
    elif failure=="foreign": lead.tenant_id=uuid4(); error=ConstructionLeadStatusNotFoundError
    elif failure=="wrong_id": lead.id=uuid4(); error=ConstructionLeadStatusNotFoundError
    else: lead.deleted_at=c.now; error=ConstructionLeadStatusNotFoundError
    c.service.transition_status=AsyncMock(return_value=result)
    with pytest.raises(error): await run(c)
    c.session.commit.assert_not_called()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_during_service_rolls_back_and_propagates(workflow_case):
    c=workflow_case
    c.service.transition_status=AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError): await run(c)
    c.session.commit.assert_not_called()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_rollback_failure_is_sanitized(workflow_case):
    c=workflow_case
    c.session.rollback.side_effect=RuntimeError("private rollback diagnostic")
    with pytest.raises(ConstructionLeadStatusOperationError) as caught:
        await run(c,next_status="qualified")
    assert "private" not in str(caught.value)
    assert "rollback failed" in str(caught.value)
    c.session.commit.assert_not_called()


@pytest.mark.parametrize("component",["repository","access_repository","event_repository","audit_event_repository"])
def test_split_session_collaborators_are_rejected(workflow_case,component):
    c=workflow_case
    if component=="audit_event_repository":
        c.service.audit_logger.event_repository=SimpleNamespace(session=object())
    else: setattr(c.service,component,SimpleNamespace(session=object()))
    with pytest.raises(ValueError): ConstructionLeadStatusWorkflow(session=c.session,status_service=c.service)


def test_factory_keeps_repositories_service_and_audit_in_one_session(workflow_case):
    c=workflow_case
    c.session.execute=AsyncMock(); c.session.add=lambda value: None; c.session.flush=AsyncMock()
    workflow=build_construction_lead_status_workflow(session=c.session,now_provider=lambda: c.now)
    assert all(item.session is c.session for item in (workflow,workflow.service,workflow.service.repository,
        workflow.service.access_repository,workflow.service.event_repository))


@pytest.fixture
def sql_workflow(sql_status):
    c=sql_status
    c.workflow=ConstructionLeadStatusWorkflow(session=c.session,status_service=c.service)
    return c


@pytest.mark.asyncio
async def test_real_workflow_commit_is_visible_from_fresh_connection_and_no_op_adds_no_audit(sql_workflow):
    c=sql_workflow
    c.session.sync.begin()
    response=await run(c)
    assert not c.session.in_transaction()
    with c.session.sync.get_bind().connect() as connection:
        stored=connection.execute(select(TABLE).where(TABLE.c.id==c.lead.id)).mappings().one()
        assert stored["status"]==response["status"] and stored["row_version"]==5
        assert connection.execute(text("SELECT count(*) FROM status_audit")).scalar_one()==1
    c.session.sync.begin()
    assert await run(c,expected_row_version=5)==response
    assert not c.session.in_transaction()
    assert c.session.commit.await_count==2 and c.session.rollback.await_count==0
    with c.session.sync.get_bind().connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM status_audit")).scalar_one()==1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["audit_after_insert","commit","projection","cancel_after_audit"])
async def test_real_workflow_failure_reverts_status_and_audit_without_caller_rollback(sql_workflow,failure):
    c=sql_workflow
    error=ConstructionLeadStatusOperationError
    if failure=="audit_after_insert": c.events.fail=True
    elif failure=="commit": c.session.commit.side_effect=RuntimeError("private commit error before commit")
    elif failure=="projection":
        original=c.workflow._response
        def bad(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError("private projection error")
        c.workflow._response=bad
    else:
        original=c.service.transition_status
        async def cancelled(**kwargs):
            await original(**kwargs)
            raise asyncio.CancelledError()
        c.service.transition_status=cancelled
        error=asyncio.CancelledError
    c.session.sync.begin()
    with pytest.raises(error): await run(c)
    assert not c.session.in_transaction()
    c.session.rollback.assert_awaited_once()
    with c.session.sync.get_bind().connect() as connection:
        stored=connection.execute(select(TABLE).where(TABLE.c.id==c.lead.id)).mappings().one()
        assert stored["status"]=="contacted" and stored["row_version"]==4
        assert connection.execute(text("SELECT count(*) FROM status_audit")).scalar_one()==0
