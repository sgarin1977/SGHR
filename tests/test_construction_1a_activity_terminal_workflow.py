import asyncio
from datetime import timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, text
from sqlalchemy.orm.attributes import set_committed_value

from services.api_idempotency import ApiIdempotencyKeyReusedError, ApiIdempotencyService, ApiIdempotencyResponseCodec
from services.construction_activity_terminal import ConstructionActivityTerminalNotFoundError, ConstructionActivityTerminalOperationError
from services.construction_activity_terminal_workflow import (
    ACTIVITY_COMPLETE_OPERATION, ACTIVITY_CANCEL_OPERATION, PUBLIC_ACTIVITY_TERMINAL_FIELDS,
    ConstructionActivityTerminalWorkflow, ConstructionActivityTerminalInProgressError,
    ConstructionActivityTerminalReplayUnavailableError, build_construction_activity_terminal_workflow,
)
from services.construction_activity_rules import ConstructionActivityValidationError, ConstructionActivityTransitionError
from services.construction_foundation import ConstructionRowVersionConflictError
from services.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError, ConstructionAccessContext, ConstructionGrantContext, CONSTRUCTION_ROLE_PERMISSIONS
from test_construction_1a_activity_terminal_service import terminal_case, sql_terminal
from test_construction_1a_activity_model import TABLE, activity_db
from test_construction_1a_lead_model import lead_db
from test_construction_1a_lead_create_workflow import MemoryRecords, SqlRecords


def setup(c):
    c.codec=ApiIdempotencyResponseCodec(encryption_key=Fernet.generate_key().decode())
    c.idempotency=ApiIdempotencyService(repository=c.records,response_codec=c.codec)
    c.workflow=ConstructionActivityTerminalWorkflow(session=c.session,activity_service=c.service,idempotency_service=c.idempotency)
    return c


@pytest.fixture
def workflow_case(terminal_case):
    c=terminal_case
    for method in ("complete_activity","cancel_activity"):
        original=getattr(c.repo,method).side_effect
        async def mutate(original=original,**kwargs):
            c.row=await original(**kwargs)
            c.row.private_internal="never-public"
            return c.row
        getattr(c.repo,method).side_effect=mutate
    async def commit():c.order.append("commit")
    async def rollback():c.order.append("rollback")
    c.session.commit.side_effect=commit;c.session.rollback.side_effect=rollback
    c.records=MemoryRecords(c.session,c.order)
    return setup(c)


async def run(c, operation="complete", **changes):
    options=dict(actor=c.actor,access_context=c.context,activity_id=c.row.id,expected_row_version=4,
        result=" Private result ",idempotency_key="private-terminal-key",trace_id="terminal-workflow-test")
    options.update(changes)
    return await getattr(c.workflow,operation+"_activity")(**options)


def saved(c):return next(iter(c.records.rows.values()))


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,status,code",[("complete","completed",ACTIVITY_COMPLETE_OPERATION),("cancel","cancelled",ACTIVITY_CANCEL_OPERATION)])
async def test_change_audit_bound_encrypted_snapshot_and_commit_then_replay_without_second_write(workflow_case,operation,status,code):
    c=workflow_case
    first=await run(c,operation)
    assert set(first)==PUBLIC_ACTIVITY_TERMINAL_FIELDS and first["id"]==str(c.row.id)
    assert first["status"]==status and first["row_version"]==5 and first["result"]=="Private result"
    assert c.order==["reserve","lead_lock","activity_lock",operation,"audit","complete","commit"]
    record=saved(c);decoded=c.codec.decrypt(record.response_ciphertext)
    assert record.operation==code and record.expires_at==c.now+timedelta(hours=24) and record.response_status==200
    assert decoded["data"]==first
    assert decoded["binding"]==dict(tenant_id=str(c.tenant),principal_type="user",principal_id=str(c.user),
        operation=code,key_hash=record.key_hash,request_hash=record.request_hash)
    assert b"Private result" not in record.response_ciphertext and b"private-terminal-key" not in record.response_ciphertext
    c.row.comment="Later metadata";c.row.result="Later result";c.row.row_version=6
    c.order.clear()
    assert await run(c,operation)==first
    assert c.order==["reserve","lead_lock","activity_lock","commit"]
    getattr(c.repo,operation+"_activity").assert_awaited_once();c.events.create_event.assert_awaited_once()
    assert c.session.commit.await_count==2;c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_terminal_operation_can_be_performed_by_authorized_non_creator(workflow_case):
    c=workflow_case;c.row.created_by_user_id=uuid4()
    first=await run(c)
    assert first["created_by_user_id"]!=str(c.user)
    assert await run(c)==first


