import inspect
from uuid import UUID

from services.construction_area_access import AREA_EDIT_PERMISSION, require_area_edit_project_scope
from services.construction_areas import (
    ConstructionProjectAreaService, ConstructionProjectAreaValidationError,
    ConstructionProjectAreaNotFoundError, ConstructionProjectAreaProjectNotFoundError,
    ConstructionProjectAreaOperationError,
)
from services.construction_foundation import ConstructionProjectTerminalError, ConstructionRowVersionConflictError
from services.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError, require_construction_access,
)

PUBLIC_AREA_PATCH_FIELDS = frozenset({"id", "tenant_id", "project_id", "name", "sort_order", "row_version"})


class ConstructionAreaPatchWorkflow:
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
            or not callable(getattr(area_service, "patch_area", None))
            or "manage_transaction" not in inspect.signature(area_service.patch_area).parameters
            or not callable(getattr(area_service.repository, "find_active_area_project_id", None))
        ):
            raise ValueError("Area patch workflow requires shared-session wiring.")
        self.session = session
        self.service = area_service

    @staticmethod
    def _response(area, *, actor, area_id, project_id, expected_row_version, fields):
        if (
            getattr(area, "id", None) != area_id
            or getattr(area, "tenant_id", None) != actor.tenant_id
            or getattr(area, "project_id", None) != project_id
            or getattr(area, "deleted_at", "missing") is not None
            or type(getattr(area, "row_version", None)) is not int
            or area.row_version not in (expected_row_version, expected_row_version + 1)
            or not isinstance(getattr(area, "name", None), str) or not area.name.strip()
            or (getattr(area, "sort_order", "missing") is not None and type(area.sort_order) is not int)
            or any(
                getattr(area, field) != (value.strip() if field == "name" else value)
                for field, value in fields.items()
            )
        ):
            raise ConstructionProjectAreaOperationError("Area patch response is invalid.")
        return {
            "id": str(area_id), "tenant_id": str(actor.tenant_id), "project_id": str(project_id),
            "name": area.name, "sort_order": area.sort_order, "row_version": area.row_version,
        }

    async def patch_area(
        self, *, actor, access_context, area_id, expected_row_version, fields, trace_id=None,
    ):
        try:
            allowed_projects = require_area_edit_project_scope(actor, access_context)
            if (
                not isinstance(area_id, UUID)
                or type(expected_row_version) is not int or expected_row_version <= 0
                or not isinstance(fields, dict) or set(fields) - {"name", "sort_order"}
                or ("name" in fields and (not isinstance(fields["name"], str) or not fields["name"].strip()))
                or ("sort_order" in fields and fields["sort_order"] is not None and type(fields["sort_order"]) is not int)
            ):
                raise ConstructionProjectAreaValidationError("Area patch request is invalid.")
            fields = dict(fields)
            if self.session.in_transaction() is not True:
                raise ConstructionProjectAreaOperationError("Area patch requires an active transaction.")
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
                permission_code=AREA_EDIT_PERMISSION, project_id=project_id,
            )
            area = await self.service.patch_area(
                actor=actor, access_context=access_context, project_id=project_id, area_id=area_id,
                expected_row_version=expected_row_version, fields=fields,
                trace_id=trace_id, manage_transaction=False,
            )
            response = self._response(
                area, actor=actor, area_id=area_id, project_id=project_id,
                expected_row_version=expected_row_version, fields=fields,
            )
            await self.session.commit()
            return response
        except Exception as error:
            await self.session.rollback()
            if isinstance(error, (
                ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionProjectAreaValidationError, ConstructionProjectAreaNotFoundError,
                ConstructionProjectAreaProjectNotFoundError, ConstructionProjectTerminalError,
                ConstructionRowVersionConflictError, ConstructionProjectAreaOperationError,
            )):
                raise
            raise ConstructionProjectAreaOperationError("Area patch workflow failed.") from None


def build_construction_area_patch_workflow(*, session):
    from database.repositories.construction import ConstructionProjectAreaRepository, ConstructionProjectRepository
    return ConstructionAreaPatchWorkflow(
        session=session,
        area_service=ConstructionProjectAreaService(
            session=session, repository=ConstructionProjectAreaRepository(session),
            project_repository=ConstructionProjectRepository(session),
        ),
    )
