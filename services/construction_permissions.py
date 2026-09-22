from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

CONSTRUCTION_ROLES = frozenset(
    {
        "OWNER",
        "ADMIN",
        "SURVEYOR",
        "ESTIMATOR",
        "PROJECT_MANAGER",
        "EXECUTOR",
        "OBSERVER",
    }
)


CONSTRUCTION_SCOPE_TYPES = frozenset(
    {
        "tenant",
        "project",
        "document",
    }
)


CONSTRUCTION_TENANT_SCOPE_ROLES = frozenset(
    {
        "OWNER",
        "ADMIN",
    }
)


CONSTRUCTION_PERMISSION_CODES = frozenset(
    {
        "construction.projects.read",
        "construction.projects.create",
        "construction.projects.edit",
        "construction.measurements.read",
        "construction.measurements.create",
        "construction.measurements.edit",
        "construction.measurements.submit",
        "construction.measurements.verify",
        "construction.estimates.read",
        "construction.estimates.create",
        "construction.estimates.edit",
        "construction.estimates.submit",
        "construction.estimates.approve",
        "construction.pricing.read",
        "construction.pricing.edit",
        "construction.costs.read",
        "construction.costs.edit",
        "construction.materials.read",
        "construction.materials.edit",
        "construction.tax.read",
        "construction.tax.edit",
        "construction.documents.generate",
        "construction.exports.internal",
        "construction.exports.client",
        "construction.exports.procurement",
        "construction.team.manage",
        "construction.access.manage",
        "construction.logistics.read",
        "construction.logistics.edit",
        "construction.planning.read",
        "construction.planning.edit",
        "construction.planning.approve",
        "construction.workforce.read",
        "construction.workforce.assign",
        "construction.workforce.crews.read",
        "construction.workforce.crews.manage",
        "construction.workforce.cv.read",
        "construction.workforce.cv.import",
        "construction.workforce.profile.edit",
        "construction.workforce.profile.verify",
        "construction.execution.read",
        "construction.execution.update",
        "construction.project_documents.read",
        "construction.project_documents.manage",
        "construction.contracts.read",
        "construction.contracts.manage",
        "construction.contracts.activate",
    }
)


CONSTRUCTION_ROLE_PERMISSIONS = MappingProxyType(
    {
        "OWNER": CONSTRUCTION_PERMISSION_CODES,
        "ADMIN": (
            CONSTRUCTION_PERMISSION_CODES
            - frozenset(
                {
                    "construction.contracts.activate",
                }
            )
        ),
        "SURVEYOR": frozenset(
            {
                "construction.projects.read",
                "construction.measurements.read",
                "construction.measurements.create",
                "construction.measurements.edit",
                "construction.measurements.submit",
                "construction.materials.read",
                "construction.documents.generate",
                "construction.exports.internal",
                "construction.logistics.read",
                "construction.planning.read",
                "construction.execution.read",
                "construction.project_documents.read",
            }
        ),
        "ESTIMATOR": frozenset(
            {
                "construction.projects.read",
                "construction.measurements.read",
                "construction.estimates.read",
                "construction.estimates.create",
                "construction.estimates.edit",
                "construction.estimates.submit",
                "construction.pricing.read",
                "construction.costs.read",
                "construction.costs.edit",
                "construction.materials.read",
                "construction.materials.edit",
                "construction.tax.read",
                "construction.documents.generate",
                "construction.exports.internal",
                "construction.exports.client",
                "construction.exports.procurement",
                "construction.logistics.read",
                "construction.planning.read",
                "construction.workforce.read",
                "construction.project_documents.read",
                "construction.contracts.read",
            }
        ),
        "PROJECT_MANAGER": frozenset(
            {
                "construction.projects.read",
                "construction.projects.create",
                "construction.projects.edit",
                "construction.measurements.read",
                "construction.measurements.create",
                "construction.measurements.edit",
                "construction.measurements.submit",
                "construction.measurements.verify",
                "construction.estimates.read",
                "construction.estimates.create",
                "construction.estimates.edit",
                "construction.estimates.submit",
                "construction.estimates.approve",
                "construction.pricing.read",
                "construction.costs.read",
                "construction.costs.edit",
                "construction.materials.read",
                "construction.materials.edit",
                "construction.tax.read",
                "construction.documents.generate",
                "construction.exports.internal",
                "construction.exports.client",
                "construction.exports.procurement",
                "construction.team.manage",
                "construction.logistics.read",
                "construction.logistics.edit",
                "construction.planning.read",
                "construction.planning.edit",
                "construction.planning.approve",
                "construction.workforce.read",
                "construction.workforce.assign",
                "construction.workforce.crews.read",
                "construction.workforce.crews.manage",
                "construction.workforce.cv.read",
                "construction.execution.read",
                "construction.execution.update",
                "construction.project_documents.read",
                "construction.project_documents.manage",
                "construction.contracts.read",
                "construction.contracts.manage",
            }
        ),
        "EXECUTOR": frozenset(
            {
                "construction.projects.read",
                "construction.measurements.read",
                "construction.materials.read",
                "construction.logistics.read",
                "construction.planning.read",
                "construction.workforce.read",
                "construction.workforce.crews.read",
                "construction.execution.read",
                "construction.execution.update",
                "construction.project_documents.read",
            }
        ),
        "OBSERVER": frozenset(
            {
                "construction.projects.read",
                "construction.measurements.read",
                "construction.estimates.read",
                "construction.pricing.read",
                "construction.costs.read",
                "construction.materials.read",
                "construction.tax.read",
                "construction.logistics.read",
                "construction.planning.read",
                "construction.workforce.read",
                "construction.workforce.crews.read",
                "construction.execution.read",
                "construction.project_documents.read",
                "construction.contracts.read",
            }
        ),
    }
)


