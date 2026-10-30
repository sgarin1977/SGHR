from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta,timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select,text,update

from database.models import ConstructionClient,ConstructionClientUserLinkRequest as Request
from services.api_identity import ApiActorContext,ApiIdentityAccessError
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction.construction_client_link_request_issuance import ConstructionClientLinkRequestIssuanceService as Issuer,hash_client_link_confirmation_token
from services.construction.construction_client_link_issuance_replay import ConstructionClientLinkTokenDeriver as Deriver
from services.construction.construction_client_link_issuance_workflow import ConstructionClientLinkIssuanceWorkflow as Workflow,ISSUE,build_construction_client_link_issuance_workflow
from services.construction.construction_client_link_confirmation_workflow import ConstructionClientLinkIdempotencyInProgressError
from services.construction.construction_client_link_recipient_context import ConstructionClientLinkRecipientContextCodec
from services.construction.construction_clients import ConstructionClientOperationError,ConstructionClientValidationError
from services.construction.construction_permissions import ConstructionAccessContext,ConstructionGrantContext,ConstructionPermissionDeniedError
from test_construction_1a_client_link_confirmation_workflow import pipeline,serialize
from test_construction_1a_client_link_confirmation import sql_confirmation,seed_request,NOW
from test_construction_1a_client_link_request_repository import request_repo
from test_construction_1a_client_link_request_model import requests_db

LIFETIME=timedelta(hours=24)  # Approved operator-link confirmation contract.


@pytest.fixture
async def issuance_pipeline(pipeline):
    p,c=pipeline,pipeline.c
    p.confirm_workflow=p.workflow
    c.actor=ApiActorContext(c.operator,c.tenant,None,(),'ru',None,'active')
    c.context=ConstructionAccessContext(c.operator,c.tenant,('OWNER',),('construction.projects.edit',),
        (ConstructionGrantContext('OWNER','tenant',c.tenant),))
    identity=SimpleNamespace(session=object(),require_active_actor=AsyncMock(side_effect=lambda **kw:kw['actor']),
        require_existing_user=AsyncMock(side_effect=lambda **kw:kw['user_id']))
    p.issue_identity=identity
    p.issue_service=Issuer(session=c.session,request_repository=c.requests,client_repository=c.clients,
        access_repository=c.access,event_repository=c.events,identity_reader=identity,confirmation_lifetime=LIFETIME,now_provider=lambda:c.now)
    p.deriver=Deriver(encryption_key=p.key)
    p.workflow=Workflow(session=c.session,issuance_service=p.issue_service,idempotency_service=p.idempotency,
        token_deriver=p.deriver,now_provider=lambda:c.now)
    original=p.ledger.reserve_record
    async def reserve(**fields):
        # Match the real shared repository's renewal of created_at on expiry.
        for row in p.ledger.all():
            if (all(getattr(row,k)==fields[k] for k in ('tenant_id','principal_type','principal_id','operation','key_hash'))
                    and row.expires_at<=fields['now']):
                row.created_at=fields['now'];p.ledger.save(row)
        return await original(**fields)
    p.ledger.reserve_record=reserve
    return p


async def invoke(p,**changes):
    c=p.c
    if not c.sync.in_transaction():c.sync.begin()
    kw=dict(actor=c.actor,access_context=c.context,client_id=c.client,target_user_id=c.target,
        expected_row_version=1,idempotency_key='issue-client-link-key',trace_id='client-issue-workflow')
    kw.update(changes)
    return await p.workflow.request_link(**kw)


def snapshot(p):
    c=p.c
    return (c.sync.execute(select(Request.__table__).order_by(Request.id)).all(),
        c.sync.execute(select(ConstructionClient.__table__).order_by(ConstructionClient.id)).all(),
        c.sync.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one(),
        c.sync.execute(text('SELECT data FROM client_link_replay_ledger ORDER BY id')).all())