@pytest.mark.asyncio
@pytest.mark.parametrize("operation",["complete","cancel"])
@pytest.mark.parametrize("key",[None,"","  ",True,17,"x"*256])
async def test_key_required_before_any_reservation_or_mutation(workflow_case,operation,key):
    c=workflow_case
    with pytest.raises(ConstructionActivityValidationError):await run(c,operation,idempotency_key=key)
    assert not c.records.rows;c.repo.complete_activity.assert_not_called();c.repo.cancel_activity.assert_not_called()
    c.session.commit.assert_not_called();c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("change",[dict(expected_row_version=5),dict(result="Private result"),dict(result=None),dict(result="Other")])
async def test_same_key_changed_raw_command_conflicts(workflow_case,change):
    c=workflow_case;await run(c)
    with pytest.raises(ApiIdempotencyKeyReusedError):await run(c,**change)
    c.repo.complete_activity.assert_awaited_once();c.events.create_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_different_activity_with_same_key_is_not_replayed(workflow_case):
    c=workflow_case;await run(c);c.row.id=uuid4()
    with pytest.raises(ApiIdempotencyKeyReusedError):await run(c)
    c.repo.complete_activity.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation",["complete","cancel"])
async def test_new_key_on_terminal_activity_does_not_create_second_audit(workflow_case,operation):
    c=workflow_case;await run(c,operation)
    with pytest.raises(ConstructionActivityTransitionError):await run(c,operation,expected_row_version=5,idempotency_key="another-key")
    c.events.create_event.assert_awaited_once();assert c.session.commit.await_count==1


@pytest.mark.asyncio
async def test_operation_namespaces_are_distinct_and_do_not_reopen_terminal_status(workflow_case):
    c=workflow_case;await run(c)
    with pytest.raises(ConstructionActivityTransitionError):await run(c,"cancel",expected_row_version=5)
    assert {r.operation for r in c.records.rows.values()}=={ACTIVITY_COMPLETE_OPERATION,ACTIVITY_CANCEL_OPERATION}
    c.repo.cancel_activity.assert_not_called();c.events.create_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_version_rolls_back_reservation_without_mutation(workflow_case):
    c=workflow_case
    with pytest.raises(ConstructionRowVersionConflictError):await run(c,expected_row_version=3)
    c.repo.complete_activity.assert_not_called();c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called();c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_processing_reservation_is_conflict_without_second_write(workflow_case):
    c=workflow_case;await run(c)
    record=saved(c);record.status="processing";record.response_status=None;record.response_ciphertext=None
    with pytest.raises(ConstructionActivityTerminalInProgressError) as caught:await run(c)
    assert caught.value.status_code==409 and caught.value.code=="idempotency_in_progress"
    c.repo.complete_activity.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("replay",[False,True])
@pytest.mark.parametrize("failure",["membership","revoked","expired","inactive"])
async def test_current_access_required_before_reserving_or_returning_replay(workflow_case,replay,failure):
    c=workflow_case
    if replay:await run(c)
    before=list(c.order)
    if failure=="membership":c.access.has_active_platform_membership.return_value=False
    elif failure=="revoked":c.grant.status="revoked"
    elif failure=="expired":c.grant.expires_at=c.now
    else:c.actor.status="blocked"
    with pytest.raises(ConstructionPermissionDeniedError):await run(c)
    assert c.order==before+["rollback"]


@pytest.mark.asyncio
@pytest.mark.parametrize("replay",[False,True])
async def test_revoke_while_waiting_for_reservation_prevents_write_or_replay(workflow_case,replay):
    c=workflow_case
    if replay:await run(c)
    original=c.records.reserve_record
    async def revoke(**kw):
        answer=await original(**kw);c.grant.status="revoked";return answer
    c.records.reserve_record=revoke
    with pytest.raises(ConstructionPermissionDeniedError):await run(c)
    assert c.repo.complete_activity.await_count==int(replay) and c.session.commit.await_count==int(replay)


@pytest.mark.asyncio
async def test_revoke_after_snapshot_write_prevents_commit(workflow_case):
    c=workflow_case;original=c.records.complete_record
    async def revoke(**kw):
        answer=await original(**kw);c.grant.status="revoked";return answer
    c.records.complete_record=revoke
    with pytest.raises(ConstructionPermissionDeniedError):await run(c)
    c.session.commit.assert_not_called();c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_project_scope_replay_requires_current_scope_and_hides_linked_crm(workflow_case):
    c=workflow_case;c.row.lead_id=None;c.row.project_id=c.project.id
    c.grant.role,c.grant.scope_type,c.grant.scope_id="PROJECT_MANAGER","project",c.project.id
    c.context=ConstructionAccessContext(c.user,c.tenant,("PROJECT_MANAGER",),tuple(CONSTRUCTION_ROLE_PERMISSIONS["PROJECT_MANAGER"]),
        (ConstructionGrantContext("PROJECT_MANAGER","project",c.project.id),))
    first=await run(c);assert await run(c)==first
    assert c.repo.find_active_activity_links.await_args.kwargs["allowed_project_ids"]==(c.project.id,)
    c.row.client_id=c.client.id
    with pytest.raises(ConstructionScopeDeniedError):await run(c)
    c.repo.complete_activity.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["missing","deleted","foreign","links","version","status","timestamp"])
