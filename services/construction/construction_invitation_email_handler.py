from uuid import UUID

from services.construction.construction_invitation_delivery import (
    ConstructionInvitationDeliveryTokenError,
)
from services.construction.construction_invitation_delivery_material import (
    ConstructionInvitationDeliveryMaterial,
)
from services.construction.construction_jobs import ConstructionJobPermanentError


class ConstructionInvitationEmailHandler:
    """Use the locked loader before calling the supplied email adapter.

    send_invitation(material=..., job_id=...) is an async integration
    callback. It must use platform email delivery. The caller owns the
    transaction; this handler does not commit or roll it back.
    """

    def __init__(self, *, session, loader, send_invitation):
        if (
            session is None
            or getattr(loader, "session", None) is not session
            or not callable(getattr(session, "in_transaction", None))
            or not callable(getattr(loader, "load", None))
            or not callable(send_invitation)
        ):
            raise ValueError("Invitation email handler requires shared-session wiring.")
        self.session = session
        self.loader = loader
        self.send_invitation = send_invitation

    async def __call__(self, job):
        if (
            not self.session.in_transaction()
            or not isinstance(getattr(job, "id", None), UUID)
            or not isinstance(getattr(job, "tenant_id", None), UUID)
            or not isinstance(getattr(job, "resource_id", None), UUID)
            or getattr(job, "job_type", None) != "INVITATION_EMAIL"
            or getattr(job, "resource_type", None) != "construction_invitation"
        ):
            raise ConstructionJobPermanentError(
                error_code="INVITATION_DELIVERY_CONTEXT_INVALID",
            )
        try:
            material = await self.loader.load(
                tenant_id=job.tenant_id, job_id=job.id,
            )
        except ConstructionInvitationDeliveryTokenError:
            raise ConstructionJobPermanentError(
                error_code="INVITATION_DELIVERY_UNAVAILABLE",
            ) from None

        if (
            not self.session.in_transaction()
            or not isinstance(material, ConstructionInvitationDeliveryMaterial)
            or material.tenant_id != job.tenant_id
            or material.invitation_id != job.resource_id
        ):
            raise ConstructionJobPermanentError(
                error_code="INVITATION_DELIVERY_CONTEXT_INVALID",
            )

        # A provider's private response is never a queue result reference.
        await self.send_invitation(material=material, job_id=job.id)
        return f"construction_invitation:{material.invitation_id}"


def build_construction_invitation_email_worker(
    *, session, encryption_key, send_invitation,
    lease_owner, lease_duration, clock, handlers=None,
    recipient_context_encryption_key=None,
):
    from services.construction.construction_invitation_delivery_loader import (
        build_construction_invitation_delivery_loader,
    )
    from services.construction.construction_invitation_recipient_email import (
        ConstructionInvitationRecipientEmailSender,
    )
    from services.construction.construction_jobs import (
        ConstructionBackgroundJobWorker, build_construction_job_queue,
    )

    configured = dict(handlers) if handlers is not None else {}
    if "INVITATION_EMAIL" in configured:
        raise ValueError("INVITATION_EMAIL handler is already configured.")
    loader = build_construction_invitation_delivery_loader(
        session=session, encryption_key=encryption_key,
        lease_owner=lease_owner, clock=clock,
    )
    sender = ConstructionInvitationRecipientEmailSender(
        send_invitation=send_invitation,
        recipient_context_encryption_key=(
            encryption_key if recipient_context_encryption_key is None
            else recipient_context_encryption_key
        ),
    )
    configured["INVITATION_EMAIL"] = ConstructionInvitationEmailHandler(
        session=session, loader=loader, send_invitation=sender,
    )
    return ConstructionBackgroundJobWorker(
        queue=build_construction_job_queue(session=session),
        handlers=configured, lease_owner=lease_owner,
        lease_duration=lease_duration, clock=clock,
    )
