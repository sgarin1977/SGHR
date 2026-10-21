from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from database.models import ConstructionBackgroundJobRecord
from database.repositories.job_queue import JobLeaseLostError
from services.construction_invitation_delivery import (
    ConstructionInvitationDeliveryTokenError,
)
from services.construction_invitation_delivery_material import (
    prepare_construction_invitation_delivery,
)


def _aware(value):
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


class ConstructionInvitationDeliveryLoader:
    """Load current delivery state under locks.

    The caller owns the active transaction and must retain both row locks
    through delivery and the queue outcome. This loader neither sends
    email nor opens, commits, or rolls back a transaction.
    """

    def __init__(
        self, *, session, invitation_repository, token_codec,
        lease_owner, clock,
    ):
        if (
            session is None
            or getattr(invitation_repository, "session", None) is not session
            or not callable(getattr(session, "in_transaction", None))
            or not callable(getattr(invitation_repository, "get_invitation_for_update", None))
            or not callable(getattr(token_codec, "decrypt", None))
            or not isinstance(lease_owner, str)
            or not lease_owner.strip()
            or not callable(clock)
        ):
            raise ValueError("Invitation delivery requires valid shared-session wiring.")
        self.session = session
        self.invitation_repository = invitation_repository
        self.token_codec = token_codec
        self.lease_owner = lease_owner.strip()
        self.clock = clock

    async def load(self, *, tenant_id, job_id):
        message = "Invitation delivery is not available."
        if not isinstance(tenant_id, UUID) or not isinstance(job_id, UUID):
            raise ConstructionInvitationDeliveryTokenError(message)
        if not self.session.in_transaction():
            raise ValueError("Invitation delivery requires an active transaction.")

        statement = (
            select(ConstructionBackgroundJobRecord)
            .where(
                ConstructionBackgroundJobRecord.tenant_id == tenant_id,
                ConstructionBackgroundJobRecord.id == job_id,
            )
            .limit(1)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        result = await self.session.execute(statement)
        job = result.scalar_one_or_none()
        if (
            job is None
            or job.id != job_id
            or job.tenant_id != tenant_id
            or job.job_type != "INVITATION_EMAIL"
            or job.resource_type != "construction_invitation"
            or not isinstance(job.resource_id, UUID)
        ):
            raise ConstructionInvitationDeliveryTokenError(message)

        invitation = await self.invitation_repository.get_invitation_for_update(
            tenant_id=tenant_id, invitation_id=job.resource_id,
        )
        # The clock is read after waiting for both database locks.
        now = self.clock()
        if not _aware(now):
            raise ValueError("Invitation delivery clock must be timezone-aware.")
        if (
            job.status != "RUNNING"
            or job.lease_owner != self.lease_owner
            or not _aware(job.lease_expires_at)
            or job.lease_expires_at <= now
        ):
            raise JobLeaseLostError("Job lease is unavailable.")
        if invitation is None:
            raise ConstructionInvitationDeliveryTokenError(message)

        return prepare_construction_invitation_delivery(
            job=job, invitation=invitation,
            token_codec=self.token_codec,
            lease_owner=self.lease_owner, as_of=now,
        )


def build_construction_invitation_delivery_loader(
    *, session, encryption_key, lease_owner, clock,
):
    from database.repositories.construction import ConstructionInvitationRepository
    from services.construction_invitation_delivery import ConstructionInvitationTokenCodec

    return ConstructionInvitationDeliveryLoader(
        session=session,
        invitation_repository=ConstructionInvitationRepository(session),
        token_codec=ConstructionInvitationTokenCodec(encryption_key=encryption_key),
        lease_owner=lease_owner, clock=clock,
    )
