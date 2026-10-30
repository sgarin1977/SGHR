from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.event import (
    EventRepository,
)
from services.construction.construction_audit import (
    ConstructionAuditChange,
    ConstructionAuditLogger,
)
from services.construction.construction_foundation import (
    ConstructionProjectTerminalError,
    ConstructionRowVersionConflictError,
    require_construction_project_mutable,
    require_expected_row_version,
)
from services.construction.construction_permissions import (
    ConstructionAccessContext,
    require_construction_access,
)


CONSTRUCTION_PROJECT_AREA_TYPES = frozenset(
    {
        "ROOM",
        "PROJECT_GENERAL",
        "EXTERIOR",
        "OTHER",
    }
)


CONSTRUCTION_PROJECT_AREA_STATUS_TRANSITIONS = {
    "draft": frozenset(
        {
            "measured",
        }
    ),
    "measured": frozenset(
        {
            "draft",
            "confirmed",
        }
    ),
    "confirmed": frozenset(),
}


class ConstructionProjectAreaTransitionError(
    Exception
):
    pass


def require_project_area_status_transition(
    *,
    current_status: str,
    next_status: str,
    reopening: bool = False,
) -> None:
    if (
        current_status == "confirmed"
        and next_status == "draft"
        and reopening is True
    ):
        return

    allowed_statuses = (
        CONSTRUCTION_PROJECT_AREA_STATUS_TRANSITIONS
        .get(current_status)
    )

    if (
        allowed_statuses is None
        or next_status not in allowed_statuses
    ):
        raise ConstructionProjectAreaTransitionError(
            "Construction project area status "
            "transition is not allowed."
        )


class ConstructionProjectAreaValidationError(
    ValueError
):
    pass


class ConstructionProjectAreaProjectNotFoundError(
    Exception
):
    pass


class ConstructionProjectAreaNotFoundError(
    Exception
):
    pass


class ConstructionProjectAreaHasActiveElementsError(
    Exception
):
    pass


class ConstructionProjectAreaOperationError(
    Exception
):
    pass


