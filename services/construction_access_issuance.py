from datetime import datetime, timezone
from uuid import UUID

from services.construction_access_mutations import ConstructionAccessMutationService
from services.construction_grants import ConstructionAccessContextResolver, ConstructionGrantOperationError
from services.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
    require_construction_role_scope, require_delegable_construction_role,
    require_delegable_construction_scope,
)


class ConstructionAccessIssuanceService(ConstructionAccessMutationService):
    """Prepare initial issuance and mandatory audit. The workflow owns commit."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not callable(getattr(self.repository, "list_active_grants", None)):
            raise ValueError("Grant issuance requires the existing access resolver repository.")
        self.resolver = ConstructionAccessContextResolver(repository=self.repository, now_provider=self._now)

    async def _authorize(self, *, actor, role, scope_type, scope_id):
        context = await self.resolver.resolve(actor=actor)
        require_delegable_construction_scope(
            actor=actor, access_context=context, scope_type=scope_type, scope_id=scope_id,
        )
        require_delegable_construction_role(
            actor=actor, access_context=context, target_role=role, scope_type=scope_type, scope_id=scope_id,
        )

    async def grant_access(
        self, *, actor, target_user_id, role, scope_type, scope_id, expires_at=None, trace_id=None,
    ):
        try:
            if (
                getattr(actor, "status", None) != "active"
                or not all(isinstance(value, UUID) for value in (
                    getattr(actor, "tenant_id", None), getattr(actor, "user_id", None), target_user_id, scope_id,
                ))
                or not self.session.in_transaction()
            ):
                raise ConstructionPermissionDeniedError("Construction access issuance is not available.")
            role = role.strip().upper() if isinstance(role, str) else ""
            scope_type = scope_type.strip().lower() if isinstance(scope_type, str) else ""
            require_construction_role_scope(role=role, scope_type=scope_type)
            if scope_type == "document":
                raise ConstructionScopeDeniedError("Document scope requires the Document Center validator.")
            if expires_at is not None and (
                not isinstance(expires_at, datetime) or expires_at.tzinfo is None or expires_at.utcoffset() is None
            ):
                raise ValueError("Construction access expiration must be aware and future.")
            await self._authorize(actor=actor, role=role, scope_type=scope_type, scope_id=scope_id)
            if scope_type == "project":
                project = await self.project_repository.get_active_project_for_update(
                    tenant_id=actor.tenant_id, project_id=scope_id,
                )
                if (
                    project is None or project.id != scope_id
                    or project.tenant_id != actor.tenant_id or project.deleted_at is not None
                ):
                    raise ConstructionScopeDeniedError("Construction project is not available.")
            if await self.repository.has_active_platform_membership(
                tenant_id=actor.tenant_id, user_id=target_user_id,
            ) is not True:
                raise ConstructionPermissionDeniedError("Construction access target is not available.")
            # Refresh scope-specific authority after awaited project/membership checks.
            await self._authorize(actor=actor, role=role, scope_type=scope_type, scope_id=scope_id)
            instant = self._now()
            if expires_at is not None:
                expires_at = expires_at.astimezone(timezone.utc)
                if expires_at <= instant:
                    raise ValueError("Construction access expiration must be aware and future.")
            grant = await self.repository.create_grant(
                tenant_id=actor.tenant_id, user_id=target_user_id, role=role,
                scope_type=scope_type, scope_id=scope_id, expires_at=expires_at,
                granted_by_user_id=actor.user_id, granted_at=instant,
            )
            if (
                not isinstance(getattr(grant, "id", None), UUID)
                or grant.tenant_id != actor.tenant_id or grant.user_id != target_user_id
                or grant.role != role or grant.scope_type != scope_type or grant.scope_id != scope_id
                or grant.expires_at != expires_at or grant.granted_at != instant
                or grant.granted_by_user_id != actor.user_id or grant.status != "active"
                or grant.revoked_at is not None or grant.revoked_by_user_id is not None
            ):
                raise ConstructionGrantOperationError("Issued grant state is invalid.")
            await self.audit_logger.record_operation(
                actor=actor, event_type="construction_access_grant_created",
                entity_type="construction_access_grant", entity_id=grant.id, operation="create",
                details={
                    "target_user_id": str(target_user_id), "role": role,
                    "scope_type": scope_type, "scope_id": str(scope_id),
                    "expires_at": expires_at.isoformat() if expires_at is not None else None,
                },
                trace_id=trace_id,
            )
            return grant
        except Exception as error:
            await self._abort(error)


def build_construction_access_issuance_service(*, session, now_provider=None):
    from database.repositories.construction import ConstructionProjectRepository
    from database.repositories.construction_access_mutations import ConstructionAccessMutationRepository
    from database.repositories.event import EventRepository
    from services.construction_membership_writers import SerializedConstructionAccessIssuanceService
    return SerializedConstructionAccessIssuanceService(
        session=session, repository=ConstructionAccessMutationRepository(session),
        project_repository=ConstructionProjectRepository(session), event_repository=EventRepository(session),
        now_provider=now_provider,
    )
