import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

from aiogram import Bot
from aiogram.types import Update
import pytest

from api.security import AccessTokenCodec, RefreshTokenCodec
from api.telegram_auth import TelegramAuthenticationError
from services.api_auth import ApiAuthAccessError, ApiAuthRefreshError, ApiAuthService
from services.api_identity import ApiIdentityService
from services.platform_bot_auth import PlatformBotAuthenticationService
from services.platform_bot_auth_proof import PlatformBotUpdateVerifier


def incoming(bot, now, *, callback=False, detached=False, **message_changes):
    message = dict(message_id=10, date=int(now.timestamp()), chat=dict(id=123, type='private'),
        from_user=dict(id=123, is_bot=False, first_name='Person'), text='Construction')
    message.update(message_changes)
    if callback:
        message['from_user'] = dict(id=bot.id, is_bot=True, first_name='Platform bot')
        raw = dict(update_id=20, callback_query=dict(id='callback', chat_instance='private',
            from_user=dict(id=123, is_bot=False, first_name='Person'), message=message, data='M_SUITE_CONSTRUCTION'))
    else:
        raw = dict(update_id=20, message=message)
    return Update.model_validate(raw, context={} if detached else {'bot': bot})


@pytest.fixture
def case():
    c = NS(now=datetime.now(timezone.utc)-timedelta(seconds=2), bot=Bot(token='123456:local-test-bot-token'),
        user_id=uuid4(), tenant=uuid4(), stored=[], pending=[], fail_commit=False)
    c.user = NS(id=c.user_id, tenant_id=c.tenant, status='active', active_role='client', language_code='uk', timezone='Europe/Kyiv')
    c.account = NS(id=uuid4(), user_id=c.user_id, status='active')
    async def commit():
        if c.fail_commit: raise RuntimeError('commit failure')
        c.stored.extend(c.pending); c.pending.clear()
    async def rollback(): c.pending.clear()
    c.session = NS(commit=AsyncMock(side_effect=commit), rollback=AsyncMock(side_effect=rollback))
    c.users = NS(session=c.session, get_by_platform_account=AsyncMock(return_value=c.account),
        has_active_tenant_membership=AsyncMock(return_value=False), list_active_roles=AsyncMock(return_value=['client']),
        list_active_permissions=AsyncMock(return_value=[]), list_active_role_scopes=AsyncMock(return_value=[]))
    c.user_service = NS(session=c.session, repository=c.users, get_user_by_id=AsyncMock(return_value=c.user),
        lock_telegram_authentication=AsyncMock())
    async def create_session(**values):
        row = NS(id=uuid4(), **values); c.pending.append(row); return row
    c.sessions = NS(session=c.session, create_session=AsyncMock(side_effect=create_session),
        get_active_session_for_update=AsyncMock(), rotate_session=AsyncMock())
    async def create_event(**values):
        c.pending.append(NS(**values))
    c.events = NS(session=c.session, create_event=AsyncMock(side_effect=create_event))
    c.codec = AccessTokenCodec(secret='local-platform-bot-authentication-test-key', issuer='local-test', audience='local-test', access_ttl_seconds=600)
    c.tokens = ApiAuthService(session=c.session, telegram_verifier=None, user_service=c.user_service,
        access_token_codec=c.codec, refresh_token_codec=RefreshTokenCodec(), session_repository=c.sessions,
        refresh_token_ttl_seconds=3600, event_repository=c.events)
    c.identity = ApiIdentityService(c.session, user_service=c.user_service, user_repository=c.users,
        event_repository=c.events, translation_service=object())
    c.service = PlatformBotAuthenticationService(session=c.session, users=c.users, user_service=c.user_service,
        identity_service=c.identity, token_service=c.tokens, events=c.events, now_provider=lambda: c.now)
    c.verifier = PlatformBotUpdateVerifier(bot=c.bot, update=incoming(c.bot,c.now,callback=True), now_provider=lambda:c.now)
    return c


async def login(c, **changes):
    return await c.service.authenticate(**dict(verifier=c.verifier, proof=c.verifier.proof, tenant_id=c.tenant, **changes))


@pytest.mark.asyncio
async def test_shared_platform_tokens_and_mandatory_audit_commit_once(case):
    c=case
    result=await login(c)
    claims=c.codec.decode_access_token(result.access_token.get_secret_value())
    assert (claims.user_id,claims.tenant_id)==(c.user_id,c.tenant)
    c.users.get_by_platform_account.assert_awaited_once_with('telegram','123')
    c.user_service.lock_telegram_authentication.assert_awaited_once_with(user_id=c.user_id,telegram_user_id=123)
    assert len(c.stored)==2 and not c.pending
    row=c.sessions.create_session.await_args.kwargs
    assert row['auth_method']=='telegram' and row['tenant_id']==c.tenant and len(row['refresh_token_hash'])==64
    assert result.refresh_token.get_secret_value() not in repr(result)+str(c.events.create_event.await_args)
    audit=c.events.create_event.await_args.kwargs
    assert audit['event_type']=='api_telegram_auth_succeeded' and audit['payload']=={'auth_method':'telegram','transport':'bot_api'}
    c.session.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('home', ['other', 'null'])
