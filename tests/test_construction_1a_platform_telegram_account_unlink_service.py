"""Authorized unlink and real local audit rollback; no live services."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import Column, JSON, MetaData, Table, Text, Uuid, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm.attributes import set_committed_value

from database.models import ApiAuthSession, EventLog, User, UserAccount
from database.repositories.user import UserRepository, PlatformTelegramAccountUnlinkedError
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.platform_telegram_account_unlink import (
    UNLINK_EVENT, UNLINK_OPERATION, PlatformTelegramAccountUnlinkService,
    build_platform_telegram_account_unlink_service,
)
from services.platform_telegram_binding_consume import (
    PlatformTelegramBindingOperationError as OperationError,
    PlatformTelegramBindingVersionConflictError as Conflict,
)
from test_construction_1a_platform_telegram_account_auth_guards import store


@pytest.fixture
def environment(store):
    c=store
    metadata=MetaData()
    for name in ('users','tenants','countries'):
        Table(name,metadata,Column('id',Uuid,primary_key=True))
    languages=Table('languages',metadata,Column('code',Text,primary_key=True))
    events=EventLog.__table__.to_metadata(metadata)
    sessions=ApiAuthSession.__table__.to_metadata(metadata)
    for column in events.c:
        if isinstance(column.type,JSONB): column.type=JSON()
    metadata.create_all(c.engine,tables=[languages,events,sessions])
    c.bridge.in_transaction=c.session.in_transaction
    c.bridge.add=c.session.add
    c.bridge.rollbacks=0
    async def rollback():
        c.bridge.rollbacks+=1
        c.session.rollback()
    c.bridge.rollback=rollback
    c.actor=ApiActorContext(user_id=c.owner,tenant_id=c.tenant,active_role=None,
        roles=(),language_code='uk',timezone=None,status='active')
    c.identity=SimpleNamespace(session=c.bridge,require_actor=AsyncMock(return_value=c.actor))
    c.service=build_platform_telegram_account_unlink_service(session=c.bridge)
    c.service.identity=c.identity
    c.second=UserAccount(id=uuid4(),user_id=c.owner,platform='telegram',platform_user_id='555555555',raw_profile={'second':True})
    c.foreign=UserAccount(id=uuid4(),user_id=c.other,platform='telegram',platform_user_id='666666666',raw_profile={})
    c.email=UserAccount(id=uuid4(),user_id=c.owner,platform='email',platform_user_id='test@example.invalid',raw_profile={})
    c.session.add_all([c.second,c.foreign,c.email])
    c.auth_ids=[]
    for owner,method in [(c.owner,'telegram'),(c.owner,'telegram'),(c.owner,'email'),(c.other,'telegram')]:
        identifier=uuid4();c.auth_ids.append(identifier)
        c.session.add(ApiAuthSession(id=identifier,tenant_id=c.tenant,user_id=owner,auth_method=method,
            refresh_token_hash=uuid4().hex+uuid4().hex,expires_at=datetime.now(timezone.utc)+timedelta(days=1)))
    c.session.commit()
    c.before=snapshot(c)
    c.session.commit(); c.session.begin()
    return c


def snapshot(c):
    return deepcopy({model.__tablename__:[tuple(row) for row in c.session.execute(
        select(model.__table__).order_by(model.id)).all()] for model in (User,UserAccount,ApiAuthSession,EventLog)})


async def unlink(c,**changes):
    values=dict(actor=c.actor,account_id=c.account.id,expected_row_version=1,trace_id='unlink:local-test')
    values.update(changes)
    return await c.service.unlink_account(**values)


def unchanged(c):
    assert snapshot(c)==c.before
    assert c.bridge.commits==0


@pytest.mark.asyncio
async def test_unlink_owns_only_selected_identity_and_exact_audit_until_caller_commit(environment):
    c=environment
    before_profile=(c.account.username,c.account.raw_profile,c.account.platform_user_id,c.account.user_id,c.account.created_at)
    result=await unlink(c)
    assert (result.account_id,result.user_id,result.tenant_id)==(c.account.id,c.owner,c.tenant)
    assert result.status=='unlinked' and result.row_version==2 and result.changed is True
    with pytest.raises(FrozenInstanceError): result.changed=False
    assert (c.account.username,c.account.raw_profile,c.account.platform_user_id,c.account.user_id,c.account.created_at)==before_profile
    assert c.account.status=='unlinked' and c.account.row_version==2
    assert c.second.status==c.foreign.status==c.email.status=='active'
    event=c.session.execute(select(EventLog)).scalar_one()
    assert event.event_type==UNLINK_EVENT and event.entity_type=='user_account' and event.entity_id==c.account.id
    assert event.tenant_id==c.tenant and event.user_id==c.owner and event.platform=='api'
    assert event.trace_id=='unlink:local-test'
    assert event.payload=={'operation':UNLINK_OPERATION,'account_id':str(c.account.id),'changes':[
        {'field':'status','old_value':'active','new_value':'unlinked'},
        {'field':'row_version','old_value':1,'new_value':2}]}
    serialized=repr(result)+json.dumps(event.payload)
    for private in ('123456789','555555555','retained-profile','test@example.invalid','refresh_token_hash','raw_profile'):
        assert private not in serialized
    now=snapshot(c)
    assert now['users']==c.before['users'] and now['api_auth_sessions']==c.before['api_auth_sessions']
    assert c.bridge.commits==0 and c.session.in_transaction()
    assert c.identity.require_actor.await_count==3
    statements=[str(s.compile(dialect=postgresql.dialect())) for s in c.bridge.statements]
    assert 'FROM users' in statements[0] and 'FOR UPDATE' in statements[0]
    assert 'FROM user_accounts' in statements[1] and 'FOR UPDATE' in statements[1]
    c.session.rollback(); unchanged(c)


@pytest.mark.asyncio
async def test_unlinked_identity_denies_new_auth_while_other_accounts_and_sessions_survive(environment):
    c=environment
    await unlink(c); c.session.commit()
    with pytest.raises(PlatformTelegramAccountUnlinkedError):
        await UserRepository(c.bridge).get_by_platform_account('telegram','123456789')
    assert await UserRepository(c.bridge).get_by_platform_account('telegram','555555555') is c.second
    assert await UserRepository(c.bridge).get_by_platform_account('email','test@example.invalid') is c.email
    assert snapshot(c)['api_auth_sessions']==c.before['api_auth_sessions']
    assert len(c.session.execute(select(UserAccount.id)).all())==4


@pytest.mark.asyncio
async def test_already_unlinked_with_current_version_is_noop_without_audit_or_version_bump(environment):
    c=environment
    await unlink(c); c.session.commit(); c.session.begin()
    before=snapshot(c); c.bridge.statements.clear()
    result=await unlink(c,expected_row_version=2)
    assert result.changed is False and result.status=='unlinked' and result.row_version==2
    assert snapshot(c)==before and len(c.session.execute(select(EventLog.id)).all())==1
    assert not any(s.is_update or s.is_insert or s.is_delete for s in c.bridge.statements)
    assert c.bridge.commits==0


@pytest.mark.asyncio
async def test_already_unlinked_does_not_bypass_optimistic_version_check(environment):
    c=environment
    await unlink(c);c.session.commit();c.session.begin()
    before=snapshot(c)
    with pytest.raises(Conflict) as error: await unlink(c,expected_row_version=1)
    assert error.value.status_code==409 and error.value.code=='ROW_VERSION_CONFLICT'
    assert snapshot(c)==before


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['missing','foreign','email','inactive_user','membership'])
async def test_authorization_denies_missing_or_foreign_identity_and_inactive_actor(environment,kind):
    c=environment
    options={}
    if kind=='missing':options['account_id']=uuid4()
    if kind=='foreign':options['account_id']=c.foreign.id
    if kind=='email':options['account_id']=c.email.id
    if kind=='inactive_user':
        c.session.execute(update(User).where(User.id==c.owner).values(status='blocked'))
        c.session.commit();c.before=snapshot(c);c.session.commit();c.session.begin()
    if kind=='membership':c.identity.require_actor.side_effect=ApiIdentityAccessError('Denied context.')
    with pytest.raises(ApiIdentityAccessError):await unlink(c,**options)
    unchanged(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['shape','blocked','missing_tenant','wrong_user','wrong_tenant'])
async def test_forged_or_changed_platform_actor_is_rejected(environment,fault):
    c=environment
    actor=c.actor
    if fault=='shape':actor=SimpleNamespace(**c.actor.__dict__)
    elif fault=='blocked':actor=replace(actor,status='blocked')
    elif fault=='missing_tenant':actor=replace(actor,tenant_id=None)
    elif fault=='wrong_user':c.identity.require_actor.return_value=replace(actor,user_id=c.other)
    elif fault=='wrong_tenant':c.identity.require_actor.return_value=replace(actor,tenant_id=uuid4())
    with pytest.raises(ApiIdentityAccessError):await unlink(c,actor=actor)
    unchanged(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('stage',['before_mutation','after_audit'])
async def test_membership_loss_during_operation_rolls_back_even_after_audit_flush(environment,stage):
    c=environment
    c.identity.require_actor.side_effect=([c.actor,ApiIdentityAccessError('Membership revoked.')]
        if stage=='before_mutation' else [c.actor,c.actor,ApiIdentityAccessError('Membership revoked.')])
    with pytest.raises(ApiIdentityAccessError):await unlink(c)
    unchanged(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('version',[None,True,0,-1,'1',2**63-1])
async def test_invalid_version_is_rejected_before_queries(environment,version):
    c=environment
    with pytest.raises(ValueError):await unlink(c,expected_row_version=version)
    assert c.bridge.statements==[]
    c.identity.require_actor.assert_not_awaited()
    unchanged(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('changes',[{'account_id':'123456789'},{'trace_id':''},{'trace_id':False},{'trace_id':'x'*129}])
async def test_invalid_account_or_trace_rejected_before_queries(environment,changes):
    c=environment
    with pytest.raises(ValueError):await unlink(c,**changes)
    assert c.bridge.statements==[]
    unchanged(c)


@pytest.mark.asyncio
async def test_service_requires_active_caller_transaction(environment):
    c=environment
    account_id=c.account.id;c.session.rollback()
    with pytest.raises(OperationError):
        await c.service.unlink_account(actor=c.actor,account_id=account_id,expected_row_version=1)
    assert c.bridge.statements==[]
    unchanged(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['stale','write_miss'])
async def test_version_conflict_changes_neither_account_nor_audit(environment,fault):
    c=environment
    options={'expected_row_version':8} if fault=='stale' else {}
    if fault=='write_miss':c.service.accounts.unlink_account=AsyncMock(return_value=None)
    with pytest.raises(Conflict):await unlink(c,**options)
    unchanged(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['before_flush','after_flush','bad_receipt'])
async def test_mandatory_audit_failure_rolls_back_real_account_update(environment,fault):
    c=environment
    original=c.service.events.create_event
    async def fail(**kwargs):
        assert c.session.get(UserAccount,c.account.id).status=='unlinked'
        if fault=='before_flush':raise RuntimeError('Audit unavailable.')
        event=await original(**kwargs)
        assert c.session.execute(select(EventLog.id)).first() is not None
        if fault=='after_flush':raise RuntimeError('Audit failed after flush.')
        return SimpleNamespace(id=event.id,tenant_id=uuid4())
    c.service.events.create_event=fail
    with pytest.raises((RuntimeError,OperationError)):await unlink(c)
    assert c.bridge.rollbacks==1
    unchanged(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['payload_mutation','trace_mutation'])
async def test_audit_cannot_silently_change_the_payload_or_trace(environment,fault):
    c=environment
    original=c.service.events.create_event
    async def corrupt(**kwargs):
        if fault=='payload_mutation':kwargs['payload']['changes']=[]
        else:kwargs['trace_id']='wrong-trace'
        return await original(**kwargs)
    c.service.events.create_event=corrupt
    with pytest.raises(OperationError):await unlink(c)
    unchanged(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('field',['id','user_id','platform_user_id','status','row_version'])
async def test_invalid_update_receipt_rolls_back_and_creates_no_audit(environment,field):
    c=environment
    original=c.service.accounts.unlink_account
    async def corrupt(**kwargs):
        record=await original(**kwargs)
        assert record.status=='unlinked'
        values={'id':uuid4(),'user_id':c.other,'platform_user_id':'changed-identity','status':'active','row_version':99}
        set_committed_value(record,field,values[field])
        return record
    c.service.accounts.unlink_account=corrupt
    with pytest.raises(OperationError):await unlink(c)
    c.session.expunge_all()
    unchanged(c)


@pytest.mark.asyncio
async def test_database_failure_after_real_update_rolls_back_owner_and_audit(environment):
    c=environment
    original=c.service.accounts.unlink_account
    async def fail(**kwargs):
        record=await original(**kwargs)
        assert record.status=='unlinked' and record.row_version==2
        raise RuntimeError('Failure after account update.')
    c.service.accounts.unlink_account=fail
    with pytest.raises(RuntimeError):await unlink(c)
    unchanged(c)


@pytest.mark.asyncio
async def test_service_failure_rolls_back_callers_earlier_work_too(environment):
    c=environment
    c.session.execute(update(UserAccount).where(UserAccount.id==c.second.id).values(username='outer-pending'))
    c.service.events.create_event=AsyncMock(side_effect=RuntimeError('Audit unavailable.'))
    with pytest.raises(RuntimeError):await unlink(c)
    unchanged(c)


@pytest.mark.asyncio
async def test_caller_can_commit_account_and_audit_together(environment):
    c=environment
    await unlink(c)
    assert c.bridge.commits==0
    c.session.commit();c.session.expire_all()
    assert c.session.get(UserAccount,c.account.id).status=='unlinked'
    assert c.session.get(UserAccount,c.account.id).row_version==2
    assert len(c.session.execute(select(EventLog.id)).all())==1
    assert snapshot(c)['api_auth_sessions']==c.before['api_auth_sessions']


@pytest.mark.asyncio
async def test_rollback_covers_cancellation_after_real_audit_flush(environment):
    import asyncio
    c=environment
    original=c.service.events.create_event
    async def cancel(**kwargs):
        await original(**kwargs)
        raise asyncio.CancelledError()
    c.service.events.create_event=cancel
    with pytest.raises(asyncio.CancelledError):await unlink(c)
    unchanged(c)


@pytest.mark.parametrize('part',['accounts','audit','identity'])
def test_service_rejects_repositories_using_another_transaction(environment,part):
    c=environment
    services=dict(account_repository=c.service.accounts,event_repository=c.service.events,identity_service=c.identity)
    services[{'accounts':'account_repository','audit':'event_repository','identity':'identity_service'}[part]]=SimpleNamespace(session=object())
    with pytest.raises(ValueError):PlatformTelegramAccountUnlinkService(session=c.bridge,**services)
