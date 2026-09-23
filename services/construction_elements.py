from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.event import (
    EventRepository,
)
from services.construction_audit import (
    ConstructionAuditChange,
    ConstructionAuditLogger,
)
from services.construction_foundation import (
    ConstructionProjectTerminalError,
    ConstructionRowVersionConflictError,
    require_construction_project_mutable,
    require_expected_row_version,
)
from services.construction_permissions import (
    ConstructionAccessContext,
    require_construction_access,
)


CONSTRUCTION_AREA_ELEMENT_TYPES = frozenset(
    {
        "WALL",
        "SLAB",
        "CEILING",
        "FLOOR",
        "WINDOW",
        "DOOR",
        "NICHE",
        "OPENING",
        "ENGINEERING_POINT",
        "LINEAR_ROUTE",
        "OTHER",
    }
)


CONSTRUCTION_PARENT_CAPABLE_ELEMENT_TYPES = (
    frozenset(
        {
            "WINDOW",
            "DOOR",
            "OPENING",
        }
    )
)


class ConstructionElementParentError(
    ValueError
):
    pass


def validate_construction_parent_element(
    *,
    element_type: str,
    tenant_id,
    project_area_id,
    parent_element,
):
    if parent_element is None:
        return None

    if (
        element_type
        not in CONSTRUCTION_PARENT_CAPABLE_ELEMENT_TYPES
    ):
        raise ConstructionElementParentError(
            "Construction element type "
            "cannot have a parent."
        )

    if (
        parent_element.tenant_id != tenant_id
        or parent_element.project_area_id
        != project_area_id
        or parent_element.element_type != "WALL"
    ):
        raise ConstructionElementParentError(
            "Construction parent element "
            "must be a wall in the same area."
        )

    return parent_element


class ConstructionElementGeometryError(
    ValueError
):
    pass


def validate_construction_geometry(
    geometry_json,
) -> dict:
    if not isinstance(geometry_json, dict):
        raise ConstructionElementGeometryError(
            "Construction element geometry "
            "must be a JSON object."
        )

    schema_version = geometry_json.get(
        "schema_version"
    )

    if (
        type(schema_version) is not int
        or schema_version != 1
    ):
        raise ConstructionElementGeometryError(
            "Construction element geometry "
            "schema version is invalid."
        )

    return geometry_json


def construction_json_equal(
    left,
    right,
) -> bool:
    if (
        isinstance(left, bool)
        or isinstance(right, bool)
    ):
        return (
            type(left) is type(right)
            and left == right
        )

    if (
        isinstance(left, dict)
        or isinstance(right, dict)
    ):
        if (
            not isinstance(left, dict)
            or not isinstance(right, dict)
            or left.keys() != right.keys()
        ):
            return False

        return all(
            construction_json_equal(
                left[key],
                right[key],
            )
            for key in left
        )

    if (
        isinstance(left, list)
        or isinstance(right, list)
    ):
        if (
            not isinstance(left, list)
            or not isinstance(right, list)
            or len(left) != len(right)
        ):
            return False

        return all(
            construction_json_equal(
                left_value,
                right_value,
            )
            for left_value, right_value in zip(
                left,
                right,
            )
        )

    return left == right


def _construction_element_field_equal(
    *,
    field: str,
    left,
    right,
) -> bool:
    if field == "geometry_json":
        return construction_json_equal(
            left,
            right,
        )

    return left == right


class ConstructionAreaElementValidationError(
    ValueError
):
    pass


class ConstructionAreaElementProjectNotFoundError(
    Exception
):
    pass


class ConstructionAreaElementAreaNotFoundError(
    Exception
):
    pass


class ConstructionAreaElementAreaConfirmedError(
    Exception
):
    pass


class ConstructionAreaElementNotFoundError(
    Exception
):
    pass


class ConstructionAreaElementParentNotFoundError(
    Exception
):
    pass


class ConstructionAreaElementHasActiveChildrenError(
    Exception
):
    pass


class ConstructionAreaElementOperationError(
    Exception
):
    pass


