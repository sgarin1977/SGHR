from uuid import UUID

from services.construction_permissions import (
    ConstructionAccessContext,
    ConstructionPermissionDeniedError,
    require_construction_access,
)
from services.construction_projects import (
    ConstructionProjectNotFoundError,
    ConstructionProjectOperationError,
    ConstructionProjectValidationError,
)


class ConstructionProjectReadService:
    """Read an undeleted project after checking tenant identity and resource scope."""

    def __init__(self, *, repository):
        if not callable(getattr(repository, "get_active_project", None)):
            raise ValueError("Project read repository is required.")
        self.repository = repository

    async def get_project(self, *, actor, access_context, project_id):
        if not isinstance(project_id, UUID):
            raise ConstructionProjectValidationError("Project identifier must be a UUID.")
        if (
            getattr(actor, "status", None) != "active"
            or not isinstance(getattr(actor, "user_id", None), UUID)
            or not isinstance(getattr(actor, "tenant_id", None), UUID)
            or not isinstance(access_context, ConstructionAccessContext)
        ):
            raise ConstructionPermissionDeniedError("Construction project read is not available.")
        require_construction_access(
            actor=actor, access_context=access_context,
            permission_code="construction.projects.read", project_id=project_id,
        )
        try:
            project = await self.repository.get_active_project(
                tenant_id=actor.tenant_id, project_id=project_id,
            )
            if (
                project is None
                or getattr(project, "id", None) != project_id
                or getattr(project, "tenant_id", None) != actor.tenant_id
                or getattr(project, "deleted_at", object()) is not None
            ):
                raise ConstructionProjectNotFoundError("Construction project is not available.")
            return project
        except ConstructionProjectNotFoundError:
            raise
        except Exception as exc:
            raise ConstructionProjectOperationError("Construction project read failed.") from exc


def build_construction_project_read_service(session):
    from database.repositories.construction import ConstructionProjectRepository

    return ConstructionProjectReadService(repository=ConstructionProjectRepository(session))
