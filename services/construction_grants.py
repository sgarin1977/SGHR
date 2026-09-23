from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from services.construction_audit import (
    ConstructionAuditChange,
    ConstructionAuditLogger,
)
from services.construction_permissions import (
    require_construction_access_manager,
    CONSTRUCTION_PERMISSION_CODES,
    CONSTRUCTION_ROLES,
    CONSTRUCTION_SCOPE_TYPES,
    ConstructionAccessContext,
    ConstructionGrantContext,
    ConstructionPermissionDeniedError,
    ConstructionRolePermissionPolicy,
    ConstructionScopeDeniedError,
    require_construction_role_scope,
    require_delegable_construction_role,
    require_delegable_construction_scope,
)


class ConstructionGrantOperationError(
    Exception
):
    pass


class ConstructionAccessContextResolver:
    def __init__(
        self,
        *,
        repository,
        role_policy=None,
        now_provider: Callable[
            [],
            datetime,
        ] | None = None,
    ) -> None:
        self.repository = repository
        self.role_policy = (
            role_policy
            if role_policy is not None
            else ConstructionRolePermissionPolicy()
        )
        self.now_provider = (
            now_provider
            or (
                lambda: datetime.now(
                    timezone.utc
                )
            )
        )

    async def resolve(
        self,
        *,
        actor,
    ) -> ConstructionAccessContext:
        grants = (
            await self.repository
            .list_active_grants(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                as_of=self.now_provider(),
            )
        )

        roles = tuple(
            sorted(
                {
                    grant.role
                    for grant in grants
                }
            )
        )

        if not set(roles).issubset(
            CONSTRUCTION_ROLES
        ):
            raise ConstructionGrantOperationError(
                "Construction access context "
                "is invalid."
            )

        grant_contexts = tuple(
            ConstructionGrantContext(
                role=grant.role,
                scope_type=grant.scope_type,
                scope_id=grant.scope_id,
            )
            for grant in grants
        )

        if any(
            grant.scope_type
            not in CONSTRUCTION_SCOPE_TYPES
            for grant in grant_contexts
        ):
            raise ConstructionGrantOperationError(
                "Construction access context "
                "is invalid."
            )

        try:
            for grant in grant_contexts:
                require_construction_role_scope(
                    role=grant.role,
                    scope_type=grant.scope_type,
                )
        except (
            ConstructionPermissionDeniedError,
            ConstructionScopeDeniedError,
        ) as exc:
            raise ConstructionGrantOperationError(
                "Construction access context "
                "is invalid."
            ) from exc

        permission_values = (
            await self.role_policy.list_permissions(
                roles=roles,
            )
        )
        permissions = tuple(
            sorted(
                set(permission_values)
            )
        )

        if not set(permissions).issubset(
            CONSTRUCTION_PERMISSION_CODES
        ):
            raise ConstructionGrantOperationError(
                "Construction role policy "
                "is invalid."
            )

        return ConstructionAccessContext(
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            roles=roles,
            permissions=permissions,
            grants=grant_contexts,
        )


