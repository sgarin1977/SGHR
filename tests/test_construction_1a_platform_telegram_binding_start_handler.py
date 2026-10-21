import ast
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher, Router
from aiogram.client.session.base import BaseSession
from aiogram.filters import CommandObject, CommandStart
from aiogram.methods import DeleteMessage, GetMe, SendMessage
from aiogram.types import Message, User
import pytest

import handlers.platform_telegram_binding as routes
from api.settings import ApiConfigurationError
from api.telegram_auth import TelegramAuthenticationError
from database.repositories.platform_telegram_accounts import PlatformTelegramAccountAlreadyLinkedError
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.api_rate_limits import (
    API_RATE_LIMIT_WINDOW_SECONDS, PUBLIC_API_RATE_LIMIT,
    ApiRequestRateLimitExceededError, ApiRequestRateLimitService,
)
from services.platform_telegram_binding_tokens import PlatformTelegramBindingTokenDeniedError
from ui.texts import t, translations
from test_construction_1a_platform_telegram_binding_bot import update
from test_construction_1a_platform_telegram_binding_consume import environment
from test_construction_1a_platform_telegram_binding_workflow import workflow, counts, KEY


class TelegramSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent, self.deleted = [], []
        self.fail_delete = False

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, GetMe):
            return User(id=bot.id, is_bot=True, first_name='Test Bot', username='ConfiguredBot')
        if isinstance(method, DeleteMessage):
            self.deleted.append((method.chat_id, method.message_id))
            if self.fail_delete:
                raise RuntimeError('private-message-body-must-not-be-logged')
            return True
        if isinstance(method, SendMessage):
            self.sent.append(method.text)
            return Message.model_validate(dict(message_id=100, date=1,
                chat=dict(id=method.chat_id, type='private'), text=method.text), context={'bot': bot})
        raise AssertionError('Unexpected Telegram method')

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b''


@pytest.fixture
def handler(monkeypatch):
    c = SimpleNamespace()
    c.telegram = TelegramSession()
    c.bot = Bot(token='123456:local-binding-handler-secret', session=c.telegram)
    c.session = SimpleNamespace(active=False, begins=0)
    c.session.in_transaction = lambda: c.session.active
    async def begin():
        c.session.active = True
        c.session.begins += 1
    c.session.begin = begin
    c.entries = 0
    @asynccontextmanager
    async def get_session():
        c.entries += 1
        yield c.session
    c.consume = AsyncMock()
    c.rates = SimpleNamespace(ensure_telegram_binding_allowed=AsyncMock())
    monkeypatch.setattr(routes, 'get_session', get_session)
    monkeypatch.setattr(routes, 'get_binding_encryption_key', lambda: KEY)
    monkeypatch.setattr(routes, 'get_api_request_rate_limit_service', lambda: c.rates)
    monkeypatch.setattr(routes, 'consume_platform_telegram_binding_bot_start', c.consume)
    return c


async def dispatch(c, *, text=None, **changes):
    dp, fallback = Dispatcher(), Router(name='ordinary_start_test')
    c.registered = 0
    @fallback.message(CommandStart())
    async def normal_start(message):
        c.registered += 1
    dp.include_router(routes.build_platform_telegram_binding_router())
    dp.include_router(fallback)
    event = update(c.bot, **changes) if text is None else update(c.bot, text=text, **changes)
    await dp.feed_update(c.bot, event)
    await dp.storage.close()
    return event


@pytest.mark.asyncio
async def test_binding_start_is_intercepted_before_ordinary_registration(handler):
    c = handler
    event = await dispatch(c)
    assert c.registered == 0 and c.entries == c.session.begins == 1
    c.consume.assert_awaited_once_with(session=c.session, bot=c.bot, update=event, encryption_key=KEY)
    c.rates.ensure_telegram_binding_allowed.assert_awaited_once_with(bot_id=c.bot.id, telegram_user_id=123456789)
    assert c.telegram.deleted == [(123456789, 10)]
    assert c.telegram.sent == [t('platform_telegram_binding_success')]
    assert 'A' * 43 not in ''.join(c.telegram.sent)


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['/start', '/start ordinary-link', '/start search_specialist'])
async def test_other_start_commands_keep_ordinary_registration_path(handler, text):
    c = handler
    await dispatch(c, text=text)
    assert c.registered == 1 and c.entries == 0
    c.consume.assert_not_awaited()
    c.rates.ensure_telegram_binding_allowed.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['/start bind_', '/start bind_invalid', '/start BIND_' + 'A'*43,
    '/start bind_' + 'A'*43 + ' extra'])
async def test_malformed_reserved_binding_never_falls_through_to_registration(handler, text):
    c = handler
    await dispatch(c, text=text)
    assert c.registered == c.entries == 0
    c.consume.assert_not_awaited()
    assert c.telegram.sent == [t('platform_telegram_binding_invalid')]


