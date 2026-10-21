from uuid import UUID

from services.construction_foundation import ConstructionRowVersionConflictError
from services.construction_invitations import (
    ConstructionInvitationOperationError,
    ConstructionInvitationRecipientDeniedError,
    ConstructionInvitationTokenDeniedError,
    ConstructionInvitationValidationError,
)


class ConstructionInvitationDeclineWorkflow:
    """Commit recipient decline and audit once, then return safe state.

    actor and tenant_id are trusted server contexts supplied by the
    caller. No membership, grants, email job, or idempotency reservation
    is created by this workflow.
    """

    def __init__(self, *, session, decline_service):
        if (
            session is None
            or getattr(decline_service, "session", None) is not session
            or getattr(
                getattr(decline_service, "repository", None),
                "session", None,
            ) is not session
            or getattr(
                getattr(
                    getattr(decline_service, "audit_logger", None),
                    "event_repository", None,
                ),
                "session", None,
            ) is not session
            or not callable(getattr(decline_service, "decline_invitation", None))
            or not callable(getattr(session, "commit", None))
            or not callable(getattr(session, "rollback", None))
        ):
            raise ValueError("Decline workflow requires one shared session.")
        self.session = session
        self.decline_service = decline_service

    async def decline_invitation(
        self, *, actor, tenant_id, invitation_id,
        expected_row_version, token, trace_id=None,
    ):
        try:
            invitation = await self.decline_service.decline_invitation(
                actor=actor, tenant_id=tenant_id,
                invitation_id=invitation_id,
                expected_row_version=expected_row_version,
                token=token, trace_id=trace_id,
            )
            version = getattr(invitation, "row_version", None)
            if (
                not isinstance(getattr(invitation, "id", None), UUID)
                or invitation.id != invitation_id
                or not isinstance(getattr(invitation, "tenant_id", None), UUID)
                or invitation.tenant_id != tenant_id
                or getattr(invitation, "status", None) != "declined"
                or type(version) is not int
                or version <= 0
            ):
                raise ConstructionInvitationOperationError(
                    "Declined invitation state is invalid."
                )
            # Snapshot scalar values before commit expires ORM objects.
            response = {
                "id": str(invitation.id),
                "status": "declined",
                "row_version": version,
            }
            await self.session.commit()
            return response
        except Exception as exc:
            await self.session.rollback()
            if isinstance(exc, (
                ConstructionInvitationOperationError,
                ConstructionInvitationRecipientDeniedError,
                ConstructionInvitationTokenDeniedError,
                ConstructionInvitationValidationError,
                ConstructionRowVersionConflictError,
            )):
                raise
            raise ConstructionInvitationOperationError(
                "Invitation decline workflow failed."
            ) from None


def build_construction_invitation_decline_workflow(
    *, session, identity_reader, now_provider=None,
):
    from services.construction_invitation_decline import (
        build_construction_invitation_decline_service,
    )

    return ConstructionInvitationDeclineWorkflow(
        session=session,
        decline_service=build_construction_invitation_decline_service(
            session=session, identity_reader=identity_reader,
            now_provider=now_provider,
        ),
    )
