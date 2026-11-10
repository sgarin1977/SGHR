"""Authorized reads of universal project areas, independent of Measurement."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from services.construction.construction_areas import (
    ConstructionProjectAreaOperationError,
    ConstructionProjectAreaProjectNotFoundError,
    ConstructionProjectAreaValidationError,
)
from services.construction.construction_grants import ConstructionAccessContextResolver
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError, require_construction_access,
)


class ProjectAreaReadItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    project_id: UUID
    area_type: Literal["ROOM", "PROJECT_GENERAL", "EXTERIOR", "OTHER"]
    name: StrictStr = Field(min_length=1)
    sort_order: StrictInt | None = Field(ge=-2147483648, le=2147483647)
    status: Literal["draft", "measured", "confirmed"]
    row_version: StrictInt = Field(ge=1)

    @field_validator("name")
    @classmethod
    def require_name(cls, value):
        if not value.strip():
            raise ValueError("Area name is required.")
        return value


class ConstructionProjectAreaReadService:
    def __init__(self, *, repository, project_repository, access_repository):
        collaborators = (repository, project_repository, access_repository)
        session = getattr(repository, "session", None)
        required = (
            (repository, "list_active_areas"),
            (project_repository, "get_active_project"),
            (access_repository, "has_active_platform_membership"),
            (access_repository, "list_active_grants"),
        )
        if (
            session is None
            or any(getattr(item, "session", None) is not session for item in collaborators)
            or any(not callable(getattr(item, method, None)) for item, method in required)
        ):
            raise ValueError("Area reads require shared-session repositories.")
        self.session = session
        self.repository = repository
        self.projects = project_repository
        self.access = access_repository
        self.resolver = ConstructionAccessContextResolver(repository=access_repository)

    @staticmethod
    def _authorize(actor, context, project_id):
        if (
            getattr(actor, "status", None) != "active"
            or not isinstance(getattr(actor, "user_id", None), UUID)
            or not isinstance(getattr(actor, "tenant_id", None), UUID)
            or not isinstance(context, ConstructionAccessContext)
        ):
            raise ConstructionPermissionDeniedError("Project areas are unavailable.")
        require_construction_access(
            actor=actor, access_context=context,
            permission_code="construction.projects.read",
            project_id=project_id,
        )

    async def _require_membership(self, actor):
        if await self.access.has_active_platform_membership(
            tenant_id=actor.tenant_id, user_id=actor.user_id,
        ) is not True:
            raise ConstructionPermissionDeniedError("Project areas are unavailable.")

    async def list_areas(self, *, actor, access_context, project_id, include_capabilities=False):
        if type(include_capabilities) is not bool:
            raise ValueError("Capability mode must be boolean.")
        if not isinstance(project_id, UUID):
            raise ConstructionProjectAreaValidationError("A project UUID is required.")
        self._authorize(actor, access_context, project_id)
        try:
            await self._require_membership(actor)
            project = await self.projects.get_active_project(
                tenant_id=actor.tenant_id, project_id=project_id,
            )
            if (
                project is None or getattr(project, "id", None) != project_id
                or getattr(project, "tenant_id", None) != actor.tenant_id
                or getattr(project, "deleted_at", object()) is not None
            ):
                raise ConstructionProjectAreaProjectNotFoundError("Project is unavailable.")

            # Do not treat a previously supplied access context as lasting
            # authority after the parent lookup.
            await self._require_membership(actor)
            fresh_context = await self.resolver.resolve(actor=actor)
            self._authorize(actor, fresh_context, project_id)
            rows = await self.repository.list_active_areas(
                tenant_id=actor.tenant_id, project_id=project_id,
            )
            if not isinstance(rows, list):
                raise ConstructionProjectAreaOperationError("Area list is invalid.")

            result, seen = [], set()
            for row in rows:
                if (
                    not isinstance(getattr(row, "id", None), UUID)
                    or row.id in seen
                    or getattr(row, "tenant_id", None) != actor.tenant_id
                    or getattr(row, "project_id", None) != project_id
                    or getattr(row, "deleted_at", object()) is not None
                ):
                    raise ConstructionProjectAreaOperationError("Area response context differs.")
                seen.add(row.id)
                result.append(ProjectAreaReadItem.model_validate({
                    field: getattr(row, field)
                    for field in ProjectAreaReadItem.model_fields
                }).model_dump(mode="json"))
            # Keep the canonical repository order:
            # sort_order ASC NULLS LAST, created_at ASC, id ASC.
            if include_capabilities:
                return {
                    "items": result,
                    **project_area_capabilities(
                        actor=actor, access_context=fresh_context,
                        project_id=project_id, project_status=project.status),
                }
            return result
        except (
            ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
            ConstructionProjectAreaProjectNotFoundError,
            ConstructionProjectAreaValidationError, ConstructionProjectAreaOperationError,
        ):
            raise
        except Exception:
            raise ConstructionProjectAreaOperationError("Project area read failed.") from None


def build_construction_project_area_read_service(*, session):
    from database.repositories.construction import (
        ConstructionAccessRepository, ConstructionProjectAreaRepository,
        ConstructionProjectRepository,
    )
    return ConstructionProjectAreaReadService(
        repository=ConstructionProjectAreaRepository(session),
        project_repository=ConstructionProjectRepository(session),
        access_repository=ConstructionAccessRepository(session),
    )

# universal_area_editor_v1


def project_area_capabilities(*, actor, access_context, project_id, project_status):
    from services.construction.construction_foundation import (
        ConstructionProjectTerminalError, require_construction_project_mutable,
    )
    can_create = True
    try:
        require_construction_access(
            actor=actor, access_context=access_context,
            permission_code="construction.projects.create", project_id=project_id)
        require_construction_project_mutable(project_status=project_status)
    except (
        ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
        ConstructionProjectTerminalError,
    ):
        can_create = False
    can_edit = True
    try:
        from services.construction.construction_area_access import require_area_edit_project_scope
        allowed_projects = require_area_edit_project_scope(actor, access_context)
        if allowed_projects is not None and project_id not in allowed_projects:
            raise ConstructionScopeDeniedError("Area edit scope is unavailable.")
        require_construction_access(
            actor=actor, access_context=access_context,
            permission_code="construction.projects.edit", project_id=project_id)
        require_construction_project_mutable(project_status=project_status)
    except (
        ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
        ConstructionProjectTerminalError,
    ):
        can_edit = False
    return {"can_create_area": can_create, "can_edit_area": can_edit}

# CON_020B_AREA_LOCALIZED_READ_V1
