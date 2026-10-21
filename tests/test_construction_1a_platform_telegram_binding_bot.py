from datetime import datetime, timedelta, timezone
import inspect
import json
from uuid import uuid4

from aiogram import Bot
from aiogram.types import Update
import pytest
from sqlalchemy import select

from api.telegram_auth import TelegramAuthenticationError
from database.models import ApiIdempotencyRecord, EventLog, PlatformTelegramBindingToken as Token, UserAccount
from database.repositories.event import EventRepository
from database.repositories.api_idempotency import ApiIdempotencyRepository
from database.repositories.platform_telegram_accounts import PlatformTelegramAccountAlreadyLinkedError
from database.repositories.platform_telegram_binding_tokens import PlatformTelegramBindingTokenRepository
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.platform_telegram_binding_bot import consume_platform_telegram_binding_bot_start as consume_start
from services.platform_telegram_binding_bot_proof import TelegramBotBindingUpdateVerifier as Verifier
from services.platform_telegram_binding_tokens import issue_platform_telegram_binding_token, PlatformTelegramBindingTokenDeniedError
from test_construction_1a_platform_telegram_binding_workflow import workflow, counts, KEY
from test_construction_1a_platform_telegram_binding_consume import environment, NOW, BOT_TOKEN


@pytest.fixture
def bot():
    # Constructing Bot does not call Telegram; these tests never invoke it.
    return Bot(token=BOT_TOKEN)


_DEFAULT_TEXT = object()


def update(bot, *, token='A' * 43, update_id=50, telegram_id=123456789,
    as_of=NOW + timedelta(minutes=1), text=_DEFAULT_TEXT, **changes):
    raw = dict(update_id=update_id, message=dict(message_id=10, date=int(as_of.timestamp()),
        chat=dict(id=telegram_id, type='private'),
        from_user=dict(id=telegram_id, is_bot=False, first_name='Test Person', username='test_name'),
        text='/start bind_' + token if text is _DEFAULT_TEXT else text))
    raw['message'].update(changes)
    return Update.model_validate(raw, context={'bot': bot})


async def consume(c, bot, event=None, **kwargs):
    if not c.env.session.in_transaction():
        c.env.session.begin()
    event = event if event is not None else update(bot, token=c.env.material.token, as_of=c.now)
    return await consume_start(session=c.env.bridge, bot=bot, update=event,
        encryption_key=KEY, now_provider=lambda: c.now, **kwargs)


def test_trusted_bot_identity_is_snapshotted_and_proof_is_opaque(bot):
    event = update(bot)
    verifier = Verifier(bot=bot, update=event)
    # Even an internal bypass of aiogram's frozen models must not rewrite
    # identity or command text already captured by the verifier.
    object.__setattr__(event.message.from_user, 'id', 999)
    object.__setattr__(event.message, 'text', '/start bind_' + 'B' * 43)
    identity = verifier.verify(verifier.proof, now=NOW + timedelta(minutes=2))
    assert identity.platform_user_id == 123456789
    assert verifier.command_text == '/start bind_' + 'A' * 43
    assert verifier.idempotency_key == 'telegram-bot-binding:123456:50'
    assert not isinstance(verifier.proof, str)
    assert 'A' * 43 not in repr(verifier) + repr(verifier.proof)


@pytest.mark.parametrize('proof', [None, '', 'init_data', {}, {'user_id': 123456789}, object()])
def test_arbitrary_client_data_is_not_a_bot_identity_proof(bot, proof):
    verifier = Verifier(bot=bot, update=update(bot))
    with pytest.raises(TelegramAuthenticationError):
        verifier.verify(proof, now=NOW + timedelta(minutes=1))


def test_proof_cannot_be_reused_with_a_different_update_verifier(bot):
    first = Verifier(bot=bot, update=update(bot))
    second = Verifier(bot=bot, update=update(bot, update_id=51))
    with pytest.raises(TelegramAuthenticationError):
        second.verify(first.proof, now=NOW + timedelta(minutes=1))


@pytest.mark.parametrize('delta', [timedelta(seconds=-31), timedelta(minutes=-5)])
def test_bot_update_from_future_is_denied(bot, delta):
    verifier = Verifier(bot=bot, update=update(bot))
    with pytest.raises(TelegramAuthenticationError):
        verifier.verify(verifier.proof, now=NOW + timedelta(minutes=1) + delta)


@pytest.mark.parametrize('now', [None, 'now', datetime(2026, 10, 14)])
def test_invalid_explicit_bot_clock_is_denied(bot, now):
    verifier = Verifier(bot=bot, update=update(bot))
    if now is None:
        # None means current server time; use another invalid explicit value.
        now = 0
    with pytest.raises(TelegramAuthenticationError):
        verifier.verify(verifier.proof, now=now)


