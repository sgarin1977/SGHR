"""Recreate a client confirmation secret without persisting the secret."""
import base64
from datetime import datetime,timezone
import hashlib
import hmac
import json
from uuid import UUID

from cryptography.fernet import Fernet
from services.api_idempotency import hash_request_payload


class ConstructionClientLinkTokenDeriver:
    def __init__(self, *, encryption_key):
        try:
            if not isinstance(encryption_key,str):raise ValueError()
            Fernet(encryption_key.encode('ascii'))
            raw=base64.urlsafe_b64decode(encryption_key.encode('ascii'))
            if len(raw)!=32:raise ValueError()
        except (ValueError,TypeError,UnicodeError):
            raise ValueError('Client link replay key is invalid.') from None
        self._key=hmac.new(raw,b'SGHR/construction.client-link/issuance-key/v1',hashlib.sha256).digest()

    def derive(self, *, reservation_id, reserved_at, tenant_id, initiated_by_user_id, payload):
        if not all(isinstance(v,UUID) for v in (reservation_id,tenant_id,initiated_by_user_id)):
            raise ValueError('Client replay context is invalid.')
        if not isinstance(reserved_at,datetime) or reserved_at.tzinfo is None or reserved_at.utcoffset() is None:
            raise ValueError('Client replay timestamp is invalid.')
        # Expired idempotency reservations reuse their UUID. created_at is
        # renewed by the shared repository, so a new retention window gets a
        # new token and cannot collide with the old request's unique hash.
        context=dict(reservation_id=str(reservation_id),reserved_at=reserved_at.astimezone(timezone.utc).isoformat(timespec='microseconds'),
            tenant_id=str(tenant_id),initiated_by_user_id=str(initiated_by_user_id),payload_hash=hash_request_payload(payload))
        encoded=json.dumps(context,sort_keys=True,separators=(',',':')).encode('ascii')
        digest=hmac.new(self._key,b'SGHR/construction.client-link/issuance-token/v1\x00'+encoded,hashlib.sha256).digest()
        return 'ccl1_'+base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')