class ConstructionProjectAreaService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository,
        project_repository,
        event_repository=None,
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
                ConstructionProjectAreaProjectNotFoundError(
                    "Construction project "
                    "is not available."
                )
            )

        require_construction_project_mutable(
            project_status=project.status,
        )
        return project

    async def create_area(
        self, *, actor, access_context: ConstructionAccessContext,
        project_id: UUID, area_type: str, name: str,
        sort_order: int | None, trace_id: str | None = None,
        manage_transaction: bool = True,
    ):
        if type(manage_transaction) is not bool:
            raise ConstructionProjectAreaValidationError("Transaction mode must be a boolean.")
        if not manage_transaction:
            collaborators = (
                self.repository, self.project_repository,
                self.audit_logger.event_repository,
            )
            if (
                not callable(getattr(self.session, "in_transaction", None))
                or self.session.in_transaction() is not True
                or any(getattr(item, "session", None) is not self.session for item in collaborators)
            ):
                raise ConstructionProjectAreaOperationError(
                    "Area creation requires one active caller transaction."
                )
        require_construction_access(
            actor=actor, access_context=access_context,
            permission_code="construction.projects.create", project_id=project_id,
        )
        normalized_area_type = area_type.strip().upper() if isinstance(area_type, str) else ""
        normalized_name = name.strip() if isinstance(name, str) else ""
        if normalized_area_type not in CONSTRUCTION_PROJECT_AREA_TYPES:
            raise ConstructionProjectAreaValidationError("Construction project area type is invalid.")
        if not normalized_name:
            raise ConstructionProjectAreaValidationError("Construction project area name is required.")
        if sort_order is not None and type(sort_order) is not int:
            raise ConstructionProjectAreaValidationError("Construction project area sort order is invalid.")
        try:
            await self._require_mutable_project_for_update(
                tenant_id=actor.tenant_id, project_id=project_id,
            )
            area = await self.repository.create_area(
                tenant_id=actor.tenant_id, project_id=project_id,
                area_type=normalized_area_type, name=normalized_name,
                sort_order=sort_order, created_by=actor.user_id,
            )
            await self.audit_logger.record_operation(
                actor=actor, event_type="construction_project_area_created",
                entity_type="construction_project_area", entity_id=area.id,
                operation="create", trace_id=trace_id,
            )
            if manage_transaction:
                await self.session.commit()
            return area
        except BaseException as error:
            if manage_transaction:
                await self.session.rollback()
            if not isinstance(error, Exception) or isinstance(error, (
                ConstructionProjectAreaProjectNotFoundError,
                ConstructionProjectTerminalError,
            )):
                raise
            raise ConstructionProjectAreaOperationError(
                "Construction project area operation failed."
            ) from error

    async def reopen_area(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        project_id: UUID,
        area_id: UUID,
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
            await (
                self
                ._require_mutable_project_for_update(
                    tenant_id=actor.tenant_id,
                    project_id=project_id,
                )
            )

            area = (
                await self.repository
                .get_active_area_for_update(
                    tenant_id=actor.tenant_id,
                    project_id=project_id,
                    area_id=area_id,
                )
            )

            if area is None:
                raise ConstructionProjectAreaNotFoundError(
                    "Construction project area "
                    "is not available."
                )

            require_expected_row_version(
                actual_row_version=(
                    area.row_version
                ),
                expected_row_version=(
                    expected_row_version
                ),
            )

            previous_status = area.status

            require_project_area_status_transition(
                current_status=previous_status,
                next_status="draft",
                reopening=True,
            )

            area = (
                await self.repository
                .set_area_status(
                    area=area,
                    status="draft",
                )
            )

            await self.audit_logger.record_operation(
                actor=actor,
                event_type=(
                    "construction_project_area_reopened"
                ),
                entity_type=(
                    "construction_project_area"
                ),
                entity_id=area.id,
                operation="reopen",
                changes=(
                    ConstructionAuditChange(
                        field="status",
                        old_value=previous_status,
                        new_value=area.status,
                    ),
                ),
                trace_id=trace_id,
            )

            await self.session.commit()
            return area
        except (
            ConstructionProjectAreaProjectNotFoundError,
            ConstructionProjectAreaNotFoundError,
            ConstructionProjectAreaTransitionError,
            ConstructionProjectTerminalError,
            ConstructionRowVersionConflictError,
        ):
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionProjectAreaOperationError(
                "Construction project area "
                "operation failed."
            ) from exc

    async def transition_area_status(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        project_id: UUID,
        area_id: UUID,
        expected_row_version: int,
        next_status: str,
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
            await (
                self
                ._require_mutable_project_for_update(
                    tenant_id=actor.tenant_id,
                    project_id=project_id,
                )
            )

            area = (
                await self.repository
                .get_active_area_for_update(
                    tenant_id=actor.tenant_id,
                    project_id=project_id,
                    area_id=area_id,
                )
            )

            if area is None:
                raise ConstructionProjectAreaNotFoundError(
                    "Construction project area "
                    "is not available."
                )

            require_expected_row_version(
                actual_row_version=(
                    area.row_version
                ),
                expected_row_version=(
                    expected_row_version
                ),
            )

            previous_status = area.status

            if next_status == previous_status:
                await self.session.commit()
                return area

            require_project_area_status_transition(
                current_status=previous_status,
                next_status=next_status,
                reopening=False,
            )

            area = (
                await self.repository
                .set_area_status(
                    area=area,
                    status=next_status,
                )
            )

            await self.audit_logger.record_operation(
                actor=actor,
                event_type=(
                    "construction_project_area_"
                    "status_changed"
                ),
                entity_type=(
                    "construction_project_area"
                ),
                entity_id=area.id,
                operation="status_change",
                changes=(
                    ConstructionAuditChange(
                        field="status",
                        old_value=previous_status,
                        new_value=area.status,
                    ),
                ),
                trace_id=trace_id,
            )

            await self.session.commit()
            return area
        except (
            ConstructionProjectAreaProjectNotFoundError,
            ConstructionProjectAreaNotFoundError,
            ConstructionProjectAreaTransitionError,
            ConstructionProjectTerminalError,
            ConstructionRowVersionConflictError,
        ):
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionProjectAreaOperationError(
                "Construction project area "
                "operation failed."
            ) from exc

    async def rename_area(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        project_id: UUID,
        area_id: UUID,
        expected_row_version: int,
        name: str,
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
            raise ConstructionProjectAreaValidationError(
                "Construction project area name "
                "is required."
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
                await self.repository
                .get_active_area_for_update(
                    tenant_id=actor.tenant_id,
                    project_id=project_id,
                    area_id=area_id,
                )
            )

            if area is None:
                raise ConstructionProjectAreaNotFoundError(
                    "Construction project area "
                    "is not available."
                )

            require_expected_row_version(
                actual_row_version=(
                    area.row_version
                ),
                expected_row_version=(
                    expected_row_version
                ),
            )

            previous_name = area.name

            if normalized_name == previous_name:
                await self.session.commit()
                return area

            area = await self.repository.rename_area(
                area=area,
                name=normalized_name,
            )

            await self.audit_logger.record_operation(
                actor=actor,
                event_type=(
                    "construction_project_area_updated"
                ),
                entity_type=(
                    "construction_project_area"
                ),
                entity_id=area.id,
                operation="update",
                changes=(
                    ConstructionAuditChange(
                        field="name",
                        old_value=previous_name,
                        new_value=area.name,
                    ),
                ),
                trace_id=trace_id,
            )

            await self.session.commit()
            return area
        except (
            ConstructionProjectAreaProjectNotFoundError,
            ConstructionProjectAreaNotFoundError,
            ConstructionProjectTerminalError,
            ConstructionRowVersionConflictError,
        ):
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionProjectAreaOperationError(
                "Construction project area "
                "operation failed."
            ) from exc

    async def archive_area(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        project_id: UUID,
        area_id: UUID,
        expected_row_version: int,
        trace_id: str | None = None,
        manage_transaction: bool = True,
    ):
        if type(manage_transaction) is not bool:
            raise ConstructionProjectAreaValidationError("Transaction mode must be a boolean.")
        if not manage_transaction and (
            not callable(getattr(self.session, "in_transaction", None))
            or self.session.in_transaction() is not True
        ):
            raise ConstructionProjectAreaOperationError("Area archive requires an active caller transaction.")
        require_construction_access(
            actor=actor,
            access_context=access_context,
            permission_code=(
                "construction.projects.edit"
            ),
            project_id=project_id,
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
                await self.repository
                .get_active_area_for_update(
                    tenant_id=actor.tenant_id,
                    project_id=project_id,
                    area_id=area_id,
                )
            )

            if area is None:
                raise ConstructionProjectAreaNotFoundError(
                    "Construction project area "
                    "is not available."
                )

            require_expected_row_version(
                actual_row_version=(
                    area.row_version
                ),
                expected_row_version=(
                    expected_row_version
                ),
            )

            if area.status == "confirmed":
                raise (
                    ConstructionProjectAreaTransitionError(
                        "Confirmed construction "
                        "project area must be "
                        "reopened before archive."
                    )
                )

            has_active_elements = (
                await self.repository
                .has_active_elements(
                    tenant_id=actor.tenant_id,
                    project_area_id=area.id,
                )
            )

            if has_active_elements:
                raise (
                    ConstructionProjectAreaHasActiveElementsError(
                        "Construction project area "
                        "has active elements."
                    )
                )

            previous_deleted_at = (
                area.deleted_at.isoformat()
                if area.deleted_at is not None
                else None
            )

            area = (
                await self.repository
                .soft_delete_area(
                    area=area,
                    deleted_at=self.now_provider(),
                )
            )

            await self.audit_logger.record_operation(
                actor=actor,
                event_type=(
                    "construction_project_area_archived"
                ),
                entity_type=(
                    "construction_project_area"
                ),
                entity_id=area.id,
                operation="archive",
                changes=(
                    ConstructionAuditChange(
                        field="deleted_at",
                        old_value=(
                            previous_deleted_at
                        ),
                        new_value=(
                            area.deleted_at
                            .isoformat()
                        ),
                    ),
                ),
                trace_id=trace_id,
            )

            if manage_transaction:
                await self.session.commit()
            return area
        except (
            ConstructionProjectAreaProjectNotFoundError,
            ConstructionProjectAreaNotFoundError,
            ConstructionProjectAreaTransitionError,
            ConstructionProjectAreaHasActiveElementsError,
            ConstructionProjectTerminalError,
            ConstructionRowVersionConflictError,
        ):
            if manage_transaction:
                await self.session.rollback()
            raise
        except Exception as exc:
            if manage_transaction:
                await self.session.rollback()
            raise ConstructionProjectAreaOperationError(
                "Construction project area "
                "operation failed."
            ) from exc

    async def get_area(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        project_id: UUID,
        area_id: UUID,
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
                await self.repository
                .get_active_area(
                    tenant_id=actor.tenant_id,
                    project_id=project_id,
                    area_id=area_id,
                )
            )
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionProjectAreaOperationError(
                "Construction project area "
                "operation failed."
            ) from exc

        if area is None:
            raise ConstructionProjectAreaNotFoundError(
                "Construction project area "
                "is not available."
            )

        return area

    async def list_areas(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        project_id: UUID,
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
            return (
                await self.repository
                .list_active_areas(
                    tenant_id=actor.tenant_id,
                    project_id=project_id,
                )
            )
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionProjectAreaOperationError(
                "Construction project area "
                "operation failed."
            ) from exc

    async def update_area_sort_order(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        project_id: UUID,
        area_id: UUID,
        expected_row_version: int,
        sort_order: int | None,
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

        if (
            sort_order is not None
            and (
                not isinstance(sort_order, int)
                or isinstance(sort_order, bool)
            )
        ):
            raise ConstructionProjectAreaValidationError(
                "Construction project area sort "
                "order is invalid."
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
                await self.repository
                .get_active_area_for_update(
                    tenant_id=actor.tenant_id,
                    project_id=project_id,
                    area_id=area_id,
                )
            )

            if area is None:
                raise ConstructionProjectAreaNotFoundError(
                    "Construction project area "
                    "is not available."
                )

            require_expected_row_version(
                actual_row_version=(
                    area.row_version
                ),
                expected_row_version=(
                    expected_row_version
                ),
            )

            previous_sort_order = (
                area.sort_order
            )

            if sort_order == previous_sort_order:
                await self.session.commit()
                return area

            area = (
                await self.repository
                .set_area_sort_order(
                    area=area,
                    sort_order=sort_order,
                )
            )

            await self.audit_logger.record_operation(
                actor=actor,
                event_type=(
                    "construction_project_area_updated"
                ),
                entity_type=(
                    "construction_project_area"
                ),
                entity_id=area.id,
                operation="update",
                changes=(
                    ConstructionAuditChange(
                        field="sort_order",
                        old_value=(
                            previous_sort_order
                        ),
                        new_value=(
                            area.sort_order
                        ),
                    ),
                ),
                trace_id=trace_id,
            )

            await self.session.commit()
            return area
        except (
            ConstructionProjectAreaProjectNotFoundError,
            ConstructionProjectAreaNotFoundError,
            ConstructionProjectTerminalError,
            ConstructionRowVersionConflictError,
        ):
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionProjectAreaOperationError(
                "Construction project area "
                "operation failed."
            ) from exc

    async def patch_area(
        self, *, actor, access_context: ConstructionAccessContext,
        project_id: UUID, area_id: UUID, expected_row_version: int,
        fields: dict[str, object], trace_id: str | None = None,
        manage_transaction: bool = True,
    ):
        from sqlalchemy.orm.exc import StaleDataError

        if type(manage_transaction) is not bool:
            raise ConstructionProjectAreaValidationError("Transaction mode must be a boolean.")
        if not manage_transaction and (
            not callable(getattr(self.session, "in_transaction", None))
            or self.session.in_transaction() is not True
        ):
            raise ConstructionProjectAreaOperationError("Area patch requires an active caller transaction.")
        require_construction_access(
            actor=actor, access_context=access_context,
            permission_code="construction.projects.edit", project_id=project_id,
        )
        if (
            not isinstance(project_id, UUID) or not isinstance(area_id, UUID)
            or type(expected_row_version) is not int or expected_row_version <= 0
            or not isinstance(fields, dict) or set(fields) - {"name", "sort_order"}
        ):
            raise ConstructionProjectAreaValidationError("Area patch request is invalid.")
        normalized = {}
        if "name" in fields:
            if not isinstance(fields["name"], str) or not fields["name"].strip():
                raise ConstructionProjectAreaValidationError("Area name is required.")
            normalized["name"] = fields["name"].strip()
        if "sort_order" in fields:
            value = fields["sort_order"]
            if value is not None and type(value) is not int:
                raise ConstructionProjectAreaValidationError("Area sort order is invalid.")
            normalized["sort_order"] = value

        try:
            await self._require_mutable_project_for_update(
                tenant_id=actor.tenant_id, project_id=project_id,
            )
            area = await self.repository.get_active_area_for_update(
                tenant_id=actor.tenant_id, project_id=project_id, area_id=area_id,
            )
            if area is None:
                raise ConstructionProjectAreaNotFoundError("Area is not available.")
            if (
                area.id != area_id or area.tenant_id != actor.tenant_id
                or area.project_id != project_id or area.deleted_at is not None
            ):
                raise ConstructionProjectAreaOperationError("Area lock context is invalid.")
            require_expected_row_version(
                actual_row_version=area.row_version, expected_row_version=expected_row_version,
            )
            changed = {
                field: value for field, value in normalized.items()
                if getattr(area, field) != value
            }
            if changed:
                changes = tuple(
                    ConstructionAuditChange(
                        field=field, old_value=getattr(area, field), new_value=value,
                    )
                    for field, value in changed.items()
                )
                area = await self.repository.update_area_fields(area=area, fields=changed)
                await self.audit_logger.record_operation(
                    actor=actor, event_type="construction_project_area_updated",
                    entity_type="construction_project_area", entity_id=area.id,
                    operation="update", changes=changes, trace_id=trace_id,
                )
            if manage_transaction:
                await self.session.commit()
            return area
        except StaleDataError:
            if manage_transaction:
                await self.session.rollback()
            raise ConstructionRowVersionConflictError() from None
        except (
            ConstructionProjectAreaProjectNotFoundError, ConstructionProjectAreaNotFoundError,
            ConstructionProjectTerminalError, ConstructionRowVersionConflictError,
            ConstructionProjectAreaOperationError,
        ):
            if manage_transaction:
                await self.session.rollback()
            raise
        except Exception as error:
            if manage_transaction:
                await self.session.rollback()
            raise ConstructionProjectAreaOperationError("Area patch failed.") from error