@dataclass(frozen=True)
class ConstructionGrantContext:
    role: str
    scope_type: str
    scope_id: UUID


@dataclass(frozen=True)
class ConstructionAccessContext:
    user_id: UUID
    tenant_id: UUID
    roles: tuple[str, ...]
    permissions: tuple[str, ...]
    grants: tuple[
        ConstructionGrantContext,
        ...,
    ]


class ConstructionPermissionDeniedError(
    Exception
):
    pass


class ConstructionScopeDeniedError(
    Exception
):
    pass


def require_construction_role_scope(
    *,
    role: str,
    scope_type: str,
) -> None:
    if role not in CONSTRUCTION_ROLES:
        raise ConstructionPermissionDeniedError(
            "Construction role is not available."
        )

    if scope_type not in CONSTRUCTION_SCOPE_TYPES:
        raise ConstructionScopeDeniedError(
            "Construction resource scope "
            "is not available."
        )

    if (
        scope_type == "tenant"
        and role
        not in CONSTRUCTION_TENANT_SCOPE_ROLES
    ):
        raise ConstructionScopeDeniedError(
            "Construction role is not allowed "
            "for tenant scope."
        )


class ConstructionRolePermissionPolicy:
    async def list_permissions(
        self,
        *,
        roles,
    ) -> tuple[str, ...]:
        normalized_roles = tuple(roles)

        if any(
            role
            not in CONSTRUCTION_ROLE_PERMISSIONS
            for role in normalized_roles
        ):
            raise ConstructionPermissionDeniedError(
                "Construction role is not allowed."
            )

        permissions: set[str] = set()

        for role in normalized_roles:
            permissions.update(
                CONSTRUCTION_ROLE_PERMISSIONS[
                    role
                ]
            )

        return tuple(
            sorted(permissions)
        )


def require_construction_access(
    *,
    actor,
    access_context: ConstructionAccessContext,
    permission_code: str,
    project_id: UUID | None = None,
    document_id: UUID | None = None,
) -> None:
    normalized_permission = (
        permission_code.strip().lower()
        if isinstance(permission_code, str)
        else ""
    )

    if (
        access_context.user_id
        != actor.user_id
        or access_context.tenant_id
        != actor.tenant_id
    ):
        raise ConstructionPermissionDeniedError(
            "Construction access context "
            "is not available."
        )

    if (
        normalized_permission
        not in CONSTRUCTION_PERMISSION_CODES
        or normalized_permission
        not in access_context.permissions
    ):
        raise ConstructionPermissionDeniedError(
            "Construction permission "
            "is not available."
        )

    active_roles = set(
        access_context.roles
    )

    if (
        not active_roles
        or not active_roles.issubset(
            CONSTRUCTION_ROLES
        )
    ):
        raise ConstructionPermissionDeniedError(
            "Construction role "
            "is not available."
        )

    permission_roles = {
        role
        for role in active_roles
        if normalized_permission
        in CONSTRUCTION_ROLE_PERMISSIONS.get(
            role,
            frozenset(),
        )
    }

    if not permission_roles:
        raise ConstructionPermissionDeniedError(
            "Construction permission "
            "is not available for active roles."
        )

    has_allowed_scope = any(
        grant.role in permission_roles
        and grant.scope_type
        in CONSTRUCTION_SCOPE_TYPES
        and (
            (
                grant.scope_type == "tenant"
                and grant.scope_id
                == access_context.tenant_id
            )
            or (
                project_id is not None
                and grant.scope_type
                == "project"
                and grant.scope_id
                == project_id
            )
            or (
                document_id is not None
                and grant.scope_type
                == "document"
                and grant.scope_id
                == document_id
            )
        )
        for grant in access_context.grants
    )

    if not has_allowed_scope:
        raise ConstructionScopeDeniedError(
            "Construction resource scope "
            "is not available."
        )


