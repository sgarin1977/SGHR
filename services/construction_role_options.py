from uuid import UUID

from services.construction_permissions import (
    CONSTRUCTION_ROLES, ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
    ConstructionRolePermissionPolicy, require_construction_role_scope,
    require_delegable_construction_scope, require_delegable_construction_role,
)

class ConstructionRoleOptionsError(Exception):
    pass

class ConstructionRoleOptionsService:
    def __init__(self, *, session, project_repository):
        if (
            session is None or getattr(project_repository, "session", None) is not session
            or not callable(getattr(project_repository, "get_active_project", None))
            or not callable(getattr(session, "in_transaction", None))
        ):
            raise ValueError("Role options require a shared Construction session.")
        self.session = session
        self.project_repository = project_repository
        self.policy = ConstructionRolePermissionPolicy()

    async def list_options(self, *, actor, access_context, scope_type, scope_id):
        if (
            getattr(actor, "status", None) != "active"
            or not isinstance(getattr(actor, "user_id", None), UUID)
            or not isinstance(getattr(actor, "tenant_id", None), UUID)
        ):
            raise ConstructionPermissionDeniedError("Construction actor is not available.")
        if scope_type not in ("tenant", "project") or not isinstance(scope_id, UUID):
            raise ConstructionScopeDeniedError("Construction scope is not available.")
        require_delegable_construction_scope(
            actor=actor, access_context=access_context, scope_type=scope_type, scope_id=scope_id,
            project_id=scope_id if scope_type == "project" else None,
        )
        try:
            if not self.session.in_transaction():
                raise ConstructionRoleOptionsError("Construction transaction is not available.")
            if scope_type == "project":
                project = await self.project_repository.get_active_project(
                    tenant_id=actor.tenant_id, project_id=scope_id,
                )
                if (
                    project is None or getattr(project, "id", None) != scope_id
                    or getattr(project, "tenant_id", None) != actor.tenant_id
                    or getattr(project, "deleted_at", None) is not None
                ):
                    raise ConstructionScopeDeniedError("Construction project is not available.")
            roles = []
            for role in sorted(CONSTRUCTION_ROLES):
                try:
                    require_construction_role_scope(role=role, scope_type=scope_type)
                    require_delegable_construction_role(
                        actor=actor, access_context=access_context, target_role=role,
                        scope_type=scope_type, scope_id=scope_id,
                        project_id=scope_id if scope_type == "project" else None,
                    )
                except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
                    continue
                permissions = await self.policy.list_permissions(roles=(role,))
                roles.append({"role": role, "permissions": list(permissions)})
            return {
                "scope_type": scope_type, "scope_id": str(scope_id),
                "roles": roles, "permissions_read_only": True,
            }
        except (ConstructionRoleOptionsError, ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
            raise
        except Exception:
            raise ConstructionRoleOptionsError("Construction role options failed.") from None

def build_construction_role_options_service(*, session):
    from database.repositories.construction import ConstructionProjectRepository
    return ConstructionRoleOptionsService(
        session=session, project_repository=ConstructionProjectRepository(session),
    )
