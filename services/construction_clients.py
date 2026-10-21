from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID

from services.construction_audit_client_correlation import construction_audit_client_details

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError
from database.repositories.construction_client_user_links import MAX_CLIENT_ROW_VERSION
from services.construction_foundation import ConstructionRowVersionConflictError, require_expected_row_version

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
        manage_transaction: bool = True,
    ):
        if type(manage_transaction) is not bool:
            raise ConstructionClientValidationError("Transaction mode must be a boolean.")
        if not manage_transaction:
            if (
                not callable(getattr(self.session, "in_transaction", None))
                or self.session.in_transaction() is not True
                or getattr(self.repository, "session", None) is not self.session
                or getattr(self.audit_logger.event_repository, "session", None) is not self.session
            ):
                raise ConstructionClientOperationError("Client creation requires a shared active caller transaction.")
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

            if not manage_transaction and (
                not isinstance(getattr(client, "id", None), UUID)
                or getattr(client, "tenant_id", None) != actor.tenant_id
                or getattr(client, "deleted_at", "missing") is not None
                or getattr(client, "display_name", None) != normalized_display_name
                or getattr(client, "client_type", None) != normalized_client_type
                or any(getattr(client, field, object()) != value for field, value in (
                    ("phone", phone), ("email", email), ("notes", notes),
                ))
            ):
                raise ConstructionClientOperationError("Created client context is invalid.")

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
                details=construction_audit_client_details(tenant_id=actor.tenant_id, entity_type="construction_client", entity=client),
            )

            if manage_transaction:
                await self.session.commit()
            return client
        except Exception as exc:
            if manage_transaction:
                await self.session.rollback()
            raise ConstructionClientOperationError(
                "Construction client operation "
                "failed."
            ) from exc

    @staticmethod
    def _locked_client_version(client, *, tenant_id, client_id, expected_row_version):
        if (client is None or getattr(client, 'id', None) != client_id
                or getattr(client, 'tenant_id', None) != tenant_id
                or getattr(client, 'deleted_at', object()) is not None):
            raise ConstructionClientNotFoundError('Construction client is not available.')
        actual = getattr(client, 'row_version', None)
        if type(actual) is not int or not 1 <= actual <= MAX_CLIENT_ROW_VERSION:
            raise ConstructionClientOperationError('Construction client version is unavailable.')
        require_expected_row_version(actual_row_version=actual, expected_row_version=expected_row_version)
        return actual

    @staticmethod
    def _require_next_client_version(client, *, tenant_id, client_id, previous_version):
        if (client is None or getattr(client, 'id', None) != client_id
                or getattr(client, 'tenant_id', None) != tenant_id
                or type(getattr(client, 'row_version', None)) is not int
                or client.row_version != previous_version + 1):
            raise ConstructionClientOperationError('Construction client mutation receipt is invalid.')

    async def update_client(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        client_id: UUID,
        expected_row_version: int,
        display_name: str,
        client_type: str,
        phone: str | None,
        email: str | None,
        notes: str | None,
        trace_id: str | None = None,
    ):
        require_construction_access(actor=actor, access_context=access_context,
            permission_code='construction.projects.edit')
        normalized_name = display_name.strip() if isinstance(display_name, str) else ''
        normalized_type = client_type.strip().lower() if isinstance(client_type, str) else ''
        if not normalized_name:
            raise ConstructionClientValidationError('Construction client display name is required.')
        if normalized_type not in {'person', 'company'}:
            raise ConstructionClientValidationError('Construction client type is invalid.')
        requested = dict(display_name=normalized_name, client_type=normalized_type,
            phone=phone, email=email, notes=notes)
        try:
            client = await self.repository.get_active_client_for_update(
                tenant_id=actor.tenant_id, client_id=client_id)
            previous_version = self._locked_client_version(client, tenant_id=actor.tenant_id,
                client_id=client_id, expected_row_version=expected_row_version)
            previous = {field: getattr(client, field) for field in requested}
            # A stale request must fail even if its desired fields happen to
            # equal the current fields. The service still owns transaction end.
            if previous == requested:
                await self.session.commit()
                return client
            if previous_version == MAX_CLIENT_ROW_VERSION:
                raise ConstructionClientValidationError('Construction client version is exhausted.')
            client = await self.repository.update_client(client=client, **requested)
            self._require_next_client_version(client, tenant_id=actor.tenant_id,
                client_id=client_id, previous_version=previous_version)
            if client.deleted_at is not None or any(getattr(client, key) != value for key, value in requested.items()):
                raise ConstructionClientOperationError('Construction client update receipt is invalid.')
            changes = tuple(ConstructionAuditChange(field, previous[field], value)
                for field, value in requested.items() if previous[field] != value)
            await self.audit_logger.record_operation(actor=actor,
                event_type='construction_client_updated', entity_type='construction_client',
                entity_id=client.id, operation='update', changes=changes, trace_id=trace_id,
                details=construction_audit_client_details(tenant_id=actor.tenant_id,
                    entity_type='construction_client', entity=client))
            await self.session.commit()
            return client
        except (ConstructionClientValidationError, ConstructionClientNotFoundError, ConstructionRowVersionConflictError):
            await self.session.rollback()
            raise
        except StaleDataError:
            await self.session.rollback()
            raise ConstructionRowVersionConflictError() from None
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionClientOperationError('Construction client operation failed.') from exc
        except BaseException:
            await self.session.rollback()
            raise

    async def archive_client(
        self,
        *,
        actor,
        access_context: ConstructionAccessContext,
        client_id: UUID,
        expected_row_version: int,
        trace_id: str | None = None,
    ):
        require_construction_access(actor=actor, access_context=access_context,
            permission_code='construction.projects.edit')
        try:
            client = await self.repository.get_active_client_for_update(
                tenant_id=actor.tenant_id, client_id=client_id)
            previous_version = self._locked_client_version(client, tenant_id=actor.tenant_id,
                client_id=client_id, expected_row_version=expected_row_version)
            if previous_version == MAX_CLIENT_ROW_VERSION:
                raise ConstructionClientValidationError('Construction client version is exhausted.')
            instant = self.now_provider()
            if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
                raise ConstructionClientOperationError('Construction client server clock must be timezone-aware.')
            instant = instant.astimezone(timezone.utc)
            client = await self.repository.soft_delete_client(client=client, deleted_at=instant)
            self._require_next_client_version(client, tenant_id=actor.tenant_id,
                client_id=client_id, previous_version=previous_version)
            if client.deleted_at != instant:
                raise ConstructionClientOperationError('Construction client archive receipt is invalid.')
            await self.audit_logger.record_operation(actor=actor,
                event_type='construction_client_archived', entity_type='construction_client',
                entity_id=client.id, operation='archive',
                changes=(ConstructionAuditChange('deleted_at', None, instant.isoformat()),), trace_id=trace_id,
                details=construction_audit_client_details(tenant_id=actor.tenant_id,
                    entity_type='construction_client', entity=client))
            await self.session.commit()
            return client
        except (ConstructionClientValidationError, ConstructionClientNotFoundError, ConstructionRowVersionConflictError):
            await self.session.rollback()
            raise
        except StaleDataError:
            await self.session.rollback()
            raise ConstructionRowVersionConflictError() from None
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionClientOperationError('Construction client operation failed.') from exc
        except BaseException:
            await self.session.rollback()
            raise
