from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from services.construction_access_errors import ConstructionAccessGrantConflictError
from services.construction_audit import ConstructionAuditChange, ConstructionAuditLogger
from services.construction_grants import ConstructionAccessContextResolver, ConstructionGrantOperationError
from services.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
    require_construction_role_scope, require_delegable_construction_role,
    require_delegable_construction_scope,
)


def is_active_grant_overlap(error):
    """Inspect structured driver diagnostics, never parse or expose SQL text."""
    current = error.orig if isinstance(error, IntegrityError) else error
    visited = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        diagnostic = getattr(current, "diag", None)
        code = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        name = getattr(current, "constraint_name", None) or getattr(diagnostic, "constraint_name", None)
        if code == "23P01" and name == "ex_construction_access_grants_active_period":
            return True
        current = getattr(current, "__cause__", None)
    return False


class ConstructionAccessMutationService:
    """Prepare grant history and audit together. The workflow owns commit."""

    def __init__(self, *, session, repository, event_repository, project_repository, now_provider=None):
        if (
            session is None
            or any(getattr(item, "session", None) is not session
                   for item in (repository, event_repository, project_repository))
            or any(not callable(getattr(repository, name, None)) for name in (
                "get_grant_for_update", "revoke_if_effective", "create_grant",
                "has_active_platform_membership",
            ))
            or not callable(getattr(project_repository, "get_active_project_for_update", None))
            or not callable(getattr(event_repository, "create_event", None))
            or (now_provider is not None and not callable(now_provider))
        ):
            raise ValueError("Grant mutations require valid shared-session wiring.")
        self.session = session
        self.repository = repository
        self.project_repository = project_repository
        self.audit_logger = ConstructionAuditLogger(event_repository=event_repository)
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Grant mutations require an aware server clock.")
        return value.astimezone(timezone.utc)

    async def _original(self, *, actor, access_context, grant_id):
        if (
            getattr(actor, "status", None) != "active"
            or not all(isinstance(value, UUID) for value in (
                getattr(actor, "tenant_id", None), getattr(actor, "user_id", None), grant_id,
            ))
            or not self.session.in_transaction()
        ):
            raise ConstructionPermissionDeniedError("Construction access operation is not available.")
        grant = await self.repository.get_grant_for_update(tenant_id=actor.tenant_id, grant_id=grant_id)
        if grant is None or grant.tenant_id != actor.tenant_id or grant.id != grant_id:
            raise ConstructionPermissionDeniedError("Construction access grant is not available.")
        require_delegable_construction_scope(
            actor=actor, access_context=access_context,
            scope_type=grant.scope_type, scope_id=grant.scope_id,
        )
        require_delegable_construction_role(
            actor=actor, access_context=access_context, target_role=grant.role,
            scope_type=grant.scope_type, scope_id=grant.scope_id,
        )
        return grant

    async def _revoke(self, *, actor, grant, instant, trace_id):
        if (
            grant.status != "active" or grant.revoked_at is not None
            or grant.revoked_by_user_id is not None
            or (grant.expires_at is not None and grant.expires_at <= instant)
        ):
            raise ConstructionAccessGrantConflictError()
        revoked = await self.repository.revoke_if_effective(
            tenant_id=actor.tenant_id, grant_id=grant.id,
            revoked_by_user_id=actor.user_id, as_of=instant,
        )
        if revoked is None:
            raise ConstructionAccessGrantConflictError()
        await self.audit_logger.record_operation(
            actor=actor, event_type="construction_access_grant_revoked",
            entity_type="construction_access_grant", entity_id=revoked.id, operation="revoke",
            changes=(
                ConstructionAuditChange("status", "active", "revoked"),
                ConstructionAuditChange("revoked_by_user_id", None, str(actor.user_id)),
                ConstructionAuditChange("revoked_at", None, instant.isoformat()),
            ),
            details={"target_user_id": str(revoked.user_id), "role": revoked.role,
                     "scope_type": revoked.scope_type, "scope_id": str(revoked.scope_id)},
            trace_id=trace_id,
        )
        return revoked

    async def _abort(self, error):
        await self.session.rollback()
        if isinstance(error, (ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                              ConstructionAccessGrantConflictError, ValueError)):
            raise error
        if is_active_grant_overlap(error):
            raise ConstructionAccessGrantConflictError() from error
        raise ConstructionGrantOperationError("Construction access operation failed.") from error

    async def revoke_access(self, *, actor, access_context, grant_id, trace_id=None):
        try:
            grant = await self._original(actor=actor, access_context=access_context, grant_id=grant_id)
            if grant.status == "revoked":
                return grant
            return await self._revoke(actor=actor, grant=grant, instant=self._now(), trace_id=trace_id)
        except Exception as error:
            await self._abort(error)

    async def replace_access(
        self, *, actor, access_context, grant_id, role, scope_type, scope_id,
        expires_at=None, trace_id=None,
    ):
        try:
            grant = await self._original(actor=actor, access_context=access_context, grant_id=grant_id)
            role = role.strip().upper() if isinstance(role, str) else ""
            scope_type = scope_type.strip().lower() if isinstance(scope_type, str) else ""
            if not isinstance(scope_id, UUID):
                raise ValueError("Construction scope must be a UUID.")
            require_construction_role_scope(role=role, scope_type=scope_type)
            if scope_type == "document":
                raise ConstructionScopeDeniedError("Document scope requires the Document Center validator.")
            require_delegable_construction_scope(
                actor=actor, access_context=access_context, scope_type=scope_type, scope_id=scope_id,
            )
            require_delegable_construction_role(
                actor=actor, access_context=access_context, target_role=role,
                scope_type=scope_type, scope_id=scope_id,
            )
            if scope_type == "project":
                project = await self.project_repository.get_active_project_for_update(
                    tenant_id=actor.tenant_id, project_id=scope_id,
                )
                if (project is None or project.id != scope_id or project.tenant_id != actor.tenant_id
                        or project.deleted_at is not None):
                    raise ConstructionScopeDeniedError("Construction project is not available.")
            if not await self.repository.has_active_platform_membership(
                tenant_id=actor.tenant_id, user_id=grant.user_id,
            ):
                raise ConstructionPermissionDeniedError("Construction access target is not available.")
            current_access = await ConstructionAccessContextResolver(
                repository=self.repository, now_provider=self.now_provider,
            ).resolve(actor=actor)
            require_delegable_construction_scope(
                actor=actor, access_context=current_access,
                scope_type=grant.scope_type, scope_id=grant.scope_id,
            )
            require_delegable_construction_scope(
                actor=actor, access_context=current_access, scope_type=scope_type, scope_id=scope_id,
            )
            require_delegable_construction_role(
                actor=actor, access_context=current_access, target_role=role,
                scope_type=scope_type, scope_id=scope_id,
            )
            instant = self._now()
            if expires_at is not None:
                if (not isinstance(expires_at, datetime) or expires_at.tzinfo is None
                        or expires_at.utcoffset() is None or expires_at <= instant):
                    raise ValueError("Construction access expiration must be aware and future.")
                expires_at = expires_at.astimezone(timezone.utc)
            target_user_id = grant.user_id
            await self._revoke(actor=actor, grant=grant, instant=instant, trace_id=trace_id)
            replacement = await self.repository.create_grant(
                tenant_id=actor.tenant_id, user_id=target_user_id, role=role,
                scope_type=scope_type, scope_id=scope_id, expires_at=expires_at,
                granted_by_user_id=actor.user_id, granted_at=instant,
            )
            await self.audit_logger.record_operation(
                actor=actor, event_type="construction_access_grant_created",
                entity_type="construction_access_grant", entity_id=replacement.id, operation="create",
                details={"target_user_id": str(target_user_id), "role": role, "scope_type": scope_type,
                         "scope_id": str(scope_id), "expires_at": expires_at.isoformat() if expires_at else None,
                         "replaces_grant_id": str(grant_id)},
                trace_id=trace_id,
            )
            return replacement
        except Exception as error:
            await self._abort(error)


def build_construction_access_mutation_service(session):
    from database.repositories.construction import ConstructionProjectRepository
    from database.repositories.construction_access_mutations import ConstructionAccessMutationRepository
    from database.repositories.event import EventRepository
    from services.construction_membership_writers import SerializedConstructionAccessMutationService
    return SerializedConstructionAccessMutationService(
        session=session, repository=ConstructionAccessMutationRepository(session),
        event_repository=EventRepository(session), project_repository=ConstructionProjectRepository(session),
    )
