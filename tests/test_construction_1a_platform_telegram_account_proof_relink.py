"""Proof-based reactivation via the existing consume operation, locally only."""
from datetime import timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from aiogram import Bot
from fastapi import Depends
import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm.attributes import set_committed_value

import api.routes.platform_telegram_binding as routes
from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_api_session, get_api_request_rate_limit_service
from api.telegram_auth import TelegramAuthenticationError
from database.models import ApiAuthSession, ApiIdempotencyRecord, EventLog, UserAccount
from database.repositories.platform_telegram_accounts import PlatformTelegramAccountAlreadyLinkedError as AlreadyLinked
from database.repositories.user import UserRepository, PlatformTelegramAccountUnlinkedError as Unlinked
from services.platform_telegram_binding_consume import (
    PlatformTelegramBindingOperationError as OperationError,
    PlatformTelegramBindingVersionConflictError as Conflict,
)
from services.platform_telegram_binding_tokens import PlatformTelegramBindingTokenDeniedError as Denied
from test_construction_1a_platform_telegram_binding_consume import environment, proof, NOW, BOT_TOKEN
from test_construction_1a_platform_telegram_binding_workflow import workflow, initiate, consume, KEY
from test_construction_1a_platform_telegram_binding_bot import update as bot_update
from services.platform_telegram_binding_bot import consume_platform_telegram_binding_bot_start


@pytest.fixture
def relink(workflow):
    c=workflow;e=c.env
    e.service.now_provider=lambda:c.now
    c.workflow.consume=e.service
    c.workflow.tokens=e.service.tokens
    c.workflow.events=e.service.events
    e.session.rollback()
    sessions=ApiAuthSession.__table__.to_metadata(e.token_table.metadata)
    sessions.create(e.session.bind)
    e.session.begin()
    # Seed an existing canonical account and its later unlinked state without
    # invoking onboarding or claiming native unlink/deployment acceptance.
    row=UserAccount(id=uuid4(),user_id=e.owner,platform='telegram',platform_user_id='123456789',
        username='retained-private-profile',raw_profile={'retained':True},status='unlinked',row_version=2)
    e.session.add(row)
    e.session.add(UserAccount(id=uuid4(),user_id=e.owner,platform='telegram',platform_user_id='555555555',raw_profile={}))
    e.session.add(UserAccount(id=uuid4(),user_id=e.owner,platform='email',platform_user_id='private@example.invalid',raw_profile={}))
    for i,method in enumerate(('telegram','email')):
        e.session.add(ApiAuthSession(id=uuid4(),tenant_id=e.tenant,user_id=e.owner,
            auth_method=method,refresh_token_hash=str(i)*64,expires_at=NOW+timedelta(days=30)))
    e.session.flush()
    # ORM INSERT establishes version 1; emulate the persisted versioned unlink.
    e.session.execute(update(UserAccount).where(UserAccount.id==row.id).values(row_version=2))
    e.session.commit();e.session.begin()
    c.account=e.session.get(UserAccount,row.id)
    assert c.account.status=='unlinked' and c.account.row_version==2
    c.baseline=state(c)
    return c


def state(c):
    e=c.env
    result={model.__tablename__:[tuple(row) for row in e.session.execute(select(model.__table__).order_by(model.id)).all()]
        for model in (UserAccount,ApiAuthSession,EventLog,ApiIdempotencyRecord)}
    result['tokens']=[tuple(row) for row in e.session.execute(select(e.token_table).order_by(e.token_table.c.id)).all()]
    return result


async def direct(c,**changes):
    e=c.env
    values=dict(token=e.material.token,telegram_init_data=proof(as_of=c.now),trace_id='proof-relink:local')
    values.update(changes)
    return await e.service.consume_token(**values)