@pytest.mark.parametrize('change', [
    {'chat': {'id': -10, 'type': 'group', 'title': 'Test'}},
    {'chat': {'id': 999, 'type': 'private'}},
    {'from_user': {'id': 123456789, 'is_bot': True, 'first_name': 'Bot'}},
    {'from_user': None}, {'text': None}, {'message_id': 0},
    {'edit_date': int(NOW.timestamp())}, {'business_connection_id': 'business'},
    {'via_bot': {'id': 123, 'is_bot': True, 'first_name': 'Bot'}},
    {'sender_chat': {'id': -10, 'type': 'channel', 'title': 'Channel'}},
    {'forward_origin': {'type': 'hidden_user', 'date': int(NOW.timestamp()), 'sender_user_name': 'Original'}},
])
def test_non_private_or_indirect_messages_cannot_supply_binding_identity(bot, change):
    with pytest.raises(TelegramAuthenticationError):
        Verifier(bot=bot, update=update(bot, **change))


def test_detached_wrong_bot_and_non_message_updates_are_denied(bot):
    event = update(bot)
    detached = Update.model_validate(event.model_dump())
    other = Bot(token='987654:other-local-test-secret')
    for event_bot, value in [(bot, detached), (other, event), (bot, event.model_dump()),
        (bot, Update.model_validate({'update_id': 1}, context={'bot': bot}))]:
        with pytest.raises(TelegramAuthenticationError):
            Verifier(bot=event_bot, update=value)


def test_idempotency_key_uses_stable_update_and_bot_ids(bot):
    a = Verifier(bot=bot, update=update(bot))
    b = Verifier(bot=bot, update=update(bot))
    other = Bot(token='987654:other-local-test-secret')
    d = Verifier(bot=other, update=update(other))
    assert a.idempotency_key == b.idempotency_key
    assert a.idempotency_key != d.idempotency_key
    assert a.idempotency_key != Verifier(bot=bot, update=update(bot, update_id=51)).idempotency_key


def test_bot_adapter_accepts_no_caller_platform_user_or_tenant():
    assert set(inspect.signature(consume_start).parameters) == {
        'session', 'bot', 'update', 'encryption_key', 'now_provider'}


@pytest.mark.asyncio
async def test_bot_consume_uses_existing_platform_user_and_atomic_shared_workflow(workflow, bot):
    c = workflow
    result = await consume(c, bot)
    assert result.user_id == c.env.owner and result.tenant_id == c.env.tenant
    assert counts(c) == (1, 1, 1, 1) and c.commits == 1
    assert len(c.env.session.execute(select(c.env.users.c.id)).all()) == 3
    account = c.env.session.execute(select(UserAccount)).scalar_one()
    audit = c.env.session.execute(select(EventLog)).scalar_one()
    record = c.env.session.execute(select(ApiIdempotencyRecord)).scalar_one()
    assert account.user_id == c.env.owner and account.platform_user_id == '123456789'
    assert audit.event_type == 'platform_telegram_binding_consumed'
    assert audit.platform == 'telegram'
    assert audit.user_id == record.principal_id == c.env.owner
    assert audit.tenant_id == record.tenant_id == c.env.tenant
    snapshot = c.workflow.idempotency.response_codec.decrypt(record.response_ciphertext)
    assert c.env.material.token not in json.dumps(audit.payload) + json.dumps(snapshot)
    assert c.env.material.token_hash not in json.dumps(audit.payload) + json.dumps(snapshot)
    assert 'init_data' not in json.dumps(snapshot)
    c.identity.require_actor.assert_not_awaited()


@pytest.mark.asyncio
async def test_retried_same_update_replays_original_result_without_second_account_or_audit(workflow, bot):
    c = workflow
    event = update(bot, token=c.env.material.token, as_of=c.now)
    first = await consume(c, bot, event)
    before = counts(c)
    c.now += timedelta(minutes=2)
    second = await consume(c, bot, Update.model_validate(event.model_dump(), context={'bot': bot}))
    assert second == first and counts(c) == before and c.commits == 2


@pytest.mark.asyncio
async def test_delayed_update_redelivery_replays_success_after_token_ttl(workflow, bot):
    c = workflow
    event = update(bot, token=c.env.material.token, as_of=c.now)
    first = await consume(c, bot, event)
    before = counts(c)
    c.now += timedelta(hours=1)
    repeated = await consume(c, bot, Update.model_validate(event.model_dump(), context={'bot': bot}))
    assert repeated == first and counts(c) == before and c.commits == 2


