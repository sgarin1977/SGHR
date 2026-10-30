import asyncio
from uuid import UUID

from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_lead_rules import ConstructionLeadValidationError, ConstructionLeadTransitionError
from services.construction.construction_lead_status import (
    ConstructionLeadStatusCommand, ConstructionLeadStatusResult, ConstructionLeadStatusService,
    ConstructionLeadStatusNotFoundError, ConstructionLeadStatusOperationError,
    build_construction_lead_status_service,
)
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError


PUBLIC_LEAD_STATUS_FIELDS = frozenset({"id", "tenant_id", "status", "row_version"})


class ConstructionLeadStatusWorkflow:
    """Own the final commit/rollback of a caller-opened status transaction."""

    def __init__(self, *, session, status_service):
        collaborators = (
            status_service, getattr(status_service, "repository", None),
            getattr(status_service, "access_repository", None),
            getattr(status_service, "event_repository", None),
            getattr(getattr(status_service, "audit_logger", None), "event_repository", None),
        )
        if (
            session is None
            or not isinstance(status_service, ConstructionLeadStatusService)
            or any(getattr(item, "session", None) is not session for item in collaborators)
            or any(not callable(getattr(session, name, None))
                   for name in ("commit", "rollback", "in_transaction"))
            or not callable(getattr(status_service, "transition_status", None))
        ):
            raise ValueError("Lead status workflow requires shared-session wiring.")
        self.session, self.service = session, status_service

    @staticmethod
    def _response(result, *, actor, lead_id, command):
        if not isinstance(result, ConstructionLeadStatusResult) or type(result.changed) is not bool:
            raise ConstructionLeadStatusOperationError("Lead status result mode is invalid.")
        lead = result.lead
        if (
            getattr(lead, "id", None) != lead_id
            or getattr(lead, "tenant_id", None) != actor.tenant_id
            or getattr(lead, "deleted_at", "missing") is not None
        ):
            raise ConstructionLeadStatusNotFoundError("Lead status resource is not available.")
        if (
            getattr(lead, "status", None) != command.next_status
            or type(getattr(lead, "row_version", None)) is not int
            or not 0 < lead.row_version < 2**63
            or lead.row_version != command.expected_row_version + int(result.changed)
        ):
            raise ConstructionLeadStatusOperationError("Lead status response binding is invalid.")
        # Materialize the response before commit can expire the ORM identity.
        return dict(id=str(lead_id), tenant_id=str(actor.tenant_id),
                    status=lead.status, row_version=lead.row_version)

    async def _rollback(self):
        try:
            await self.session.rollback()
        except Exception:
            raise ConstructionLeadStatusOperationError("Lead status transaction rollback failed.") from None

    async def transition_status(
        self, *, actor, access_context, lead_id, expected_row_version, next_status, trace_id=None,
    ):
        try:
            self.service._authorize(actor, access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionLeadStatusOperationError("Lead status workflow requires an active transaction.")
            if not isinstance(lead_id, UUID):
                raise ConstructionLeadValidationError("Lead identifier must be a UUID.")
            command = ConstructionLeadStatusCommand(expected_row_version, next_status)
            result = await self.service.transition_status(actor=actor, access_context=access_context,
                lead_id=lead_id, command=command, trace_id=trace_id)
            response = self._response(result, actor=actor, lead_id=lead_id, command=command)
            # Also close successful no-op transactions and release their row lock.
            await self.session.commit()
            return response
        except asyncio.CancelledError:
            await self._rollback()
            raise
        except Exception as error:
            await self._rollback()
            if isinstance(error, (
                ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionLeadValidationError, ConstructionLeadTransitionError,
                ConstructionRowVersionConflictError, ConstructionLeadStatusNotFoundError,
                ConstructionLeadStatusOperationError,
            )):
                raise
            raise ConstructionLeadStatusOperationError("Lead status workflow failed.") from None


def build_construction_lead_status_workflow(*, session, now_provider=None):
    return ConstructionLeadStatusWorkflow(session=session,
        status_service=build_construction_lead_status_service(session=session, now_provider=now_provider))