@pytest.mark.asyncio
async def test_live_consume_restores_same_uuid_and_profile_with_one_secret_free_audit(relink):
    c=relink;e=c.env
    result=await direct(c)
    assert result.account_id==c.account.id and result.user_id==e.owner and result.tenant_id==e.tenant
    assert result.account_created is False and c.account.status=='active' and c.account.row_version==3
    assert c.account.username=='retained-private-profile' and c.account.raw_profile=={'retained':True}
    assert c.account.platform_user_id=='123456789'
    event=e.session.execute(select(EventLog)).scalar_one()
    assert event.event_type=='platform_telegram_binding_consumed' and event.entity_id==e.binding_id
    assert event.tenant_id==e.tenant and event.user_id==e.owner
    assert event.payload['account_id']==str(c.account.id) and event.payload['account_created'] is False
    assert event.payload['account_reactivated'] is True
    assert event.payload['changes'][-2:]==[
        {'field':'account.status','old_value':'unlinked','new_value':'active'},
        {'field':'account.row_version','old_value':2,'new_value':3}]
    text=repr(result)+json.dumps(event.payload)
    for private in (e.material.token,e.material.token_hash,BOT_TOKEN,'123456789','retained-private-profile'):
        assert private not in text
    assert state(c)['api_auth_sessions']==c.baseline['api_auth_sessions']
    assert len(e.session.execute(select(e.users.c.id)).all())==3
    assert e.bridge.rollbacks==0 and c.commits==0 and e.session.in_transaction()
    e.session.rollback();assert state(c)==c.baseline


@pytest.mark.asyncio
async def test_authenticated_workflow_commits_relink_with_existing_snapshot_contract_and_replays(relink):
    c=relink;e=c.env
    result=await consume(c,e.material.token)
    before=state(c)
    row=e.session.execute(select(ApiIdempotencyRecord)).scalar_one()
    data=c.workflow.idempotency.response_codec.decrypt(row.response_ciphertext)['data']
    assert set(data)=={'binding_id','account_id','user_id','tenant_id','consumed_at','row_version','account_created'}
    assert data['account_created'] is False
    c.now+=timedelta(minutes=20)
    assert await consume(c,e.material.token)==result
    assert state(c)==before and c.commits==2


@pytest.mark.asyncio
async def test_old_consume_replay_after_later_unlink_cannot_restore_trust_again(relink):
    c=relink;e=c.env
    first=await consume(c,e.material.token)
    e.session.execute(update(UserAccount).where(UserAccount.id==c.account.id).values(status='unlinked',row_version=4))
    e.session.commit();before=state(c)
    assert await consume(c,e.material.token)==first
    assert state(c)==before and c.account.status=='unlinked' and c.account.row_version==4
    with pytest.raises(Denied):await consume(c,e.material.token,key='different-old-token-key')
    assert state(c)==before


@pytest.mark.asyncio
async def test_normal_lookup_and_create_do_not_reactivate_without_a_new_valid_consume(relink):
    c=relink;e=c.env
    with pytest.raises(Unlinked):await UserRepository(e.bridge).get_by_platform_account('telegram','123456789')
    with pytest.raises(Unlinked):await e.service.accounts.create_or_get_account(user_id=e.owner,telegram_user_id=123456789)
    assert state(c)==c.baseline
    await direct(c)
    assert await UserRepository(e.bridge).get_by_platform_account('telegram','123456789') is c.account
    assert c.account.status=='active'


@pytest.mark.asyncio
async def test_second_valid_binding_to_active_identity_does_not_bump_account_version_again(relink):
    c=relink;e=c.env
    await consume(c,e.material.token)
    issued=await initiate(c,key='another-binding')
    second=await consume(c,issued.token,key='another-consume')
    assert second.account_id==c.account.id and second.account_created is False
    assert c.account.row_version==3 and c.account.status=='active'
    audits=e.session.execute(select(EventLog).where(EventLog.event_type=='platform_telegram_binding_consumed')).scalars().all()
    assert len(audits)==2 and sum('account_reactivated' in event.payload for event in audits)==1


