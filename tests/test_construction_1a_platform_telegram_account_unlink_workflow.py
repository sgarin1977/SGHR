"""Unlink replay and real local transaction rollback, without live services."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Column, MetaData, Table, Uuid, select, update
from sqlalchemy.orm.attributes import set_committed_value

from database.models import ApiIdempotencyRecord, EventLog, User, UserAccount
from services.api_identity import ApiIdentityAccessError
from services.api_idempotency import ApiIdempotencyKeyReusedError, hash_request_payload
from services.platform_telegram_account_unlink import UNLINK_OPERATION
from services.platform_telegram_account_unlink_workflow import (
    PlatformTelegramAccountUnlinkWorkflow, build_platform_telegram_account_unlink_workflow,
)
from services.platform_telegram_binding_consume import (
    PlatformTelegramBindingOperationError as OperationError,
    PlatformTelegramBindingVersionConflictError as Conflict,
)
from services.platform_telegram_binding_workflow import PlatformTelegramBindingIdempotencyInProgressError as InProgress
from test_construction_1a_platform_telegram_account_unlink_service import environment, snapshot, store


KEY=Fernet.generate_key().decode('ascii')


@pytest.fixture
def workflow(environment):
    c=environment
    c.session.rollback()
    metadata=MetaData()
    Table('tenants',metadata,Column('id',Uuid,primary_key=True))
    table=ApiIdempotencyRecord.__table__.to_metadata(metadata)
    table.create(c.engine)
    original=c.bridge.execute
    class Result:
        def __init__(self,result):self.result=result
        def scalar_one_or_none(self):
            row=self.result.scalar_one_or_none()
            if isinstance(row,ApiIdempotencyRecord):
                # SQLite loses timezone metadata; this explicit test adapter
                # restores UTC, without asserting native PostgreSQL behavior.
                for name in ('created_at','completed_at','expires_at'):
                    value=getattr(row,name)
                    if value is not None and value.tzinfo is None:
                        set_committed_value(row,name,value.replace(tzinfo=timezone.utc))
            return row
        def __getattr__(self,name):return getattr(self.result,name)
    async def execute(statement):return Result(await original(statement))
    c.bridge.execute=execute
    c.now=datetime(2026,10,16,12,tzinfo=timezone.utc)
    c.workflow=build_platform_telegram_account_unlink_workflow(session=c.bridge,encryption_key=KEY,now_provider=lambda:c.now)
    c.workflow.service=c.service
    c.session.begin()
    c.workflow_before=state(c)
    return c


def state(c):
    return deepcopy(dict(**snapshot(c),idempotency=[tuple(row) for row in c.session.execute(
        select(ApiIdempotencyRecord.__table__).order_by(ApiIdempotencyRecord.id)).all()]))


async def unlink(c,key='private-unlink-request-key',**changes):
    if not c.session.in_transaction():c.session.begin()
    values=dict(actor=c.actor,account_id=c.account.id,expected_row_version=1,idempotency_key=key,trace_id='unlink:workflow:local')
    values.update(changes)
    return await c.workflow.unlink_account(**values)


def unchanged(c,before=None):
    assert state(c)==(c.workflow_before if before is None else before)


@pytest.mark.asyncio
async def test_unlink_commits_account_exact_audit_and_encrypted_result_in_one_transaction(workflow):
    c=workflow
    result=await unlink(c)
    assert result.changed and result.row_version==2 and result.status=='unlinked'
    assert c.bridge.commits==1 and not c.session.in_transaction()
    record=c.session.execute(select(ApiIdempotencyRecord)).scalar_one()
    audit=c.session.execute(select(EventLog)).scalar_one()
    assert record.operation==UNLINK_OPERATION and record.status=='completed' and record.response_status==200
    assert record.tenant_id==audit.tenant_id==c.tenant and record.principal_id==audit.user_id==c.owner
    assert record.principal_type=='user' and record.completed_at is not None
    payload=dict(account_id=str(c.account.id),expected_row_version=1)
    assert record.request_hash==hash_request_payload(payload)
    assert record.key_hash==hashlib.sha256(b'private-unlink-request-key').hexdigest()
    decoded=c.workflow.idempotency.response_codec.decrypt(record.response_ciphertext)
    assert decoded['data']==dict(account_id=str(c.account.id),user_id=str(c.owner),tenant_id=str(c.tenant),
        status='unlinked',row_version=2,changed=True)
    assert decoded['binding']==dict(tenant_id=str(c.tenant),principal_type='user',principal_id=str(c.owner),
        operation=UNLINK_OPERATION,key_hash=record.key_hash,request_hash=record.request_hash)
    serialized=json.dumps(decoded)+json.dumps(audit.payload)+repr(result)
    for private in ('123456789','retained-profile','private-unlink-request-key','raw_profile','refresh_token_hash'):
        assert private not in serialized
    assert snapshot(c)['api_auth_sessions']==c.before['api_auth_sessions']
    assert len(c.session.execute(select(EventLog.id)).all())==1


@pytest.mark.asyncio
async def test_same_key_replays_original_success_despite_old_expected_version_and_new_trace(workflow):
    c=workflow
    first=await unlink(c)
    before=state(c)
    c.service.unlink_account=AsyncMock(side_effect=AssertionError('Replay must not reapply unlink.'))
    repeated=await unlink(c,trace_id='different-attempt-trace')
    assert repeated==first and c.bridge.commits==2
    c.service.unlink_account.assert_not_awaited()
    unchanged(c,before)


@pytest.mark.asyncio
async def test_old_key_never_unlinks_a_later_reactivated_identity(workflow):
    c=workflow
    first=await unlink(c)
    # Simulate a later versioned state; this does not implement the proof-based
    # relink operation or establish its native acceptance.
    c.session.execute(update(UserAccount).where(UserAccount.id==c.account.id).values(status='active',row_version=3))
    c.session.commit();before=state(c)
    c.service.unlink_account=AsyncMock(side_effect=AssertionError('Historical replay cannot mutate current binding.'))
    assert await unlink(c)==first
    assert c.session.get(UserAccount,c.account.id).status=='active'
    assert c.session.get(UserAccount,c.account.id).row_version==3
    unchanged(c,before)


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['account','version'])
async def test_same_key_with_another_command_is_conflict_without_writes(workflow,change):
    c=workflow
    await unlink(c);before=state(c)
    options=dict(account_id=c.second.id) if change=='account' else dict(expected_row_version=2)
    with pytest.raises(ApiIdempotencyKeyReusedError):await unlink(c,**options)
    unchanged(c,before)
    assert c.bridge.commits==1


@pytest.mark.asyncio
@pytest.mark.parametrize('key',[None,'',' ','x'*256,True])
async def test_idempotency_key_is_required_before_any_query(workflow,key):
    c=workflow
    with pytest.raises(ValueError):await unlink(c,key=key)
    assert c.bridge.statements==[] and c.bridge.commits==0
    c.identity.require_actor.assert_not_awaited()
    unchanged(c)


@pytest.mark.asyncio
async def test_processing_reservation_returns_in_progress_and_is_not_overwritten(workflow):
    c=workflow
    c.session.add(ApiIdempotencyRecord(id=uuid4(),tenant_id=c.tenant,principal_type='user',principal_id=c.owner,
        operation=UNLINK_OPERATION,key_hash=hashlib.sha256(b'private-unlink-request-key').hexdigest(),
        request_hash=hash_request_payload(dict(account_id=str(c.account.id),expected_row_version=1)),
        status='processing',created_at=c.now,expires_at=c.now+timedelta(hours=24)))
    c.session.commit();before=state(c)
    with pytest.raises(InProgress) as error:await unlink(c)
    assert error.value.status_code==409 and error.value.code=='idempotency_in_progress'
    unchanged(c,before)


@pytest.mark.asyncio
async def test_current_version_noop_is_cached_without_another_audit(workflow):
    c=workflow
    await unlink(c)
    result=await unlink(c,key='new-noop-key',expected_row_version=2)
    assert result.changed is False and result.row_version==2
    before=state(c)
    assert await unlink(c,key='new-noop-key',expected_row_version=2)==result
    unchanged(c,before)
    assert len(c.session.execute(select(ApiIdempotencyRecord.id)).all())==2
    assert len(c.session.execute(select(EventLog.id)).all())==1


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['membership','blocked_user','owner_changed'])
async def test_replay_rechecks_platform_authorization_and_account_ownership(workflow,fault):
    c=workflow
    await unlink(c)
    if fault=='membership':c.identity.require_actor.side_effect=ApiIdentityAccessError('Membership unavailable.')
    elif fault=='blocked_user':c.session.execute(update(User).where(User.id==c.owner).values(status='blocked'))
    else:c.session.execute(update(UserAccount).where(UserAccount.id==c.account.id).values(user_id=c.other))
    c.session.commit();before=state(c)
    with pytest.raises(ApiIdentityAccessError):await unlink(c)
    unchanged(c,before)
    assert c.bridge.commits==1


@pytest.mark.asyncio
async def test_identical_key_is_isolated_by_platform_user(workflow):
    c=workflow
    await unlink(c)
    c.actor=replace(c.actor,user_id=c.other);c.identity.require_actor.return_value=c.actor
    result=await unlink(c,account_id=c.foreign.id)
    assert result.user_id==c.other and result.account_id==c.foreign.id
    rows=c.session.execute(select(ApiIdempotencyRecord)).scalars().all()
    assert len(rows)==2 and len({r.key_hash for r in rows})==1
    assert {r.principal_id for r in rows}=={c.owner,c.other}


@pytest.mark.asyncio
async def test_identical_key_is_isolated_by_trusted_tenant_context(workflow):
    c=workflow
    await unlink(c)
    second_tenant=uuid4()
    tenants=Table('tenants',MetaData(),Column('id',Uuid,primary_key=True))
    c.session.execute(tenants.insert().values(id=second_tenant))
    c.session.commit()
    c.actor=replace(c.actor,tenant_id=second_tenant);c.identity.require_actor.return_value=c.actor
    result=await unlink(c,expected_row_version=2)
    assert result.tenant_id==second_tenant and result.changed is False
    records=c.session.execute(select(ApiIdempotencyRecord)).scalars().all()
    assert len(records)==2 and {r.tenant_id for r in records}=={c.tenant,second_tenant}
    assert len(c.session.execute(select(EventLog.id)).all())==1


@pytest.mark.asyncio
async def test_expired_idempotency_does_not_waive_current_version_validation(workflow):
    c=workflow
    await unlink(c);before=state(c)
    c.now+=timedelta(hours=24,seconds=1)
    with pytest.raises(Conflict):await unlink(c)
    unchanged(c,before)


@pytest.mark.asyncio
async def test_expired_key_can_renew_under_shared_retention_with_a_current_noop(workflow):
    c=workflow
    await unlink(c)
    record_id=c.session.execute(select(ApiIdempotencyRecord.id)).scalar_one()
    c.now+=timedelta(hours=24,seconds=1)
    result=await unlink(c,expected_row_version=2)
    assert result.changed is False and result.row_version==2
    record=c.session.execute(select(ApiIdempotencyRecord)).scalar_one()
    assert record.id==record_id and len(c.session.execute(select(EventLog.id)).all())==1


@pytest.mark.asyncio
@pytest.mark.parametrize('stage',['audit','completion','encryption','commit'])
async def test_failure_rolls_back_account_audit_and_reservation_together(workflow,stage):
    c=workflow
    if stage=='audit':
        original=c.service.events.create_event
        async def fail(**kwargs):
            row=await original(**kwargs)
            assert row.id is not None and c.account.status=='unlinked'
            assert len(c.session.execute(select(ApiIdempotencyRecord.id)).all())==1
            raise RuntimeError('Failure after audit flush.')
        c.service.events.create_event=fail
    elif stage=='completion':
        original=c.workflow.idempotency.complete
        async def fail(**kwargs):
            row=await original(**kwargs)
            assert row.status=='completed' and c.account.status=='unlinked'
            assert len(c.session.execute(select(EventLog.id)).all())==1
            raise RuntimeError('Failure after completion flush.')
        c.workflow.idempotency.complete=fail
    elif stage=='encryption':
        def fail(_):raise RuntimeError('Codec unavailable.')
        c.workflow.idempotency.response_codec.encrypt=fail
    else:c.bridge.commit=AsyncMock(side_effect=RuntimeError('Commit unavailable.'))
    with pytest.raises(RuntimeError):await unlink(c)
    unchanged(c)


@pytest.mark.asyncio
async def test_retry_resolves_a_successful_commit_whose_response_was_lost(workflow):
    c=workflow
    original=c.bridge.commit
    async def lost():
        await original()
        raise RuntimeError('Simulated response loss after local commit.')
    c.bridge.commit=lost
    with pytest.raises(RuntimeError):await unlink(c)
    assert c.account.status=='unlinked'
    before=state(c)
    c.bridge.commit=original
    result=await unlink(c)
    assert result.changed is True and result.row_version==2
    unchanged(c,before)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['tenant','principal','operation'])
async def test_reservation_context_must_match_the_current_command(workflow,fault):
    c=workflow
    original=c.workflow.idempotency.reserve
    async def corrupt(**kwargs):
        reservation=await original(**kwargs)
        field={'tenant':'tenant_id','principal':'principal_id','operation':'operation'}[fault]
        set_committed_value(reservation.record,field,'wrong-operation' if fault=='operation' else uuid4())
        return reservation
    c.workflow.idempotency.reserve=corrupt
    with pytest.raises(OperationError):await unlink(c)
    c.session.expunge_all();unchanged(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['account','tenant','version','extra_field','binding'])
async def test_invalid_encrypted_snapshot_is_not_reported_as_success(workflow,fault):
    c=workflow
    await unlink(c)
    row=c.session.execute(select(ApiIdempotencyRecord)).scalar_one()
    payload=c.workflow.idempotency.response_codec.decrypt(row.response_ciphertext)
    if fault=='account':payload['data']['account_id']=str(c.second.id)
    elif fault=='tenant':payload['data']['tenant_id']=str(uuid4())
    elif fault=='version':payload['data']['row_version']=True
    elif fault=='extra_field':payload['data']['telegram_secret']='must-not-be-returned'
    else:payload['binding']['operation']='different-operation'
    row.response_ciphertext=c.workflow.idempotency.response_codec.encrypt(payload)
    c.session.commit();before=state(c)
    with pytest.raises(OperationError):await unlink(c)
    unchanged(c,before)


@pytest.mark.asyncio
async def test_another_encryption_key_cannot_replace_or_reapply_completed_unlink(workflow):
    c=workflow
    await unlink(c);before=state(c)
    replacement=build_platform_telegram_account_unlink_workflow(session=c.bridge,
        encryption_key=Fernet.generate_key().decode('ascii'),now_provider=lambda:c.now)
    replacement.service=c.service;c.workflow=replacement
    with pytest.raises(InvalidToken):await unlink(c)
    unchanged(c,before)


@pytest.mark.asyncio
async def test_no_active_transaction_fails_without_reserving_or_mutating(workflow):
    c=workflow
    identifier=c.account.id;c.session.rollback()
    with pytest.raises(OperationError):
        await c.workflow.unlink_account(actor=c.actor,account_id=identifier,expected_row_version=1,idempotency_key='key')
    assert c.bridge.statements==[]
    unchanged(c)


def test_workflow_rejects_cross_session_or_nonatomic_idempotency(workflow):
    c=workflow
    for repo in (SimpleNamespace(session=object(),reserve_record=lambda:None),SimpleNamespace(session=c.bridge)):
        fake=SimpleNamespace(repository=repo,response_codec=c.workflow.idempotency.response_codec)
        with pytest.raises(ValueError):PlatformTelegramAccountUnlinkWorkflow(unlink_service=c.service,idempotency_service=fake)