async def test_replay_rechecks_current_resource(workflow_case,failure):
    c=workflow_case;await run(c)
    row=SimpleNamespace(**vars(c.row));error=ConstructionActivityTerminalOperationError
    if failure=="missing":row=None;error=ConstructionActivityTerminalReplayUnavailableError
    elif failure=="deleted":row.deleted_at=c.now;error=ConstructionActivityTerminalReplayUnavailableError
    elif failure=="foreign":row.tenant_id=uuid4();error=ConstructionActivityTerminalReplayUnavailableError
    elif failure=="links":row.lead_id=uuid4();error=ConstructionActivityTerminalReplayUnavailableError
    elif failure=="version":row.row_version=4
    elif failure=="status":row.status="planned"
    else:row.completed_at=c.now.replace(tzinfo=None)
    c.repo.get_active_activity_for_update_by_links.side_effect=None;c.repo.get_active_activity_for_update_by_links.return_value=row
    with pytest.raises(error):await run(c)
    assert c.session.commit.await_count==1;c.events.create_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_replay_rechecks_parent_not_deleted(workflow_case):
    c=workflow_case;await run(c);c.lead.deleted_at=c.now
    with pytest.raises(ConstructionActivityTerminalNotFoundError):await run(c)
    c.events.create_event.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["binding","extra","tenant","id","status","version","result","timestamp","ciphertext"])
async def test_corrupt_encrypted_snapshot_cannot_be_published(workflow_case,failure):
    c=workflow_case;await run(c);record=saved(c);snapshot=c.codec.decrypt(record.response_ciphertext)
    if failure=="ciphertext":record.response_ciphertext=b"private-invalid-cipher"
    else:
        if failure=="binding":snapshot["binding"]["principal_id"]=str(uuid4())
        elif failure=="extra":snapshot["data"]["private_internal"]="private"
        elif failure in ("tenant","id"):snapshot["data"]["tenant_id" if failure=="tenant" else "id"]=str(uuid4())
        elif failure=="status":snapshot["data"]["status"]="cancelled"
        elif failure=="version":snapshot["data"]["row_version"]=6
        elif failure=="result":snapshot["data"]["result"]="Changed"
        else:snapshot["data"]["completed_at"]=None
        record.response_ciphertext=c.codec.encrypt(snapshot)
    with pytest.raises(ConstructionActivityTerminalOperationError) as caught:await run(c)
    assert "private-invalid" not in str(caught.value);assert c.session.commit.await_count==1


@pytest.mark.asyncio
@pytest.mark.parametrize("field",["tenant_id","principal_id","principal_type","operation","key_hash","expires_at"])
async def test_reservation_context_is_bound_before_service_call(workflow_case,field):
    c=workflow_case;original=c.records.reserve_record
    async def corrupt(**kw):
        row,new=await original(**kw)
        setattr(row,field,uuid4() if field in ("tenant_id","principal_id") else c.now if field=="expires_at" else "wrong")
        return row,new
    c.records.reserve_record=corrupt
    with pytest.raises(ConstructionActivityTerminalOperationError):await run(c)
    c.repo.complete_activity.assert_not_called();c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["audit","completion","commit","projection","completed_id","cancelled"])
async def test_failures_roll_back_and_private_details_are_sanitized(workflow_case,failure):
    c=workflow_case
    if failure=="audit":c.events.create_event.side_effect=RuntimeError("private-error")
    elif failure=="completion":c.records.fail_complete=True
    elif failure=="commit":c.session.commit.side_effect=RuntimeError("private-error")
    elif failure=="cancelled":c.records.reserve_record=AsyncMock(side_effect=asyncio.CancelledError())
    elif failure=="projection":
        original=c.repo.complete_activity.side_effect
        async def malformed(**kw):
            row=await original(**kw);row.due_at=c.now.replace(tzinfo=None);return row
        c.repo.complete_activity.side_effect=malformed
    else:
        original=c.records.complete_record
        async def changed(**kw):
            row=await original(**kw);row.id=uuid4();return row
        c.records.complete_record=changed
    with pytest.raises(asyncio.CancelledError if failure=="cancelled" else ConstructionActivityTerminalOperationError) as caught:await run(c)
    assert "private-error" not in str(caught.value);c.session.rollback.assert_awaited_once()
    if failure!="commit":c.session.commit.assert_not_called()


