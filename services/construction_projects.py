from decimal import Decimal
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
    ConstructionRowVersionConflictError,
    require_expected_row_version,
)
from services.construction_permissions import (
    ConstructionAccessContext,
    require_construction_access,
)


CONSTRUCTION_PROJECT_STATUS_TRANSITIONS = {
    "draft": frozenset(
        {
            "planning",
            "cancelled",
        }
    ),
    "planning": frozenset(
        {
            "active",
            "cancelled",
        }
    ),
    "active": frozenset(
        {
            "completed",
            "cancelled",
        }
    ),
    "completed": frozenset(),
    "cancelled": frozenset(),
}


class ConstructionProjectTransitionError(
    Exception
):
    pass


def require_project_status_transition(
    *,
    current_status: str,
    next_status: str,
) -> None:
    allowed_statuses = (
        CONSTRUCTION_PROJECT_STATUS_TRANSITIONS
        .get(current_status)
    )

    if (
        allowed_statuses is None
        or next_status
        not in allowed_statuses
    ):
        raise ConstructionProjectTransitionError(
            "Construction project status "
            "transition is not allowed."
        )


class ConstructionProjectValidationError(
    ValueError
):
    pass


class ConstructionProjectNotFoundError(
    Exception
):
    pass


class ConstructionProjectOperationError(
    Exception
):
    pass


