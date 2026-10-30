from uuid import UUID

from services.construction.construction_grants import ConstructionGrantOperationError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction.construction_team_block import ConstructionTeamBlockResult, build_construction_team_block_service


PUBLIC_TEAM_BLOCK_FIELDS = frozenset({"user_id", "revoked_grant_ids"})


class ConstructionTeamBlockWorkflow:
    """One commit for the current tenant's grant revocations and their audit."""

    def __init__(self, *, session, block_service):
        collaborators = (
            block_service, getattr(block_service, "repository", None),
            getattr(getattr(block_service, "audit_logger", None), "event_repository", None),
        )
        if (
            session is None
            or any(getattr(item, "session", None) is not session for item in collaborators)
            or any(not callable(getattr(session, name, None)) for name in ("commit", "rollback", "in_transaction"))
            or not callable(getattr(block_service, "block_access", None))
        ):
            raise ValueError("Team block workflow requires valid shared-session wiring.")
        self.session = session
        self.service = block_service

    @staticmethod
    def _public(result, *, user_id):
        if (
            not isinstance(result, ConstructionTeamBlockResult)
            or result.user_id != user_id
            or not isinstance(result.revoked_grant_ids, tuple)
            or any(not isinstance(value, UUID) for value in result.revoked_grant_ids)
            or len(set(result.revoked_grant_ids)) != len(result.revoked_grant_ids)
            or result.revoked_grant_ids != tuple(sorted(result.revoked_grant_ids, key=lambda value: value.int))
        ):
            raise ConstructionGrantOperationError("Team block result is invalid.")
        return {
            "user_id": str(user_id),
            "revoked_grant_ids": [str(value) for value in result.revoked_grant_ids],
        }

    async def block_access(self, *, actor, user_id, trace_id=None):
        if not isinstance(user_id, UUID):
            await self.session.rollback()
            raise ValueError("Team block target must be a UUID.")
        try:
            if (
                getattr(actor, "status", None) != "active"
                or not all(isinstance(value, UUID) for value in (
                    getattr(actor, "tenant_id", None), getattr(actor, "user_id", None),
                ))
            ):
                raise ConstructionPermissionDeniedError("Construction team operation is not available.")
            if not self.session.in_transaction():
                raise ConstructionGrantOperationError("Team block workflow requires an active transaction.")
            result = await self.service.block_access(actor=actor, user_id=user_id, trace_id=trace_id)
            response = self._public(result, user_id=user_id)
            await self.session.commit()
            return response
        except Exception as error:
            await self.session.rollback()
            if isinstance(error, (
                ConstructionGrantOperationError, ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
            )):
                raise
            raise ConstructionGrantOperationError("Construction team block workflow failed.") from None


def build_construction_team_block_workflow(*, session, now_provider=None):
    from services.construction.construction_membership_writers import SerializedConstructionTeamBlockWorkflow
    return SerializedConstructionTeamBlockWorkflow(
        session=session,
        block_service=build_construction_team_block_service(session=session, now_provider=now_provider),
    )