@pytest.mark.asyncio
@pytest.mark.parametrize('bad',['signature','token','expired','revoked','used'])
async def test_relink_requires_both_verified_identity_and_live_unused_token(relink,bad):
    c=relink;e=c.env
    values={}
    if bad=='signature':values['telegram_init_data']='private-forged-proof'
    elif bad=='token':values['token']='A'*43
    elif bad=='expired':c.now=NOW+timedelta(minutes=15);e.service.now_provider=lambda:c.now
    else:
        field='revoked_at' if bad=='revoked' else 'consumed_at'
        e.session.execute(update(e.token_table).values(**{field:NOW,'row_version':2}))
        e.session.commit();e.session.begin();c.baseline=state(c)
    with pytest.raises((Denied,TelegramAuthenticationError)):await direct(c,**values)
    assert state(c)==c.baseline and c.account.status=='unlinked'


@pytest.mark.asyncio
@pytest.mark.parametrize('status',['active','unlinked'])
async def test_foreign_owner_remains_409_even_if_the_account_was_unlinked(relink,status):
    c=relink;e=c.env
    e.session.execute(update(UserAccount).where(UserAccount.id==c.account.id).values(user_id=e.other,status=status))
    e.session.commit();e.session.begin();before=state(c)
    with pytest.raises(AlreadyLinked) as error:await direct(c)
    assert error.value.status_code==409 and error.value.code=='TELEGRAM_ACCOUNT_ALREADY_LINKED'
    assert state(c)==before


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['token_ttl','proof_ttl'])
async def test_wait_for_account_lock_rechecks_ttls_and_rolls_back_pending_consume(relink,fault):
    c=relink;e=c.env
    first=NOW+timedelta(minutes=14,seconds=50) if fault=='token_ttl' else NOW+timedelta(minutes=1)
    last=NOW+timedelta(minutes=15) if fault=='token_ttl' else NOW+timedelta(minutes=6,seconds=1)
    clock=iter((first,first,last))
    e.service.now_provider=lambda:next(clock)
    with pytest.raises((Denied,TelegramAuthenticationError)):
        await direct(c,telegram_init_data=proof(as_of=first))
    assert state(c)==c.baseline


@pytest.mark.asyncio
async def test_conditional_reactivation_miss_is_version_conflict_and_preserves_token(relink):
    c=relink;e=c.env
    e.service.accounts.reactivate_account=AsyncMock(return_value=None)
    with pytest.raises(Conflict):await direct(c)
    assert state(c)==c.baseline


@pytest.mark.asyncio
@pytest.mark.parametrize('field',['id','user_id','platform_user_id','status','row_version'])
async def test_bad_reactivation_receipt_rolls_back_token_and_account(relink,field):
    c=relink;e=c.env
    original=e.service.accounts.reactivate_account
    async def corrupt(**kwargs):
        row=await original(**kwargs)
        values={'id':uuid4(),'user_id':e.other,'platform_user_id':'different-identity','status':'unlinked','row_version':99}
        set_committed_value(row,field,values[field]);return row
    e.service.accounts.reactivate_account=corrupt
    with pytest.raises(OperationError):await direct(c)
    e.session.expunge_all();assert state(c)==c.baseline


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['audit_before','audit_after','completion'])
async def test_failure_rolls_back_reactivation_consume_audit_and_idempotency(relink,failure):
    c=relink;e=c.env
    if failure.startswith('audit'):
        original=e.service.events.create_event
        async def fail(**kwargs):
            assert c.account.status=='active' and c.account.row_version==3
            if failure=='audit_after':await original(**kwargs)
            raise RuntimeError('Simulated mandatory audit failure.')
        e.service.events.create_event=fail
    else:
        original=c.workflow.idempotency.complete
        async def fail(**kwargs):
            await original(**kwargs)
            assert c.account.status=='active' and len(e.session.execute(select(EventLog.id)).all())==1
            raise RuntimeError('Simulated completion failure.')
        c.workflow.idempotency.complete=fail
    with pytest.raises(RuntimeError):await consume(c,e.material.token)
    assert state(c)==c.baseline and c.commits==0


