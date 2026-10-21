"""Same-client/target reissuance: real local SQL rollback and keyed replay.

SQLite verifies transactional composition; PostgreSQL locking/concurrency and
deployed HTTP acceptance are deliberately outside these local tests.
"""
import asyncio
from dataclasses import replace
from datetime import timedelta, timezone
from hashlib import sha256
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from database.models import ConstructionClient, ConstructionClientUserLinkRequest as Request
from database.repositories.construction_client_link_requests import ConstructionClientLinkRequestState as State, MAX_VERSION
from services.api_identity import ApiActorContext
from services.construction_client_link_confirmation import ConstructionClientLinkConfirmationDeniedError as Denied
from services.construction_client_link_recipient_context import ConstructionClientLinkRecipientContextCodec
from services.construction_clients import ConstructionClientOperationError, ConstructionClientValidationError
from services.construction_foundation import ConstructionRowVersionConflictError
from services.construction_permissions import ConstructionPermissionDeniedError
from test_construction_1a_client_link_request_issuance import issuance, issue, NOW
from test_construction_1a_client_link_issuance_workflow import issuance_pipeline, invoke, snapshot
from test_construction_1a_client_link_confirmation_workflow import pipeline
from test_construction_1a_client_link_confirmation import sql_confirmation, seed_request
from test_construction_1a_client_link_request_repository import request_repo
from test_construction_1a_client_link_request_model import requests_db


def state(c, **changes):
    values=dict(request_id=uuid4(),tenant_id=c.tenant,client_id=c.client,
        initiated_by_user_id=c.operator,target_user_id=c.target,expected_linked_user_id=None,
        expected_client_row_version=1,token_hash=sha256(uuid4().bytes).hexdigest(),
        issued_at=NOW-timedelta(minutes=1),expires_at=NOW+timedelta(hours=1),
        consumed_at=None,updated_at=NOW-timedelta(minutes=1),row_version=1)
    values.update(changes)
    return State(**values)


def row(c, request_id):
    result=dict(c.sync.execute(select(Request.__table__).where(Request.id==request_id)).mappings().one())
    for key,value in result.items():
        if hasattr(value,'tzinfo') and value.tzinfo is None:result[key]=value.replace(tzinfo=timezone.utc)
    return result


async def extra(c, **changes):
    if not c.sync.in_transaction():c.sync.begin()
    values={**c.seed,'request_id':uuid4(),'token_hash':sha256(uuid4().bytes).hexdigest()}
    values.update(changes)
    return await c.requests.create_request(**values)


@pytest.mark.asyncio
async def test_authorized_parent_lock_precedes_old_request_locks_and_all_writes(issuance):
    c=issuance
    old=state(c)
    steps=[]
    async def client(**kw):steps.append('client-lock');return c.base
    async def active(**kw):
        assert steps==['client-lock']
        assert kw==dict(tenant_id=c.tenant,client_id=c.client,target_user_id=c.target,as_of=NOW)
        steps.append('request-locks');return (old,)
    async def invalidate(**kw):
        steps.append('invalidate')
        assert kw==dict(state=old,as_of=NOW)
        return replace(old,invalidated_at=NOW,updated_at=NOW,row_version=2)
    original=c.requests.create_request.side_effect
    async def create(**kw):steps.append('create');return await original(**kw)
    c.clients.get_state_for_update.side_effect=client
    c.requests.list_active_requests_for_update.side_effect=active
    c.requests.invalidate_request.side_effect=invalidate
    c.requests.create_request.side_effect=create
    result=await issue(c)
    assert steps==['client-lock','request-locks','invalidate','create']
    assert c.audit_rows[0].payload['invalidated_request_ids']==[str(old.request_id)]
    assert result.request.invalidated_at is None
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad',['untyped_list','none','untyped_state','tenant','client','target',
    'consumed','invalidated','expired','future','duplicate','exhausted'])
async def test_invalid_locked_receipts_fail_before_any_invalidation(issuance,bad):
    c=issuance
    old=state(c)
    states=(old,)
    if bad=='untyped_list':states=[old]
    elif bad=='none':states=None
    elif bad=='untyped_state':states=(SimpleNamespace(**vars(old)),)
    elif bad in ('tenant','client','target'):
        states=(replace(old,**{bad+'_id' if bad!='target' else 'target_user_id':uuid4()}),)
    elif bad in ('consumed','invalidated'):states=(replace(old,**{bad+'_at':NOW}),)
    elif bad=='expired':states=(replace(old,expires_at=NOW),)
    elif bad=='future':states=(replace(old,issued_at=NOW+timedelta(seconds=1),updated_at=NOW+timedelta(seconds=1)),)
    elif bad=='duplicate':states=(old,old)
    else:states=(replace(old,row_version=MAX_VERSION),)
    c.requests.list_active_requests_for_update.return_value=states
    with pytest.raises((ConstructionClientOperationError,ConstructionClientValidationError)):await issue(c)
    c.requests.invalidate_request.assert_not_called()
    c.requests.create_request.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad',['missing','untyped','wrong_version','wrong_scope','not_invalidated'])