class ConstructionAccessGrantService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository,
        event_repository,
        project_repository=None,
        now_provider: Callable[
            [],
            datetime,
        ] | None = None,
    ) -> None:
        self.session = session
        self.repository = repository
        self.project_repository = (
            project_repository
        )
        self.event_repository = event_repository
        self.audit_logger = ConstructionAuditLogger(
            event_repository=event_repository,
        )
        self.now_provider = (
            now_provider
            or (
                lambda: datetime.now(
                    timezone.utc
                )
            )
        )

    async def list_access(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        target_user_id: UUID,
        project_id: UUID | None = None,
    ):
        require_construction_access_manager(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
        )

        query = {
            "tenant_id": actor.tenant_id,
            "user_id": target_user_id,
            "as_of": self.now_provider(),
        }

        if project_id is not None:
            query.update(
                {
                    "scope_type": "project",
                    "scope_id": project_id,
                }
            )

        try:
            return (
                await self.repository
                .list_active_grants(
                    **query,
                )
            )
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionGrantOperationError(
                "Construction access operation "
                "failed."
            ) from exc

    async def revoke_access(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        grant_id: UUID,
        project_id: UUID | None = None,
        trace_id: str | None = None,
    ):
        revoked_at = self.now_provider()

        try:
            grant = (
                await self.repository
                .get_grant_for_update(
                    tenant_id=actor.tenant_id,
                    grant_id=grant_id,
                )
            )

            if grant is None:
                raise ConstructionPermissionDeniedError(
                    "Construction access grant "
                    "is not available."
                )

            require_delegable_construction_scope(
                actor=actor,
                access_context=access_context,
                scope_type=grant.scope_type,
                scope_id=grant.scope_id,
                project_id=project_id,
            )

            if grant.status == "revoked":
                await self.session.commit()
                return grant

            previous_status = grant.status
            previous_revoked_by_user_id = (
                str(grant.revoked_by_user_id)
                if getattr(
                    grant,
                    "revoked_by_user_id",
                    None,
                )
                is not None
                else None
            )
            previous_revoked_at = (
                grant.revoked_at.isoformat()
                if getattr(
                    grant,
                    "revoked_at",
                    None,
                )
                is not None
                else None
            )

            grant = await self.repository.revoke_grant(
                grant=grant,
                revoked_by_user_id=(
                    actor.user_id
                ),
                revoked_at=revoked_at,
            )

            await self.audit_logger.record_operation(
                actor=actor,
                event_type=(
                    "construction_access_grant_revoked"
                ),
                entity_type=(
                    "construction_access_grant"
                ),
                entity_id=grant.id,
                operation="revoke",
                changes=(
                    ConstructionAuditChange(
                        field="status",
                        old_value=previous_status,
                        new_value=grant.status,
                    ),
                    ConstructionAuditChange(
                        field=(
                            "revoked_by_user_id"
                        ),
                        old_value=(
                            previous_revoked_by_user_id
                        ),
                        new_value=str(
                            grant.revoked_by_user_id
                        ),
                    ),
                    ConstructionAuditChange(
                        field="revoked_at",
                        old_value=(
                            previous_revoked_at
                        ),
                        new_value=(
                            grant.revoked_at
                            .isoformat()
                        ),
                    ),
                ),
                details={
                    "target_user_id": str(
                        grant.user_id
                    ),
                    "role": grant.role,
                    "scope_type": (
                        grant.scope_type
                    ),
                    "scope_id": str(
                        grant.scope_id
                    ),
                },
                trace_id=trace_id,
            )

            await self.session.commit()
            return grant
        except (
            ConstructionPermissionDeniedError,
            ConstructionScopeDeniedError,
        ):
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionGrantOperationError(
                "Construction access operation "
                "failed."
            ) from exc

    async def grant_access(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        target_user_id: UUID,
        role: str,
        scope_type: str,
        scope_id: UUID,
        project_id: UUID | None = None,
        expires_at: datetime | None = None,
        trace_id: str | None = None,
    ):
        normalized_role = (
            role.strip().upper()
            if isinstance(role, str)
            else ""
        )
        normalized_scope_type = (
            scope_type.strip().lower()
            if isinstance(scope_type, str)
            else ""
        )
        granted_at = self.now_provider()

        if normalized_role not in CONSTRUCTION_ROLES:
            raise ConstructionPermissionDeniedError(
                "Construction role "
                "is not available."
            )

        if (
            normalized_scope_type
            not in CONSTRUCTION_SCOPE_TYPES
        ):
            raise ConstructionScopeDeniedError(
                "Construction resource scope "
                "is not available."
            )

        if normalized_scope_type == "document":
            raise ConstructionScopeDeniedError(
                "Construction document scope "
                "is not available before "
                "Document Center."
            )

        require_construction_role_scope(
            role=normalized_role,
            scope_type=normalized_scope_type,
        )

        if (
            expires_at is not None
            and (
                expires_at.tzinfo is None
                or expires_at <= granted_at
            )
        ):
            raise ValueError(
                "Construction access expiration "
                "must be timezone-aware and future."
            )

        require_delegable_construction_scope(
            actor=actor,
            access_context=access_context,
            scope_type=normalized_scope_type,
            scope_id=scope_id,
            project_id=project_id,
        )
        require_delegable_construction_role(
            actor=actor,
            access_context=access_context,
            target_role=normalized_role,
            scope_type=normalized_scope_type,
            scope_id=scope_id,
            project_id=project_id,
        )

        if normalized_scope_type == "project":
            if (
                project_id is not None
                and project_id != scope_id
            ):
                raise ConstructionScopeDeniedError(
                    "Construction project scope "
                    "is not available."
                )

            if self.project_repository is None:
                raise ConstructionScopeDeniedError(
                    "Construction project scope "
                    "cannot be verified."
                )

            project = (
                await self.project_repository
                .get_active_project(
                    tenant_id=actor.tenant_id,
                    project_id=scope_id,
                )
            )

            if (
                project is None
                or getattr(
                    project,
                    "id",
                    None,
                )
                != scope_id
                or getattr(
                    project,
                    "tenant_id",
                    None,
                )
                != actor.tenant_id
                or getattr(
                    project,
                    "deleted_at",
                    None,
                )
                is not None
            ):
                raise ConstructionScopeDeniedError(
                    "Construction project scope "
                    "is not available."
                )

        has_membership = (
            await self.repository
            .has_active_platform_membership(
                tenant_id=actor.tenant_id,
                user_id=target_user_id,
            )
        )
        if not has_membership:
            raise ConstructionPermissionDeniedError(
                "Construction access target "
                "is not available."
            )

        try:
            grant = await self.repository.create_grant(
                tenant_id=actor.tenant_id,
                user_id=target_user_id,
                role=normalized_role,
                scope_type=(
                    normalized_scope_type
                ),
                scope_id=scope_id,
                expires_at=expires_at,
                granted_by_user_id=(
                    actor.user_id
                ),
                granted_at=granted_at,
            )

            await self.audit_logger.record_operation(
                actor=actor,
                event_type=(
                    "construction_access_grant_created"
                ),
                entity_type=(
                    "construction_access_grant"
                ),
                entity_id=grant.id,
                operation="create",
                details={
                    "target_user_id": str(
                        target_user_id
                    ),
                    "role": normalized_role,
                    "scope_type": (
                        normalized_scope_type
                    ),
                    "scope_id": str(scope_id),
                    "expires_at": (
                        expires_at.isoformat()
                        if expires_at is not None
                        else None
                    ),
                },
                trace_id=trace_id,
            )

            await self.session.commit()
            return grant
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionGrantOperationError(
                "Construction access operation "
                "failed."
            ) from exc