class ConstructionAreaElementService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository,
        area_repository,
        project_repository,
        event_repository=None,
        now_provider: Callable[
            [],
            datetime,
        ] | None = None,
    ) -> None:
        self.session = session
        self.repository = repository
        self.area_repository = area_repository
        self.project_repository = (
            project_repository
        )
        self.event_repository = (
            event_repository
            if event_repository is not None
            else EventRepository(session)
        )
        self.audit_logger = ConstructionAuditLogger(
            event_repository=(
                self.event_repository
            ),
        )
        self.now_provider = (
            now_provider
            or (
                lambda: datetime.now(
                    timezone.utc
                )
            )
        )

    async def _require_mutable_project_for_update(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
    ):
        project = (
            await self.project_repository
            .get_active_project_for_update(
                tenant_id=tenant_id,
                project_id=project_id,
            )
        )

        if project is None:
            raise (
                ConstructionAreaElementProjectNotFoundError(
                    "Construction project "
                    "is not available."
                )
            )

        require_construction_project_mutable(
            project_status=project.status,
        )
        return project

    async def create_element(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        project_id: UUID,
        project_area_id: UUID,
        parent_element_id: UUID | None,
        element_type: str,
        name: str,
        sort_order: int | None,
        geometry_json: dict,
        trace_id: str | None = None,
    ):
        require_construction_access(
            actor=actor,
            access_context=access_context,
            permission_code=(
                "construction.projects.create"
            ),
            project_id=project_id,
        )

        normalized_element_type = (
            element_type.strip().upper()
            if isinstance(element_type, str)
            else ""
        )
        normalized_name = (
            name.strip()
            if isinstance(name, str)
            else ""
        )

        if (
            normalized_element_type
            not in CONSTRUCTION_AREA_ELEMENT_TYPES
        ):
            raise ConstructionAreaElementValidationError(
                "Construction area element type "
                "is invalid."
            )

        if not normalized_name:
            raise ConstructionAreaElementValidationError(
                "Construction area element name "
                "is required."
            )

        if (
            sort_order is not None
            and (
                not isinstance(sort_order, int)
                or isinstance(sort_order, bool)
            )
        ):
            raise ConstructionAreaElementValidationError(
                "Construction area element sort "
                "order is invalid."
            )

        validated_geometry = (
            validate_construction_geometry(
                geometry_json
            )
        )

        if (
            parent_element_id is not None
            and normalized_element_type
            not in (
                CONSTRUCTION_PARENT_CAPABLE_ELEMENT_TYPES
            )
        ):
            raise ConstructionElementParentError(
                "Construction element type "
                "cannot have a parent."
            )

        try:
            await (
                self
                ._require_mutable_project_for_update(
                    tenant_id=actor.tenant_id,
                    project_id=project_id,
                )
            )

            area = (
                await self.area_repository
                .get_active_area_for_update(
                    tenant_id=actor.tenant_id,
                    project_id=project_id,
                    area_id=project_area_id,
                )
            )

            if area is None:
                raise (
                    ConstructionAreaElementAreaNotFoundError(
                        "Construction project area "
                        "is not available."
                    )
                )

            if area.status == "confirmed":
                raise (
                    ConstructionAreaElementAreaConfirmedError(
                        "Confirmed construction "
                        "project area must be "
                        "reopened before element "
                        "changes."
                    )
                )

            parent_element = None

            if parent_element_id is not None:
                parent_element = (
                    await self.repository
                    .get_active_parent_wall(
                        tenant_id=actor.tenant_id,
                        project_area_id=(
                            project_area_id
                        ),
                        parent_element_id=(
                            parent_element_id
                        ),
                    )
                )

                if parent_element is None:
                    raise (
                        ConstructionAreaElementParentNotFoundError(
                            "Construction parent "
                            "element is not available."
                        )
                    )

            validate_construction_parent_element(
                element_type=(
                    normalized_element_type
                ),
                tenant_id=actor.tenant_id,
                project_area_id=project_area_id,
                parent_element=parent_element,
            )

            element = (
                await self.repository
                .create_element(
                    tenant_id=actor.tenant_id,
                    project_area_id=(
                        project_area_id
                    ),
                    parent_element_id=(
                        parent_element_id
                    ),
                    element_type=(
                        normalized_element_type
                    ),
                    name=normalized_name,
                    sort_order=sort_order,
                    geometry_json=(
                        validated_geometry
                    ),
                    created_by=actor.user_id,
                )
            )

            await self.audit_logger.record_operation(
                actor=actor,
                event_type=(
                    "construction_area_element_created"
                ),
                entity_type=(
                    "construction_area_element"
                ),
                entity_id=element.id,
                operation="create",
                trace_id=trace_id,
            )

            await self.session.commit()
            return element
        except (
            ConstructionAreaElementProjectNotFoundError,
            ConstructionAreaElementAreaNotFoundError,
            ConstructionAreaElementAreaConfirmedError,
            ConstructionAreaElementParentNotFoundError,
            ConstructionElementParentError,
            ConstructionProjectTerminalError,
        ):
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionAreaElementOperationError(
                "Construction area element "
                "operation failed."
            ) from exc

    async def list_elements(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        project_id: UUID,
        project_area_id: UUID,
    ):
        require_construction_access(
            actor=actor,
            access_context=access_context,
            permission_code=(
                "construction.projects.read"
            ),
            project_id=project_id,
        )

        try:
            area = (
                await self.area_repository
                .get_active_area(
                    tenant_id=actor.tenant_id,
                    project_id=project_id,
                    area_id=project_area_id,
                )
            )

            if area is None:
                raise (
                    ConstructionAreaElementAreaNotFoundError(
                        "Construction project area "
                        "is not available."
                    )
                )

            return (
                await self.repository
                .list_active_elements(
                    tenant_id=actor.tenant_id,
                    project_area_id=(
                        project_area_id
                    ),
                )
            )
        except (
            ConstructionAreaElementAreaNotFoundError
        ):
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionAreaElementOperationError(
                "Construction area element "
                "operation failed."
            ) from exc

    async def get_element(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        project_id: UUID,
        project_area_id: UUID,
        element_id: UUID,
    ):
        require_construction_access(
            actor=actor,
            access_context=access_context,
            permission_code=(
                "construction.projects.read"
            ),
            project_id=project_id,
        )

        try:
            area = (
                await self.area_repository
                .get_active_area(
                    tenant_id=actor.tenant_id,
                    project_id=project_id,
                    area_id=project_area_id,
                )
            )

            if area is None:
                raise (
                    ConstructionAreaElementAreaNotFoundError(
                        "Construction project area "
                        "is not available."
                    )
                )

            element = (
                await self.repository
                .get_active_element(
                    tenant_id=actor.tenant_id,
                    project_area_id=(
                        project_area_id
                    ),
                    element_id=element_id,
                )
            )

            if element is None:
                raise (
                    ConstructionAreaElementNotFoundError(
                        "Construction area element "
                        "is not available."
                    )
                )

            return element
        except (
            ConstructionAreaElementAreaNotFoundError,
            ConstructionAreaElementNotFoundError,
        ):
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionAreaElementOperationError(
                "Construction area element "
                "operation failed."
            ) from exc

    async def update_element(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        project_id: UUID,
        project_area_id: UUID,
        element_id: UUID,
        expected_row_version: int,
        parent_element_id: UUID | None,
        name: str,
        sort_order: int | None,
        geometry_json: dict,
        trace_id: str | None = None,
    ):
        require_construction_access(
            actor=actor,
            access_context=access_context,
            permission_code=(
                "construction.projects.edit"
            ),
            project_id=project_id,
        )

        normalized_name = (
            name.strip()
            if isinstance(name, str)
            else ""
        )

        if not normalized_name:
            raise ConstructionAreaElementValidationError(
                "Construction area element name "
                "is required."
            )

        if (
            sort_order is not None
            and (
                not isinstance(sort_order, int)
                or isinstance(sort_order, bool)
            )
        ):
            raise ConstructionAreaElementValidationError(
                "Construction area element sort "
                "order is invalid."
            )

        validated_geometry = (
            validate_construction_geometry(
                geometry_json
            )
        )

        try:
            await (
                self
                ._require_mutable_project_for_update(
                    tenant_id=actor.tenant_id,
                    project_id=project_id,
                )
            )

            area = (
                await self.area_repository
                .get_active_area_for_update(
                    tenant_id=actor.tenant_id,
                    project_id=project_id,
                    area_id=project_area_id,
                )
            )

            if area is None:
                raise (
                    ConstructionAreaElementAreaNotFoundError(
                        "Construction project area "
                        "is not available."
                    )
                )

            if area.status == "confirmed":
                raise (
                    ConstructionAreaElementAreaConfirmedError(
                        "Confirmed construction "
                        "project area must be "
                        "reopened before element "
                        "changes."
                    )
                )

            element = (
                await self.repository
                .get_active_element_for_update(
                    tenant_id=actor.tenant_id,
                    project_area_id=(
                        project_area_id
                    ),
                    element_id=element_id,
                )
            )

            if element is None:
                raise (
                    ConstructionAreaElementNotFoundError(
                        "Construction area element "
                        "is not available."
                    )
                )

            require_expected_row_version(
                actual_row_version=(
                    element.row_version
                ),
                expected_row_version=(
                    expected_row_version
                ),
            )

            parent_element = None

            if parent_element_id is not None:
                if (
                    element.element_type
                    not in (
                        CONSTRUCTION_PARENT_CAPABLE_ELEMENT_TYPES
                    )
                ):
                    raise ConstructionElementParentError(
                        "Construction element type "
                        "cannot have a parent."
                    )

                parent_element = (
                    await self.repository
                    .get_active_parent_wall(
                        tenant_id=actor.tenant_id,
                        project_area_id=(
                            project_area_id
                        ),
                        parent_element_id=(
                            parent_element_id
                        ),
                    )
                )

                if parent_element is None:
                    raise (
                        ConstructionAreaElementParentNotFoundError(
                            "Construction parent "
                            "element is not available."
                        )
                    )

            validate_construction_parent_element(
                element_type=element.element_type,
                tenant_id=actor.tenant_id,
                project_area_id=project_area_id,
                parent_element=parent_element,
            )

            previous_values = {
                "name": element.name,
                "sort_order": (
                    element.sort_order
                ),
                "geometry_json": deepcopy(
                    element.geometry_json
                ),
                "parent_element_id": (
                    str(element.parent_element_id)
                    if element.parent_element_id
                    is not None
                    else None
                ),
            }

            requested_values = {
                "name": normalized_name,
                "sort_order": sort_order,
                "geometry_json": deepcopy(
                    validated_geometry
                ),
                "parent_element_id": (
                    str(parent_element_id)
                    if parent_element_id
                    is not None
                    else None
                ),
            }

            compared_fields = (
                "name",
                "sort_order",
                "geometry_json",
                "parent_element_id",
            )

            if all(
                _construction_element_field_equal(
                    field=field,
                    left=previous_values[field],
                    right=requested_values[field],
                )
                for field in compared_fields
            ):
                await self.session.commit()
                return element

            element = (
                await self.repository
                .update_element(
                    element=element,
                    name=normalized_name,
                    sort_order=sort_order,
                    geometry_json=(
                        validated_geometry
                    ),
                    parent_element_id=(
                        parent_element_id
                    ),
                )
            )

            current_values = {
                "name": element.name,
                "sort_order": (
                    element.sort_order
                ),
                "geometry_json": deepcopy(
                    element.geometry_json
                ),
                "parent_element_id": (
                    str(element.parent_element_id)
                    if element.parent_element_id
                    is not None
                    else None
                ),
            }

            changes = tuple(
                ConstructionAuditChange(
                    field=field,
                    old_value=(
                        previous_values[field]
                    ),
                    new_value=(
                        current_values[field]
                    ),
                )
                for field in compared_fields
                if not (
                    _construction_element_field_equal(
                        field=field,
                        left=previous_values[field],
                        right=current_values[field],
                    )
                )
            )

            await self.audit_logger.record_operation(
                actor=actor,
                event_type=(
                    "construction_area_element_updated"
                ),
                entity_type=(
                    "construction_area_element"
                ),
                entity_id=element.id,
                operation="update",
                changes=changes,
                trace_id=trace_id,
            )

            await self.session.commit()
            return element
        except (
            ConstructionAreaElementProjectNotFoundError,
            ConstructionAreaElementAreaNotFoundError,
            ConstructionAreaElementAreaConfirmedError,
            ConstructionAreaElementNotFoundError,
            ConstructionAreaElementParentNotFoundError,
            ConstructionElementParentError,
            ConstructionProjectTerminalError,
            ConstructionRowVersionConflictError,
        ):
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionAreaElementOperationError(
                "Construction area element "
                "operation failed."
            ) from exc

    async def archive_element(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        project_id: UUID,
        project_area_id: UUID,
        element_id: UUID,
        expected_row_version: int,
        trace_id: str | None = None,
    ):
        require_construction_access(
            actor=actor,
            access_context=access_context,
            permission_code=(
                "construction.projects.edit"
            ),
            project_id=project_id,
        )

        try:
            await self._require_mutable_project_for_update(
                tenant_id=actor.tenant_id,
                project_id=project_id,
            )

            area = (
                await self.area_repository
                .get_active_area_for_update(
                    tenant_id=actor.tenant_id,
                    project_id=project_id,
                    area_id=project_area_id,
                )
            )

            if area is None:
                raise (
                    ConstructionAreaElementAreaNotFoundError(
                        "Construction project area "
                        "is not available."
                    )
                )

            if area.status == "confirmed":
                raise (
                    ConstructionAreaElementAreaConfirmedError(
                        "Confirmed construction project "
                        "area must be reopened first."
                    )
                )

            element = (
                await self.repository
                .get_active_element_for_update(
                    tenant_id=actor.tenant_id,
                    project_area_id=(
                        project_area_id
                    ),
                    element_id=element_id,
                )
            )

            if element is None:
                raise (
                    ConstructionAreaElementNotFoundError(
                        "Construction area element "
                        "is not available."
                    )
                )

            require_expected_row_version(
                actual_row_version=(
                    element.row_version
                ),
                expected_row_version=(
                    expected_row_version
                ),
            )

            if element.element_type == "WALL":
                has_active_children = (
                    await self.repository
                    .has_active_wall_children(
                        tenant_id=actor.tenant_id,
                        project_area_id=(
                            project_area_id
                        ),
                        wall_element_id=element.id,
                    )
                )

                if has_active_children:
                    raise (
                        ConstructionAreaElementHasActiveChildrenError(
                            "Construction wall has "
                            "active child elements."
                        )
                    )

            previous_deleted_at = (
                element.deleted_at.isoformat()
                if element.deleted_at is not None
                else None
            )

            element = (
                await self.repository
                .soft_delete_element(
                    element=element,
                    deleted_at=(
                        self.now_provider()
                    ),
                )
            )

            await self.audit_logger.record_operation(
                actor=actor,
                event_type=(
                    "construction_area_element_archived"
                ),
                entity_type=(
                    "construction_area_element"
                ),
                entity_id=element.id,
                operation="archive",
                changes=(
                    ConstructionAuditChange(
                        field="deleted_at",
                        old_value=(
                            previous_deleted_at
                        ),
                        new_value=(
                            element.deleted_at
                            .isoformat()
                        ),
                    ),
                ),
                trace_id=trace_id,
            )

            await self.session.commit()
            return element
        except (
            ConstructionAreaElementProjectNotFoundError,
            ConstructionAreaElementAreaNotFoundError,
            ConstructionAreaElementAreaConfirmedError,
            ConstructionAreaElementNotFoundError,
            ConstructionAreaElementHasActiveChildrenError,
            ConstructionProjectTerminalError,
            ConstructionRowVersionConflictError,
        ):
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionAreaElementOperationError(
                "Construction area element "
                "operation failed."
            ) from exc