async def test_conditional_invalidation_receipt_must_match_exactly(issuance,bad):
    c=issuance
    old=state(c)
    c.requests.list_active_requests_for_update.return_value=(old,)
    saved=replace(old,invalidated_at=NOW,updated_at=NOW,row_version=2)
    if bad=='missing':saved=None
    elif bad=='untyped':saved=SimpleNamespace(**vars(saved))
    elif bad=='wrong_version':saved=replace(saved,row_version=3)
    elif bad=='wrong_scope':saved=replace(saved,target_user_id=uuid4())
    else:saved=old
    c.requests.invalidate_request.return_value=saved
    with pytest.raises(ConstructionRowVersionConflictError if bad=='missing' else ConstructionClientOperationError):await issue(c)
    c.requests.create_request.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_fresh_issue_invalidates_all_live_same_pair_and_audits_the_ids(issuance_pipeline):
    p,c=issuance_pipeline,issuance_pipeline.c
    old=[c.request,await extra(c),await extra(c)]
    c.sync.commit()
    initial={s.request_id:row(c,s.request_id) for s in old}
    clients=c.sync.execute(select(ConstructionClient.__table__).order_by(ConstructionClient.id)).all()
    result=await invoke(p)
    for s in old:
        saved=row(c,s.request_id)
        assert saved=={**initial[s.request_id],'invalidated_at':c.now,'updated_at':c.now,'row_version':2}
    new=row(c,result.request.request_id)
    assert new['invalidated_at'] is None and new['consumed_at'] is None and new['row_version']==1
    assert new['expires_at']==new['issued_at']+timedelta(hours=24)
    assert c.sync.execute(select(ConstructionClient.__table__).order_by(ConstructionClient.id)).all()==clients
    event=c.audit_rows[-1]
    assert event.entity_id==result.request.request_id and event.id==result.audit_id
    assert event.payload['invalidated_request_ids']==[str(s.request_id) for s in sorted(old,key=lambda s:(s.issued_at,s.request_id))]
    assert event.payload['related_client_id']==str(c.client)
    assert result.token not in json.dumps(event.payload)
    assert all(s.token_hash not in json.dumps(event.payload) for s in old)
    with Session(c.engine) as reader:
        assert all(reader.get(Request,s.request_id).invalidated_at is not None for s in old)
        assert reader.get(Request,result.request.request_id).invalidated_at is None
    assert c.session.commit.await_count==1


@pytest.mark.asyncio
async def test_other_pairs_and_terminal_or_expired_history_are_unchanged(issuance_pipeline):
    p,c=issuance_pipeline,issuance_pipeline.c
    different_client=await extra(c,client_id=c.other_client)
    different_target=await extra(c,target_user_id=c.foreign_user)
    expired=await extra(c,issued_at=NOW-timedelta(hours=2),expires_at=NOW-timedelta(hours=1))
    consumed=await extra(c)
    invalidated=await extra(c)
    await c.requests.consume_request(state=consumed,as_of=NOW)
    await c.requests.invalidate_request(state=invalidated,as_of=NOW)
    c.sync.commit()
    untouched=[different_client,different_target,expired,consumed,invalidated]
    before={s.request_id:row(c,s.request_id) for s in untouched}
    await invoke(p)
    assert {s.request_id:row(c,s.request_id) for s in untouched}==before


@pytest.mark.asyncio
async def test_request_with_stale_client_base_is_still_superseded(issuance_pipeline):
    p,c=issuance_pipeline,issuance_pipeline.c
    c.sync.execute(update(ConstructionClient.__table__).where(ConstructionClient.id==c.client).values(row_version=2))
    c.sync.commit()
    fresh=await invoke(p,expected_row_version=2)
    assert row(c,c.request_id)['invalidated_at']==NOW
    assert fresh.request.expected_client_row_version==2
    assert c.sync.get(ConstructionClient,c.client).row_version==2


@pytest.mark.asyncio
async def test_replaying_old_issue_never_supersedes_the_newer_request(issuance_pipeline):
    p,c=issuance_pipeline,issuance_pipeline.c
    old=await invoke(p)
    c.now+=timedelta(seconds=1)
    new=await invoke(p,idempotency_key='new-issue')
    assert row(c,old.request.request_id)['invalidated_at']==c.now
    before=snapshot(p)
    assert await invoke(p)==old
    assert snapshot(p)==before
    assert row(c,new.request.request_id)['invalidated_at'] is None
    assert len(c.audit_rows)==2


