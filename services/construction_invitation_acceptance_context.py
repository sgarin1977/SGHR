from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import text

from services.construction_foundation import (
    ConstructionRowVersionConflictError, require_expected_row_version,
)
from services.construction_invitations import (
    ConstructionInvitationOperationError,
    ConstructionInvitationRecipientDeniedError,
    ConstructionInvitationTokenDeniedError,
    ConstructionInvitationValidationError,
    require_construction_invitation_email,
    require_construction_invitation_token,
)


class ConstructionInvitationAcceptanceContext:
    """Prepare a fresh pending acceptance; the outer service owns the transaction.

    The caller supplies authenticated Platform identity and a trusted invitation
    tenant, already bound to this active Construction transaction for the lock.
    The recipient need not already belong to that tenant. Canonical email proof
    uses a separate Platform session. Continue membership, grants, accepted and
    audit in this same transaction; roll it back on any failure.
    """

    def __init__(
        self, *, session, repository, identity_reader, now_provider=None,
    ):
        if (
            session is None
            or getattr(repository, "session", None) is not session
            or not callable(getattr(session, "in_transaction", None))
            or not callable(getattr(session, "execute", None))
            or not callable(getattr(repository, "get_invitation_for_update", None))
            or not callable(getattr(identity_reader, "has_verified_email", None))
            or getattr(identity_reader, "session", None) is session
            or (now_provider is not None and not callable(now_provider))
        ):
            raise ValueError("Invitation acceptance requires valid session wiring.")
        self.session = session
        self.repository = repository
        self.identity_reader = identity_reader
        self.now_provider = (
            now_provider if now_provider is not None
            else lambda: datetime.now(timezone.utc)
        )

    async def prepare_pending_acceptance(
        self, *, actor, tenant_id, invitation_id, expected_row_version, token,
    ):
        try:
            recipient_user_id = getattr(actor, "user_id", None)
            if (
                getattr(actor, "status", None) != "active"
                or not isinstance(recipient_user_id, UUID)
            ):
                raise ConstructionInvitationRecipientDeniedError(
                    "Invitation recipient is not available."
                )
            if (
                not isinstance(tenant_id, UUID)
                or not isinstance(invitation_id, UUID)
                or not self.session.in_transaction()
            ):
                raise ConstructionInvitationValidationError(
                    "Invitation acceptance requires an active trusted context."
                )

            invitation = await self.repository.get_invitation_for_update(
                tenant_id=tenant_id, invitation_id=invitation_id,
            )
            if (
                invitation is None
                or getattr(invitation, "tenant_id", None) != tenant_id
                or getattr(invitation, "id", None) != invitation_id
            ):
                raise ConstructionInvitationTokenDeniedError(
                    "Invitation token is not available."
                )
            require_construction_invitation_token(
                invitation=invitation, token=token, as_of=self.now_provider(),
            )
            await require_construction_invitation_email(
                actor=SimpleNamespace(user_id=recipient_user_id, status="active"),
                invitation_email=invitation.email,
                identity_reader=self.identity_reader,
            )
            require_expected_row_version(
                actual_row_version=invitation.row_version,
                expected_row_version=expected_row_version,
            )
            # Canonical identity verification may have taken time.
            require_construction_invitation_token(
                invitation=invitation, token=token, as_of=self.now_provider(),
            )
            await self.session.execute(
                text("""
                    SELECT set_config('app.current_tenant_id', :tenant_id, true),
                           set_config('app.current_invitation_recipient_id',
                                      :recipient_id, true),
                           set_config('app.current_invitation_id',
                                      :invitation_id, true)
                """),
                {
                    "tenant_id": str(invitation.tenant_id),
                    "recipient_id": str(recipient_user_id),
                    "invitation_id": str(invitation.id),
                },
            )
            # Internal locked ORM row; the outer workflow serializes public data.
            return invitation
        except (
            ConstructionInvitationRecipientDeniedError,
            ConstructionInvitationTokenDeniedError,
            ConstructionInvitationValidationError,
            ConstructionRowVersionConflictError,
        ):
            raise
        except Exception:
            raise ConstructionInvitationOperationError(
                "Invitation acceptance preparation failed."
            ) from None


def build_construction_invitation_acceptance_context(
    *, session, identity_reader, now_provider=None,
):
    from database.repositories.construction import ConstructionInvitationRepository

    return ConstructionInvitationAcceptanceContext(
        session=session,
        repository=ConstructionInvitationRepository(session),
        identity_reader=identity_reader,
        now_provider=now_provider,
    )
