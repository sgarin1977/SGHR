"""Atomic standalone client creation using existing domain and replay services."""
import hashlib
import inspect
from datetime import datetime, timezone
from uuid import UUID

from services.api_idempotency import (
    ApiIdempotencyKeyReusedError, build_api_idempotency_service,
    hash_request_payload,
)
from services.construction.construction_client_card_read import ClientCardClientResponse
from services.construction.construction_clients import (
    ConstructionClientService, ConstructionClientValidationError,
    ConstructionClientNotFoundError, ConstructionClientOperationError,
)
from services.construction.construction_grants import ConstructionAccessContextResolver
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError, require_construction_access,
)

OPERATION = "construction.client.create"
FIELDS = frozenset(ClientCardClientResponse.model_fields)

class ClientCreationInProgressError(ConstructionClientOperationError):
    code = "idempotency_in_progress"

class ConstructionClientCreateWorkflow:
    def __init__(self, *, session, client_service, access_repository,
                 idempotency_service=None, now_provider=None):
        collaborators = (
            client_service, getattr(client_service, "repository", None),
            getattr(getattr(client_service, "audit_logger", None), "event_repository", None),
            access_repository,
        )
        if idempotency_service is not None:
            collaborators += (getattr(idempotency_service, "repository", None),)
        if (
            session is None
            or any(getattr(item, "session", None) is not session for item in collaborators)
            or any(not callable(getattr(session, name, None))
                   for name in ("commit", "rollback", "in_transaction"))
            or not callable(getattr(client_service, "create_client", None))
            or "manage_transaction" not in inspect.signature(client_service.create_client).parameters
            or not callable(getattr(client_service.repository, "get_active_client", None))
            or any(not callable(getattr(access_repository, name, None))
                   for name in ("has_active_platform_membership", "list_active_grants"))
            or (
                idempotency_service is not None
                and any(not callable(getattr(idempotency_service, name, None))
                        for name in ("reserve", "complete"))
            )
            or (now_provider is not None and not callable(now_provider))
        ):
            raise ValueError("Client creation requires valid shared-session collaborators.")
        self.session = session
        self.service = client_service
        self.access = access_repository
        self.idempotency = idempotency_service
        self.clock = now_provider or (lambda: datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(
            repository=access_repository, now_provider=self._now,
        )

    def _now(self):
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionClientOperationError("Client creation clock is invalid.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _authorize(actor, context):
        if (
            getattr(actor, "status", None) != "active"
            or not isinstance(getattr(actor, "user_id", None), UUID)
            or not isinstance(getattr(actor, "tenant_id", None), UUID)
            or not isinstance(context, ConstructionAccessContext)
        ):
            raise ConstructionPermissionDeniedError("Client creation is unavailable.")
        require_construction_access(
            actor=actor, access_context=context,
            permission_code="construction.projects.create",
        )

    async def _fresh_context(self, actor):
        if await self.access.has_active_platform_membership(
            tenant_id=actor.tenant_id, user_id=actor.user_id,
        ) is not True:
            raise ConstructionPermissionDeniedError("Client creation membership is unavailable.")
        context = await self.resolver.resolve(actor=actor)
        self._authorize(actor, context)
        return context

    @staticmethod
    def _receipt(response, *, actor, payload):
        if not isinstance(response, dict) or set(response) != FIELDS:
            raise ConstructionClientOperationError("Client creation receipt is invalid.")
        item = ClientCardClientResponse.model_validate(response)
        public = item.model_dump(mode="json")
        if (
            public != response
            or item.tenant_id != actor.tenant_id
            or item.display_name != payload["display_name"].strip()
            or item.client_type != payload["client_type"].strip().lower()
            or item.phone != payload["phone"]
            or item.email != payload["email"]
            or item.notes != payload["notes"]
            or item.linked_user_id is not None
            or item.row_version != 1
        ):
            raise ConstructionClientOperationError("Client creation receipt binding differs.")
        return public

    async def _create(self, *, actor, context, payload, trace_id):
        row = await self.service.create_client(
            actor=actor, access_context=context,
            **payload, trace_id=trace_id, manage_transaction=False,
        )
        if (
            getattr(row, "tenant_id", None) != actor.tenant_id
            or not isinstance(getattr(row, "id", None), UUID)
            or getattr(row, "deleted_at", object()) is not None
        ):
            raise ConstructionClientOperationError("Created client context is invalid.")
        response = ClientCardClientResponse.model_validate({
            name: getattr(row, name) for name in FIELDS
        }).model_dump(mode="json")
        return self._receipt(response, actor=actor, payload=payload)

    async def create_client(self, *, actor, access_context, display_name,
                            client_type, phone=None, email=None, notes=None,
                            idempotency_key=None, trace_id=None):
        try:
            self._authorize(actor, access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionClientOperationError("An active client transaction is required.")
            if (
                not isinstance(display_name, str) or not display_name.strip()
                or not isinstance(client_type, str)
                or client_type.strip().lower() not in ("person", "company")
                or any(value is not None and not isinstance(value, str)
                       for value in (phone, email, notes))
                or (
                    idempotency_key is not None
                    and (
                        not isinstance(idempotency_key, str)
                        or not idempotency_key.strip()
                        or len(idempotency_key) > 255
                    )
                )
            ):
                raise ConstructionClientValidationError("Client creation input is invalid.")
            payload = dict(
                display_name=display_name, client_type=client_type,
                phone=phone, email=email, notes=notes,
            )
            reservation = None
            if idempotency_key is not None:
                if self.idempotency is None:
                    raise ConstructionClientOperationError("Client replay storage is not configured.")
                reservation = await self.idempotency.reserve(
                    tenant_id=actor.tenant_id,
                    principal_type="user", principal_id=actor.user_id,
                    operation=OPERATION, idempotency_key=idempotency_key,
                    payload=payload, now=self._now(),
                )
                record = reservation.record
                if (
                    record.tenant_id != actor.tenant_id
                    or record.principal_type != "user"
                    or record.principal_id != actor.user_id
                    or record.operation != OPERATION
                    or record.key_hash != hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
                    or record.request_hash != hash_request_payload(payload)
                    or type(reservation.is_replay) is not bool
                ):
                    raise ConstructionClientOperationError("Client reservation binding differs.")

            # The reservation may have waited for another transaction.
            context = await self._fresh_context(actor)
            if reservation is not None and reservation.is_replay:
                if reservation.response_status is None and reservation.record.status == "processing":
                    raise ClientCreationInProgressError("Client creation is processing.")
                if reservation.response_status != 201 or reservation.record.status != "completed":
                    raise ConstructionClientOperationError("Client replay state is invalid.")
                response = self._receipt(
                    reservation.response_payload, actor=actor, payload=payload,
                )
                row = await self.service.repository.get_active_client(
                    tenant_id=actor.tenant_id, client_id=UUID(response["id"]),
                )
                if (
                    row is None or getattr(row, "id", None) != UUID(response["id"])
                    or getattr(row, "tenant_id", None) != actor.tenant_id
                    or getattr(row, "deleted_at", object()) is not None
                ):
                    raise ConstructionClientNotFoundError("Created client is unavailable.")
            else:
                if reservation is not None and reservation.record.status != "processing":
                    raise ConstructionClientOperationError("Client reservation state is invalid.")
                response = await self._create(
                    actor=actor, context=context, payload=payload, trace_id=trace_id,
                )
                if reservation is not None:
                    await self.idempotency.complete(
                        reservation=reservation, response_status=201,
                        response_payload=response, now=self._now(),
                    )
            await self.session.commit()
            return response
        except BaseException as error:
            await self.session.rollback()
            if not isinstance(error, Exception) or isinstance(error, (
                ConstructionClientValidationError, ConstructionClientNotFoundError,
                ConstructionClientOperationError, ConstructionPermissionDeniedError,
                ConstructionScopeDeniedError, ApiIdempotencyKeyReusedError,
            )):
                raise
            raise ConstructionClientOperationError("Client creation failed.") from None

def build_construction_client_create_workflow(
    *, session, idempotency_encryption_key=None,
):
    from database.repositories.construction import (
        ConstructionClientRepository, ConstructionAccessRepository,
    )
    return ConstructionClientCreateWorkflow(
        session=session,
        client_service=ConstructionClientService(
            session=session, repository=ConstructionClientRepository(session),
        ),
        access_repository=ConstructionAccessRepository(session),
        idempotency_service=(
            build_api_idempotency_service(
                session, encryption_key=idempotency_encryption_key,
            )
            if idempotency_encryption_key is not None else None
        ),
    )