@pytest.mark.asyncio
async def test_old_token_denied_but_new_token_confirms_without_target_membership(issuance_pipeline):
    p,c=issuance_pipeline,issuance_pipeline.c
    old=await invoke(p)
    codec=ConstructionClientLinkRecipientContextCodec(encryption_key=p.key)
    old_envelope=codec.issue_for_request(material=old)
    c.now+=timedelta(seconds=1)
    new=await invoke(p,idempotency_key='replacement-issue')
    new_envelope=codec.issue_for_request(material=new)
    actor=ApiActorContext(c.target,None,None,(),'ru',None,'active')
    before=snapshot(p)
    if not c.sync.in_transaction():c.sync.begin()
    with pytest.raises(Denied):
        await p.confirm_workflow.confirm_request(actor=actor,recipient_context=old_envelope.recipient_context,
            token=old_envelope.token,idempotency_key='old-token-confirm')
    assert snapshot(p)==before
    if not c.sync.in_transaction():c.sync.begin()
    result=await p.confirm_workflow.confirm_request(actor=actor,recipient_context=new_envelope.recipient_context,
        token=new_envelope.token,idempotency_key='new-token-confirm')
    assert result.linked_user_id==c.target and result.client_row_version==2
    assert row(c,old.request.request_id)['consumed_at'] is None
    assert row(c,new.request.request_id)['consumed_at']==c.now


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['audit','complete','commit','bad_completion','cancel_complete',
    'final_authority','target_missing','expired_before_commit','second_invalidation','create'])
async def test_failed_reissuance_restores_old_requests_audit_and_replay_rows(issuance_pipeline,failure):
    p,c=issuance_pipeline,issuance_pipeline.c
    first=await invoke(p)
    await extra(c)
    c.sync.commit()
    before=snapshot(p)
    if failure=='audit':
        original=c.events.create_event.side_effect
        async def audit(**kw):
            event=await original(**kw)
            assert row(c,first.request.request_id)['invalidated_at']==c.now
            raise RuntimeError('private audit failure after real writes')
        c.events.create_event.side_effect=audit
    elif failure=='complete':p.flags.fail_complete=True
    elif failure=='commit':p.flags.fail_commit=True
    elif failure=='bad_completion':p.flags.corrupt_complete=True
    elif failure=='cancel_complete':p.flags.cancel_complete=True
    elif failure=='second_invalidation':
        original=c.requests.invalidate_request
        calls=0
        async def invalidate(**kw):
            nonlocal calls
            calls+=1
            saved=await original(**kw)
            if calls==2:return None
            return saved
        c.requests.invalidate_request=invalidate
    elif failure=='create':c.requests.create_request=AsyncMock(return_value=None)
    else:
        original=p.ledger.complete_record
        async def complete(**kw):
            saved=await original(**kw)
            if failure=='final_authority':c.access.has_active_platform_membership.return_value=False
            elif failure=='target_missing':p.issue_identity.require_existing_user.side_effect=lambda **_:None
            else:c.now+=timedelta(hours=24)
            return saved
        p.ledger.complete_record=complete
    expected=asyncio.CancelledError if failure=='cancel_complete' else (
        ConstructionPermissionDeniedError if failure=='final_authority' else
        ConstructionRowVersionConflictError if failure in ('second_invalidation','create') else ConstructionClientOperationError)
    with pytest.raises(expected):await invoke(p,idempotency_key='failed-reissue')
    assert snapshot(p)==before
    assert row(c,first.request.request_id)['invalidated_at'] is None
    assert len(p.ledger.all())==1


@pytest.mark.asyncio
async def test_expired_request_reissuance_preserves_expired_row(issuance_pipeline):
    p,c=issuance_pipeline,issuance_pipeline.c
    old=await invoke(p)
    before=row(c,old.request.request_id)
    c.now=old.request.expires_at
    fresh=await invoke(p,idempotency_key='after-expiry')
    assert row(c,old.request.request_id)==before
    assert fresh.request.issued_at==c.now and fresh.request.expires_at==c.now+timedelta(hours=24)


@pytest.mark.asyncio
async def test_replacement_reissuance_does_not_change_previous_client_identity(issuance_pipeline):
    p,c=issuance_pipeline,issuance_pipeline.c
    c.sync.execute(update(ConstructionClient.__table__).where(ConstructionClient.id==c.client).values(linked_user_id=c.foreign_user))
    c.sync.commit()
    kw=dict(actor=c.actor,access_context=c.context,client_id=c.client,target_user_id=c.target,
        previous_user_id=c.foreign_user,expected_row_version=1)
    if not c.sync.in_transaction():c.sync.begin()
    old=await p.workflow.request_replacement(**kw,idempotency_key='replace-first')
    if not c.sync.in_transaction():c.sync.begin()
    fresh=await p.workflow.request_replacement(**kw,idempotency_key='replace-second')
    assert row(c,old.request.request_id)['invalidated_at']==c.now
    assert fresh.request.expected_linked_user_id==c.foreign_user
    client=c.sync.get(ConstructionClient,c.client)
    assert client.linked_user_id==c.foreign_user and client.row_version==1