@pytest.mark.asyncio
@pytest.mark.parametrize('error,key', [
    (PlatformTelegramBindingTokenDeniedError('do-not-print-token'), 'invalid'),
    (TelegramAuthenticationError('do-not-print-proof'), 'invalid'),
    (PlatformTelegramAccountAlreadyLinkedError(), 'conflict'),
    (ApiIdempotencyKeyReusedError('do-not-print-request'), 'unavailable'),
    (RuntimeError('do-not-print-database-password'), 'unavailable'),
])
async def test_binding_errors_have_safe_replies_and_no_registration(handler, caplog, error, key):
    c = handler
    c.consume.side_effect = error
    await dispatch(c)
    assert c.registered == 0
    assert c.telegram.sent == [t('platform_telegram_binding_' + key)]
    assert 'do-not-print' not in caplog.text + ''.join(c.telegram.sent)


@pytest.mark.asyncio
async def test_rate_limit_denial_happens_before_db_or_binding_writes(handler):
    c = handler
    c.rates.ensure_telegram_binding_allowed.side_effect = ApiRequestRateLimitExceededError()
    await dispatch(c)
    assert c.entries == c.registered == 0
    c.consume.assert_not_awaited()
    assert c.telegram.sent == [t('platform_telegram_binding_rate_limited')]


@pytest.mark.asyncio
async def test_missing_platform_key_does_not_open_database_session(handler, monkeypatch, caplog):
    c = handler
    def unavailable():
        raise ApiConfigurationError('do-not-print-config')
    monkeypatch.setattr(routes, 'get_binding_encryption_key', unavailable)
    await dispatch(c)
    assert c.entries == c.registered == 0 and 'do-not-print' not in caplog.text
    c.consume.assert_not_awaited()
    assert c.telegram.sent == [t('platform_telegram_binding_unavailable')]


@pytest.mark.asyncio
async def test_rate_store_outage_fails_closed_without_database_work(handler):
    c = handler
    c.rates.ensure_telegram_binding_allowed.side_effect = RuntimeError('redis-unavailable')
    await dispatch(c)
    assert c.entries == c.registered == 0
    c.consume.assert_not_awaited()
    assert c.telegram.sent == [t('platform_telegram_binding_unavailable')]


@pytest.mark.asyncio
async def test_group_link_does_not_start_registration_or_binding(handler):
    c = handler
    await dispatch(c, chat=dict(id=-100, type='group', title='Test'))
    assert c.entries == c.registered == 0
    c.consume.assert_not_awaited()
    c.rates.ensure_telegram_binding_allowed.assert_not_awaited()
    assert c.telegram.sent == [t('platform_telegram_binding_private')]


@pytest.mark.asyncio
async def test_detached_context_cannot_invoke_binding(handler):
    c = handler
    event = update(c.bot)
    await routes.binding_start(event.message, c.bot, CommandObject(command='start', args='bind_'+'A'*43))
    assert c.entries == 0 and c.telegram.sent == [] and c.telegram.deleted == []
    c.consume.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_failure_does_not_expose_input_or_prevent_binding(handler, caplog):
    c = handler
    c.telegram.fail_delete = True
    await dispatch(c)
    c.consume.assert_awaited_once()
    assert c.telegram.sent == [t('platform_telegram_binding_success')]
    assert 'private-message-body' not in caplog.text


@pytest.mark.asyncio
async def test_bot_mention_command_uses_same_private_binding_path(handler):
    c = handler
    await dispatch(c, text='/start@ConfiguredBot bind_'+'A'*43)
    assert c.registered == 0 and c.entries == 1
    c.consume.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_bot_session_and_user_start_service_are_not_called_for_binding(handler, monkeypatch):
    from services.user_start import UserStartService
    forbidden = AsyncMock(side_effect=AssertionError('No registration during binding'))
    monkeypatch.setattr(UserStartService, 'register_user', forbidden)
    await dispatch(handler)
    forbidden.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatched_binding_commits_shared_local_workflow_once_and_replays(handler, workflow, monkeypatch):
    from services.platform_telegram_binding_bot import consume_platform_telegram_binding_bot_start as real_consume
    c, domain = handler, workflow
    @asynccontextmanager
    async def get_session():
        if not domain.env.session.in_transaction():
            domain.env.session.begin()
        yield domain.env.bridge
    async def consume(**kwargs):
        return await real_consume(**kwargs, now_provider=lambda: domain.now)
    monkeypatch.setattr(routes, 'get_session', get_session)
    monkeypatch.setattr(routes, 'consume_platform_telegram_binding_bot_start', consume)
    for _ in range(2):
        await dispatch(c, token=domain.env.material.token, as_of=domain.now)
        assert c.registered == 0
    assert counts(domain) == (1, 1, 1, 1) and domain.commits == 2
    assert c.telegram.sent == [t('platform_telegram_binding_success')]*2