@pytest.mark.asyncio
async def test_atomic_issuance_commits_request_audit_and_secret_free_encrypted_receipt(issuance_pipeline):
    p,c=issuance_pipeline,issuance_pipeline.c
    generator=p.issue_service.token_factory
    result=await invoke(p)
    assert result.request.token_hash==hash_client_link_confirmation_token(result.token)
    assert result.request.expires_at==NOW+LIFETIME and result.request.initiated_by_user_id==c.operator
    assert result.request.consumed_at is None and result.request.row_version==1
    assert p.issue_service.token_factory is generator
    assert c.sync.get(ConstructionClient,c.client).linked_user_id is None
    assert c.sync.get(ConstructionClient,c.client).row_version==1
    assert c.sync.execute(text('SELECT count(*) FROM users')).scalar_one()==3
    assert c.sync.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one()==1
    row=p.ledger.all()[0]
    assert row.operation==ISSUE and row.principal_id==c.operator and row.response_status==201
    receipt=p.codec.decrypt(row.response_ciphertext)
    assert result.token not in json.dumps(receipt) and result.request.token_hash not in json.dumps(receipt)
    assert result.token not in json.dumps(vars(row),default=serialize)
    assert receipt['material']['audit_id']==str(result.audit_id)
    c.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_replay_returns_same_token_uuid_expiry_and_audit_without_second_request(issuance_pipeline):
    p=issuance_pipeline
    first=await invoke(p)
    before=snapshot(p)
    p.c.now=NOW+timedelta(hours=1)
    second=await invoke(p,trace_id='new-retry-trace')
    assert second==first and second.token==first.token
    assert snapshot(p)==before and len(p.c.audit_rows)==1
    assert p.c.session.commit.await_count==2


@pytest.mark.asyncio
async def test_confirmation_then_issuance_replay_does_not_reactivate_consumed_request(issuance_pipeline):
    p,c=issuance_pipeline,issuance_pipeline.c
    first=await invoke(p)
    codec=ConstructionClientLinkRecipientContextCodec(encryption_key=p.key)
    recipient=codec.issue_for_request(material=first)
    if not c.sync.in_transaction():c.sync.begin()
    confirmed=await p.confirm_workflow.confirm_request(actor=ApiActorContext(c.target,None,None,(),'ru',None,'active'),
        recipient_context=recipient.recipient_context,token=recipient.token,idempotency_key='confirm-issued-request')
    assert confirmed.linked_user_id==c.target
    before=snapshot(p)
    assert await invoke(p)==first
    assert snapshot(p)==before
    row=c.sync.get(Request,first.request.request_id)
    assert row.consumed_at is not None and row.row_version==2
    assert len(c.audit_rows)==3


@pytest.mark.asyncio
async def test_later_client_change_does_not_cause_replay_to_overwrite_or_refresh_request(issuance_pipeline):
    p,c=issuance_pipeline,issuance_pipeline.c
    first=await invoke(p)
    c.sync.execute(update(ConstructionClient.__table__).where(ConstructionClient.id==c.client).values(linked_user_id=c.foreign_user,row_version=2))
    c.sync.commit()
    before=snapshot(p)
    assert await invoke(p)==first
    assert snapshot(p)==before and c.sync.get(ConstructionClient,c.client).linked_user_id==c.foreign_user


@pytest.mark.asyncio
async def test_replay_retention_renewal_gets_new_token_when_the_record_uuid_is_reused(issuance_pipeline):
    p=issuance_pipeline
    first=await invoke(p)
    record_id=p.ledger.all()[0].id
    p.c.now=NOW+timedelta(hours=25)
    second=await invoke(p)
    assert p.ledger.all()[0].id==record_id
    assert second.request.request_id!=first.request.request_id
    assert second.token!=first.token and second.request.token_hash!=first.request.token_hash
    assert second.request.issued_at==p.c.now and second.request.expires_at==p.c.now+LIFETIME


@pytest.mark.asyncio
async def test_distinct_keys_and_unkeyed_commands_do_not_share_tokens_or_mutate_the_generator(issuance_pipeline):
    p=issuance_pipeline
    generator=p.issue_service.token_factory
    first=await invoke(p)
    second=await invoke(p,idempotency_key='another-key')
    third=await invoke(p,idempotency_key=None)
    assert len({m.token for m in (first,second,third)})==3
    assert len({m.request.request_id for m in (first,second,third)})==3
    assert p.issue_service.token_factory is generator and len(p.ledger.all())==2


