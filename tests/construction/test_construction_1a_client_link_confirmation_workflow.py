import asyncio
from dataclasses import replace
from contextlib import nullcontext
from datetime import datetime,timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID,uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select,text,update

from database.models import ConstructionClient,ConstructionClientUserLinkRequest as Request
from services.api_identity import ApiIdentityAccessError
from services.api_idempotency import ApiIdempotencyService,ApiIdempotencyResponseCodec,ApiIdempotencyKeyReusedError
from services.construction.construction_client_link_confirmation import ConstructionClientLinkConfirmationDeniedError as Denied
from services.construction.construction_client_link_recipient_context import ConstructionClientLinkRecipientContextCodec
from services.construction.construction_client_link_request_issuance import ConstructionClientLinkIssuanceResult
from services.construction.construction_client_link_confirmation_workflow import (
    ConstructionClientLinkConfirmationWorkflow as Workflow,ConstructionClientLinkIdempotencyInProgressError,
    build_construction_client_link_confirmation_workflow,CONFIRM,
)
from services.construction.construction_clients import ConstructionClientOperationError,ConstructionClientValidationError
from services.construction.construction_permissions import ConstructionPermissionDeniedError
from test_construction_1a_client_link_confirmation import sql_confirmation,seed_request,TOKEN,NOW
from test_construction_1a_client_link_request_repository import request_repo
from test_construction_1a_client_link_request_model import requests_db


def serialize(value):
    if isinstance(value,UUID):return str(value)
    if isinstance(value,datetime):return value.isoformat()
    if isinstance(value,bytes):return value.hex()
    raise TypeError()


@pytest.fixture
async def pipeline(sql_confirmation):
    c=sql_confirmation
    await seed_request(c)
    c.sync.execute(text('CREATE TABLE client_link_replay_ledger(id TEXT PRIMARY KEY,data TEXT NOT NULL)'))
    c.sync.commit()
    flags=SimpleNamespace(fail_complete=False,fail_commit=False,corrupt_complete=False,cancel_complete=False)
    class Ledger:
        session=c.session
        calls=0
        def save(self,row):
            # Same caller-owned transaction as the real client/request/audit writes.
            c.sync.execute(text('INSERT INTO client_link_replay_ledger VALUES(:id,:data) ON CONFLICT(id) DO UPDATE SET data=excluded.data'),
                dict(id=str(row.id),data=json.dumps(vars(row),default=serialize,sort_keys=True)))
        def all(self):
            records=[]
            for raw, in c.sync.execute(text('SELECT data FROM client_link_replay_ledger')):
                data=json.loads(raw)
                for key in ('id','tenant_id','principal_id'):data[key]=UUID(data[key])
                for key in ('created_at','expires_at','completed_at'):
                    if data[key] is not None:data[key]=datetime.fromisoformat(data[key])
                if data['response_ciphertext'] is not None:data['response_ciphertext']=bytes.fromhex(data['response_ciphertext'])
                records.append(SimpleNamespace(**data))
            return records
        async def reserve_record(self,**fields):
            self.calls+=1
            for row in self.all():
                if all(getattr(row,k)==fields[k] for k in ('tenant_id','principal_type','principal_id','operation','key_hash')):
                    if row.expires_at>fields['now']:return row,False
                    # Emulate the shared repository's expired-record reset,
                    # which remains rolled back if the one-time service denies.
                    row.request_hash=fields['request_hash'];row.expires_at=fields['expires_at']
                    row.status='processing';row.response_status=row.response_ciphertext=row.completed_at=None
                    self.save(row)
                    return row,True
            row=SimpleNamespace(id=uuid4(),tenant_id=fields['tenant_id'],principal_type=fields['principal_type'],
                principal_id=fields['principal_id'],operation=fields['operation'],key_hash=fields['key_hash'],
                request_hash=fields['request_hash'],expires_at=fields['expires_at'],created_at=fields['now'],
                status='processing',response_status=None,response_ciphertext=None,completed_at=None)
            self.save(row)
            return row,True
        async def complete_record(self,*,record,**fields):
            for key,value in fields.items():setattr(record,key,value)
            record.status='completed'
            self.save(record)
            if flags.cancel_complete:raise asyncio.CancelledError()
            if flags.fail_complete:raise RuntimeError('private completion failure')
            if flags.corrupt_complete:record.id=uuid4()
            return record
    ledger=Ledger()
    key=Fernet.generate_key().decode('ascii')
    codec=ApiIdempotencyResponseCodec(encryption_key=key)
    idempotency=ApiIdempotencyService(repository=ledger,response_codec=codec)
    context_codec=ConstructionClientLinkRecipientContextCodec(encryption_key=key)
    request=context_codec.issue_for_request(material=ConstructionClientLinkIssuanceResult(c.request,TOKEN,uuid4()))
    original=c.session.execute
    c.tenant_bindings=[]
    async def execute(statement,parameters=None):
        if parameters is not None:
            assert 'set_config' in str(statement) and set(parameters)=={'tenant_id'}
            c.tenant_bindings.append(parameters['tenant_id'])
            return None
        return await original(statement)
    async def commit():
        if flags.fail_commit:raise RuntimeError('private commit failure')
        c.sync.commit()
    c.session.execute=execute
    c.session.commit=AsyncMock(side_effect=commit)
    c.service.confirm_request=AsyncMock(wraps=c.service.confirm_request)
    workflow=Workflow(session=c.session,confirmation_service=c.service,idempotency_service=idempotency,
        context_codec=context_codec,now_provider=lambda:c.now)
    return SimpleNamespace(c=c,flags=flags,ledger=ledger,codec=codec,key=key,
        idempotency=idempotency,context_codec=context_codec,request=request,workflow=workflow)


