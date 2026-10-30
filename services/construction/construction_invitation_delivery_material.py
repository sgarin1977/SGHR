from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from services.construction.construction_invitation_delivery import (
    ConstructionInvitationDeliveryTokenError,
)
from services.construction.construction_invitations import (
    normalize_construction_invitation_email,
    require_construction_invitation_token,
    resolve_construction_invitation_status,
)


@dataclass(frozen=True)
class ConstructionInvitationDeliveryMaterial:
    invitation_id: UUID
    tenant_id: UUID
    email: str
    expires_at: datetime
    token: str = field(repr=False)


def prepare_construction_invitation_delivery(
    *, job, invitation, token_codec, lease_owner, as_of,
):
    """Caller must hold the invitation row lock through delivery."""
    message = "Invitation delivery is not available."

    def aware(value):
        return (
            isinstance(value, datetime)
            and value.tzinfo is not None
            and value.utcoffset() is not None
        )

    try:
        if (
            not aware(as_of)
            or not isinstance(lease_owner, str)
            or not lease_owner.strip()
            or getattr(job, "job_type", None) != "INVITATION_EMAIL"
            or getattr(job, "resource_type", None) != "construction_invitation"
            or getattr(job, "status", None) != "RUNNING"
            or getattr(job, "lease_owner", None) != lease_owner
            or not aware(getattr(job, "lease_expires_at", None))
            or job.lease_expires_at <= as_of
        ):
            raise ValueError(message)

        if (
            not isinstance(getattr(job, "id", None), UUID)
            or not isinstance(getattr(job, "user_id", None), UUID)
            or not isinstance(getattr(job, "tenant_id", None), UUID)
            or not isinstance(getattr(job, "resource_id", None), UUID)
            or not isinstance(getattr(invitation, "id", None), UUID)
            or not isinstance(getattr(invitation, "tenant_id", None), UUID)
            or job.tenant_id != invitation.tenant_id
            or job.resource_id != invitation.id
        ):
            raise ValueError(message)

        metadata = getattr(invitation, "delivery_metadata", None)
        if (
            not isinstance(metadata, dict)
            or metadata.get("job_id") != str(job.id)
            or getattr(job, "idempotency_key", None)
            != f"invitation:{invitation.id}:{invitation.token_hash}"
        ):
            raise ValueError(message)

        if (
            resolve_construction_invitation_status(
                status=invitation.status,
                expires_at=invitation.expires_at,
                as_of=as_of,
            ) != "pending"
            or getattr(invitation, "token_used_at", True) is not None
        ):
            raise ValueError(message)

        token = token_codec.decrypt(
            ciphertext=metadata.get("token_ciphertext"),
            tenant_id=invitation.tenant_id,
            invitation_id=invitation.id,
        )
        require_construction_invitation_token(
            invitation=invitation, token=token, as_of=as_of,
        )
        return ConstructionInvitationDeliveryMaterial(
            invitation_id=invitation.id,
            tenant_id=invitation.tenant_id,
            email=normalize_construction_invitation_email(invitation.email),
            expires_at=invitation.expires_at,
            token=token,
        )
    except Exception:
        raise ConstructionInvitationDeliveryTokenError(message) from None
