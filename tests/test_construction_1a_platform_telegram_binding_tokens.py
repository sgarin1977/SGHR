from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import hashlib
from unittest.mock import patch

import pytest

from services.platform_telegram_binding_tokens import (
    PLATFORM_TELEGRAM_BINDING_TOKEN_TTL as TTL, PLATFORM_TELEGRAM_BINDING_TOKEN_ENTROPY_BYTES,
    PlatformTelegramBindingTokenMaterial, PlatformTelegramBindingTokenValidationError as Invalid,
    PlatformTelegramBindingTokenDeniedError as Denied, hash_platform_telegram_binding_token as digest,
    verify_platform_telegram_binding_token as verify, issue_platform_telegram_binding_token as issue,
    require_live_platform_telegram_binding_token as require_live,
)


NOW = datetime(2026, 10, 14, 12, 34, 56, 123456, tzinfo=timezone.utc)
SECRET = 'Opaque_secret-used-by-both-code-and-link_12345'


def material():
    with patch('services.platform_telegram_binding_tokens.secrets.token_urlsafe', return_value=SECRET) as generate:
        value = issue(as_of=NOW)
    generate.assert_called_once_with(32)
    return value


def options(value=None):
    value = value or material()
    return dict(token=value.token, expected_hash=value.token_hash, issued_at=value.issued_at,
        expires_at=value.expires_at, consumed_at=None, revoked_at=None, as_of=NOW)


@pytest.mark.parametrize('offset', [-7, 0, 3])
def test_issuance_uses_approved_ttl_entropy_and_exact_utc_microseconds(offset):
    with patch('services.platform_telegram_binding_tokens.secrets.token_urlsafe', return_value=SECRET):
        value = issue(as_of=NOW.astimezone(timezone(timedelta(hours=offset))))
    assert TTL == timedelta(minutes=15) and PLATFORM_TELEGRAM_BINDING_TOKEN_ENTROPY_BYTES == 32
    assert value.issued_at == NOW and value.issued_at.tzinfo is timezone.utc
    assert value.expires_at == NOW+TTL and value.expires_at.microsecond == 123456
    assert value.token_hash == hashlib.sha256(SECRET.encode()).hexdigest()
    assert value.token == SECRET


def test_generated_token_is_url_safe_and_one_material_supports_both_presentations():
    value = issue(as_of=NOW)
    assert len(value.token) == 43 and all(char.isalnum() or char in '-_' for char in value.token)
    assert len(value.token_hash) == 64 and value.token_hash != value.token
    code_secret = value.token
    deep_link_secret = value.token
    assert verify(token=code_secret, expected_hash=value.token_hash)
    assert verify(token=deep_link_secret, expected_hash=value.token_hash)


def test_secrets_are_not_in_repr_and_material_is_frozen():
    value = material()
    assert SECRET not in repr(value) and value.token_hash not in repr(value)
    assert 'token=' not in repr(value) and 'token_hash=' not in repr(value)
    with pytest.raises(FrozenInstanceError): value.expires_at = NOW+timedelta(days=1)


@pytest.mark.parametrize('token', [None, '', 123, True, {}, [], b'bytes', '\ud800'])
def test_invalid_token_input_is_generic_without_echo(token):
    with pytest.raises(Invalid, match=r'^Telegram binding token is invalid\.$'): digest(token)
    assert verify(token=token, expected_hash='0'*64) is False


@pytest.mark.parametrize('hash_value', [None, '', '0'*63, '0'*65, 'G'*64, 'A'*64, {}, [], b'0'*64])
def test_invalid_stored_hash_is_not_accepted(hash_value):
    assert verify(token=SECRET, expected_hash=hash_value) is False


def test_opaque_secret_is_not_trimmed_or_case_normalized_and_comparison_is_constant_time():
    expected = digest(SECRET)
    for wrong in (SECRET.lower(), SECRET+' ', ' '+SECRET, SECRET+'x'):
        assert not verify(token=wrong, expected_hash=expected)
    with patch('services.platform_telegram_binding_tokens.hmac.compare_digest', wraps=__import__('hmac').compare_digest) as compare:
        assert verify(token=SECRET, expected_hash=expected)
    compare.assert_called_once_with(expected, expected)


@pytest.mark.parametrize('delta', [timedelta(0), timedelta(minutes=14), TTL-timedelta(microseconds=1)])
def test_live_window_includes_issue_time_and_instant_before_expiry(delta):
    values = options(); values['as_of'] = NOW+delta
    assert require_live(**values) is None


@pytest.mark.parametrize('delta', [-timedelta(microseconds=1), TTL, TTL+timedelta(microseconds=1), timedelta(days=1)])
def test_future_issuance_and_expiry_boundary_are_denied(delta):
    values = options(); values['as_of'] = NOW+delta
    with pytest.raises(Denied, match=r'^Telegram binding token is not available\.$'): require_live(**values)


@pytest.mark.parametrize('field', ['consumed_at', 'revoked_at'])
@pytest.mark.parametrize('state', [NOW, NOW-timedelta(minutes=1), False, '', 'malformed'])
def test_any_used_or_revoked_state_fails_closed(field, state):
    values = options(); values[field] = state
    with pytest.raises(Denied): require_live(**values)


@pytest.mark.parametrize('field', ['issued_at', 'expires_at', 'as_of'])
@pytest.mark.parametrize('value', [None, 'private-invalid-time', datetime(2026, 10, 14), True])
def test_invalid_or_naive_snapshot_time_is_denied_without_private_detail(field, value):
    values = options(); values[field] = value
    with pytest.raises(Denied) as caught: require_live(**values)
    assert str(caught.value) == 'Telegram binding token is not available.'


@pytest.mark.parametrize('field,value', [('expires_at', NOW+timedelta(minutes=14)), ('expires_at', NOW+timedelta(minutes=16)),
    ('token', 'wrong-secret'), ('expected_hash', 'not-a-hash')])
def test_unsupported_ttl_wrong_secret_or_bad_hash_is_denied(field, value):
    values = options(); values[field] = value
    with pytest.raises(Denied): require_live(**values)


def test_snapshot_check_does_not_consume_or_mutate_and_does_not_claim_atomicity():
    value = material(); values = options(value); original = dict(values)
    require_live(**values); require_live(**values)
    assert values == original and value.token == SECRET
    values['consumed_at'] = NOW
    with pytest.raises(Denied): require_live(**values)
    for field in ('consumed_at', 'revoked_at'):
        missing = dict(original); del missing[field]
        with pytest.raises(TypeError): require_live(**missing)


@pytest.mark.parametrize('changes', [dict(token='wrong'), dict(token_hash='0'*64),
    dict(issued_at=NOW.replace(tzinfo=None)), dict(expires_at=NOW+timedelta(minutes=16))])
def test_invalid_issuance_material_is_rejected(changes):
    with pytest.raises(Invalid): replace(material(), **changes)


def test_maximum_datetime_overflow_is_controlled():
    with pytest.raises(Invalid): issue(as_of=datetime.max.replace(tzinfo=timezone.utc))
    values = options(); values['issued_at'] = datetime.max.replace(tzinfo=timezone.utc)
    with pytest.raises(Denied): require_live(**values)