async def invoke(p,**changes):
    if not p.c.sync.in_transaction():p.c.sync.begin()
    kw=dict(actor=p.c.actor,recipient_context=p.request.recipient_context,token=TOKEN,
        idempotency_key='confirm-request-key',trace_id='client-link-workflow')
    kw.update(changes)
    return await p.workflow.confirm_request(**kw)


def snapshot(p):
    c=p.c
    return (tuple(c.sync.execute(select(Request.consumed_at,Request.row_version)).one()),
        tuple(c.sync.execute(select(ConstructionClient.linked_user_id,ConstructionClient.row_version).where(ConstructionClient.id==c.client)).one()),
        c.sync.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one(),
        c.sync.execute(text('SELECT data FROM client_link_replay_ledger ORDER BY id')).all())


@pytest.mark.asyncio
async def test_fresh_workflow_commits_consumption_link_audit_and_encrypted_replay_in_one_commit(pipeline):
    p,c=pipeline,pipeline.c
    result=await invoke(p)
    assert result.client_changed and result.linked_user_id==c.target and result.client_row_version==2
    c.session.commit.assert_awaited_once()
    assert c.tenant_bindings==[str(c.tenant)]
    rows=p.ledger.all()
    assert len(rows)==1
    row=rows[0]
    assert row.operation==CONFIRM and row.tenant_id==c.tenant and row.principal_id==c.target
    assert row.status=='completed' and row.response_status==200
    saved=p.codec.decrypt(row.response_ciphertext)
    assert saved['data']['client_id']==str(c.client) and saved['binding']['operation']==CONFIRM
    assert TOKEN not in json.dumps(saved) and c.request.token_hash not in json.dumps(saved)
    assert TOKEN not in json.dumps(vars(row),default=serialize)
    assert c.sync.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one()==2
    assert c.sync.execute(text('SELECT count(*) FROM users')).scalar_one()==3


