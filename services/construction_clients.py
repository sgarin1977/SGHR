from collections.abc import Callable
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
from services.construction_permissions import (
    CONSTRUCTION_PERMISSION_CODES,
    ConstructionAccessContext,
    require_construction_access,
)


class ConstructionClientValidationError(
    ValueError
):
    pass


class ConstructionClientNotFoundError(
    Exception
):
    pass


class ConstructionClientOperationError(
    Exception
):
    pass


class ConstructionClientService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository,
        event_repository=None,
        now_provider: Callable[
            [],
            datetime,
        ] | None = None,
    ) -> None:
        self.session = session
        self.repository = repository
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

    async def create_client(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        display_name: str,
        client_type: str,
        phone: str | None,
        email: str | None,
        notes: str | None,
        trace_id: str | None = None,
    ):
        require_construction_access(
            actor=actor,
            access_context=access_context,
            permission_code=(
                "construction.projects.create"
            ),
        )

        normalized_display_name = (
            display_name.strip()
            if isinstance(display_name, str)
            else ""
        )
        normalized_client_type = (
            client_type.strip().lower()
            if isinstance(client_type, str)
            else ""
        )

        if not normalized_display_name:
            raise ConstructionClientValidationError(
                "Construction client display name "
                "is required."
            )

        if normalized_client_type not in {
            "person",
            "company",
        }:
            raise ConstructionClientValidationError(
                "Construction client type "
                "is invalid."
            )

        assert (
            "construction.projects.create"
            in CONSTRUCTION_PERMISSION_CODES
        )

        try:
            client = (
                await self.repository.create_client(
                    tenant_id=actor.tenant_id,
                    display_name=(
                        normalized_display_name
                    ),
                    client_type=(
                        normalized_client_type
                    ),
                    phone=phone,
                    email=email,
                    notes=notes,
                )
            )

            await self.audit_logger.record_operation(
                actor=actor,
                event_type=(
                    "construction_client_created"
                ),
                entity_type=(
                    "construction_client"
                ),
                entity_id=client.id,
                operation="create",
                trace_id=trace_id,
            )

            await self.session.commit()
            return client
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionClientOperationError(
                "Construction client operation "
                "failed."
            ) from exc

    async def update_client(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        client_id: UUID,
        display_name: str,
        client_type: str,
        phone: str | None,
        email: str | None,
        notes: str | None,
        trace_id: str | None = None,
    ):
        require_construction_access(
            actor=actor,
            access_context=access_context,
            permission_code=(
                "construction.projects.edit"
            ),
        )

        normalized_display_name = (
            display_name.strip()
            if isinstance(display_name, str)
            else ""
        )
        normalized_client_type = (
            client_type.strip().lower()
            if isinstance(client_type, str)
            else ""
        )

        if not normalized_display_name:
            raise ConstructionClientValidationError(
                "Construction client display name "
                "is required."
            )

        if normalized_client_type not in {
            "person",
            "company",
        }:
            raise ConstructionClientValidationError(
                "Construction client type "
                "is invalid."
            )

        try:
            client = (
                await self.repository
                .get_active_client_for_update(
                    tenant_id=actor.tenant_id,
                    client_id=client_id,
                )
            )

            if client is None:
                raise ConstructionClientNotFoundError(
                    "Construction client "
                    "is not available."
                )

            previous_values = {
                "display_name": (
                    client.display_name
                ),
                "client_type": (
                    client.client_type
                ),
                "phone": client.phone,
                "email": client.email,
                "notes": client.notes,
            }

            client = (
                await self.repository
                .update_client(
                    client=client,
                    display_name=(
                        normalized_display_name
                    ),
                    client_type=(
                        normalized_client_type
                    ),
                    phone=phone,
                    email=email,
                    notes=notes,
                )
            )

            current_values = {
                "display_name": (
                    client.display_name
                ),
                "client_type": (
                    client.client_type
                ),
                "phone": client.phone,
                "email": client.email,
                "notes": client.notes,
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
                for field in (
                    "display_name",
                    "client_type",
                    "phone",
                    "email",
                    "notes",
                )
                if (
                    previous_values[field]
                    != current_values[field]
                )
            )

            await self.audit_logger.record_operation(
                actor=actor,
                event_type=(
                    "construction_client_updated"
                ),
                entity_type=(
                    "construction_client"
                ),
                entity_id=client.id,
                operation="update",
                changes=changes,
                trace_id=trace_id,
            )

            await self.session.commit()
            return client
        except ConstructionClientNotFoundError:
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionClientOperationError(
                "Construction client operation "
                "failed."
            ) from exc

    async def archive_client(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        client_id: UUID,
        trace_id: str | None = None,
    ):
        require_construction_access(
            actor=actor,
            access_context=access_context,
            permission_code=(
                "construction.projects.edit"
            ),
        )

        try:
            client = (
                await self.repository
                .get_active_client_for_update(
                    tenant_id=actor.tenant_id,
                    client_id=client_id,
                )
            )

            if client is None:
                raise ConstructionClientNotFoundError(
                    "Construction client "
                    "is not available."
                )

            previous_deleted_at = (
                client.deleted_at.isoformat()
                if client.deleted_at is not None
                else None
            )

            client = (
                await self.repository
                .soft_delete_client(
                    client=client,
                    deleted_at=(
                        self.now_provider()
                    ),
                )
            )

            await self.audit_logger.record_operation(
                actor=actor,
                event_type=(
                    "construction_client_archived"
                ),
                entity_type=(
                    "construction_client"
                ),
                entity_id=client.id,
                operation="archive",
                changes=(
                    ConstructionAuditChange(
                        field="deleted_at",
                        old_value=(
                            previous_deleted_at
                        ),
                        new_value=(
                            client.deleted_at
                            .isoformat()
                        ),
                    ),
                ),
                trace_id=trace_id,
            )

            await self.session.commit()
            return client
        except ConstructionClientNotFoundError:
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionClientOperationError(
                "Construction client operation "
                "failed."
            ) from exc