def test_wiring_requires_one_session_and_encryption(workflow_case):
    c=workflow_case
    factory=build_construction_activity_terminal_workflow(session=c.session,encryption_key=Fernet.generate_key().decode(),now_provider=lambda:c.now)
    assert factory.service.session is c.session and factory.idempotency.repository.session is c.session
    c.records.session=object()
    with pytest.raises(ValueError):ConstructionActivityTerminalWorkflow(session=c.session,activity_service=c.service,idempotency_service=c.idempotency)


@pytest.fixture
def sql_workflow(sql_terminal):
    c=sql_terminal
    c.session.sync.execute(text("CREATE TABLE lead_test_idempotency(id TEXT PRIMARY KEY,tenant_id TEXT,principal_type TEXT,"
        "principal_id TEXT,operation TEXT,key_hash TEXT,request_hash TEXT,expires_at TEXT,status TEXT,"
        "response_status INTEGER,response_ciphertext BLOB,UNIQUE(tenant_id,principal_type,principal_id,operation,key_hash))"))
    c.session.sync.commit()
    original=c.repo.get_active_activity_for_update_by_links
    async def hydrate(**kw):
        row=await original(**kw)
        if row is not None:
            for field in ("due_at","created_at","updated_at","completed_at","cancelled_at"):
                value=getattr(row,field)
                if value is not None and value.tzinfo is None:set_committed_value(row,field,value.replace(tzinfo=timezone.utc))
        return row
    c.repo.get_active_activity_for_update_by_links=hydrate
    original_mutation=c.repo._finalize_activity
    async def mutate_with_sqlite_dates(**kw):
        row=await original_mutation(**kw)
        if row is not None:
            for field in ("due_at","created_at","updated_at","completed_at","cancelled_at"):
                value=getattr(row,field)
                if value is not None and value.tzinfo is None:set_committed_value(row,field,value.replace(tzinfo=timezone.utc))
        return row
    c.repo._finalize_activity=mutate_with_sqlite_dates
    c.records=SqlRecords(c.session)
    c.audit_insert_seen=False;c.completion_write_seen=False
    original_audit=c.events.create_event
    async def audit_with_receipt(**kw):
        try:return await original_audit(**kw)
        finally:
            c.audit_insert_seen=c.session.sync.execute(text("SELECT count(*) FROM terminal_service_audit")).scalar_one()==1
    c.events.create_event=audit_with_receipt
    original_completion=c.records.complete_record
    async def completion_with_receipt(**kw):
        try:return await original_completion(**kw)
        finally:
            c.completion_write_seen=c.session.sync.execute(text("SELECT count(*) FROM lead_test_idempotency WHERE status='completed'")).scalar_one()==1
    c.records.complete_record=completion_with_receipt
    setup(c);c.session.sync.begin()
    return c


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,status",[("complete","completed"),("cancel","cancelled")])
async def test_real_sql_commit_and_replay_with_single_audit_and_reservation(sql_workflow,operation,status):
    c=sql_workflow;first=await run(c,operation)
    c.session.sync.begin();assert await run(c,operation)==first
    with c.session.sync.get_bind().connect() as connection:
        row=connection.execute(select(TABLE).where(TABLE.c.id==c.row.id)).mappings().one()
        assert row["status"]==status and row["row_version"]==5
        for table in ("terminal_service_audit","lead_test_idempotency"):
            assert connection.execute(text("SELECT count(*) FROM "+table)).scalar_one()==1
    assert await c.repo.get_next_action_for_lead(tenant_id=c.tenant,lead_id=c.lead.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation",["complete","cancel"])
@pytest.mark.parametrize("failure",["audit","completion","commit"])
async def test_real_sql_rolls_back_update_audit_and_reservation_together(sql_workflow,operation,failure):
    c=sql_workflow
    if failure=="audit":c.events.fail=True
    elif failure=="completion":c.records.fail_complete=True
    else:c.session.commit.side_effect=RuntimeError("private-commit-failure")
    with pytest.raises(ConstructionActivityTerminalOperationError):await run(c,operation)
    assert c.audit_insert_seen is True
    assert c.completion_write_seen is (failure!="audit")
    with c.session.sync.get_bind().connect() as connection:
        row=connection.execute(select(TABLE).where(TABLE.c.id==c.row.id)).mappings().one()
        assert row["status"]=="planned" and row["row_version"]==4 and row["result"] is None
        assert row["completed_at"] is None and row["cancelled_at"] is None
        for table in ("terminal_service_audit","lead_test_idempotency"):
            assert connection.execute(text("SELECT count(*) FROM "+table)).scalar_one()==0
    c.session.rollback.assert_awaited_once()