def require_construction_access_manager(
    *,
    actor,
    access_context: ConstructionAccessContext,
    project_id: UUID | None = None,
    document_id: UUID | None = None,
) -> None:
    require_construction_access(
        actor=actor,
        access_context=access_context,
        permission_code=(
            "construction.access.manage"
        ),
        project_id=project_id,
        document_id=document_id,
    )


def require_delegable_construction_scope(
    *,
    actor,
    access_context: ConstructionAccessContext,
    scope_type: str,
    scope_id: UUID,
    project_id: UUID | None = None,
) -> None:
    normalized_scope_type = (
        scope_type.strip().lower()
        if isinstance(scope_type, str)
        else ""
    )

    if (
        normalized_scope_type
        not in CONSTRUCTION_SCOPE_TYPES
    ):
        raise ConstructionScopeDeniedError(
            "Construction resource scope "
            "is not available."
        )

    if (
        normalized_scope_type == "tenant"
        and scope_id != actor.tenant_id
    ):
        raise ConstructionScopeDeniedError(
            "Construction resource scope "
            "is not available."
        )

    if normalized_scope_type == "tenant":
        require_construction_access_manager(
            actor=actor,
            access_context=access_context,
        )
        return

    if normalized_scope_type == "project":
        require_construction_access_manager(
            actor=actor,
            access_context=access_context,
            project_id=scope_id,
        )
        return

    require_construction_access_manager(
        actor=actor,
        access_context=access_context,
        project_id=project_id,
        document_id=scope_id,
    )


def require_delegable_construction_role(
    *,
    actor,
    access_context: ConstructionAccessContext,
    target_role: str,
    scope_type: str,
    scope_id: UUID,
    project_id: UUID | None = None,
) -> None:
    if (
        access_context.user_id != actor.user_id
        or access_context.tenant_id
        != actor.tenant_id
    ):
        raise ConstructionPermissionDeniedError(
            "Construction access context "
            "is not available."
        )

    target_permissions = (
        CONSTRUCTION_ROLE_PERMISSIONS.get(
            target_role
        )
    )
    if target_permissions is None:
        raise ConstructionPermissionDeniedError(
            "Construction role is not available."
        )

    if scope_type not in CONSTRUCTION_SCOPE_TYPES:
        raise ConstructionScopeDeniedError(
            "Construction resource scope "
            "is not available."
        )

    active_roles = set(
        access_context.roles
    )
    effective_permissions: set[str] = set()

    for grant in access_context.grants:
        if grant.role not in active_roles:
            continue

        role_permissions = (
            CONSTRUCTION_ROLE_PERMISSIONS.get(
                grant.role
            )
        )
        if role_permissions is None:
            continue

        applies_to_target = (
            (
                grant.scope_type == "tenant"
                and grant.scope_id
                == actor.tenant_id
            )
            or (
                scope_type == "project"
                and grant.scope_type
                == "project"
                and grant.scope_id
                == scope_id
            )
            or (
                scope_type == "document"
                and grant.scope_type
                == "document"
                and grant.scope_id
                == scope_id
            )
            or (
                scope_type == "document"
                and project_id is not None
                and grant.scope_type
                == "project"
                and grant.scope_id
                == project_id
            )
        )

        if applies_to_target:
            effective_permissions.update(
                role_permissions
            )

    if not set(target_permissions).issubset(
        effective_permissions
    ):
        raise ConstructionPermissionDeniedError(
            "Construction role cannot be "
            "delegated beyond actor permissions."
        )
