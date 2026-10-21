"""Authenticated routing envelope for client-link recipient confirmation.

This carries no raw binding secret and grants no membership or data access.
The separate one-time token and authenticated target are still required. The
envelope has no independent TTL: the database request controls fresh consume,
while an authorized idempotency replay can resolve a previously consumed token.
"""
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass, field
import hashlib
import hmac
import json
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from database.repositories.construction_client_link_requests import ConstructionClientLinkRequestState
from services.construction_client_link_request_issuance import (
    ConstructionClientLinkIssuanceResult, hash_client_link_confirmation_token,
)
from services.construction_client_link_confirmation import ConstructionClientLinkConfirmationDeniedError


@dataclass(frozen=True)
class ConstructionClientLinkRecipientContext:
    tenant_id: UUID
    client_id: UUID
    request_id: UUID
    target_user_id: UUID
    expected_request_row_version: int


@dataclass(frozen=True)
class ConstructionClientLinkRecipientRequest:
    recipient_context: str = field(repr=False)
    token: str = field(repr=False)


class ConstructionClientLinkRecipientContextCodec:
    PURPOSE = 'construction.client-link.recipient-context.v1'
    MAX_CONTEXT_LENGTH = 4096
    REPLAYABLE_PREFIX = 'cclrc2_'
    REPLAYABLE_PURPOSE = b'SGHR/construction.client-link/recipient-context/v2'

    def __init__(self, *, encryption_key):
        try:
            if not isinstance(encryption_key, str) or not encryption_key:raise ValueError()
            self._fernet = Fernet(encryption_key.encode('ascii'))
            raw = urlsafe_b64decode(encryption_key.encode('ascii'))
            self._replayable_cipher = AESSIV(hmac.new(raw, self.REPLAYABLE_PURPOSE, hashlib.sha512).digest())
        except (ValueError,TypeError,UnicodeError):
            raise ValueError('Client recipient context key is invalid.') from None

    def _request_payload(self, material):
        if (not isinstance(material, ConstructionClientLinkIssuanceResult)
                or not isinstance(material.request, ConstructionClientLinkRequestState)
                or not isinstance(material.audit_id, UUID)):
            raise ValueError('Verified server issuance material is required.')
        state = material.request
        if (state.consumed_at is not None or state.invalidated_at is not None or state.row_version != 1
                or not hmac.compare_digest(hash_client_link_confirmation_token(material.token),state.token_hash)):
            raise ValueError('Verified initial request material is required.')
        payload = dict(purpose=self.PURPOSE,tenant_id=str(state.tenant_id),client_id=str(state.client_id),
            request_id=str(state.request_id),target_user_id=str(state.target_user_id),
            expected_request_row_version=state.row_version,token_hash=state.token_hash)
        serialized = json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode('ascii')
        return serialized

    def issue_for_request(self, *, material):
        serialized = self._request_payload(material)
        return ConstructionClientLinkRecipientRequest(self._fernet.encrypt(serialized).decode('ascii'),material.token)

    def issue_replayable_for_request(self, *, material):
        """Stable encrypted routing proof for HTTP replay; no new DB snapshot.

        AES-SIV is deterministic authenticated encryption without an external
        nonce. Its purpose-derived key is separate from legacy Fernet contexts.
        Only immutable request context is encrypted; the raw token stays out.
        """
        serialized = self._request_payload(material)
        protected = self._replayable_cipher.encrypt(serialized, [self.REPLAYABLE_PURPOSE])
        context = self.REPLAYABLE_PREFIX + urlsafe_b64encode(protected).decode('ascii')
        return ConstructionClientLinkRecipientRequest(context, material.token)



    def resolve(self, *, recipient_context, token):
        try:
            if (not isinstance(recipient_context,str) or not recipient_context
                    or len(recipient_context)>self.MAX_CONTEXT_LENGTH):raise ValueError()
            digest = hash_client_link_confirmation_token(token)
            if recipient_context.startswith(self.REPLAYABLE_PREFIX):
                encoded = recipient_context[len(self.REPLAYABLE_PREFIX):]
                protected = urlsafe_b64decode(encoded.encode('ascii'))
                if urlsafe_b64encode(protected).decode('ascii') != encoded:raise ValueError()
                serialized = self._replayable_cipher.decrypt(protected, [self.REPLAYABLE_PURPOSE])
            else:
                serialized = self._fernet.decrypt(recipient_context.encode('ascii'))
            payload = json.loads(serialized.decode('ascii'))
            expected = {'purpose','tenant_id','client_id','request_id','target_user_id','expected_request_row_version','token_hash'}
            if (not isinstance(payload,dict) or set(payload)!=expected or payload['purpose']!=self.PURPOSE
                    or type(payload['expected_request_row_version']) is not int or payload['expected_request_row_version']!=1
                    or not isinstance(payload['token_hash'],str) or not hmac.compare_digest(payload['token_hash'],digest)):
                raise ValueError()
            ids = []
            for name in ('tenant_id','client_id','request_id','target_user_id'):
                value = payload[name]
                if not isinstance(value,str):raise ValueError()
                identifier = UUID(value)
                if str(identifier)!=value:raise ValueError()
                ids.append(identifier)
            return ConstructionClientLinkRecipientContext(*ids,payload['expected_request_row_version'])
        except (InvalidToken,InvalidTag,ValueError,TypeError,UnicodeError,KeyError):
            raise ConstructionClientLinkConfirmationDeniedError() from None
