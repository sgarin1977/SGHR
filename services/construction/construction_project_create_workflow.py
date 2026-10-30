import hashlib
import inspect
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

from services.api_idempotency import (
    ApiIdempotencyKeyReusedError, build_api_idempotency_service, hash_request_payload,
)
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError, require_construction_access,
)
from services.construction.construction_projects import (
    ConstructionProjectNotFoundError, ConstructionProjectOperationError,
    ConstructionProjectService, ConstructionProjectValidationError,
)

PROJECT_CREATE_OPERATION = "construction.project.create"
PUBLIC_PROJECT_FIELDS = frozenset({
    "id", "tenant_id", "client_id", "name", "status", "responsible_user_id",
    "comment", "address_raw", "address_formatted", "country_code", "region",
    "city", "postal_code", "latitude", "longitude", "address_provider",
    "provider_place_id", "address_verification_status", "created_by",
    "created_at", "updated_at", "row_version",
})


class ConstructionProjectIdempotencyInProgressError(ConstructionProjectOperationError):
    status_code = 409
    code = "idempotency_in_progress"


class ConstructionProjectCreateWorkflow:
    """Create and audit atomically, with optional encrypted idempotency replay."""

    def __init__(self, *, session, project_service, response_projector, idempotency_service=None, now_provider=None):
        collaborators = (
            project_service, getattr(project_service, "repository", None),
            getattr(project_service, "client_repository", None),
            getattr(project_service, "access_repository", None),
            getattr(getattr(project_service, "audit_logger", None), "event_repository", None),
        )
        if idempotency_service is not None:
            collaborators += (getattr(idempotency_service, "repository", None),)
        if (
            session is None or any(getattr(item, "session", None) is not session for item in collaborators)
            or any(not callable(getattr(session, name, None)) for name in ("commit", "rollback", "in_transaction"))
            or not callable(getattr(project_service, "create_project", None))
            or "manage_transaction" not in inspect.signature(project_service.create_project).parameters
            or not callable(getattr(project_service.repository, "get_active_project", None))
            or (
                idempotency_service is not None
                and any(not callable(getattr(idempotency_service, name, None)) for name in ("reserve", "complete"))
            )
            or not callable(response_projector)
            or (now_provider is not None and not callable(now_provider))
        ):
            raise ValueError("Project workflow requires valid shared-session wiring.")
        self.session = session
        self.service = project_service
        self.idempotency = idempotency_service
        self.projector = response_projector
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionProjectOperationError("Workflow clock must be aware.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _authorize(actor, access_context):
        if (
            getattr(actor, "status", None) != "active"
            or not isinstance(getattr(actor, "user_id", None), UUID)
            or not isinstance(getattr(actor, "tenant_id", None), UUID)
            or not isinstance(access_context, ConstructionAccessContext)
        ):
            raise ConstructionPermissionDeniedError("Construction project creation is not available.")
        require_construction_access(
            actor=actor, access_context=access_context, permission_code="construction.projects.create",
        )

    def _response(self, response, *, actor, payload):
        if not isinstance(response, dict) or set(response) != PUBLIC_PROJECT_FIELDS:
            raise ConstructionProjectOperationError("Project response is invalid.")
        response = json.loads(json.dumps(response, allow_nan=False))
        if (
            response["tenant_id"] != str(actor.tenant_id)
            or response["created_by"] != str(actor.user_id)
            or response["name"] != payload["name"].strip()
            or response["address_raw"] != payload["address_raw"].strip()
            or response["client_id"] != payload["client_id"]
            or response["responsible_user_id"] != payload["responsible_user_id"]
            or response["comment"] != payload["comment"]
            or response["status"] != "draft"
            or response["address_verification_status"] != "pending"
            or type(response["row_version"]) is not int or response["row_version"] != 1
        ):
            raise ConstructionProjectOperationError("Project response binding is invalid.")
        entity = SimpleNamespace(**response, deleted_at=None)
        for field in ("id", "tenant_id", "created_by", "client_id", "responsible_user_id"):
            value = response[field]
            if value is not None:
                if not isinstance(value, str) or str(UUID(value)) != value:
                    raise ConstructionProjectOperationError("Project response identifier is invalid.")
                setattr(entity, field, UUID(value))
        checked = self.projector(entity, tenant_id=actor.tenant_id, project_id=entity.id)
        if checked != response:
            raise ConstructionProjectOperationError("Project response is not canonical.")
        return response

    async def _create_response(
        self, *, actor, access_context, payload, client_id, responsible_user_id, trace_id,
    ):
        project = await self.service.create_project(
            actor=actor, access_context=access_context,
            name=payload["name"], address_raw=payload["address_raw"],
            client_id=client_id, responsible_user_id=responsible_user_id,
            comment=payload["comment"], trace_id=trace_id, manage_transaction=False,
        )
        if (
            not isinstance(getattr(project, "id", None), UUID)
            or getattr(project, "tenant_id", None) != actor.tenant_id
            or getattr(project, "created_by", None) != actor.user_id
            or getattr(project, "deleted_at", object()) is not None
        ):
            raise ConstructionProjectOperationError("Created project context is invalid.")
        return self._response(
            self.projector(project, tenant_id=actor.tenant_id, project_id=project.id),
            actor=actor, payload=payload,
        )

    async def create_project(
        self, *, actor, access_context, name, address_raw,
        client_id=None, responsible_user_id=None, comment=None,
        idempotency_key=None, trace_id=None,
    ):
        try:
            self._authorize(actor, access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionProjectOperationError("Project workflow requires an active transaction.")
            if (
                (
                    idempotency_key is not None
                    and (
                        not isinstance(idempotency_key, str)
                        or not idempotency_key.strip() or len(idempotency_key) > 255
                    )
                )
                or not isinstance(name, str) or not name.strip()
                or not isinstance(address_raw, str) or not address_raw.strip()
                or (client_id is not None and not isinstance(client_id, UUID))
                or (responsible_user_id is not None and not isinstance(responsible_user_id, UUID))
                or (comment is not None and not isinstance(comment, str))
            ):
                raise ConstructionProjectValidationError("Project creation request is invalid.")
            payload = {
                "name": name, "address_raw": address_raw,
                "client_id": str(client_id) if client_id is not None else None,
                "responsible_user_id": str(responsible_user_id) if responsible_user_id is not None else None,
                "comment": comment,
            }
            if idempotency_key is None:
                response = await self._create_response(
                    actor=actor, access_context=access_context, payload=payload,
                    client_id=client_id, responsible_user_id=responsible_user_id, trace_id=trace_id,
                )
                await self.session.commit()
                return response
            if self.idempotency is None:
                raise ConstructionProjectOperationError("Project idempotency is not configured.")
            reservation = await self.idempotency.reserve(
                tenant_id=actor.tenant_id, principal_type="user", principal_id=actor.user_id,
                operation=PROJECT_CREATE_OPERATION, idempotency_key=idempotency_key,
                payload=payload, now=self._now(),
            )
            record = reservation.record
            if (
                record.tenant_id != actor.tenant_id or record.principal_type != "user"
                or record.principal_id != actor.user_id or record.operation != PROJECT_CREATE_OPERATION
                or record.key_hash != hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
                or record.request_hash != hash_request_payload(payload)
                or type(reservation.is_replay) is not bool
            ):
                raise ConstructionProjectOperationError("Project reservation context is invalid.")
            if reservation.is_replay:
                if reservation.response_status is None and record.status == "processing":
                    raise ConstructionProjectIdempotencyInProgressError("Project creation is processing.")
                if reservation.response_status != 201 or record.status != "completed":
                    raise ConstructionProjectOperationError("Stored project response is invalid.")
                response = self._response(reservation.response_payload, actor=actor, payload=payload)
                self._authorize(actor, access_context)
                project_id = UUID(response["id"])
                project = await self.service.repository.get_active_project(
                    tenant_id=actor.tenant_id, project_id=project_id,
                )
                if (
                    project is None or getattr(project, "id", None) != project_id
                    or getattr(project, "tenant_id", None) != actor.tenant_id
                    or getattr(project, "deleted_at", object()) is not None
                    or getattr(project, "created_by", None) != actor.user_id
                ):
                    raise ConstructionProjectNotFoundError("Created project is not available.")
            else:
                if record.status != "processing":
                    raise ConstructionProjectOperationError("Project reservation state is invalid.")
                response = await self._create_response(
                    actor=actor, access_context=access_context, payload=payload,
                    client_id=client_id, responsible_user_id=responsible_user_id, trace_id=trace_id,
                )
                await self.idempotency.complete(
                    reservation=reservation, response_status=201,
                    response_payload=response, now=self._now(),
                )
            await self.session.commit()
            return response
        except Exception as error:
            await self.session.rollback()
            if isinstance(error, (
                ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionProjectValidationError, ConstructionProjectNotFoundError,
                ConstructionProjectOperationError, ApiIdempotencyKeyReusedError,
            )):
                raise
            raise ConstructionProjectOperationError("Project creation workflow failed.") from None


def build_construction_project_create_workflow(
    *, session, response_projector, idempotency_encryption_key=None, now_provider=None,
):
    from database.repositories.construction import (
        ConstructionAccessRepository, ConstructionClientRepository, ConstructionProjectRepository,
    )
    return ConstructionProjectCreateWorkflow(
        session=session,
        project_service=ConstructionProjectService(
            session=session, repository=ConstructionProjectRepository(session),
            client_repository=ConstructionClientRepository(session),
            access_repository=ConstructionAccessRepository(session),
        ),
        idempotency_service=(
            build_api_idempotency_service(session, encryption_key=idempotency_encryption_key)
            if idempotency_encryption_key is not None else None
        ),
        response_projector=response_projector, now_provider=now_provider,
    )