@pytest.mark.asyncio
async def test_explicit_replacement_request_preserves_previous_link_until_target_confirmation(issuance_pipeline):
    p,c=issuance_pipeline,issuance_pipeline.c
    c.sync.execute(update(ConstructionClient.__table__).where(ConstructionClient.id==c.client).values(linked_user_id=c.foreign_user))
    result=await p.workflow.request_replacement(actor=c.actor,access_context=c.context,client_id=c.client,target_user_id=c.target,
        previous_user_id=c.foreign_user,expected_row_version=1,idempotency_key='replacement-key')
    assert result.request.expected_linked_user_id==c.foreign_user
    assert c.sync.get(ConstructionClient,c.client).linked_user_id==c.foreign_user
    before=snapshot(p)
    if not c.sync.in_transaction():c.sync.begin()
    replay=await p.workflow.request_replacement(actor=c.actor,access_context=c.context,client_id=c.client,target_user_id=c.target,
        previous_user_id=c.foreign_user,expected_row_version=1,idempotency_key='replacement-key')
    assert replay==result and snapshot(p)==before


@pytest.mark.asyncio
@pytest.mark.parametrize('changed',['target','client','version','operation'])
async def test_same_key_with_changed_semantic_request_is_rejected(issuance_pipeline,changed):
    p,c=issuance_pipeline,issuance_pipeline.c
    await invoke(p)
    before=snapshot(p)
    kw={'target_user_id':c.foreign_user} if changed=='target' else {'client_id':c.other_client} if changed=='client' else {'expected_row_version':2}
    if changed=='operation':
        if not c.sync.in_transaction():c.sync.begin()
        action=p.workflow.request_replacement(actor=c.actor,access_context=c.context,client_id=c.client,target_user_id=c.target,
            previous_user_id=c.foreign_user,expected_row_version=1,idempotency_key='issue-client-link-key')
    else:action=invoke(p,**kw)
    with pytest.raises(ApiIdempotencyKeyReusedError):await action
    assert snapshot(p)==before


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['audit','completion','commit','bad_completion','final_authority','target_missing','expired_during_complete'])
async def test_failure_rolls_back_pending_request_audit_and_idempotency_together(issuance_pipeline,failure):
    p,c=issuance_pipeline,issuance_pipeline.c
    before=snapshot(p)
    if failure=='audit':c.events.create_event.side_effect=RuntimeError('private audit failure')
    elif failure=='completion':p.flags.fail_complete=True
    elif failure=='commit':p.flags.fail_commit=True
    elif failure=='bad_completion':p.flags.corrupt_complete=True
    else:
        original=p.ledger.complete_record
        async def complete(**kw):
            row=await original(**kw)
            if failure=='final_authority':c.access.has_active_platform_membership.return_value=False
            elif failure=='target_missing':p.issue_identity.require_existing_user.return_value=None;p.issue_identity.require_existing_user.side_effect=None
            else:c.now=NOW+LIFETIME
            return row
        p.ledger.complete_record=complete
    with pytest.raises((ConstructionClientOperationError,ConstructionPermissionDeniedError)) as caught:await invoke(p)
    assert 'private' not in str(caught.value)
    assert snapshot(p)==before


@pytest.mark.asyncio
@pytest.mark.parametrize('denial',['rights','membership','identity','target'])
async def test_replay_requires_fresh_edit_authority_and_existing_target(issuance_pipeline,denial):
    p,c=issuance_pipeline,issuance_pipeline.c
    await invoke(p)
    before=snapshot(p)
    if denial=='rights':c.grant.status='revoked'
    elif denial=='membership':c.access.has_active_platform_membership.return_value=False
    elif denial=='identity':p.issue_identity.require_active_actor.side_effect=ApiIdentityAccessError('inactive')
    else:p.issue_identity.require_existing_user.side_effect=None;p.issue_identity.require_existing_user.return_value=None
    with pytest.raises((ConstructionPermissionDeniedError,ApiIdentityAccessError,ConstructionClientOperationError)):await invoke(p)
    assert snapshot(p)==before