@pytest.mark.asyncio
async def test_replay_after_token_ttl_returns_original_receipt_without_second_mutation_or_audit(pipeline):
    p,c=pipeline,pipeline.c
    first=await invoke(p)
    before=snapshot(p)
    c.now=NOW+timedelta(hours=1)
    second=await invoke(p,trace_id='retry-new-trace')
    assert second==first and snapshot(p)==before
    assert c.service.confirm_request.await_count==1 and len(c.audit_rows)==2
    assert c.session.commit.await_count==2


@pytest.mark.asyncio
async def test_reencrypted_equivalent_context_replays_the_same_normalized_request(pipeline):
    p=pipeline
    first=await invoke(p)
    another=p.context_codec.issue_for_request(material=ConstructionClientLinkIssuanceResult(p.c.request,TOKEN,uuid4()))
    assert another.recipient_context!=p.request.recipient_context
    assert await invoke(p,recipient_context=another.recipient_context)==first
    assert p.c.service.confirm_request.await_count==1


@pytest.mark.asyncio
@pytest.mark.parametrize('later_change',['unlink','archive'])
async def test_replay_returns_historical_success_without_reapplying_a_later_changed_link(pipeline,later_change):
    p,c=pipeline,pipeline.c
    first=await invoke(p)
    values={'linked_user_id':None,'row_version':3} if later_change=='unlink' else {'deleted_at':NOW+timedelta(seconds=2),'row_version':3}
    c.sync.execute(update(ConstructionClient.__table__).where(ConstructionClient.id==c.client).values(**values))
    c.sync.commit()
    assert await invoke(p)==first
    assert c.sync.execute(select(ConstructionClient.row_version).where(ConstructionClient.id==c.client)).scalar_one()==3
    if later_change=='unlink':assert c.sync.execute(select(ConstructionClient.linked_user_id).where(ConstructionClient.id==c.client)).scalar_one() is None
    assert c.service.confirm_request.await_count==1


@pytest.mark.asyncio
async def test_without_a_key_confirmation_remains_one_time_and_creates_no_idempotency_record(pipeline):
    p=pipeline
    assert (await invoke(p,idempotency_key=None)).client_changed
    assert p.ledger.calls==0 and p.ledger.all()==[]
    with pytest.raises(Denied):await invoke(p,idempotency_key=None)
    assert p.c.session.commit.await_count==1


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['complete','commit','wrong_completion_id','cancel_complete','audit','final_authority'])
async def test_real_workflow_rolls_back_request_client_audit_and_reservation_on_any_required_failure(pipeline,failure):
    p,c=pipeline,pipeline.c
    before=snapshot(p)
    if failure=='complete':p.flags.fail_complete=True
    elif failure=='commit':p.flags.fail_commit=True
    elif failure=='wrong_completion_id':p.flags.corrupt_complete=True
    elif failure=='cancel_complete':p.flags.cancel_complete=True
    elif failure=='audit':c.events.create_event.side_effect=RuntimeError('private audit failure')
    else:
        original=p.ledger.complete_record
        async def complete(**kw):
            row=await original(**kw)
            c.access.has_active_platform_membership.return_value=False
            return row
        p.ledger.complete_record=complete
    expected=asyncio.CancelledError if failure=='cancel_complete' else ConstructionPermissionDeniedError if failure=='final_authority' else ConstructionClientOperationError
    with pytest.raises(expected) as caught:await invoke(p)
    assert 'private' not in str(caught.value)
    assert snapshot(p)==before
    assert c.session.rollback.await_count>=1


@pytest.mark.asyncio
async def test_same_key_with_another_request_is_rejected_without_touching_either_client_or_audit(pipeline):
    p,c=pipeline,pipeline.c
    await invoke(p)
    before=snapshot(p)
    other=replace(c.request,request_id=uuid4())
    context=p.context_codec.issue_for_request(material=ConstructionClientLinkIssuanceResult(other,TOKEN,uuid4()))
    with pytest.raises(ApiIdempotencyKeyReusedError):await invoke(p,recipient_context=context.recipient_context)
    assert snapshot(p)==before and c.service.confirm_request.await_count==1


