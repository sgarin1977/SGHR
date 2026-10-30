"""Canonical account lifecycle/authentication regression without live services."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import Column, JSON, MetaData, Table, UniqueConstraint, Uuid, create_engine, event, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from database.models import User, UserAccount
from database.repositories.user import UserRepository, PlatformTelegramAccountUnlinkedError as Denied
from database.repositories.platform_telegram_accounts import PlatformTelegramAccountRepository
from database.repositories.moderation import ModerationRepository
from services.user import UserService, TelegramUserData


class Bridge:
    def __init__(self, session):
        self.session, self.statements, self.commits = session, [], 0

    @property
    def no_autoflush(self): return self.session.no_autoflush

    async def execute(self, statement):
        self.statements.append(statement)
        return self.session.execute(statement)

    async def get(self, *args, **kwargs): return self.session.get(*args, **kwargs)
    async def flush(self): self.session.flush()
    async def rollback(self): self.session.rollback()
    async def commit(self):
        self.commits += 1
        self.session.commit()


@pytest.fixture
def store():
    # Real local SQL/ORM checks. PostgreSQL/RLS/concurrency acceptance is separate.
    metadata = MetaData()
    for name in ('tenants','countries','cities'):
        Table(name, metadata, Column('id', Uuid, primary_key=True))
    tables = [User.__table__.to_metadata(metadata), UserAccount.__table__.to_metadata(metadata)]
    for table in tables:
        for column in table.c:
            if isinstance(column.type, JSONB): column.type = JSON()
    tables[1].append_constraint(UniqueConstraint('platform','platform_user_id'))
    engine = create_engine('sqlite+pysqlite:///:memory:')
    @event.listens_for(engine,'connect')
    def foreign_keys(connection, _): connection.execute('PRAGMA foreign_keys=ON')
    metadata.create_all(engine)
    tenant, owner, other = uuid4(), uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(metadata.tables['tenants'].insert().values(id=tenant))
    with Session(engine,expire_on_commit=False) as session:
        session.add_all([User(id=owner,tenant_id=tenant),User(id=other,tenant_id=tenant)])
        first = UserAccount(id=uuid4(),user_id=owner,platform='telegram',platform_user_id='123456789',
            username='retained-profile',raw_profile={'retained':True})
        session.add(first)
        session.commit()
        bridge = Bridge(session)
        yield SimpleNamespace(session=session,bridge=bridge,owner=owner,other=other,tenant=tenant,account=first,engine=engine)
        session.rollback()
    engine.dispose()


def data():
    return TelegramUserData(platform_user_id='123456789',username='test',first_name='Test',last_name=None,language_code='uk')


def test_canonical_model_matches_applied_lifecycle_defaults_and_versioning(store):
    c = store
    assert c.account.status == 'active' and c.account.row_version == 1
    assert UserAccount.__mapper__.version_id_col is UserAccount.__table__.c.row_version
    assert str(UserAccount.__table__.c.status.server_default.arg) == "'active'"
    assert str(UserAccount.__table__.c.row_version.server_default.arg) == '1'
    c.account.status = 'unlinked'
    c.session.flush()
    assert c.account.row_version == 2
    assert c.account.username == 'retained-profile' and c.account.raw_profile == {'retained':True}
    assert c.account.user_id == c.owner


@pytest.mark.parametrize('platform,status,version', [
    ('telegram','unknown',1),('email','unlinked',1),('telegram','active',0),('telegram','active',-1),
])
def test_invalid_lifecycle_rows_are_rejected_by_storage_checks(store,platform,status,version):
    c = store
    with pytest.raises(IntegrityError):
        c.session.execute(UserAccount.__table__.insert().values(id=uuid4(),user_id=c.owner,
            platform=platform,platform_user_id='another',status=status,row_version=version,raw_profile={}))
    c.session.rollback()


def test_stale_orm_write_cannot_overwrite_another_account_change(store):
    c = store
    c.session.commit()
    with Session(c.engine,expire_on_commit=False) as a, Session(c.engine,expire_on_commit=False) as b:
        first, stale = a.get(UserAccount,c.account.id), b.get(UserAccount,c.account.id)
        first.username = 'changed'
        a.commit()
        stale.status = 'unlinked'
        with pytest.raises(StaleDataError): b.commit()
        b.rollback()
    c.session.expire_all()
    assert c.session.get(UserAccount,c.account.id).status == 'active'


@pytest.mark.asyncio
async def test_active_identity_still_resolves_and_unknown_identity_remains_missing(store):
    c = store
    repo = UserRepository(c.bridge)
    assert await repo.get_by_platform_account('telegram','123456789') is c.account
    assert await repo.get_by_platform_account('telegram','nonexistent') is None
    assert c.bridge.commits == 0


@pytest.mark.asyncio
async def test_unlinked_identity_is_not_hidden_as_missing_or_automatically_recreated(store):
    c = store
    c.account.status='unlinked'
    c.session.commit()
    repo = UserRepository(c.bridge)
    with pytest.raises(Denied): await repo.get_by_platform_account('telegram','123456789')
    with pytest.raises(Denied):
        await repo.create_telegram_user_core(platform_user_id='123456789',username='changed',first_name='Changed',
            last_name=None,language_code='uk',role='client')
    users = UserService(c.bridge)
    users.events.create_event = AsyncMock(side_effect=AssertionError('No audit/onboarding writes on a denied login.'))
    with pytest.raises(Denied): await users.register_telegram_user(data())
    assert c.bridge.commits == 0
    assert len(c.session.execute(select(User.id)).all()) == 2
    assert len(c.session.execute(select(UserAccount.id)).all()) == 1
    assert c.account.status == 'unlinked' and c.account.row_version == 2
    assert c.account.username == 'retained-profile'
    users.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_lookup_reloads_cached_identity_and_does_not_flush_unsaved_reactivation(store):
    c = store
    c.session.execute(update(UserAccount).where(UserAccount.id==c.account.id).values(status='unlinked',row_version=2)
        .execution_options(synchronize_session=False))
    c.session.commit()
    assert c.account.status == 'active'  # Deliberately stale identity map.
    c.account.username = 'unsaved-profile'
    with pytest.raises(Denied): await UserRepository(c.bridge).get_by_platform_account('telegram','123456789')
    assert c.account.status == 'unlinked' and c.account.username == 'retained-profile'
    assert c.bridge.commits == 0


@pytest.mark.parametrize('status',[None,'unknown','',False])
@pytest.mark.asyncio
async def test_malformed_identity_state_fails_closed(status):
    from contextlib import nullcontext
    session = SimpleNamespace(no_autoflush=nullcontext(),execute=AsyncMock(return_value=SimpleNamespace(
        scalar_one_or_none=lambda: SimpleNamespace(status=status))))
    with pytest.raises(Denied): await UserRepository(session).get_by_platform_account('telegram','123')
    assert '123' not in str(Denied())


@pytest.mark.asyncio
async def test_unlinked_account_cannot_resolve_bot_user_or_requester_context(store):
    c=store
    c.account.status='unlinked'
    c.session.commit()
    users=UserService(c.bridge)
    with pytest.raises(Denied): await users.get_user_by_telegram_id('123456789')
    with pytest.raises(Denied): await users.get_requester_context('123456789')
    assert await ModerationRepository(c.bridge).get_user_by_telegram_id('123456789') is None


@pytest.mark.asyncio
async def test_other_telegram_account_and_email_are_preserved_and_resolve_normally(store):
    c=store
    second=UserAccount(id=uuid4(),user_id=c.owner,platform='telegram',platform_user_id='987654321')
    email=UserAccount(id=uuid4(),user_id=c.owner,platform='email',platform_user_id='test@example.com')
    c.session.add_all([second,email])
    c.account.status='unlinked'
    c.session.commit()
    repo=UserRepository(c.bridge)
    assert await repo.get_by_platform_account('telegram','987654321') is second
    assert await repo.get_by_platform_account('email','test@example.com') is email
    assert await repo.get_telegram_account_by_user_id(c.owner) is second
    assert await PlatformTelegramAccountRepository(c.bridge).list_accounts(user_id=c.owner) == (second,)
    assert await ModerationRepository(c.bridge).get_user_by_telegram_id('987654321') is not None
    second.status='unlinked'
    c.session.flush()
    assert await repo.get_telegram_account_by_user_id(c.owner) is None


@pytest.mark.asyncio
async def test_binding_does_not_report_unlinked_connection_as_active_before_explicit_relink(store):
    c=store
    c.account.status='unlinked'
    c.session.commit()
    with pytest.raises(Denied):
        await PlatformTelegramAccountRepository(c.bridge).create_or_get_account(user_id=c.owner,telegram_user_id=123456789)
    assert c.account.status=='unlinked' and c.account.user_id==c.owner


def auth_service():
    from services.api_auth import ApiAuthService
    db=SimpleNamespace(rollback=AsyncMock(),commit=AsyncMock())
    users=SimpleNamespace(register_telegram_user=AsyncMock(side_effect=Denied()),get_user_by_id=AsyncMock())
    sessions=SimpleNamespace(create_session=AsyncMock(),revoke_all_sessions=AsyncMock())
    access=SimpleNamespace(issue_access_token=AsyncMock(),access_ttl_seconds=900)
    refresh=SimpleNamespace(issue_refresh_token=AsyncMock())
    verifier=SimpleNamespace(verify=lambda *a,**kw: SimpleNamespace(platform_user_id=123456789,
        username='private-name',first_name='Private',last_name=None,language_code='uk'))
    return ApiAuthService(session=db,telegram_verifier=verifier,user_service=users,access_token_codec=access,
        refresh_token_codec=refresh,session_repository=sessions,refresh_token_ttl_seconds=3600),db,users,sessions,access,refresh


@pytest.mark.asyncio
async def test_unlinked_telegram_login_creates_no_session_tokens_or_global_revoke():
    from services.api_auth import ApiAuthAccessError
    service,db,users,sessions,access,refresh=auth_service()
    with pytest.raises(ApiAuthAccessError) as caught:
        await service.authenticate_telegram(init_data='test-signed-proof',device_id=None,device_name=None,
            now=datetime.now(timezone.utc))
    assert str(caught.value)=='Authenticated user is not available.'
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
    users.get_user_by_id.assert_not_awaited()
    sessions.create_session.assert_not_awaited()
    sessions.revoke_all_sessions.assert_not_awaited()
    access.issue_access_token.assert_not_awaited()
    refresh.issue_refresh_token.assert_not_awaited()


@pytest.mark.asyncio
async def test_unlinked_login_http_uses_existing_403_envelope_without_identity_leak():
    import httpx
    from api.app import create_app
    from api.auth import get_api_auth_service
    from api.dependencies import get_api_request_rate_limit_service
    service,*_=auth_service()
    app=create_app()
    app.dependency_overrides[get_api_auth_service]=lambda:service
    app.dependency_overrides[get_api_request_rate_limit_service]=lambda:SimpleNamespace(ensure_public_api_allowed=AsyncMock())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://testserver') as client:
        response=await client.post('/api/v1/auth/telegram',json={'init_data':'test-signed-proof'})
    assert response.status_code==403 and response.json()['error']['code']=='account_unavailable'
    assert '123456789' not in response.text and 'private-name' not in response.text


@pytest.mark.asyncio
async def test_binding_transport_maps_disconnected_account_to_existing_forbidden_response():
    from api.errors import ApiHttpError
    from api.routes.platform_telegram_binding import _run
    async def denied(): raise Denied()
    with pytest.raises(ApiHttpError) as caught: await _run(denied())
    assert caught.value.status_code==403 and caught.value.code=='account_unavailable'


@pytest.mark.asyncio
async def test_normal_start_for_unlinked_identity_replies_without_onboarding_menu_or_state_changes(monkeypatch):
    import handlers.start as start
    from ui.texts import t,translations
    @asynccontextmanager
    async def session(): yield object()
    register=AsyncMock(side_effect=Denied())
    monkeypatch.setattr(start,'get_session',session)
    monkeypatch.setattr(start,'UserStartService',lambda session:SimpleNamespace(register_user=register))
    monkeypatch.setattr(start,'delete_telegram_messages',AsyncMock())
    menu=AsyncMock(side_effect=AssertionError('No ordinary menu on a disconnected identity.'))
    monkeypatch.setattr(start,'replace_main_menu_message',menu)
    message=SimpleNamespace(from_user=SimpleNamespace(id=123456789,language_code='uk',first_name='Test',username=None,last_name=None),
        bot=object(),chat=SimpleNamespace(id=123456789),message_id=1,answer=AsyncMock())
    state=SimpleNamespace(update_data=AsyncMock(),get_data=AsyncMock())
    await start.cmd_start(message,state)
    message.answer.assert_awaited_once_with(t('platform_telegram_account_unlinked','uk'))
    menu.assert_not_awaited()
    state.update_data.assert_not_awaited()
    assert set(translations['platform_telegram_account_unlinked'])=={'ru','en','pt','uk','pl','de','nl'}
