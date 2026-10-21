from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy.orm.exc import StaleDataError

from services.construction_audit import (
    ConstructionAuditChange, ConstructionAuditLogger,
)
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


class ConstructionInvitationDeclineService:
    """Decline as the verified recipient, without creating access.

    actor comes from authenticated Platform identity. tenant_id is a
    trusted server invitation context, not proof of membership and not
    an untrusted request-body value. Platform identity may be read using
    its own session; invitation and audit writes share one session.
    """

    def __init__(
        self, *, session, repository, event_repository,
        identity_reader, now_provider=None,
    ):
        if (
            session is None
            or getattr(repository, "session", None) is not session
            or getattr(event_repository, "session", None) is not session
            or not callable(getattr(repository, "get_invitation_for_update", None))
            or not callable(getattr(repository, "decline_invitation", None))
            or not callable(getattr(event_repository, "create_event", None))
            or not callable(getattr(identity_reader, "has_verified_email", None))
            or (now_provider is not None and not callable(now_provider))
        ):
            raise ValueError("Invitation decline requires valid shared-session wiring.")
        self.session = session
        self.repository = repository
        self.identity_reader = identity_reader
        self.audit_logger = ConstructionAuditLogger(
            event_repository=event_repository,
        )
        self.now_provider = (
            now_provider if now_provider is not None
            else lambda: datetime.now(timezone.utc)
        )

    async def decline_invitation(
        self, *, actor, tenant_id, invitation_id,
        expected_row_version, token, trace_id=None,
    ):
        try:
            if (
                getattr(actor, "status", None) != "active"
                or not isinstance(getattr(actor, "user_id", None), UUID)
            ):
                raise ConstructionInvitationRecipientDeniedError(
                    "Invitation recipient is not available."
                )
            if not isinstance(tenant_id, UUID) or not isinstance(invitation_id, UUID):
                raise ConstructionInvitationValidationError(
                    "Invitation context is invalid."
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
                invitation=invitation, token=token,
                as_of=self.now_provider(),
            )
            await require_construction_invitation_email(
                actor=actor, invitation_email=invitation.email,
                identity_reader=self.identity_reader,
            )
            require_expected_row_version(
                actual_row_version=invitation.row_version,
                expected_row_version=expected_row_version,
            )
            # Identity verification may have taken time.
            require_construction_invitation_token(
                invitation=invitation, token=token,
                as_of=self.now_provider(),
            )

            old_status = invitation.status
            invitation = await self.repository.decline_invitation(
                invitation=invitation,
            )
            await self.audit_logger.record_operation(
                actor=SimpleNamespace(
                    tenant_id=invitation.tenant_id,
                    user_id=actor.user_id,
                ),
                event_type="construction_invitation_declined",
                entity_type="construction_invitation",
                entity_id=invitation.id,
                operation="decline",
                changes=(ConstructionAuditChange(
                    field="status", old_value=old_status,
                    new_value="declined",
                ),),
                trace_id=trace_id,
            )
            return invitation
        except StaleDataError:
            await self.session.rollback()
            raise ConstructionRowVersionConflictError() from None
        except (
            ConstructionInvitationRecipientDeniedError,
            ConstructionInvitationTokenDeniedError,
            ConstructionInvitationValidationError,
            ConstructionRowVersionConflictError,
        ):
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise ConstructionInvitationOperationError(
                "Invitation decline failed."
            ) from None


def build_construction_invitation_decline_service(
    *, session, identity_reader, now_provider=None,
):
    from database.repositories.construction import ConstructionInvitationRepository
    from database.repositories.event import EventRepository

    return ConstructionInvitationDeclineService(
        session=session,
        repository=ConstructionInvitationRepository(session),
        event_repository=EventRepository(session),
        identity_reader=identity_reader,
        now_provider=now_provider,
    )