class ConstructionProjectService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository,
        client_repository=None,
        access_repository=None,
        event_repository=None,
    ) -> None:
        self.session = session
        self.repository = repository
        self.client_repository = (
            client_repository
        )
        self.access_repository = (
            access_repository
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

    async def create_project(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        name: str,
        address_raw: str,
        client_id: UUID | None,
        responsible_user_id: UUID | None,
        comment: str | None,
        trace_id: str | None = None,
    ):
        require_construction_access(
            actor=actor,
            access_context=access_context,
            permission_code=(
                "construction.projects.create"
            ),
        )

        normalized_name = (
            name.strip()
            if isinstance(name, str)
            else ""
        )
        normalized_address_raw = (
            address_raw.strip()
            if isinstance(address_raw, str)
            else ""
        )

        if not normalized_name:
            raise ConstructionProjectValidationError(
                "Construction project name "
                "is required."
            )

        if not normalized_address_raw:
            raise ConstructionProjectValidationError(
                "Construction project address "
                "is required."
            )

        if client_id is not None:
            if self.client_repository is None:
                raise ConstructionProjectOperationError(
                    "Construction project operation "
                    "failed."
                )

            try:
                client = (
                    await self.client_repository
                    .get_active_client(
                        tenant_id=actor.tenant_id,
                        client_id=client_id,
                    )
                )
            except Exception as exc:
                await self.session.rollback()
                raise ConstructionProjectOperationError(
                    "Construction project operation "
                    "failed."
                ) from exc

            if client is None:
                raise ConstructionProjectValidationError(
                    "Construction project client "
                    "is not available."
                )

        if responsible_user_id is not None:
            if self.access_repository is None:
                raise ConstructionProjectOperationError(
                    "Construction project operation "
                    "failed."
                )

            try:
                has_membership = (
                    await self.access_repository
                    .has_active_platform_membership(
                        tenant_id=actor.tenant_id,
                        user_id=responsible_user_id,
                    )
                )
            except Exception as exc:
                await self.session.rollback()
                raise ConstructionProjectOperationError(
                    "Construction project operation "
                    "failed."
                ) from exc

            if not has_membership:
                raise ConstructionProjectValidationError(
                    "Construction project responsible "
                    "user is not available."
                )

        try:
            project = (
                await self.repository.create_project(
                    tenant_id=actor.tenant_id,
                    client_id=client_id,
                    name=normalized_name,
                    responsible_user_id=(
                        responsible_user_id
                    ),
                    comment=comment,
                    address_raw=(
                        normalized_address_raw
                    ),
                    created_by=actor.user_id,
                )
            )

            await self.audit_logger.record_operation(
                actor=actor,
                event_type=(
                    "construction_project_created"
                ),
                entity_type=(
                    "construction_project"
                ),
                entity_id=project.id,
                operation="create",
                trace_id=trace_id,
            )

            await self.session.commit()
            return project
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionProjectOperationError(
                "Construction project operation "
                "failed."
            ) from exc

    async def update_project(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        project_id: UUID,
        expected_row_version: int,
        name: str,
        client_id: UUID | None,
        responsible_user_id: UUID | None,
        comment: str | None,
        address_raw: str,
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
        normalized_address_raw = (
            address_raw.strip()
            if isinstance(address_raw, str)
            else ""
        )

        if not normalized_name:
            raise ConstructionProjectValidationError(
                "Construction project name "
                "is required."
            )

        if not normalized_address_raw:
            raise ConstructionProjectValidationError(
                "Construction project address "
                "is required."
            )

        def audit_value(value):
            if isinstance(
                value,
                (UUID, Decimal),
            ):
                return str(value)
            return value

        try:
            project = (
                await self.repository
                .get_active_project_for_update(
                    tenant_id=actor.tenant_id,
                    project_id=project_id,
                )
            )

            if project is None:
                raise ConstructionProjectNotFoundError(
                    "Construction project "
                    "is not available."
                )

            require_expected_row_version(
                actual_row_version=(
                    project.row_version
                ),
                expected_row_version=(
                    expected_row_version
                ),
            )

            if (
                client_id
                != project.client_id
                and client_id is not None
            ):
                if self.client_repository is None:
                    raise (
                        ConstructionProjectOperationError(
                            "Construction project "
                            "operation failed."
                        )
                    )

                client = (
                    await self.client_repository
                    .get_active_client(
                        tenant_id=actor.tenant_id,
                        client_id=client_id,
                    )
                )
                if client is None:
                    raise (
                        ConstructionProjectValidationError(
                            "Construction project "
                            "client is not available."
                        )
                    )

            if (
                responsible_user_id
                != project.responsible_user_id
                and responsible_user_id is not None
            ):
                if self.access_repository is None:
                    raise (
                        ConstructionProjectOperationError(
                            "Construction project "
                            "operation failed."
                        )
                    )

                has_membership = (
                    await self.access_repository
                    .has_active_platform_membership(
                        tenant_id=actor.tenant_id,
                        user_id=(
                            responsible_user_id
                        ),
                    )
                )
                if not has_membership:
                    raise (
                        ConstructionProjectValidationError(
                            "Construction project "
                            "responsible user is "
                            "not available."
                        )
                    )

            address_changed = (
                normalized_address_raw
                != project.address_raw
            )
            changes = []

            def add_change(
                field: str,
                old_value,
                new_value,
            ) -> None:
                if old_value == new_value:
                    return
                changes.append(
                    ConstructionAuditChange(
                        field=field,
                        old_value=audit_value(
                            old_value
                        ),
                        new_value=audit_value(
                            new_value
                        ),
                    )
                )

            add_change(
                "name",
                project.name,
                normalized_name,
            )
            add_change(
                "client_id",
                project.client_id,
                client_id,
            )
            add_change(
                "responsible_user_id",
                project.responsible_user_id,
                responsible_user_id,
            )
            add_change(
                "comment",
                project.comment,
                comment,
            )
            add_change(
                "address_raw",
                project.address_raw,
                normalized_address_raw,
            )

            if address_changed:
                address_reset_values = (
                    (
                        "address_formatted",
                        project.address_formatted,
                        None,
                    ),
                    (
                        "country_code",
                        project.country_code,
                        None,
                    ),
                    (
                        "region",
                        project.region,
                        None,
                    ),
                    (
                        "city",
                        project.city,
                        None,
                    ),
                    (
                        "postal_code",
                        project.postal_code,
                        None,
                    ),
                    (
                        "latitude",
                        project.latitude,
                        None,
                    ),
                    (
                        "longitude",
                        project.longitude,
                        None,
                    ),
                    (
                        "address_provider",
                        project.address_provider,
                        None,
                    ),
                    (
                        "provider_place_id",
                        project.provider_place_id,
                        None,
                    ),
                    (
                        "address_verification_status",
                        (
                            project
                            .address_verification_status
                        ),
                        "pending",
                    ),
                )
                for (
                    field,
                    old_value,
                    new_value,
                ) in address_reset_values:
                    add_change(
                        field,
                        old_value,
                        new_value,
                    )

            if not changes:
                await self.session.commit()
                return project

            project = (
                await self.repository.update_project(
                    project=project,
                    name=normalized_name,
                    client_id=client_id,
                    responsible_user_id=(
                        responsible_user_id
                    ),
                    comment=comment,
                    address_raw=(
                        normalized_address_raw
                    ),
                    reset_address_verification=(
                        address_changed
                    ),
                )
            )

            await self.audit_logger.record_operation(
                actor=actor,
                event_type=(
                    "construction_project_updated"
                ),
                entity_type=(
                    "construction_project"
                ),
                entity_id=project.id,
                operation="update",
                changes=tuple(changes),
                trace_id=trace_id,
            )

            await self.session.commit()
            return project
        except (
            ConstructionProjectValidationError,
            ConstructionProjectNotFoundError,
            ConstructionRowVersionConflictError,
        ):
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionProjectOperationError(
                "Construction project operation "
                "failed."
            ) from exc

    async def transition_project_status(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        project_id: UUID,
        next_status: str,
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
            project = (
                await self.repository
                .get_active_project_for_update(
                    tenant_id=actor.tenant_id,
                    project_id=project_id,
                )
            )

            if project is None:
                raise ConstructionProjectNotFoundError(
                    "Construction project "
                    "is not available."
                )

            require_expected_row_version(
                actual_row_version=(
                    project.row_version
                ),
                expected_row_version=(
                    expected_row_version
                ),
            )

            previous_status = project.status

            if next_status == previous_status:
                await self.session.commit()
                return project

            require_project_status_transition(
                current_status=previous_status,
                next_status=next_status,
            )

            project = (
                await self.repository
                .set_project_status(
                    project=project,
                    status=next_status,
                )
            )

            await self.audit_logger.record_operation(
                actor=actor,
                event_type=(
                    "construction_project_"
                    "status_changed"
                ),
                entity_type=(
                    "construction_project"
                ),
                entity_id=project.id,
                operation="status_change",
                changes=(
                    ConstructionAuditChange(
                        field="status",
                        old_value=previous_status,
                        new_value=project.status,
                    ),
                ),
                trace_id=trace_id,
            )

            await self.session.commit()
            return project
        except (
            ConstructionProjectNotFoundError,
            ConstructionProjectTransitionError,
            ConstructionRowVersionConflictError,
        ):
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionProjectOperationError(
                "Construction project operation "
                "failed."
            ) from exc
