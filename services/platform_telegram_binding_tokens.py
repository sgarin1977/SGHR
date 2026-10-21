"""Shared Platform Telegram binding token primitives; no storage or transport."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets


PLATFORM_TELEGRAM_BINDING_TOKEN_TTL = timedelta(minutes=15)
PLATFORM_TELEGRAM_BINDING_TOKEN_ENTROPY_BYTES = 32


class PlatformTelegramBindingTokenValidationError(ValueError):
    pass


class PlatformTelegramBindingTokenDeniedError(Exception):
    pass


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PlatformTelegramBindingTokenValidationError('Telegram binding requires an aware server timestamp.')
    return value.astimezone(timezone.utc)


def hash_platform_telegram_binding_token(token):
    # Token is opaque: code and deep-link presentations must not strip, fold or
    # otherwise transform the underlying secret. Transport formatting is separate.
    if not isinstance(token, str) or not token:
        raise PlatformTelegramBindingTokenValidationError('Telegram binding token is invalid.')
    try:
        return hashlib.sha256(token.encode('utf-8')).hexdigest()
    except UnicodeError:
        raise PlatformTelegramBindingTokenValidationError('Telegram binding token is invalid.') from None


def verify_platform_telegram_binding_token(*, token, expected_hash):
    if (not isinstance(expected_hash, str) or len(expected_hash) != 64
        or any(char not in '0123456789abcdef' for char in expected_hash)):
        return False
    try:
        calculated = hash_platform_telegram_binding_token(token)
    except PlatformTelegramBindingTokenValidationError:
        return False
    return hmac.compare_digest(calculated, expected_hash)


@dataclass(frozen=True, eq=False)
class PlatformTelegramBindingTokenMaterial:
    """Transient issuance result; repositories must persist only token_hash.

    Raw token is intentionally available for the recipient-facing code/deep-link
    response, but is excluded from repr and must not be serialized into audit.
    """
    token: str = field(repr=False)
    token_hash: str = field(repr=False)
    issued_at: datetime
    expires_at: datetime

    def __post_init__(self):
        try:
            issued, expires = _utc(self.issued_at), _utc(self.expires_at)
            if (expires != issued + PLATFORM_TELEGRAM_BINDING_TOKEN_TTL
                or not verify_platform_telegram_binding_token(token=self.token, expected_hash=self.token_hash)):
                raise PlatformTelegramBindingTokenValidationError('Telegram binding token material is invalid.')
        except (ValueError, TypeError, OverflowError):
            raise PlatformTelegramBindingTokenValidationError('Telegram binding token material is invalid.') from None
        object.__setattr__(self, 'issued_at', issued)
        object.__setattr__(self, 'expires_at', expires)


def issue_platform_telegram_binding_token(*, as_of):
    issued_at = _utc(as_of)
    try:
        expires_at = issued_at + PLATFORM_TELEGRAM_BINDING_TOKEN_TTL
    except OverflowError:
        raise PlatformTelegramBindingTokenValidationError('Telegram binding expiry is invalid.') from None
    token = secrets.token_urlsafe(PLATFORM_TELEGRAM_BINDING_TOKEN_ENTROPY_BYTES)
    return PlatformTelegramBindingTokenMaterial(token=token, token_hash=hash_platform_telegram_binding_token(token),
        issued_at=issued_at, expires_at=expires_at)


def require_live_platform_telegram_binding_token(*, token, expected_hash, issued_at, expires_at,
    consumed_at, revoked_at, as_of):
    """Validate a locked token snapshot; caller must atomically consume in DB.

    State timestamps are required arguments: omitting a consumed/revoked state
    must not silently make a partially loaded record look usable. The helper
    never consumes, revokes, resends or mutates a record by itself.
    """
    denied = 'Telegram binding token is not available.'
    try:
        issued, expires, now = _utc(issued_at), _utc(expires_at), _utc(as_of)
        if (consumed_at is not None or revoked_at is not None
            or expires != issued + PLATFORM_TELEGRAM_BINDING_TOKEN_TTL
            or now < issued or now >= expires
            or not verify_platform_telegram_binding_token(token=token, expected_hash=expected_hash)):
            raise PlatformTelegramBindingTokenDeniedError(denied)
    except (PlatformTelegramBindingTokenValidationError, ValueError, TypeError, OverflowError):
        raise PlatformTelegramBindingTokenDeniedError(denied) from None