@pytest.mark.asyncio
async def test_delayed_first_delivery_cannot_consume_expired_token(workflow, bot):
    c = workflow
    event = update(bot, token=c.env.material.token, as_of=c.now)
    c.now += timedelta(minutes=16)
    with pytest.raises(PlatformTelegramBindingTokenDeniedError):
        await consume(c, bot, event)
    assert counts(c) == (1, 0, 0, 0) and c.commits == 0


@pytest.mark.asyncio
async def test_new_update_cannot_consume_an_already_used_token(workflow, bot):
    c = workflow
    await consume(c, bot)
    before = counts(c)
    with pytest.raises(PlatformTelegramBindingTokenDeniedError):
        await consume(c, bot, update(bot, token=c.env.material.token, update_id=51, as_of=c.now))
    assert counts(c) == before and c.commits == 1


@pytest.mark.asyncio
async def test_same_update_key_with_different_live_code_conflicts(workflow, bot):
    c = workflow
    await consume(c, bot)
    material = issue_platform_telegram_binding_token(as_of=NOW)
    c.env.session.add(Token(id=uuid4(), user_id=c.env.owner, tenant_id=c.env.tenant,
        initiated_by_user_id=c.env.owner, token_hash=material.token_hash,
        issued_at=NOW, expires_at=material.expires_at, updated_at=NOW, row_version=1))
    c.env.session.commit()
    before = counts(c)
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await consume(c, bot, update(bot, token=material.token, as_of=c.now))
    assert counts(c) == before and c.commits == 1


@pytest.mark.asyncio
async def test_existing_foreign_telegram_account_rolls_back_token_and_reservation(workflow, bot):
    c = workflow
    c.env.session.add(UserAccount(id=uuid4(), user_id=c.env.other, platform='telegram',
        platform_user_id='123456789', raw_profile={}))
    c.env.session.commit()
    with pytest.raises(PlatformTelegramAccountAlreadyLinkedError):
        await consume(c, bot)
    assert counts(c) == (1, 1, 0, 0) and c.commits == 0
    assert c.env.session.get(Token, c.env.binding_id).consumed_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize('point', ['audit', 'idempotency_completion'])
async def test_failure_after_real_local_writes_rolls_back_all_binding_changes(workflow, bot, monkeypatch, point):
    c = workflow
    target, method = (EventRepository, 'create_event') if point == 'audit' else (ApiIdempotencyRepository, 'complete_record')
    original = getattr(target, method)
    async def fail_after_flush(self, **kwargs):
        await original(self, **kwargs)
        assert c.env.session.execute(select(UserAccount)).scalar_one().user_id == c.env.owner
        assert c.env.session.execute(select(EventLog)).scalar_one().entity_id == c.env.binding_id
        raise RuntimeError('forced mandatory write failure')
    monkeypatch.setattr(target, method, fail_after_flush)
    with pytest.raises(RuntimeError, match='forced mandatory write failure'):
        await consume(c, bot)
    assert counts(c) == (1, 0, 0, 0) and c.commits == 0
    assert c.env.session.get(Token, c.env.binding_id).consumed_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['/start', '/start other', '/start bind_invalid', '/start bind_' + 'A'*43 + ' extra'])
async def test_invalid_binding_commands_create_no_user_account_or_audit(workflow, bot, text):
    c = workflow
    with pytest.raises((ValueError, TelegramAuthenticationError)):
        await consume(c, bot, update(bot, text=text, as_of=c.now))
    assert counts(c) == (1, 0, 0, 0) and c.commits == 0
    assert c.env.bridge.statements == []


@pytest.mark.asyncio
async def test_binding_token_ttl_is_rechecked_after_blocking_locks(workflow, bot, monkeypatch):
    c = workflow
    original = PlatformTelegramBindingTokenRepository.get_token_for_update
    async def delayed(self, **kwargs):
        result = await original(self, **kwargs)
        c.now += timedelta(minutes=16)
        return result
    monkeypatch.setattr(PlatformTelegramBindingTokenRepository, 'get_token_for_update', delayed)
    with pytest.raises(PlatformTelegramBindingTokenDeniedError):
        await consume(c, bot)
    assert counts(c) == (1, 0, 0, 0) and c.commits == 0


def test_signed_init_data_service_rejects_opaque_bot_proof(workflow, bot):
    c = workflow
    verifier = Verifier(bot=bot, update=update(bot))
    with pytest.raises(PlatformTelegramBindingTokenDeniedError):
        c.workflow.consume._identity(verifier.proof, c.now)
