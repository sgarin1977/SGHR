"""Transient code/deep-link presentation for the approved binding workflow.

No route or bot handler is registered here. A decoded code is only the binding
secret; consume must still verify trusted Telegram identity, TTL and stored
server context through the existing workflow. Never persist this presentation
in audit/idempotency snapshots or log its secret-bearing fields.
"""
import base64
import binascii
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from uuid import UUID

from services.platform_telegram_binding_tokens import PLATFORM_TELEGRAM_BINDING_TOKEN_TTL
from services.platform_telegram_binding_workflow import PlatformTelegramBindingInitiationResult
from services.platform_telegram_binding_lifecycle import PlatformTelegramBindingResendResult


_START_PREFIX = 'bind_'
_CODE_LENGTH = 43
_MAX_START_LENGTH = 64


class PlatformTelegramBindingPresentationError(ValueError):
    def __init__(self):
        super().__init__('Telegram binding presentation is invalid.')


def read_platform_telegram_binding_code(value):
    """Validate an opaque code without trimming, case folding or rewriting it."""
    if not isinstance(value, str) or re.fullmatch(r'[A-Za-z0-9_-]{43}', value) is None:
        raise PlatformTelegramBindingPresentationError()
    try:
        raw = base64.b64decode((value+'=').encode('ascii'), altchars=b'-_', validate=True)
    except (ValueError, UnicodeError, binascii.Error):
        raise PlatformTelegramBindingPresentationError() from None
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=') != value:
        raise PlatformTelegramBindingPresentationError()
    return value


def read_platform_telegram_binding_start_parameter(value):
    """Read only the /start argument; URLs and complete commands are not accepted."""
    if (not isinstance(value, str) or len(value) > _MAX_START_LENGTH
        or not value.startswith(_START_PREFIX) or len(value) != len(_START_PREFIX)+_CODE_LENGTH):
        raise PlatformTelegramBindingPresentationError()
    return read_platform_telegram_binding_code(value[len(_START_PREFIX):])


@dataclass(frozen=True)
class PlatformTelegramBindingPresentation:
    binding_id: UUID
    user_id: UUID
    tenant_id: UUID
    issued_at: datetime
    expires_at: datetime
    row_version: int
    code: str = field(repr=False)
    deep_link: str = field(repr=False)


def present_platform_telegram_binding(result, *, bot_username, expected_user_id, expected_tenant_id):
    """Format one authorized issuance receipt for a configured target bot.

    bot_username comes from trusted server configuration, never a caller URL.
    Expiry is preserved even on a replay after TTL; presentation does not renew
    or authorize the token. Public response fields remain an HTTP-adapter choice.
    """
    if (not isinstance(result, (PlatformTelegramBindingInitiationResult, PlatformTelegramBindingResendResult))
        or not isinstance(expected_user_id, UUID) or not isinstance(expected_tenant_id, UUID)
        or not isinstance(result.binding_id, UUID) or result.user_id != expected_user_id
        or result.tenant_id != expected_tenant_id or type(result.row_version) is not int or result.row_version != 1
        or not isinstance(bot_username, str) or re.fullmatch(r'[A-Za-z0-9_]{5,32}', bot_username) is None):
        raise PlatformTelegramBindingPresentationError()
    if any(not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None
        for value in (result.issued_at, result.expires_at)):
        raise PlatformTelegramBindingPresentationError()
    issued, expires = result.issued_at.astimezone(timezone.utc), result.expires_at.astimezone(timezone.utc)
    if expires-issued != PLATFORM_TELEGRAM_BINDING_TOKEN_TTL:
        raise PlatformTelegramBindingPresentationError()
    code = read_platform_telegram_binding_code(result.token)
    payload = _START_PREFIX+code
    deep_link = 'https://t.me/'+bot_username+'?start='+payload
    return PlatformTelegramBindingPresentation(result.binding_id, expected_user_id, expected_tenant_id,
        issued, expires, 1, code, deep_link)
