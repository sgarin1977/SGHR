import asyncio
import hashlib
from datetime import datetime
from uuid import UUID

from services.api_idempotency import ApiIdempotencyKeyReusedError, build_api_idempotency_service, hash_request_payload
from services.construction_activity_create import (
    ConstructionActivityCreateCommand, ConstructionActivityCreateService,
    ConstructionActivityCreateNotFoundError, ConstructionActivityCreateOperationError,
    build_construction_activity_create_service,
)
from services.construction_activity_rules import (
    CONSTRUCTION_ACTIVITY_STATUSES, ConstructionActivityLinks, ConstructionActivityValidationError,
    normalize_construction_activity_schedule,
)
from services.construction_activity_response import (
    ActivityCreatedResponse, PUBLIC_ACTIVITY_CREATED_FIELDS, public_created_activity,
)
from services.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError


ACTIVITY_CREATE_OPERATION = "construction.activity.create"


class ConstructionActivityIdempotencyInProgressError(ConstructionActivityCreateOperationError):
    status_code = 409
    code = "idempotency_in_progress"


class ConstructionActivityReplayUnavailableError(ConstructionActivityCreateNotFoundError):
    status_code = 404
    code = "not_found"


class ConstructionActivityCreateWorkflow:
    """Reserve, create/audit, encrypt completion and commit in one shared transaction."""

    def __init__(self, *, session, activity_service, idempotency_service):
        collaborators = (
            activity_service, getattr(activity_service, "repository", None),
            getattr(activity_service, "lead_repository", None), getattr(activity_service, "client_repository", None),
            getattr(activity_service, "project_repository", None), getattr(activity_service, "access_repository", None),
            getattr(activity_service, "event_repository", None),
            getattr(getattr(activity_service, "audit_logger", None), "event_repository", None),
            getattr(idempotency_service, "repository", None),
        )
        codec = getattr(idempotency_service, "response_codec", None)
        if (
            session is None or not isinstance(activity_service, ConstructionActivityCreateService)
            or any(getattr(item, "session", None) is not session for item in collaborators)
            or any(not callable(getattr(session, name, None)) for name in ("commit", "rollback", "in_transaction"))
            or not callable(getattr(activity_service.repository, "get_active_activity_for_update", None))
            or any(not callable(getattr(idempotency_service, name, None)) for name in ("reserve", "complete"))
            or any(not callable(getattr(codec, name, None)) for name in ("encrypt", "decrypt"))
        ):
            raise ValueError("Activity workflow requires shared-session wiring and an encrypted response codec.")
        self.session, self.service, self.idempotency = session, activity_service, idempotency_service

    async def _current_access(self, actor, context, links):
        self.service._authorize(actor, context, links)
        current = await self.service._context(actor)
        self.service._authorize(actor, current, links)
        return current

    @staticmethod
    def _record(record, *, binding, instant):
        deadline = getattr(record, "expires_at", None)
        if (
            not isinstance(getattr(record, "id", None), UUID)
            or getattr(record, "tenant_id", None) != UUID(binding["tenant_id"])
            or getattr(record, "principal_id", None) != UUID(binding["principal_id"])
            or any(getattr(record, field, None) != binding[field]
                   for field in ("principal_type", "operation", "key_hash", "request_hash"))
            or not isinstance(deadline, datetime) or deadline.tzinfo is None or deadline.utcoffset() is None
            or deadline <= instant
        ):
            raise ConstructionActivityCreateOperationError("Activity reservation context is invalid.")

    @staticmethod
    def _response(value, *, actor, command):
        if not isinstance(value, dict) or set(value) != PUBLIC_ACTIVITY_CREATED_FIELDS:
            raise ConstructionActivityCreateOperationError("Activity response fields are invalid.")
        item = ActivityCreatedResponse.model_validate(value)
        checked = item.model_dump(mode="json")
        for field in ("id", "tenant_id", "lead_id", "client_id", "project_id", "responsible_user_id", "created_by_user_id"):
            if value[field] is not None and (not isinstance(value[field], str) or value[field] != checked[field]):
                raise ConstructionActivityCreateOperationError("Activity response identifier is not canonical.")
        if (
            item.tenant_id != actor.tenant_id or item.created_by_user_id != actor.user_id
            or item.type != command.activity_type or item.comment != command.comment
            or item.responsible_user_id != command.responsible_user_id
            or item.due_at != command.schedule.due_at or item.timezone != command.schedule.timezone
            or any(getattr(item, field) != getattr(command.links, field) for field in ("lead_id", "client_id", "project_id"))
            or item.created_at != item.updated_at
        ):
            raise ConstructionActivityCreateOperationError("Activity response binding is invalid.")
        return checked

    def _snapshot(self, value, *, binding, actor, command):
        if not isinstance(value, dict) or set(value) != {"binding", "data"} or value["binding"] != binding:
            raise ConstructionActivityCreateOperationError("Stored activity snapshot binding is invalid.")
        return self._response(value["data"], actor=actor, command=command)

    async def _replay_resource(self, *, actor, context, command, response):
        # Retain the creation lock order before locking the existing activity.
        for repo, method, field in (
            (self.service.lead_repository, "get_active_lead_for_update", "lead_id"),
            (self.service.client_repository, "get_active_client_for_update", "client_id"),
            (self.service.project_repository, "get_active_project_for_update", "project_id"),
        ):
            resource_id = getattr(command.links, field)
            if resource_id is not None:
                self.service._resource(await getattr(repo, method)(tenant_id=actor.tenant_id, **{field: resource_id}),
                                       tenant_id=actor.tenant_id, resource_id=resource_id)
        activity_id = UUID(response["id"])
        row = await self.service.repository.get_active_activity_for_update(tenant_id=actor.tenant_id, activity_id=activity_id)
        if (
            row is None or getattr(row, "id", None) != activity_id
            or getattr(row, "tenant_id", None) != actor.tenant_id
            or getattr(row, "created_by_user_id", None) != actor.user_id
            or getattr(row, "deleted_at", "missing") is not None
            or any(getattr(row, field, "missing") != getattr(command.links, field)
                   for field in ("lead_id", "client_id", "project_id"))
        ):
            raise ConstructionActivityReplayUnavailableError("Created activity is not available.")
        if (
            getattr(row, "status", None) not in CONSTRUCTION_ACTIVITY_STATUSES
            or type(getattr(row, "row_version", None)) is not int or not 1 <= row.row_version < 2**63
        ):
            raise ConstructionActivityCreateOperationError("Created activity state is invalid.")
        return await self._current_access(actor, context, command.links)

    async def _rollback(self):
        try:
            await self.session.rollback()
        except Exception:
            raise ConstructionActivityCreateOperationError("Activity transaction rollback failed.") from None

    async def create_activity(
        self, *, actor, access_context, activity_type, due_at, timezone_code,
        lead_id=None, client_id=None, project_id=None, responsible_user_id=None, comment=None,
        idempotency_key=None, trace_id=None,
    ):
        try:
            self.service._identity(actor, access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionActivityCreateOperationError("Activity workflow requires an active transaction.")
            if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 255:
                raise ConstructionActivityValidationError("Activity creation requires a valid Idempotency-Key.")
            command = ConstructionActivityCreateCommand(
                activity_type=activity_type, links=ConstructionActivityLinks(lead_id, client_id, project_id),
                schedule=normalize_construction_activity_schedule(due_at=due_at, timezone_code=timezone_code),
                responsible_user_id=responsible_user_id, comment=comment,
            )
            self.service._authorize(actor, access_context, command.links)
            # Bind supplied values, preserving whitespace and the supplied deadline offset.
            payload = dict(activity_type=activity_type, due_at=due_at.isoformat(), timezone_code=timezone_code,
                lead_id=str(lead_id) if lead_id is not None else None,
                client_id=str(client_id) if client_id is not None else None,
                project_id=str(project_id) if project_id is not None else None,
                responsible_user_id=str(responsible_user_id) if responsible_user_id is not None else None, comment=comment)
            with self.session.no_autoflush:
                context = await self._current_access(actor, access_context, command.links)
                instant = self.service._now()
                binding = dict(tenant_id=str(actor.tenant_id), principal_type="user", principal_id=str(actor.user_id),
                    operation=ACTIVITY_CREATE_OPERATION, key_hash=hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest(),
                    request_hash=hash_request_payload(payload))
                reservation = await self.idempotency.reserve(tenant_id=actor.tenant_id, principal_type="user",
                    principal_id=actor.user_id, operation=ACTIVITY_CREATE_OPERATION,
                    idempotency_key=idempotency_key, payload=payload, now=instant)
                record = reservation.record
                self._record(record, binding=binding, instant=instant)
                reserved_id = record.id
                if type(reservation.is_replay) is not bool:
                    raise ConstructionActivityCreateOperationError("Activity reservation mode is invalid.")
                context = await self._current_access(actor, context, command.links)
                if reservation.is_replay:
                    if record.status == "processing" and reservation.response_status is None:
                        raise ConstructionActivityIdempotencyInProgressError("Activity creation is processing.")
                    if record.status != "completed" or type(reservation.response_status) is not int or reservation.response_status != 201:
                        raise ConstructionActivityCreateOperationError("Stored activity response state is invalid.")
                    response = self._snapshot(reservation.response_payload, binding=binding, actor=actor, command=command)
                    context = await self._replay_resource(actor=actor, context=context, command=command, response=response)
                else:
                    if record.status != "processing":
                        raise ConstructionActivityCreateOperationError("Activity reservation state is invalid.")
                    row = await self.service.create_activity(actor=actor, access_context=context, command=command, trace_id=trace_id)
                    response = self._response(public_created_activity(row, tenant_id=actor.tenant_id, activity_id=row.id),
                                              actor=actor, command=command)
                    completed = await self.idempotency.complete(reservation=reservation, response_status=201,
                        response_payload={"binding": binding, "data": response}, now=self.service._now())
                    self._record(completed, binding=binding, instant=self.service._now())
                    if (
                        completed.id != reserved_id or completed.status != "completed"
                        or type(completed.response_status) is not int or completed.response_status != 201
                    ):
                        raise ConstructionActivityCreateOperationError("Completed activity reservation is invalid.")
                    stored = self.idempotency.response_codec.decrypt(completed.response_ciphertext)
                    if self._snapshot(stored, binding=binding, actor=actor, command=command) != response:
                        raise ConstructionActivityCreateOperationError("Completed activity snapshot is invalid.")
                await self._current_access(actor, context, command.links)
            await self.session.commit()
            return response
        except asyncio.CancelledError:
            await self._rollback()
            raise
        except Exception as error:
            await self._rollback()
            if isinstance(error, (ApiIdempotencyKeyReusedError, ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionActivityValidationError, ConstructionActivityCreateNotFoundError, ConstructionActivityCreateOperationError)):
                raise
            raise ConstructionActivityCreateOperationError("Activity creation workflow failed.") from None


def build_construction_activity_create_workflow(*, session, encryption_key, now_provider=None):
    return ConstructionActivityCreateWorkflow(session=session,
        activity_service=build_construction_activity_create_service(session=session, now_provider=now_provider),
        idempotency_service=build_api_idempotency_service(session, encryption_key=encryption_key))
