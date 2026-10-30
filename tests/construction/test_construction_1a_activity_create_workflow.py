import asyncio
from datetime import timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, text

from services.api_idempotency import ApiIdempotencyKeyReusedError, ApiIdempotencyService, ApiIdempotencyResponseCodec
from services.construction.construction_activity_create import ConstructionActivityCreateOperationError, ConstructionActivityCreateNotFoundError
from services.construction.construction_activity_create_workflow import (
    ACTIVITY_CREATE_OPERATION, ConstructionActivityCreateWorkflow, ConstructionActivityIdempotencyInProgressError,
    ConstructionActivityReplayUnavailableError, build_construction_activity_create_workflow,
)
from services.construction.construction_activity_response import PUBLIC_ACTIVITY_CREATED_FIELDS
from services.construction.construction_activity_rules import ConstructionActivityValidationError
from services.construction.construction_permissions import ConstructionPermissionDeniedError
from test_construction_1a_activity_create_service import activity_create_case, sql_activity_create
from test_construction_1a_activity_model import TABLE, activity_db
from test_construction_1a_lead_model import lead_db
from test_construction_1a_lead_create_workflow import MemoryRecords, SqlRecords


@pytest.fixture
def workflow_case(activity_create_case):
    c=activity_create_case
    original=c.repo.create_activity.side_effect
    async def create(**kwargs):
        c.row=await original(**kwargs)
        c.row.internal_marker="private-internal"
        return c.row
    c.repo.create_activity.side_effect=create
    async def read(**kwargs):
        c.order.append("activity_lock")
        return c.row
    c.repo.get_active_activity_for_update=AsyncMock(side_effect=read)
    async def commit(): c.order.append("commit")
    async def rollback(): c.order.append("rollback")
    c.session.commit.side_effect=commit; c.session.rollback.side_effect=rollback
    c.records=MemoryRecords(c.session,c.order)
    c.codec=ApiIdempotencyResponseCodec(encryption_key=Fernet.generate_key().decode())
    c.idempotency=ApiIdempotencyService(repository=c.records,response_codec=c.codec)
    c.workflow=ConstructionActivityCreateWorkflow(session=c.session,activity_service=c.service,idempotency_service=c.idempotency)
    return c


async def run(c,**changes):
    data=dict(actor=c.actor,access_context=c.context,activity_type=" CALL ",due_at=c.now,
        timezone_code=" Europe/Lisbon ",lead_id=c.lead.id,comment=" Private comment ",
        idempotency_key="activity-private-key",trace_id="activity-workflow-test")
    data.update(changes)
    return await c.workflow.create_activity(**data)


def record(c): return next(iter(c.records.rows.values()))


@pytest.mark.asyncio
async def test_activity_audit_encrypted_bound_completion_and_commit_are_one_ordered_operation(workflow_case):
    c=workflow_case
    response=await run(c)
    assert set(response)==PUBLIC_ACTIVITY_CREATED_FIELDS
    assert response["id"]==str(c.row.id) and response["tenant_id"]==str(c.tenant)
    assert response["status"]=="planned" and response["row_version"]==1 and response["type"]=="call"
    assert response["timezone"]=="Europe/Lisbon" and response["comment"]=="Private comment"
    assert c.order==["reserve","lead_lock","create","audit","complete","commit"]
    saved=record(c)
    snapshot=c.codec.decrypt(saved.response_ciphertext)
    assert snapshot["data"]==response
    assert snapshot["binding"]==dict(tenant_id=str(c.tenant),principal_type="user",principal_id=str(c.user),
        operation=ACTIVITY_CREATE_OPERATION,key_hash=saved.key_hash,request_hash=saved.request_hash)
    assert saved.expires_at==c.now+timedelta(hours=24) and saved.response_status==201
    assert b"Private comment" not in saved.response_ciphertext and b"activity-private-key" not in saved.response_ciphertext
    assert "binding" not in response and "internal_marker" not in response
    c.session.commit.assert_awaited_once(); c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_create_replay_preserves_original_snapshot_after_completion_and_metadata_changes(workflow_case):
    c=workflow_case
    first=await run(c)
    c.row.status,c.row.row_version,c.row.comment="completed",7,"Later comment"
    c.order.clear()
    assert await run(c)==first
    assert c.order==["reserve","lead_lock","activity_lock","commit"]
    c.repo.create_activity.assert_awaited_once(); c.events.create_event.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes",[dict(activity_type="message"),dict(activity_type="call"),dict(comment="Private comment"),
    dict(comment="Other"),dict(timezone_code="UTC"),dict(timezone_code="Europe/Lisbon"),dict(lead_id=uuid4()),
    dict(client_id=uuid4()),dict(responsible_user_id=uuid4()),dict(due_at_offset=True)])
async def test_same_key_with_changed_supplied_command_returns_key_reused(workflow_case,changes):
    c=workflow_case
    await run(c)
    if changes.pop("due_at_offset",False): changes["due_at"]=c.now.astimezone(timezone(timedelta(hours=3)))
    with pytest.raises(ApiIdempotencyKeyReusedError): await run(c,**changes)
    c.repo.create_activity.assert_awaited_once(); c.events.create_event.assert_awaited_once()
    assert c.session.commit.await_count==1 and c.session.rollback.await_count==1


