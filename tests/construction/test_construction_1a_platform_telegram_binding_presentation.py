import base64
from dataclasses import replace
from datetime import timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

from api.telegram_auth import TelegramAuthenticationError
from services.platform_telegram_binding_presentation import (
    PlatformTelegramBindingPresentationError as Invalid,
    present_platform_telegram_binding as present,
    read_platform_telegram_binding_code as read_code,
    read_platform_telegram_binding_start_parameter as read_start,
)
from services.platform_telegram_binding_workflow import PlatformTelegramBindingInitiationResult
from services.platform_telegram_binding_tokens import (
    PlatformTelegramBindingTokenDeniedError as Denied, issue_platform_telegram_binding_token,
)
from test_construction_1a_platform_telegram_binding_consume import environment, NOW
from test_construction_1a_platform_telegram_binding_workflow import workflow, initiate, consume, counts
from test_construction_1a_platform_telegram_binding_lifecycle_workflow import lifecycle_workflow, mutate


@pytest.fixture
def receipt():
    material = issue_platform_telegram_binding_token(as_of=NOW)
    return PlatformTelegramBindingInitiationResult(uuid4(), uuid4(), uuid4(), material.issued_at, material.expires_at, 1, material.token)


def show(result, **changes):
    options = dict(bot_username='SGHR_test_bot', expected_user_id=result.user_id, expected_tenant_id=result.tenant_id)
    options.update(changes)
    return present(result, **options)


def test_code_and_private_chat_start_link_contain_identical_underlying_secret(receipt):
    result = show(receipt)
    url = urlsplit(result.deep_link)
    assert (url.scheme, url.netloc, url.path) == ('https', 't.me', '/SGHR_test_bot')
    parameter, = parse_qs(url.query)['start']
    assert len(parameter) == 48 and len(parameter) <= 64
    assert read_start(parameter) == read_code(result.code) == receipt.token
    assert result.issued_at == receipt.issued_at and result.expires_at == receipt.expires_at
    assert result.binding_id == receipt.binding_id and result.user_id == receipt.user_id and result.tenant_id == receipt.tenant_id
    assert result.code not in repr(result) and result.deep_link not in repr(result)
    assert not url.fragment and not url.username and not url.password


@pytest.mark.parametrize('bot', [None, '', '@SGHR_test_bot', 'https://t.me/SGHR_test_bot',
    'private.invalid/bot', 'bot?start=evil', 'bot#fragment', 'bot:password', 'бот_name',
    ' bot_name ', 'b'*33, 'tiny', 'bot\nname'])
def test_server_target_must_be_plain_configured_bot_username_without_url_parts(receipt, bot):
    with pytest.raises(Invalid) as error: show(receipt, bot_username=bot)
    assert str(error.value) == 'Telegram binding presentation is invalid.'
    assert receipt.token not in str(error.value)


@pytest.mark.parametrize('fault', ['wrong_type', 'tenant', 'user', 'binding', 'version', 'bool_version', 'naive', 'expiry', 'token'])
def test_invalid_or_cross_context_receipt_is_rejected(receipt, fault):
    result = receipt
    if fault == 'wrong_type': result = SimpleNamespace(**vars(receipt))
    if fault == 'tenant': result = replace(receipt, tenant_id=uuid4())
    if fault == 'user': result = replace(receipt, user_id=uuid4())
    if fault == 'binding': result = replace(receipt, binding_id=str(receipt.binding_id))
    if fault == 'version': result = replace(receipt, row_version=2)
    if fault == 'bool_version': result = replace(receipt, row_version=True)
    if fault == 'naive': result = replace(receipt, issued_at=receipt.issued_at.replace(tzinfo=None))
    if fault == 'expiry': result = replace(receipt, expires_at=receipt.expires_at+timedelta(seconds=1))
    if fault == 'token': result = replace(receipt, token='private-invalid-token')
    with pytest.raises(Invalid):
        present(result, bot_username='SGHR_test_bot', expected_user_id=receipt.user_id, expected_tenant_id=receipt.tenant_id)


@pytest.mark.parametrize('code', [None, '', ' ', 'short', 'x'*44, 'x'*42, 'x'*43+'=',
    ' '*43, 'a'*42+'!', 'a'*42+'é', '\n'+'A'*43, 'A'*43+'\n'])
def test_code_is_opaque_bounded_canonical_base64url_without_normalization(code):
    with pytest.raises(Invalid) as error: read_code(code)
    assert str(error.value) == 'Telegram binding presentation is invalid.'


def test_noncanonical_base64_trailing_bits_cannot_alias_the_same_code():
    canonical = base64.urlsafe_b64encode(b'\x00'*32).decode().rstrip('=')
    assert read_code(canonical) == canonical
    alternate = canonical[:-1]+'B'
    with pytest.raises(Invalid): read_code(alternate)


@pytest.mark.parametrize('argument', [None, '', 'bind_', '/start bind_'+'A'*43, 'https://t.me/bot?start=bind_'+'A'*43,
    'invite_'+'A'*43, 'BIND_'+'A'*43, 'bind_'+'A'*44, 'bind_'+'A'*43+' ', 'bind_'+'a'*60])
def test_start_argument_does_not_accept_full_urls_commands_or_other_flow_prefixes(argument):
    with pytest.raises(Invalid) as error: read_start(argument)
    assert str(error.value) == 'Telegram binding presentation is invalid.'


def test_utc_normalization_does_not_change_code_link_or_ttl(receipt):
    offset = timezone(timedelta(hours=3))
    shifted = replace(receipt, issued_at=receipt.issued_at.astimezone(offset), expires_at=receipt.expires_at.astimezone(offset))
    assert show(shifted) == show(receipt)


@pytest.mark.asyncio
async def test_workflow_replay_presents_same_code_link_and_original_expiry(workflow):
    c = workflow
    first = show(await initiate(c))
    before = counts(c)
    c.now += timedelta(minutes=20)
    repeated = show(await initiate(c))
    assert repeated == first and repeated.expires_at < c.now and counts(c) == before


@pytest.mark.asyncio
async def test_code_or_link_enters_same_existing_token_verified_consume_workflow(workflow):
    c = workflow
    display = show(await initiate(c))
    parameter, = parse_qs(urlsplit(display.deep_link).query)['start']
    first = await consume(c, read_start(parameter))
    before = counts(c)
    repeated = await consume(c, read_code(display.code))
    assert first == repeated and counts(c) == before
    assert repeated.user_id == c.actor.user_id and repeated.tenant_id == c.actor.tenant_id


@pytest.mark.asyncio
async def test_decoded_link_alone_cannot_bypass_verified_telegram_identity(workflow):
    c = workflow
    display = show(await initiate(c))
    before = counts(c)
    with pytest.raises(TelegramAuthenticationError):
        await c.workflow.consume_binding(token=display.code, telegram_init_data='forged-private-proof', idempotency_key='consume')
    assert counts(c) == before


@pytest.mark.asyncio
async def test_resend_updates_code_and_link_while_old_issued_code_stays_revoked(lifecycle_workflow):
    c = lifecycle_workflow
    original_code = c.env.material.token
    replaced = show(await mutate(c))
    assert replaced.code != original_code
    before = counts(c)
    with pytest.raises(Denied): await consume(c, original_code)
    assert counts(c) == before
    parameter, = parse_qs(urlsplit(replaced.deep_link).query)['start']
    consumed = await consume(c, read_start(parameter))
    assert consumed.binding_id == replaced.binding_id