@pytest.mark.asyncio
async def test_key_rotation_cannot_return_a_different_secret(issuance_pipeline):
    p=issuance_pipeline
    await invoke(p)
    before=snapshot(p)
    p.workflow.deriver=Deriver(encryption_key=Fernet.generate_key().decode())
    with pytest.raises(ConstructionClientOperationError):await invoke(p)
    assert snapshot(p)==before


@pytest.mark.asyncio
async def test_modified_encrypted_receipt_is_checked_against_immutable_request_context(issuance_pipeline):
    p=issuance_pipeline
    await invoke(p)
    row=p.ledger.all()[0]
    receipt=p.codec.decrypt(row.response_ciphertext)
    receipt['material']['request']['expected_client_row_version']=900
    row.response_ciphertext=p.codec.encrypt(receipt)
    p.ledger.save(row);p.c.sync.commit()
    before=snapshot(p)
    with pytest.raises(ConstructionClientOperationError):await invoke(p)
    assert snapshot(p)==before


@pytest.mark.asyncio
async def test_changed_instance_lifetime_does_not_change_replayed_expiration(issuance_pipeline):
    p=issuance_pipeline
    first=await invoke(p)
    p.issue_service.confirmation_lifetime=timedelta(hours=2)
    assert await invoke(p)==first
    assert len(p.c.audit_rows)==1


@pytest.mark.asyncio
async def test_in_progress_reservation_does_not_create_another_pending_request(issuance_pipeline):
    p=issuance_pipeline
    await invoke(p)
    row=p.ledger.all()[0]
    row.status='processing';row.response_status=row.response_ciphertext=None
    p.ledger.save(row);p.c.sync.commit()
    before=snapshot(p)
    with pytest.raises(ConstructionClientLinkIdempotencyInProgressError):await invoke(p)
    assert snapshot(p)==before


def test_deriver_is_stable_purpose_specific_and_binds_reserved_time_payload_and_principal():
    key=Fernet.generate_key().decode()
    d=Deriver(encryption_key=key)
    fields=dict(reservation_id=uuid4(),reserved_at=NOW,tenant_id=uuid4(),initiated_by_user_id=uuid4(),payload={'client_id':'example'})
    token=d.derive(**fields)
    assert d.derive(**fields)==token and hash_client_link_confirmation_token(token)
    for change in ({'reservation_id':uuid4()},{'reserved_at':NOW+timedelta(days=1)},
            {'tenant_id':uuid4()},{'initiated_by_user_id':uuid4()},{'payload':{'client_id':'changed'}}):
        assert d.derive(**{**fields,**change})!=token
    assert d.derive(**{**fields,'reserved_at':NOW.astimezone(timezone(timedelta(hours=2)))})==token
    assert token not in repr(d)


@pytest.mark.parametrize('key',[None,'','bad','ю'*32])
def test_deriver_rejects_invalid_configuration_without_echoing_key(key):
    with pytest.raises(ValueError) as caught:Deriver(encryption_key=key)
    assert str(caught.value)=='Client link replay key is invalid.'


def test_factory_uses_approved_lifetime_and_reuses_platform_key_and_shared_session(issuance_pipeline):
    p,c=issuance_pipeline,issuance_pipeline.c
    platform=SimpleNamespace(execute=AsyncMock(),no_autoflush=nullcontext())
    default_workflow=build_construction_client_link_issuance_workflow(session=c.session,platform_session=platform,encryption_key=p.key)
    assert default_workflow.service.confirmation_lifetime==timedelta(hours=24)
    workflow=build_construction_client_link_issuance_workflow(session=c.session,platform_session=platform,encryption_key=p.key,confirmation_lifetime=LIFETIME)
    assert workflow.service.session is workflow.idempotency.repository.session is c.session
    assert workflow.service.identity.session is platform and workflow.service.confirmation_lifetime==LIFETIME
