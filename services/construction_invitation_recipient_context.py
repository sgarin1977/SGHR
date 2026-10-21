import json
from dataclasses import dataclass, field
from uuid import UUID
from cryptography.fernet import Fernet, InvalidToken
from services.construction_invitation_delivery_material import ConstructionInvitationDeliveryMaterial
from services.construction_invitations import ConstructionInvitationTokenDeniedError, hash_construction_invitation_token, verify_construction_invitation_token

@dataclass(frozen=True)
class ConstructionInvitationRecipientContext:
    tenant_id: UUID
    invitation_id: UUID

@dataclass(frozen=True)
class ConstructionInvitationRecipientRequest:
    invitation_context: str = field(repr=False)
    token: str = field(repr=False)

class ConstructionInvitationRecipientContextCodec:
    """Authenticated routing envelope, not authorization or membership.

    Issue only from server delivery material. The recipient still needs
    authenticated Platform identity, verified invitation email and the
    existing one-time token. Fresh acceptance checks database expiry/status.
    The routing envelope itself has no independent business TTL so an
    authorized idempotency replay can resolve an already accepted invitation.
    Never expose delivery ciphertext or idempotency ciphertext as this envelope.
    """
    PURPOSE = 'construction.invitation.recipient-context.v1'
    MAX_CONTEXT_LENGTH = 4096

    def __init__(self, *, encryption_key):
        try:
            if not isinstance(encryption_key, str) or not encryption_key:
                raise ValueError
            self._fernet = Fernet(encryption_key.encode('ascii'))
        except (TypeError, ValueError, UnicodeError):
            raise ValueError('Invitation context encryption key is invalid.') from None

    def issue_for_delivery(self, *, material):
        if not isinstance(material, ConstructionInvitationDeliveryMaterial) or not isinstance(material.tenant_id, UUID) or (not isinstance(material.invitation_id, UUID)) or (not isinstance(material.token, str)) or (not material.token):
            raise ValueError('Trusted invitation delivery material is required.')
        payload = {'purpose': self.PURPOSE, 'tenant_id': str(material.tenant_id), 'invitation_id': str(material.invitation_id), 'token_hash': hash_construction_invitation_token(material.token)}
        serialized = json.dumps(payload, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        return ConstructionInvitationRecipientRequest(invitation_context=self._fernet.encrypt(serialized).decode('ascii'), token=material.token)

    def resolve(self, *, invitation_context, token):
        message = 'Invitation context is not available.'
        try:
            if not isinstance(invitation_context, str) or not invitation_context or len(invitation_context) > self.MAX_CONTEXT_LENGTH or (not isinstance(token, str)) or (not token):
                raise ValueError
            serialized = self._fernet.decrypt(invitation_context.encode('ascii'))
            payload = json.loads(serialized.decode('utf-8'))
            if not isinstance(payload, dict) or set(payload) != {'purpose', 'tenant_id', 'invitation_id', 'token_hash'} or payload['purpose'] != self.PURPOSE or (not isinstance(payload['tenant_id'], str)) or (not isinstance(payload['invitation_id'], str)) or (not verify_construction_invitation_token(token=token, expected_hash=payload['token_hash'])):
                raise ValueError
            return ConstructionInvitationRecipientContext(tenant_id=UUID(payload['tenant_id']), invitation_id=UUID(payload['invitation_id']))
        except (InvalidToken, TypeError, ValueError, UnicodeError, KeyError):
            raise ConstructionInvitationTokenDeniedError(message) from None