@pytest.mark.asyncio
async def test_handler_reports_failure_after_real_audit_rollback_without_registering(handler, workflow, monkeypatch):
    from services.platform_telegram_binding_bot import consume_platform_telegram_binding_bot_start as real_consume
    from database.repositories.event import EventRepository
    c, domain = handler, workflow
    @asynccontextmanager
    async def get_session():
        yield domain.env.bridge
    async def consume(**kwargs):
        return await real_consume(**kwargs, now_provider=lambda: domain.now)
    original = EventRepository.create_event
    async def fail_after_flush(self, **kwargs):
        await original(self, **kwargs)
        raise RuntimeError('mandatory audit failed')
    monkeypatch.setattr(routes, 'get_session', get_session)
    monkeypatch.setattr(routes, 'consume_platform_telegram_binding_bot_start', consume)
    monkeypatch.setattr(EventRepository, 'create_event', fail_after_flush)
    await dispatch(c, token=domain.env.material.token, as_of=domain.now)
    assert c.registered == 0 and domain.commits == 0 and counts(domain) == (1,0,0,0)
    assert c.telegram.sent == [t('platform_telegram_binding_unavailable')]


@pytest.mark.asyncio
async def test_reply_uses_telegram_interface_language(handler):
    c = handler
    await dispatch(c, from_user=dict(id=123456789, is_bot=False, first_name='Test', language_code='uk'))
    assert c.telegram.sent == [t('platform_telegram_binding_success','uk')]


@pytest.mark.asyncio
async def test_telegram_limiter_uses_shared_public_budget_and_trusted_sender_keys():
    calls = []
    class Store:
        async def increment(self, **kwargs):
            calls.append(kwargs)
            return PUBLIC_API_RATE_LIMIT if len(calls) == 1 else PUBLIC_API_RATE_LIMIT + 1
    service = ApiRequestRateLimitService(store=Store())
    assert await service.ensure_telegram_binding_allowed(bot_id=123, telegram_user_id=456) == PUBLIC_API_RATE_LIMIT
    with pytest.raises(ApiRequestRateLimitExceededError):
        await service.ensure_telegram_binding_allowed(bot_id=123, telegram_user_id=456)
    assert calls == [dict(key='api-rate-limit:telegram-binding:bot:123:telegram-user:456',
        window_seconds=API_RATE_LIMIT_WINDOW_SECONDS)]*2


@pytest.mark.asyncio
@pytest.mark.parametrize('bot_id,user_id', [(0,1),(-1,1),(True,1),('1',1),(1,0),(1,-1),(1,True),(1,'1')])
async def test_limiter_rejects_untyped_or_invalid_identities_before_store(bot_id, user_id):
    store = SimpleNamespace(increment=AsyncMock())
    with pytest.raises(ValueError):
        await ApiRequestRateLimitService(store=store).ensure_telegram_binding_allowed(bot_id=bot_id, telegram_user_id=user_id)
    store.increment.assert_not_awaited()


@pytest.mark.asyncio
async def test_binding_rate_limit_separates_bots_and_senders_and_resets_window():
    from services.api_rate_limits import InMemoryApiRequestRateLimitStore
    now = [0.0]
    service = ApiRequestRateLimitService(store=InMemoryApiRequestRateLimitStore(clock=lambda:now[0]))
    for _ in range(PUBLIC_API_RATE_LIMIT):
        await service.ensure_telegram_binding_allowed(bot_id=1,telegram_user_id=2)
    with pytest.raises(ApiRequestRateLimitExceededError):
        await service.ensure_telegram_binding_allowed(bot_id=1,telegram_user_id=2)
    assert await service.ensure_telegram_binding_allowed(bot_id=1,telegram_user_id=3) == 1
    assert await service.ensure_telegram_binding_allowed(bot_id=3,telegram_user_id=2) == 1
    now[0] += API_RATE_LIMIT_WINDOW_SECONDS
    assert await service.ensure_telegram_binding_allowed(bot_id=1,telegram_user_id=2) == 1


@pytest.mark.parametrize('language', ['ru','en','pt','uk','pl','de','nl'])
def test_binding_responses_use_existing_translation_registry(language):
    keys = [key for key in translations if key.startswith('platform_telegram_binding_')]
    assert len(keys) == 6
    for key in keys:
        assert isinstance(translations[key][language], str) and translations[key][language]
        assert t(key, language) != key


def test_binding_router_is_registered_before_ordinary_start_in_bot_entrypoint():
    tree = ast.parse(Path('bot.py').read_text(encoding='utf-8-sig'))
    main, = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'main']
    routers = [node.value.args[0].id for node in main.body if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == 'include_router' and node.value.args and isinstance(node.value.args[0], ast.Name)]
    assert routers[:2] == ['platform_telegram_binding_router','start_router']
