"""Atomic creation of a universal project area; no Measurement dependency."""
import hashlib
import inspect
from datetime import datetime, timezone
from uuid import UUID

from services.api_idempotency import (
    ApiIdempotencyKeyReusedError, build_api_idempotency_service,
    hash_request_payload,
)
from services.construction.construction_areas import (
    CONSTRUCTION_PROJECT_AREA_TYPES, ConstructionProjectAreaService,
    ConstructionProjectAreaValidationError, ConstructionProjectAreaOperationError,
    ConstructionProjectAreaProjectNotFoundError, ConstructionProjectAreaNotFoundError,
)
from services.construction.construction_foundation import ConstructionProjectTerminalError
from services.construction.construction_grants import ConstructionAccessContextResolver
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError, require_construction_access,
)

OPERATION = "construction.project_area.create"
RESPONSE_FIELDS = frozenset({
    "id", "tenant_id", "project_id", "area_type",
    "name", "sort_order", "status", "row_version",
})


class AreaCreationInProgressError(ConstructionProjectAreaOperationError):
    code = "idempotency_in_progress"


class ConstructionAreaCreateWorkflow:
    def __init__(
        self, *, session, area_service, access_repository,
        idempotency_service=None, now_provider=None,
    ):
        collaborators = (
            area_service, getattr(area_service, "repository", None),
            getattr(area_service, "project_repository", None),
            getattr(getattr(area_service, "audit_logger", None), "event_repository", None),
            access_repository,
        )
        if idempotency_service is not None:
            collaborators += (getattr(idempotency_service, "repository", None),)
        if (
            session is None
            or any(getattr(item, "session", None) is not session for item in collaborators)
            or any(not callable(getattr(session, method, None))
                   for method in ("commit", "rollback", "in_transaction"))
            or not callable(getattr(area_service, "create_area", None))
            or "manage_transaction" not in inspect.signature(area_service.create_area).parameters
            or not callable(getattr(area_service.repository, "get_active_area", None))
            or not callable(getattr(area_service.project_repository, "get_active_project_for_update", None))
            or any(not callable(getattr(access_repository, method, None)) for method in (
                "has_active_platform_membership", "list_active_grants",
            ))
            or (
                idempotency_service is not None and any(
                    not callable(getattr(idempotency_service, method, None))
                    for method in ("reserve", "complete")
                )
            )
            or (now_provider is not None and not callable(now_provider))
        ):
            raise ValueError("Area creation requires shared-session collaborators.")
        self.session, self.service = session, area_service
        self.access, self.idempotency = access_repository, idempotency_service
        self.clock = now_provider or (lambda: datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(
            repository=access_repository, now_provider=self._now,
        )

    def _now(self):
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionProjectAreaOperationError("Area creation clock is invalid.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _authorize(actor, context, project_id):
        if (
            getattr(actor, "status", None) != "active"
            or not isinstance(getattr(actor, "user_id", None), UUID)
            or not isinstance(getattr(actor, "tenant_id", None), UUID)
            or not isinstance(context, ConstructionAccessContext)
        ):
            raise ConstructionPermissionDeniedError("Area creation is unavailable.")
        require_construction_access(
            actor=actor, access_context=context,
            permission_code="construction.projects.create", project_id=project_id,
        )

    async def _fresh_context(self, actor, project_id):
        if await self.access.has_active_platform_membership(
            tenant_id=actor.tenant_id, user_id=actor.user_id,
        ) is not True:
            raise ConstructionPermissionDeniedError("Area creation membership is unavailable.")
        context = await self.resolver.resolve(actor=actor)
        self._authorize(actor, context, project_id)
        return context

    @staticmethod
    def _receipt(value, *, actor, payload):
        if not isinstance(value, dict) or set(value) != RESPONSE_FIELDS:
            raise ConstructionProjectAreaOperationError("Area creation receipt is invalid.")
        for field in ("id", "tenant_id", "project_id"):
            raw = value[field]
            if not isinstance(raw, str) or str(UUID(raw)) != raw:
                raise ConstructionProjectAreaOperationError("Area receipt identifier is invalid.")
        if (
            value["tenant_id"] != str(actor.tenant_id)
            or value["project_id"] != payload["project_id"]
            or value["area_type"] != payload["area_type"].strip().upper()
            or value["name"] != payload["name"].strip()
            or value["sort_order"] != payload["sort_order"]
            or (
                value["sort_order"] is not None
                and type(value["sort_order"]) is not int
            )
            or value["status"] != "draft"
            or type(value["row_version"]) is not int or value["row_version"] != 1
        ):
            raise ConstructionProjectAreaOperationError("Area creation receipt binding differs.")
        return dict(value)

    async def create_area(
        self, *, actor, access_context, project_id, area_type, name,
        sort_order=None, idempotency_key=None, trace_id=None,
    ):
        try:
            if (
                not isinstance(project_id, UUID)
                or not isinstance(area_type, str)
                or area_type.strip().upper() not in CONSTRUCTION_PROJECT_AREA_TYPES
                or not isinstance(name, str) or not name.strip()
                or (
                    sort_order is not None and (
                        type(sort_order) is not int
                        or not -2147483648 <= sort_order <= 2147483647
                    )
                )
                or (
                    idempotency_key is not None and (
                        not isinstance(idempotency_key, str)
                        or not idempotency_key.strip() or len(idempotency_key) > 255
                    )
                )
            ):
                raise ConstructionProjectAreaValidationError("Area creation input is invalid.")
            self._authorize(actor, access_context, project_id)
            if self.session.in_transaction() is not True:
                raise ConstructionProjectAreaOperationError("An active area transaction is required.")
            payload = {
                "project_id": str(project_id), "area_type": area_type,
                "name": name, "sort_order": sort_order,
            }
            reservation = None
            if idempotency_key is not None:
                if self.idempotency is None:
                    raise ConstructionProjectAreaOperationError("Area replay storage is not configured.")
                reservation = await self.idempotency.reserve(
                    tenant_id=actor.tenant_id, principal_type="user",
                    principal_id=actor.user_id, operation=OPERATION,
                    idempotency_key=idempotency_key, payload=payload, now=self._now(),
                )
                record = reservation.record
                if (
                    record.tenant_id != actor.tenant_id
                    or record.principal_type != "user" or record.principal_id != actor.user_id
                    or record.operation != OPERATION
                    or record.key_hash != hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
                    or record.request_hash != hash_request_payload(payload)
                    or type(reservation.is_replay) is not bool
                ):
                    raise ConstructionProjectAreaOperationError("Area reservation binding differs.")

            project = await self.service.project_repository.get_active_project_for_update(
                tenant_id=actor.tenant_id, project_id=project_id,
            )
            if (
                project is None or getattr(project, "id", None) != project_id
                or getattr(project, "tenant_id", None) != actor.tenant_id
                or getattr(project, "deleted_at", object()) is not None
            ):
                raise ConstructionProjectAreaProjectNotFoundError("Project is unavailable.")

            # Both receipt and project locks may have waited. Refresh membership,
            # grant expiry, role and scope before any area/audit mutation.
            context = await self._fresh_context(actor, project_id)
            if reservation is not None and reservation.is_replay:
                if reservation.response_status is None and reservation.record.status == "processing":
                    raise AreaCreationInProgressError("Area creation is processing.")
                if reservation.response_status != 201 or reservation.record.status != "completed":
                    raise ConstructionProjectAreaOperationError("Area replay state is invalid.")
                response = self._receipt(
                    reservation.response_payload, actor=actor, payload=payload,
                )
                row = await self.service.repository.get_active_area(
                    tenant_id=actor.tenant_id, project_id=project_id,
                    area_id=UUID(response["id"]),
                )
                if (
                    row is None or row.id != UUID(response["id"])
                    or row.tenant_id != actor.tenant_id or row.project_id != project_id
                    or row.deleted_at is not None
                ):
                    raise ConstructionProjectAreaNotFoundError("Created area is unavailable.")
            else:
                if reservation is not None and reservation.record.status != "processing":
                    raise ConstructionProjectAreaOperationError("Area reservation state is invalid.")
                row = await self.service.create_area(
                    actor=actor, access_context=context, project_id=project_id,
                    area_type=area_type, name=name, sort_order=sort_order,
                    trace_id=trace_id, manage_transaction=False,
                )
                if (
                    not isinstance(row.id, UUID) or row.created_by != actor.user_id
                    or row.deleted_at is not None
                ):
                    raise ConstructionProjectAreaOperationError("Created area context is invalid.")
                response = self._receipt({
                    field: str(getattr(row, field))
                    if field in ("id", "tenant_id", "project_id")
                    else getattr(row, field)
                    for field in RESPONSE_FIELDS
                }, actor=actor, payload=payload)
                if reservation is not None:
                    completed = await self.idempotency.complete(
                        reservation=reservation, response_status=201,
                        response_payload=response, now=self._now(),
                    )
                    if completed is None or completed.status != "completed":
                        raise ConstructionProjectAreaOperationError("Area receipt completion failed.")
            await self.session.commit()
            return response
        except BaseException as error:
            await self.session.rollback()
            if not isinstance(error, Exception) or isinstance(error, (
                ConstructionProjectAreaValidationError, ConstructionProjectAreaOperationError,
                ConstructionProjectAreaProjectNotFoundError, ConstructionProjectAreaNotFoundError,
                ConstructionProjectTerminalError, ConstructionPermissionDeniedError,
                ConstructionScopeDeniedError, ApiIdempotencyKeyReusedError,
            )):
                raise
            raise ConstructionProjectAreaOperationError("Area creation failed.") from None


def build_construction_area_create_workflow(
    *, session, idempotency_encryption_key=None,
):
    from database.repositories.construction import (
        ConstructionAccessRepository, ConstructionProjectAreaRepository,
        ConstructionProjectRepository,
    )
    return ConstructionAreaCreateWorkflow(
        session=session,
        area_service=ConstructionProjectAreaService(
            session=session, repository=ConstructionProjectAreaRepository(session),
            project_repository=ConstructionProjectRepository(session),
        ),
        access_repository=ConstructionAccessRepository(session),
        idempotency_service=(
            build_api_idempotency_service(
                session, encryption_key=idempotency_encryption_key,
            ) if idempotency_encryption_key is not None else None
        ),
    )