@pytest.mark.asyncio
@pytest.mark.parametrize("key",[None,"","   ",True,17,"x"*256])
async def test_key_is_required_before_reservation(workflow_case,key):
    c=workflow_case
    with pytest.raises(ConstructionActivityValidationError): await run(c,idempotency_key=key)
    assert not c.records.rows
    c.repo.create_activity.assert_not_called(); c.session.commit.assert_not_called()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_processing_reservation_is_conflict_without_second_insert(workflow_case):
    c=workflow_case
    await run(c)
    saved=record(c); saved.status="processing"; saved.response_status=None; saved.response_ciphertext=None
    with pytest.raises(ConstructionActivityIdempotencyInProgressError) as caught: await run(c)
    assert caught.value.status_code==409 and caught.value.code=="idempotency_in_progress"
    c.repo.create_activity.assert_awaited_once(); c.events.create_event.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("replay",[False,True])
@pytest.mark.parametrize("failure",["membership","revoked","expired","inactive"])
async def test_current_access_is_required_before_reservation_for_fresh_and_replay(workflow_case,replay,failure):
    c=workflow_case
    if replay: await run(c)
    before=list(c.order)
    if failure=="membership": c.access.has_active_platform_membership.return_value=False
    elif failure=="revoked": c.grant.status="revoked"
    elif failure=="expired": c.grant.expires_at=c.now
    else: c.actor.status="blocked"
    with pytest.raises(ConstructionPermissionDeniedError): await run(c)
    assert c.order==before+["rollback"]


@pytest.mark.asyncio
@pytest.mark.parametrize("replay",[False,True])
async def test_access_loss_after_reservation_cannot_create_or_replay(workflow_case,replay):
    c=workflow_case
    if replay: await run(c)
    original=c.records.reserve_record
    async def revoke(**kwargs):
        result=await original(**kwargs); c.grant.status="revoked"; return result
    c.records.reserve_record=revoke
    with pytest.raises(ConstructionPermissionDeniedError): await run(c)
    assert c.repo.create_activity.await_count==int(replay) and c.session.commit.await_count==int(replay)


@pytest.mark.asyncio
async def test_access_loss_after_completion_prevents_commit(workflow_case):
    c=workflow_case
    original=c.records.complete_record
    async def revoke(**kwargs):
        result=await original(**kwargs); c.grant.status="revoked"; return result
    c.records.complete_record=revoke
    with pytest.raises(ConstructionPermissionDeniedError): await run(c)
    c.session.commit.assert_not_called(); c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["missing","deleted","foreign","creator","links","version","status"])
async def test_replay_checks_current_activity_identity_owner_links_and_availability(workflow_case,failure):
    c=workflow_case
    await run(c)
    error=ConstructionActivityReplayUnavailableError
    if failure=="missing": c.repo.get_active_activity_for_update.side_effect=None; c.repo.get_active_activity_for_update.return_value=None
    elif failure=="deleted": c.row.deleted_at=c.now
    elif failure=="foreign": c.row.tenant_id=uuid4()
    elif failure=="creator": c.row.created_by_user_id=uuid4()
    elif failure=="links": c.row.lead_id=uuid4()
    elif failure=="version": c.row.row_version=True; error=ConstructionActivityCreateOperationError
    else: c.row.status="unexpected"; error=ConstructionActivityCreateOperationError
    with pytest.raises(error): await run(c)
    c.repo.create_activity.assert_awaited_once(); assert c.session.commit.await_count==1


@pytest.mark.asyncio
async def test_replay_checks_linked_resource_not_deleted(workflow_case):
    c=workflow_case
    await run(c); c.lead.deleted_at=c.now
    with pytest.raises(ConstructionActivityCreateNotFoundError): await run(c)
    c.repo.create_activity.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("field",["tenant_id","principal_id","principal_type","operation","key_hash","request_hash","expires_at"])
async def test_reservation_binding_cannot_cross_context(workflow_case,field):
    c=workflow_case
    await run(c)
    setattr(record(c),field,uuid4() if field in ("tenant_id","principal_id") else c.now.replace(tzinfo=None) if field=="expires_at" else "wrong")
    with pytest.raises((ConstructionActivityCreateOperationError,ApiIdempotencyKeyReusedError)): await run(c)
    assert c.session.commit.await_count==1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["binding","extra","tenant","creator","version","deadline","ciphertext"])