async def test_selected_secondary_membership_does_not_rewrite_home_tenant(case, home):
    c=case
    original=uuid4() if home=='other' else None
    c.user.tenant_id=original
    c.users.has_active_tenant_membership.return_value=True
    result=await login(c)
    assert c.user.tenant_id==original and result.tenant_id==c.tenant
    c.users.has_active_tenant_membership.assert_awaited_once_with(user_id=c.user_id,tenant_id=c.tenant)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing','unlinked','blocked','nonmember','identity_changed'])
async def test_denied_identity_or_membership_never_creates_session(case, failure):
    c=case
    if failure=='missing': c.users.get_by_platform_account.return_value=None
    elif failure=='unlinked': c.account.status='unlinked'
    elif failure=='blocked': c.user.status='blocked'
    elif failure=='nonmember': c.user.tenant_id=uuid4()
    else:
        from database.repositories.user import PlatformTelegramAccountUnlinkedError
        c.user_service.lock_telegram_authentication.side_effect=PlatformTelegramAccountUnlinkedError()
    with pytest.raises(ApiAuthAccessError): await login(c)
    c.sessions.create_session.assert_not_called()
    c.events.create_event.assert_not_called()
    assert not c.stored and not c.pending
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['audit','commit','cancel'])
async def test_session_and_audit_are_not_persisted_when_the_operation_fails(case, failure):
    c=case
    if failure=='audit': c.events.create_event.side_effect=RuntimeError('audit failure')
    elif failure=='commit': c.fail_commit=True
    else: c.events.create_event.side_effect=asyncio.CancelledError()
    with pytest.raises((RuntimeError,asyncio.CancelledError)): await login(c)
    c.sessions.create_session.assert_awaited_once()
    assert not c.pending and not c.stored
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('proof', [None, {}, 'signed-init-data', {'user_id':'123'}])
async def test_raw_request_data_is_not_accepted_as_bot_proof(case, proof):
    c=case
    with pytest.raises(TelegramAuthenticationError):
        await c.service.authenticate(verifier=c.verifier,proof=proof,tenant_id=c.tenant)
    c.users.get_by_platform_account.assert_not_called()


@pytest.mark.parametrize('callback', [False,True])
def test_trusted_identity_is_snapshotted_from_private_bot_dispatch(case, callback):
    c=case
    event=incoming(c.bot,c.now,callback=callback)
    verifier=PlatformBotUpdateVerifier(bot=c.bot,update=event,now_provider=lambda:c.now)
    sender=event.callback_query.from_user if callback else event.message.from_user
    object.__setattr__(sender,'id',999)
    assert verifier.verify(verifier.proof,now=c.now).platform_user_id==123


@pytest.mark.parametrize('failure', ['group','detached','sender_chat','foreign_bot'])
def test_untrusted_transport_context_is_denied(case, failure):
    c=case
    changes={}
    if failure=='group':changes['chat']=dict(id=-123,type='group')
    if failure=='sender_chat':changes['sender_chat']=dict(id=-123,type='channel')
    event=incoming(c.bot,c.now,callback=True,detached=failure=='detached',**changes)
    bot=Bot(token='999999:another-local-bot-token') if failure=='foreign_bot' else c.bot
    with pytest.raises(TelegramAuthenticationError): PlatformBotUpdateVerifier(bot=bot,update=event,now_provider=lambda:c.now)


@pytest.mark.parametrize('seconds', [-1,60,100])
def test_ephemeral_proof_cannot_be_used_outside_its_dispatch_window(case, seconds):
    c=case
    with pytest.raises(TelegramAuthenticationError):
        c.verifier.verify(c.verifier.proof,now=c.now+timedelta(seconds=seconds))


@pytest.mark.asyncio
@pytest.mark.parametrize('home', ['other','null'])
async def test_refresh_keeps_selected_secondary_organization_and_checks_membership(case, home):
    c=case
    c.user.tenant_id=uuid4() if home=='other' else None
    c.users.has_active_tenant_membership.return_value=True
    result=await login(c)
    c.sessions.get_active_session_for_update.return_value=c.stored[0]
    refreshed=await c.tokens.refresh_tokens(refresh_token=result.refresh_token.get_secret_value(),now=c.now)
    claims=c.codec.decode_access_token(refreshed.access_token)
    assert (claims.user_id,claims.tenant_id)==(c.user_id,c.tenant)
    assert c.users.has_active_tenant_membership.await_count==2


@pytest.mark.asyncio
async def test_secondary_membership_loss_blocks_refresh(case):
    c=case
    c.user.tenant_id=None;c.users.has_active_tenant_membership.return_value=True
    result=await login(c)
    c.sessions.get_active_session_for_update.return_value=c.stored[0]
    c.users.has_active_tenant_membership.return_value=False
    with pytest.raises(ApiAuthRefreshError):
        await c.tokens.refresh_tokens(refresh_token=result.refresh_token.get_secret_value(),now=c.now)
    c.sessions.rotate_session.assert_not_called()
