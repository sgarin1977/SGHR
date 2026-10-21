import inspect
from datetime import datetime, timezone
from uuid import UUID

from services.construction_areas import (
    ConstructionProjectAreaService, ConstructionProjectAreaValidationError,
    ConstructionProjectAreaNotFoundError, ConstructionProjectAreaProjectNotFoundError,
    ConstructionProjectAreaTransitionError, ConstructionProjectAreaHasActiveElementsError,
    ConstructionProjectAreaOperationError,
)
from services.construction_foundation import ConstructionProjectTerminalError, ConstructionRowVersionConflictError
from services.construction_permissions import (
    CONSTRUCTION_ROLES, CONSTRUCTION_ROLE_PERMISSIONS,
    ConstructionAccessContext, ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
    require_construction_access, require_construction_role_scope,
)

AREA_ARCHIVE_PERMISSION = "construction.projects.edit"
PUBLIC_AREA_ARCHIVE_FIELDS = frozenset({"id", "tenant_id", "project_id", "deleted_at", "row_version"})


class ConstructionAreaArchiveWorkflow:
    def __init__(self, *, session, area_service):
        collaborators = (
            area_service, getattr(area_service, "repository", None),
            getattr(area_service, "project_repository", None),
            getattr(getattr(area_service, "audit_logger", None), "event_repository", None),
        )
        if (
            session is None
            or any(getattr(item, "session", None) is not session for item in collaborators)
            or any(not callable(getattr(session, name, None)) for name in ("commit", "rollback", "in_transaction"))
            or not callable(getattr(area_service, "archive_area", None))
            or "manage_transaction" not in inspect.signature(area_service.archive_area).parameters
            or not callable(getattr(area_service.repository, "find_active_area_project_id", None))
        ):
            raise ValueError("Area archive workflow requires shared-session wiring.")
        self.session = session
        self.service = area_service

    @staticmethod
    def _allowed_projects(actor, context):
        from services.construction_area_access import require_area_edit_project_scope
        return require_area_edit_project_scope(actor, context)

    @staticmethod
    def _response(area, *, actor, area_id, project_id, expected_row_version):
        deleted_at = getattr(area, "deleted_at", None)
        if (
            getattr(area, "id", None) != area_id
            or getattr(area, "tenant_id", None) != actor.tenant_id
            or getattr(area, "project_id", None) != project_id
            or type(getattr(area, "row_version", None)) is not int
            or area.row_version != expected_row_version + 1
            or not isinstance(deleted_at, datetime)
            or deleted_at.tzinfo is None or deleted_at.utcoffset() is None
        ):
            raise ConstructionProjectAreaOperationError("Archived area response is invalid.")
        return {
            "id": str(area_id), "tenant_id": str(actor.tenant_id), "project_id": str(project_id),
            "deleted_at": deleted_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "row_version": area.row_version,
        }

    async def archive_area(self, *, actor, access_context, area_id, expected_row_version, trace_id=None):
        try:
            allowed_projects = self._allowed_projects(actor, access_context)
            if not isinstance(area_id, UUID) or type(expected_row_version) is not int or expected_row_version <= 0:
                raise ConstructionProjectAreaValidationError("Area archive request is invalid.")
            if self.session.in_transaction() is not True:
                raise ConstructionProjectAreaOperationError("Area archive requires an active transaction.")
            project_id = await self.service.repository.find_active_area_project_id(
                tenant_id=actor.tenant_id, area_id=area_id, allowed_project_ids=allowed_projects,
            )
            if project_id is None or (
                allowed_projects is not None and project_id not in allowed_projects
            ):
                raise ConstructionProjectAreaNotFoundError("Area is not available.")
            if not isinstance(project_id, UUID):
                raise ConstructionProjectAreaOperationError("Area project context is invalid.")
            require_construction_access(
                actor=actor, access_context=access_context,
                permission_code=AREA_ARCHIVE_PERMISSION, project_id=project_id,
            )
            area = await self.service.archive_area(
                actor=actor, access_context=access_context, project_id=project_id, area_id=area_id,
                expected_row_version=expected_row_version, trace_id=trace_id, manage_transaction=False,
            )
            response = self._response(
                area, actor=actor, area_id=area_id, project_id=project_id,
                expected_row_version=expected_row_version,
            )
            await self.session.commit()
            return response
        except Exception as error:
            await self.session.rollback()
            if isinstance(error, (
                ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionProjectAreaValidationError, ConstructionProjectAreaNotFoundError,
                ConstructionProjectAreaProjectNotFoundError, ConstructionProjectAreaTransitionError,
                ConstructionProjectAreaHasActiveElementsError, ConstructionProjectTerminalError,
                ConstructionRowVersionConflictError, ConstructionProjectAreaOperationError,
            )):
                raise
            raise ConstructionProjectAreaOperationError("Area archive workflow failed.") from None


def build_construction_area_archive_workflow(*, session):
    from database.repositories.construction import ConstructionProjectAreaRepository, ConstructionProjectRepository
    return ConstructionAreaArchiveWorkflow(
        session=session,
        area_service=ConstructionProjectAreaService(
            session=session, repository=ConstructionProjectAreaRepository(session),
            project_repository=ConstructionProjectRepository(session),
        ),
    )
