"""Recreate a binding secret without persisting it in response snapshots."""
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
from uuid import UUID

from cryptography.fernet import Fernet
from services.platform_telegram_binding_tokens import (
    PLATFORM_TELEGRAM_BINDING_TOKEN_TTL, PlatformTelegramBindingTokenMaterial,
    hash_platform_telegram_binding_token,
)


class PlatformTelegramBindingTokenDeriver:
    """Use the stable Platform idempotency key with a separate HMAC domain.

    Database metadata alone cannot recover a secret. The existing configured
    idempotency key must remain available throughout the replay retention period.
    This does not add a key-rotation mechanism or store a recoverable token blob.
    """
    def __init__(self, *, encryption_key):
        try:
            if not isinstance(encryption_key, str): raise ValueError()
            Fernet(encryption_key.encode('ascii'))
            raw = base64.urlsafe_b64decode(encryption_key.encode('ascii'))
            if len(raw) != 32: raise ValueError()
        except (ValueError, TypeError, UnicodeError):
            raise ValueError('Configured Platform idempotency key is invalid.') from None
        self._key = hmac.new(raw, b'SGHR/platform.telegram.binding/token-derivation-key/v1', hashlib.sha256).digest()

    def issue(self, *, binding_id, user_id, tenant_id, initiated_by_user_id, issued_at):
        if any(not isinstance(value, UUID) for value in (binding_id, user_id, tenant_id, initiated_by_user_id)):
            raise ValueError('Binding token context is invalid.')
        if not isinstance(issued_at, datetime) or issued_at.tzinfo is None or issued_at.utcoffset() is None:
            raise ValueError('Binding token timestamp is invalid.')
        issued_at = issued_at.astimezone(timezone.utc)
        context = dict(binding_id=str(binding_id), user_id=str(user_id), tenant_id=str(tenant_id),
            initiated_by_user_id=str(initiated_by_user_id), issued_at=issued_at.isoformat(timespec='microseconds'))
        encoded = json.dumps(context, sort_keys=True, separators=(',', ':')).encode('ascii')
        digest = hmac.new(self._key, b'SGHR/platform.telegram.binding/token/v1\x00'+encoded, hashlib.sha256).digest()
        token = base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')
        return PlatformTelegramBindingTokenMaterial(token=token, token_hash=hash_platform_telegram_binding_token(token),
            issued_at=issued_at, expires_at=issued_at+PLATFORM_TELEGRAM_BINDING_TOKEN_TTL)