@pytest.mark.asyncio
async def test_repository_conditional_update_changes_only_state_and_version_and_keeps_caller_transaction(relink):
    c=relink;e=c.env
    row=await e.service.accounts.reactivate_account(account_id=c.account.id,user_id=e.owner,
        telegram_user_id=123456789,expected_row_version=2)
    assert row is c.account and row.status=='active' and row.row_version==3
    e.session.flush();assert row.row_version==3
    assert row.username=='retained-private-profile' and row.raw_profile=={'retained':True}
    sql=str(e.bridge.statements[-1].compile(dialect=postgresql.dialect()))
    for field in ('id','user_id','platform','platform_user_id','status','row_version'):
        assert 'user_accounts.'+field+' =' in sql
    assert 'updated_at=now()' in sql and 'RETURNING user_accounts.id' in sql
    assert c.commits==0 and e.session.in_transaction()
    e.session.rollback();assert state(c)==c.baseline


@pytest.mark.asyncio
@pytest.mark.parametrize('case',['version','owner','telegram','missing','active'])
async def test_repository_does_not_reactivate_a_nonmatching_identity_state(relink,case):
    c=relink;e=c.env
    if case=='active':
        e.session.execute(update(UserAccount).where(UserAccount.id==c.account.id).values(status='active'))
        e.session.commit();e.session.begin()
    before=state(c)
    row=await e.service.accounts.reactivate_account(account_id=uuid4() if case=='missing' else c.account.id,
        user_id=e.other if case=='owner' else e.owner,
        telegram_user_id=1 if case=='telegram' else 123456789,expected_row_version=7 if case=='version' else 2)
    assert row is None and state(c)==before and c.commits==0


@pytest.mark.asyncio
@pytest.mark.parametrize('version',[True,0,'2',2**63-1])
async def test_repository_rejects_invalid_version_before_query(relink,version):
    c=relink;e=c.env
    e.bridge.statements.clear()
    with pytest.raises(ValueError):await e.service.accounts.reactivate_account(account_id=c.account.id,
        user_id=e.owner,telegram_user_id=123456789,expected_row_version=version)
    assert e.bridge.statements==[] and state(c)==c.baseline


@pytest.mark.asyncio
async def test_trusted_private_bot_update_can_relink_through_the_same_consume_workflow(relink):
    c=relink;e=c.env
    bot=Bot(token=BOT_TOKEN)  # Construction does not call Telegram.
    event=bot_update(bot,token=e.material.token,as_of=c.now)
    first=await consume_platform_telegram_binding_bot_start(session=e.bridge,bot=bot,update=event,
        encryption_key=KEY,now_provider=lambda:c.now)
    assert first.account_id==c.account.id and first.account_created is False
    assert c.account.status=='active' and c.account.row_version==3
    before=state(c)
    if not e.session.in_transaction():e.session.begin()
    repeated=await consume_platform_telegram_binding_bot_start(session=e.bridge,bot=bot,update=event,
        encryption_key=KEY,now_provider=lambda:c.now)
    assert repeated==first and state(c)==before
    audit=e.session.execute(select(EventLog)).scalar_one()
    assert audit.platform=='telegram' and audit.payload['account_reactivated'] is True


@pytest.mark.asyncio
async def test_existing_sessionless_http_consume_relinks_without_new_fields_or_platform_actor(relink):
    c=relink;e=c.env;app=create_app()
    app.dependency_overrides[get_api_session]=lambda:e.bridge
    def forbidden():raise AssertionError('Consume does not require a Platform session.')
    app.dependency_overrides[get_current_actor]=forbidden
    app.dependency_overrides[get_api_request_rate_limit_service]=lambda:SimpleNamespace(ensure_public_api_allowed=AsyncMock())
    def provide(session=Depends(routes.get_binding_session)):
        return SimpleNamespace(base=c.workflow)
    app.dependency_overrides[routes.get_binding_workflow]=provide
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://testserver') as client:
        response=await client.post('/api/v1/platform/integrations/telegram/bindings/consume',
            json={'token':e.material.token,'init_data':proof(as_of=c.now)},headers={'Idempotency-Key':'http-relink'})
    assert response.status_code==200,response.text
    assert set(response.json()['data'])=={'binding_id','account_id','consumed_at','row_version','account_created'}
    assert response.json()['data']['account_id']==str(c.account.id) and response.json()['data']['account_created'] is False
    assert c.account.status=='active' and c.account.row_version==3
