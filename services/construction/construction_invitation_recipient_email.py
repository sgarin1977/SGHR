from dataclasses import dataclass, field
from uuid import UUID

from services.construction.construction_invitation_delivery_material import ConstructionInvitationDeliveryMaterial
from services.construction.construction_invitation_recipient_context import ConstructionInvitationRecipientContextCodec
from services.construction.construction_jobs import ConstructionJobPermanentError

@dataclass(frozen=True)
class ConstructionInvitationRecipientEmailMaterial(ConstructionInvitationDeliveryMaterial):
    """Internal email payload; token and routing context must never be logged."""
    invitation_context: str = field(repr=False)

class ConstructionInvitationRecipientEmailSender:
    """Add API-compatible recipient context after the locked delivery loader.

    The callback still receives material=..., job_id=... and uses Platform
    email delivery. Its material includes invitation_context and token for
    the recipient frontend. This adapter does not create a URL, open a
    transaction, send through a provider itself, or close the caller's session.
    """
    def __init__(self, *, send_invitation, recipient_context_encryption_key):
        if not callable(send_invitation):
            raise ValueError("Platform invitation email callback is required.")
        self.send_invitation = send_invitation
        self.codec = ConstructionInvitationRecipientContextCodec(
            encryption_key=recipient_context_encryption_key,
        )

    async def __call__(self, *, material, job_id):
        if not isinstance(job_id, UUID):
            raise ConstructionJobPermanentError(error_code="INVITATION_DELIVERY_CONTEXT_INVALID")
        try:
            request = self.codec.issue_for_delivery(material=material)
            enriched = ConstructionInvitationRecipientEmailMaterial(
                invitation_id=material.invitation_id, tenant_id=material.tenant_id,
                email=material.email, expires_at=material.expires_at,
                token=request.token, invitation_context=request.invitation_context,
            )
        except (TypeError, ValueError, AttributeError):
            raise ConstructionJobPermanentError(
                error_code="INVITATION_DELIVERY_CONTEXT_INVALID",
            ) from None
        await self.send_invitation(material=enriched, job_id=job_id)