@pytest.mark.asyncio
@pytest.mark.parametrize('denial',['wrong_target','bad_context','wrong_token','inactive_target','revoked_initiator'])
async def test_replay_requires_original_recipient_proof_and_fresh_platform_and_edit_authority(pipeline,denial):
    p,c=pipeline,pipeline.c
    await invoke(p)
    before=snapshot(p)
    kw={}
    if denial=='wrong_target':kw['actor']=replace(c.actor,user_id=c.operator)
    elif denial=='bad_context':kw['recipient_context']='modified ciphertext'
    elif denial=='wrong_token':kw['token']='bad secret'
    elif denial=='inactive_target':c.identity.require_active_user.side_effect=ApiIdentityAccessError('inactive')
    else:c.grant.status='revoked'
    with pytest.raises((Denied,ApiIdentityAccessError,ConstructionPermissionDeniedError)):await invoke(p,**kw)
    assert snapshot(p)==before and c.service.confirm_request.await_count==1


@pytest.mark.asyncio
async def test_new_key_does_not_turn_a_consumed_token_into_a_second_confirmation(pipeline):
    p=pipeline
    await invoke(p)
    before=snapshot(p)
    with pytest.raises(Denied):await invoke(p,idempotency_key='different-key')
    assert snapshot(p)==before and len(p.ledger.all())==1


@pytest.mark.asyncio
async def test_completed_snapshot_tampering_is_detected_and_does_not_reapply_mutation(pipeline):
    p=pipeline
    await invoke(p)
    row=p.ledger.all()[0]
    data=p.codec.decrypt(row.response_ciphertext)
    data['data']['client_row_version']=900
    row.response_ciphertext=p.codec.encrypt(data)
    p.ledger.save(row)
    p.c.sync.commit()
    before=snapshot(p)
    with pytest.raises(ConstructionClientOperationError):await invoke(p)
    assert snapshot(p)==before and p.c.service.confirm_request.await_count==1


@pytest.mark.asyncio
async def test_in_progress_reservation_is_conflict_without_calling_confirmation(pipeline):
    p=pipeline
    await invoke(p)
    row=p.ledger.all()[0]
    row.status='processing';row.response_status=row.response_ciphertext=None
    p.ledger.save(row);p.c.sync.commit()
    before=snapshot(p)
    with pytest.raises(ConstructionClientLinkIdempotencyInProgressError):await invoke(p)
    assert snapshot(p)==before and p.c.service.confirm_request.await_count==1


@pytest.mark.asyncio
async def test_expired_idempotency_reset_cannot_reconsume_request(pipeline):
    p=pipeline
    await invoke(p)
    before=snapshot(p)
    p.c.now=NOW+timedelta(hours=25)
    with pytest.raises(Denied):await invoke(p)
    assert snapshot(p)==before


@pytest.mark.asyncio
@pytest.mark.parametrize('key',['', ' ', 123, 'k'*256])
async def test_invalid_present_key_is_rejected_before_any_reservation_or_write(pipeline,key):
    p=pipeline
    with pytest.raises(ConstructionClientValidationError):await invoke(p,idempotency_key=key)
    assert p.ledger.calls==0 and p.c.service.confirm_request.await_count==0


def test_factory_wires_existing_encryption_key_and_shared_transaction(pipeline):
    p,c=pipeline,pipeline.c
    platform=SimpleNamespace(execute=AsyncMock(),no_autoflush=nullcontext())
    workflow=build_construction_client_link_confirmation_workflow(session=c.session,platform_session=platform,encryption_key=p.key)
    assert workflow.service.session is workflow.idempotency.repository.session is c.session
    assert workflow.context_codec.PURPOSE=='construction.client-link.recipient-context.v1'
