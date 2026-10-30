"""Local SQL and auth ordering checks; native PostgreSQL concurrency is separate."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.dialects import postgresql

from database.models import User, UserAccount
from database.repositories.platform_telegram_accounts import (
    PlatformTelegramAccountRepository as Repository,
    PlatformTelegramAccountAlreadyLinkedError,
    PlatformTelegramIdentityUnavailableError,
)
from database.repositories.user import PlatformTelegramAccountUnlinkedError
from services.user import UserService
from services.api_auth import ApiAuthService, ApiAuthAccessError
from test_construction_1a_platform_telegram_account_auth_guards import store


def sql(statement):
    return str(statement.compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_auth_locks_user_then_exact_account_and_refreshes_both(store):
    c=store
    owner=c.session.get(User,c.owner)
    c.session.execute(update(User).where(User.id==c.owner).values(language_code='en').execution_options(synchronize_session=False))
    c.session.execute(update(UserAccount).where(UserAccount.id==c.account.id).values(username='server-profile',row_version=2).execution_options(synchronize_session=False))
    assert c.account.username=='retained-profile'
    assert await Repository(c.bridge).lock_account_for_authentication(user_id=c.owner,telegram_user_id=123456789) is c.account
    assert owner.language_code=='en' and c.account.username=='server-profile' and c.account.row_version==2
    statements=c.bridge.statements
    assert len(statements)==2
    assert 'FROM users' in sql(statements[0]) and 'FOR UPDATE' in sql(statements[0])
    assert 'FROM user_accounts' in sql(statements[1]) and 'FOR UPDATE' in sql(statements[1])
    assert all(s.get_execution_options()['populate_existing'] is True for s in statements)
    assert c.bridge.commits==0 and c.session.in_transaction()


@pytest.mark.asyncio
@pytest.mark.parametrize('state,error',[
    ('unlinked',PlatformTelegramAccountUnlinkedError),
    ('foreign',PlatformTelegramAccountAlreadyLinkedError),
    ('missing',PlatformTelegramIdentityUnavailableError),
    ('inactive_user',PlatformTelegramIdentityUnavailableError),
])
async def test_auth_rejects_untrusted_identity_states_without_writes(store,state,error):
    c=store
    if state=='unlinked': c.account.status='unlinked'
    if state=='foreign': c.account.user_id=c.other
    if state=='inactive_user': c.session.get(User,c.owner).status='blocked'
    c.session.commit()
    with pytest.raises(error):
        await Repository(c.bridge).lock_account_for_authentication(user_id=c.owner,
            telegram_user_id=987654321 if state=='missing' else 123456789)
    assert c.bridge.commits==0
    assert not any(sql(s).startswith(('UPDATE','INSERT','DELETE')) for s in c.bridge.statements)


@pytest.mark.asyncio
async def test_owned_lock_preserves_unlinked_state_and_filters_foreign_email_and_missing(store):
    c=store
    repo=Repository(c.bridge)
    c.account.status='unlinked'
    email=UserAccount(id=uuid4(),user_id=c.owner,platform='email',platform_user_id='test@example.invalid',raw_profile={})
    c.session.add(email)
    c.session.commit()
    assert await repo.get_owned_account_for_update(account_id=c.account.id,user_id=c.owner) is c.account
    assert c.account.status=='unlinked' and c.account.row_version==2
    assert await repo.get_owned_account_for_update(account_id=c.account.id,user_id=c.other) is None
    assert await repo.get_owned_account_for_update(account_id=email.id,user_id=c.owner) is None
    assert await repo.get_owned_account_for_update(account_id=uuid4(),user_id=c.owner) is None
    assert all('FOR UPDATE' in sql(s) for s in c.bridge.statements)
    assert c.bridge.commits==0


@pytest.mark.asyncio
async def test_unlink_updates_only_requested_active_account_version_and_rollback_restores_it(store):
    c=store
    account_id=c.account.id
    second=UserAccount(id=uuid4(),user_id=c.owner,platform='telegram',platform_user_id='222222222',raw_profile={})
    email=UserAccount(id=uuid4(),user_id=c.owner,platform='email',platform_user_id='test@example.invalid',raw_profile={})
    c.session.add_all([second,email]); c.session.commit()
    repo=Repository(c.bridge)
    result=await repo.unlink_account(account_id=account_id,user_id=c.owner,expected_row_version=1)
    assert result is c.account and result.status=='unlinked' and result.row_version==2
    assert result.user_id==c.owner and result.platform_user_id=='123456789'
    assert result.username=='retained-profile' and result.raw_profile=={'retained':True}
    assert second.status==email.status=='active' and second.row_version==email.row_version==1
    c.session.flush()
    assert result.row_version==2 and not c.session.dirty
    mutation=sql(c.bridge.statements[-1])
    for predicate in ('user_accounts.id =','user_accounts.user_id =','user_accounts.platform =',
        'user_accounts.status =','user_accounts.row_version ='):
        assert predicate in mutation
    assert 'updated_at=now()' in mutation and 'RETURNING user_accounts.id' in mutation
    assert c.bridge.commits==0
    c.session.rollback(); c.session.expire_all()
    assert c.session.get(UserAccount,account_id).status=='active'
    assert c.session.get(UserAccount,account_id).row_version==1


@pytest.mark.asyncio
@pytest.mark.parametrize('case',['stale','already_unlinked','foreign','email','missing'])
async def test_conditional_unlink_never_changes_nonmatching_state(store,case):
    c=store
    if case=='already_unlinked': c.account.status='unlinked'
    if case=='email': c.account.platform='email'
    c.session.commit()
    before=(c.account.status,c.account.row_version,c.account.user_id)
    result=await Repository(c.bridge).unlink_account(
        account_id=uuid4() if case=='missing' else c.account.id,
        user_id=c.other if case=='foreign' else c.owner,
        expected_row_version=8 if case=='stale' else c.account.row_version)
    assert result is None
    c.session.expire_all()
    assert (c.account.status,c.account.row_version,c.account.user_id)==before
    assert c.bridge.commits==0


@pytest.mark.asyncio
@pytest.mark.parametrize('version',[True,False,0,-1,1.5,'1',None,2**63-1,2**63])
async def test_invalid_unlink_version_rejected_before_query(store,version):
    c=store
    with pytest.raises(ValueError):
        await Repository(c.bridge).unlink_account(account_id=c.account.id,user_id=c.owner,expected_row_version=version)
    assert c.bridge.statements==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('method,values',[
    ('unlink_account',dict(account_id='bad',user_id=uuid4(),expected_row_version=1)),
    ('get_owned_account_for_update',dict(account_id=uuid4(),user_id='bad')),
    ('lock_account_for_authentication',dict(user_id=uuid4(),telegram_user_id=True)),
    ('lock_account_for_authentication',dict(user_id=uuid4(),telegram_user_id='123456789')),
    ('lock_account_for_authentication',dict(user_id='bad',telegram_user_id=123456789)),
])
async def test_invalid_identifiers_rejected_before_query(store,method,values):
    with pytest.raises(ValueError): await getattr(Repository(store.bridge),method)(**values)
    assert store.bridge.statements==[]


@pytest.mark.asyncio
async def test_pending_local_reactivation_does_not_autoflush_before_trusted_read(store):
    c=store
    c.account.status='unlinked'; c.session.commit()
    c.account.status='active'
    with pytest.raises(PlatformTelegramAccountUnlinkedError):
        await Repository(c.bridge).lock_account_for_authentication(user_id=c.owner,telegram_user_id=123456789)
    assert c.account.status=='unlinked' and c.account.row_version==2


@pytest.mark.asyncio
@pytest.mark.parametrize('method',['unlink_account','get_owned_account_for_update'])
async def test_inactive_user_cannot_read_or_unlink_account(store,method):
    c=store
    c.session.get(User,c.owner).status='blocked'; c.session.commit()
    kwargs=dict(account_id=c.account.id,user_id=c.owner)
    if method=='unlink_account': kwargs['expected_row_version']=1
    with pytest.raises(PlatformTelegramIdentityUnavailableError): await getattr(Repository(c.bridge),method)(**kwargs)
    assert c.account.status=='active' and c.account.row_version==1
    assert len(c.bridge.statements)==1 and c.bridge.commits==0


def auth_service(c,registration):
    users=UserService(c.bridge)
    users.register_telegram_user=registration
    verifier=SimpleNamespace(verify=lambda *a,**kw: SimpleNamespace(platform_user_id=123456789,
        username='local',first_name='Local',last_name=None,language_code='uk'))
    calls=[]
    def refresh():
        calls.append('refresh')
        assert len(c.bridge.statements)==2
        return SimpleNamespace(token='opaque-refresh',token_hash='a'*64)
    def access(**kw): calls.append('access'); return 'signed-access'
    async def create(**kw): calls.append('session'); return SimpleNamespace(id=uuid4())
    sessions=SimpleNamespace(create_session=AsyncMock(side_effect=create),revoke_all_sessions=AsyncMock())
    service=ApiAuthService(session=c.bridge,telegram_verifier=verifier,user_service=users,
        access_token_codec=SimpleNamespace(access_ttl_seconds=900,issue_access_token=access),
        refresh_token_codec=SimpleNamespace(issue_refresh_token=refresh),session_repository=sessions,
        refresh_token_ttl_seconds=3600,event_repository=None)
    return service,calls,sessions


@pytest.mark.asyncio
async def test_real_identity_lock_precedes_credential_creation_and_auth_commit(store):
    c=store
    service,calls,sessions=auth_service(c,AsyncMock(return_value=SimpleNamespace(user_id=c.owner)))
    result=await service.authenticate_telegram(init_data='verified-local',device_id=None,device_name=None,
        now=datetime.now(timezone.utc))
    assert result.access_token=='signed-access' and calls==['refresh','access','session']
    assert c.bridge.commits==1 and not c.session.in_transaction()
    sessions.revoke_all_sessions.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['unlinked','foreign','tenant_removed','inactive'])
async def test_final_lock_denies_changes_after_registration_without_issuing_tokens(store,change):
    c=store
    cached_user=c.session.get(User,c.owner)
    async def registration(_):
        if change in ('unlinked','foreign'):
            changes={'status':'unlinked','row_version':2} if change=='unlinked' else {'user_id':c.other}
            c.session.execute(update(UserAccount).where(UserAccount.id==c.account.id).values(**changes).execution_options(synchronize_session=False))
        else:
            changes={'tenant_id':None} if change=='tenant_removed' else {'status':'blocked'}
            c.session.execute(update(User).where(User.id==c.owner).values(**changes).execution_options(synchronize_session=False))
        c.session.commit()
        # ORM cache still contains the old state; the final gate must reload it.
        assert cached_user.tenant_id==c.tenant and cached_user.status=='active'
        return SimpleNamespace(user_id=c.owner)
    service,calls,sessions=auth_service(c,registration)
    with pytest.raises(ApiAuthAccessError):
        await service.authenticate_telegram(init_data='verified-local',device_id=None,device_name=None)
    assert calls==[] and c.bridge.commits==0
    sessions.create_session.assert_not_awaited()
    sessions.revoke_all_sessions.assert_not_awaited()
    assert not c.session.in_transaction()


@pytest.mark.asyncio
async def test_auth_lock_failure_rolls_back_without_session_and_does_not_leak_identity(store,monkeypatch):
    c=store
    monkeypatch.setattr(Repository,'lock_account_for_authentication',AsyncMock(side_effect=PlatformTelegramAccountAlreadyLinkedError()))
    service,calls,sessions=auth_service(c,AsyncMock(return_value=SimpleNamespace(user_id=c.owner)))
    with pytest.raises(ApiAuthAccessError,match='Authenticated user is not available') as error:
        await service.authenticate_telegram(init_data='verified-local',device_id=None,device_name=None)
    assert '123456789' not in str(error.value) and str(c.owner) not in str(error.value)
    assert calls==[] and c.bridge.commits==0
    sessions.create_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_generic_lock_database_failure_is_rolled_back_and_not_downgraded_to_success(store,monkeypatch):
    c=store
    monkeypatch.setattr(Repository,'lock_account_for_authentication',AsyncMock(side_effect=RuntimeError('local storage failure')))
    service,calls,sessions=auth_service(c,AsyncMock(return_value=SimpleNamespace(user_id=c.owner)))
    with pytest.raises(RuntimeError,match='local storage failure'):
        await service.authenticate_telegram(init_data='verified-local',device_id=None,device_name=None)
    assert calls==[] and c.bridge.commits==0
    sessions.create_session.assert_not_awaited()