async def test_corrupt_or_swapped_encrypted_snapshot_is_not_returned(workflow_case,failure):
    c=workflow_case
    await run(c)
    saved=record(c); snapshot=c.codec.decrypt(saved.response_ciphertext)
    if failure=="binding": snapshot["binding"]["principal_id"]=str(uuid4())
    elif failure=="extra": snapshot["data"]["internal"]="private snapshot detail"
    elif failure=="tenant": snapshot["data"]["tenant_id"]=str(uuid4())
    elif failure=="creator": snapshot["data"]["created_by_user_id"]=str(uuid4())
    elif failure=="version": snapshot["data"]["row_version"]=True
    elif failure=="deadline": snapshot["data"]["due_at"]="2026-10-13T20:00:00Z"
    else: saved.response_ciphertext=b"invalid"
    if failure!="ciphertext": saved.response_ciphertext=c.codec.encrypt(snapshot)
    with pytest.raises(ConstructionActivityCreateOperationError): await run(c)
    assert c.session.commit.await_count==1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["audit","complete","commit","transaction","command","clock","new_record_id"])
async def test_any_creation_completion_or_commit_failure_rolls_back(workflow_case,failure):
    c=workflow_case
    options={}; error=ConstructionActivityCreateOperationError
    if failure=="audit": c.events.create_event.side_effect=RuntimeError("private audit diagnostic")
    elif failure=="complete": c.records.fail_complete=True
    elif failure=="commit": c.session.commit.side_effect=RuntimeError("private commit diagnostic")
    elif failure=="transaction": c.session.in_transaction=lambda: False
    elif failure=="command": options["lead_id"]=None; error=ConstructionActivityValidationError
    elif failure=="clock": c.service.now_provider=lambda: c.now.replace(tzinfo=None)
    else:
        original=c.records.complete_record
        async def changed_id(**kwargs):
            row=await original(**kwargs); row.id=uuid4(); return row
        c.records.complete_record=changed_id
    with pytest.raises(error) as caught: await run(c,**options)
    assert "private" not in str(caught.value)
    c.session.rollback.assert_awaited_once()
    if failure!="commit": c.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_cancellation_after_actual_service_work_rolls_back(workflow_case):
    c=workflow_case
    original=c.service.create_activity
    async def cancel(**kwargs):
        await original(**kwargs); raise asyncio.CancelledError()
    c.service.create_activity=cancel
    with pytest.raises(asyncio.CancelledError): await run(c)
    c.session.rollback.assert_awaited_once(); c.session.commit.assert_not_called()


def test_factory_requires_encryption_and_one_shared_session(workflow_case):
    c=workflow_case
    with pytest.raises(ValueError): build_construction_activity_create_workflow(session=c.session,encryption_key="")
    workflow=build_construction_activity_create_workflow(session=c.session,encryption_key=Fernet.generate_key().decode(),now_provider=lambda: c.now)
    assert workflow.session is workflow.service.session is workflow.idempotency.repository.session is c.session
    c.records.session=object()
    with pytest.raises(ValueError): ConstructionActivityCreateWorkflow(session=c.session,activity_service=c.service,idempotency_service=c.idempotency)


@pytest.fixture
def sql_workflow(sql_activity_create):
    c=sql_activity_create
    with c.session.sync.begin():
        c.session.sync.execute(text("CREATE TABLE lead_test_idempotency(id TEXT PRIMARY KEY,tenant_id TEXT,principal_type TEXT,"
            "principal_id TEXT,operation TEXT,key_hash TEXT,request_hash TEXT,expires_at TEXT,status TEXT,"
            "response_status INTEGER,response_ciphertext BLOB,UNIQUE(tenant_id,principal_type,principal_id,operation,key_hash))"))
    c.records=SqlRecords(c.session)
    c.idempotency=ApiIdempotencyService(repository=c.records,response_codec=ApiIdempotencyResponseCodec(encryption_key=Fernet.generate_key().decode()))
    c.workflow=ConstructionActivityCreateWorkflow(session=c.session,activity_service=c.service,idempotency_service=c.idempotency)
    c.session.sync.begin()
    return c


@pytest.mark.asyncio
async def test_real_database_commits_activity_audit_and_reservation_and_replays_without_duplicates(sql_workflow):
    c=sql_workflow
    first=await run(c)
    assert not c.session.in_transaction()
    c.session.sync.begin()
    assert await run(c)==first
    assert c.session.commit.await_count==2 and c.session.rollback.await_count==0
    with c.session.sync.get_bind().connect() as connection:
        assert len(connection.execute(select(TABLE.c.id)).all())==1
        for table in ("activity_creation_audit","lead_test_idempotency"):
            assert connection.execute(text("SELECT count(*) FROM "+table)).scalar_one()==1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["audit","complete","commit"])
async def test_real_failure_automatically_rolls_back_activity_audit_and_idempotency(sql_workflow,failure):
    c=sql_workflow
    if failure=="audit": c.events.fail=True
    elif failure=="complete": c.records.fail_complete=True
    else: c.session.commit.side_effect=RuntimeError("private failure before real commit")
    with pytest.raises(ConstructionActivityCreateOperationError): await run(c)
    c.session.rollback.assert_awaited_once()
    with c.session.sync.get_bind().connect() as connection:
        assert connection.execute(select(TABLE.c.id)).all()==[]
        for table in ("activity_creation_audit","lead_test_idempotency"):
            assert connection.execute(text("SELECT count(*) FROM "+table)).scalar_one()==0
