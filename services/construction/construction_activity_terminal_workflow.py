import asyncio
import hashlib
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_validator

from services.api_idempotency import ApiIdempotencyKeyReusedError, build_api_idempotency_service, hash_request_payload
from services.construction.construction_activity_rules import ConstructionActivityLinks, ConstructionActivityValidationError, ConstructionActivityTransitionError, normalize_construction_activity_schedule, ACTIVITY_RESULT_UNSET
from services.construction.construction_activity_terminal import (
    ConstructionActivityTerminalCommand, ConstructionActivityTerminalService,
    ConstructionActivityTerminalNotFoundError, ConstructionActivityTerminalOperationError,
    build_construction_activity_terminal_service,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError


ACTIVITY_COMPLETE_OPERATION = "construction.activity.complete"
ACTIVITY_CANCEL_OPERATION = "construction.activity.cancel"


class ConstructionActivityTerminalInProgressError(ConstructionActivityTerminalOperationError):
    status_code = 409
    code = "idempotency_in_progress"


class ConstructionActivityTerminalReplayUnavailableError(ConstructionActivityTerminalNotFoundError):
    status_code = 404
    code = "not_found"


class ActivityTerminalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    type: Literal["call", "message", "meeting", "site_visit", "follow_up", "task", "other"]
    status: Literal["completed", "cancelled"]
    lead_id: UUID | None
    client_id: UUID | None
    project_id: UUID | None
    due_at: AwareDatetime
    timezone: StrictStr = Field(min_length=1)
    responsible_user_id: UUID | None
    comment: StrictStr | None
    result: StrictStr | None
    completed_at: AwareDatetime | None
    cancelled_at: AwareDatetime | None
    created_by_user_id: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
    row_version: StrictInt = Field(ge=2, le=2**63-1)

    @field_validator("due_at", "created_at", "updated_at", "completed_at", "cancelled_at")
    @classmethod
    def utc_timestamp(cls, value):
        return value.astimezone(timezone.utc) if value is not None else None

    @model_validator(mode="after")
    def terminal_invariants(self):
        ConstructionActivityLinks(self.lead_id, self.client_id, self.project_id)
        normalize_construction_activity_schedule(due_at=self.due_at, timezone_code=self.timezone)
        terminal = self.completed_at if self.status == "completed" else self.cancelled_at
        other = self.cancelled_at if self.status == "completed" else self.completed_at
        if terminal is None or other is not None or terminal != self.updated_at:
            raise ValueError("Activity terminal response timestamps are invalid.")
        return self


PUBLIC_ACTIVITY_TERMINAL_FIELDS = frozenset(ActivityTerminalResponse.model_fields)


class ConstructionActivityTerminalWorkflow:
    """Terminal change, mandatory audit and encrypted replay share one transaction."""

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
            session is None or not isinstance(activity_service, ConstructionActivityTerminalService)
            or any(getattr(item, "session", None) is not session for item in collaborators)
            or any(not callable(getattr(session, name, None)) for name in ("commit", "rollback", "in_transaction"))
            or any(not callable(getattr(idempotency_service, name, None)) for name in ("reserve", "complete"))
            or any(not callable(getattr(codec, name, None)) for name in ("encrypt", "decrypt"))
        ):
            raise ValueError("Activity terminal workflow requires shared-session wiring and an encrypted response codec.")
        self.session, self.service, self.idempotency = session, activity_service, idempotency_service

    async def _current_links(self, actor, context, activity_id):
        self.service._scope(actor, context)
        current = await self.service._context(actor)
        allowed = self.service._scope(actor, current)
        value = await self.service.repository.find_active_activity_links(
            tenant_id=actor.tenant_id, activity_id=activity_id, allowed_project_ids=allowed)
        if value is None:
            raise ConstructionActivityTerminalNotFoundError("Activity is not available in the authorized scope.")
        links = self.service._links(value, activity_id)
        self.service._authorize_links(actor, current, links)
        return current, links

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
            raise ConstructionActivityTerminalOperationError("Activity reservation context is invalid.")

    @staticmethod
    def _response(value, *, actor, activity_id, command, status, links):
        if not isinstance(value, dict) or set(value) != PUBLIC_ACTIVITY_TERMINAL_FIELDS:
            raise ConstructionActivityTerminalOperationError("Activity terminal response fields are invalid.")
        item = ActivityTerminalResponse.model_validate(value)
        checked = item.model_dump(mode="json")
        for field in ("id", "tenant_id", "lead_id", "client_id", "project_id", "responsible_user_id", "created_by_user_id"):
            if value[field] is not None and (not isinstance(value[field], str) or value[field] != checked[field]):
                raise ConstructionActivityTerminalOperationError("Activity response identifier is not canonical.")
        if (
            item.id != activity_id or item.tenant_id != actor.tenant_id or item.status != status
            or item.row_version != command.expected_row_version+1 or (command.result_provided and item.result != command.result)
            or any(getattr(item, field) != getattr(links, field) for field in ("lead_id", "client_id", "project_id"))
        ):
            raise ConstructionActivityTerminalOperationError("Activity terminal response binding is invalid.")
        return checked

    def _snapshot(self, value, *, binding, actor, activity_id, command, status, links):
        if not isinstance(value, dict) or set(value) != {"binding", "data"} or value["binding"] != binding:
            raise ConstructionActivityTerminalOperationError("Stored activity snapshot binding is invalid.")
        return self._response(value["data"], actor=actor, activity_id=activity_id, command=command, status=status, links=links)

    async def _replay_resource(self, *, actor, context, activity_id, links, response, status):
        # The same parent-before-activity order is used by creation and terminal services.
        for repo, method, field in (
            (self.service.lead_repository, "get_active_lead_for_update", "lead_id"),
            (self.service.client_repository, "get_active_client_for_update", "client_id"),
            (self.service.project_repository, "get_active_project_for_update", "project_id"),
        ):
            reference = getattr(links, field)
            if reference is not None:
                self.service._resource(await getattr(repo, method)(tenant_id=actor.tenant_id, **{field: reference}),
                                       tenant_id=actor.tenant_id, resource_id=reference)
        current = await self.service._context(actor)
        self.service._authorize_links(actor, current, links)
        row = await self.service.repository.get_active_activity_for_update_by_links(
            tenant_id=actor.tenant_id, activity_id=activity_id, links=links)
        if (
            row is None or getattr(row, "id", None) != activity_id
            or getattr(row, "tenant_id", None) != actor.tenant_id or getattr(row, "deleted_at", "missing") is not None
            or any(getattr(row, field, "missing") != getattr(links, field) for field in ("lead_id", "client_id", "project_id"))
        ):
            raise ConstructionActivityTerminalReplayUnavailableError("Activity replay resource is not available.")
        if (
            getattr(row, "status", None) != status or type(getattr(row, "row_version", None)) is not int
            or not response["row_version"] <= row.row_version < 2**63
        ):
            raise ConstructionActivityTerminalOperationError("Activity replay resource state is invalid.")
        terminal_field = "completed_at" if status == "completed" else "cancelled_at"
        other_field = "cancelled_at" if status == "completed" else "completed_at"
        value = getattr(row, terminal_field, None)
        if (
            not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None
            or getattr(row, other_field, "missing") is not None
        ):
            raise ConstructionActivityTerminalOperationError("Activity replay timestamps are invalid.")
        return current

    async def _rollback(self):
        try:
            await self.session.rollback()
        except Exception:
            raise ConstructionActivityTerminalOperationError("Activity transaction rollback failed.") from None

    async def _finalize(self, *, actor, access_context, activity_id, expected_row_version,
                        result, idempotency_key, trace_id, status, operation):
        try:
            self.service._scope(actor, access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionActivityTerminalOperationError("Activity workflow requires an active transaction.")
            if not isinstance(activity_id, UUID):
                raise ConstructionActivityValidationError("Activity operation requires a UUID.")
            if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 255:
                raise ConstructionActivityValidationError("Activity terminal operation requires a valid Idempotency-Key.")
            command = ConstructionActivityTerminalCommand(expected_row_version, result)
            payload = dict(activity_id=str(activity_id), expected_row_version=expected_row_version)
            if command.result_provided:
                payload["result"] = result
            with self.session.no_autoflush:
                context, links = await self._current_links(actor, access_context, activity_id)
                instant = self.service._now()
                binding = dict(tenant_id=str(actor.tenant_id), principal_type="user", principal_id=str(actor.user_id),
                    operation=operation, key_hash=hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest(),
                    request_hash=hash_request_payload(payload))
                reservation = await self.idempotency.reserve(tenant_id=actor.tenant_id, principal_type="user",
                    principal_id=actor.user_id, operation=operation, idempotency_key=idempotency_key,
                    payload=payload, now=instant)
                record = reservation.record
                self._record(record, binding=binding, instant=instant)
                reserved_id = record.id
                if type(reservation.is_replay) is not bool:
                    raise ConstructionActivityTerminalOperationError("Activity reservation mode is invalid.")
                context, current_links = await self._current_links(actor, context, activity_id)
                if current_links != links:
                    raise ConstructionRowVersionConflictError()
                if reservation.is_replay:
                    if record.status == "processing" and reservation.response_status is None:
                        raise ConstructionActivityTerminalInProgressError("Activity operation is processing.")
                    if record.status != "completed" or type(reservation.response_status) is not int or reservation.response_status != 200:
                        raise ConstructionActivityTerminalOperationError("Stored activity response state is invalid.")
                    response = self._snapshot(reservation.response_payload, binding=binding,
                        actor=actor, activity_id=activity_id, command=command, status=status, links=links)
                    context = await self._replay_resource(actor=actor, context=context, activity_id=activity_id,
                        links=links, response=response, status=status)
                else:
                    if record.status != "processing":
                        raise ConstructionActivityTerminalOperationError("Activity reservation state is invalid.")
                    method = self.service.complete_activity if status == "completed" else self.service.cancel_activity
                    row = await method(actor=actor, access_context=context, activity_id=activity_id,
                        command=command, trace_id=trace_id)
                    if getattr(row, "deleted_at", "missing") is not None:
                        raise ConstructionActivityTerminalOperationError("Activity response resource is not available.")
                    value = ActivityTerminalResponse.model_validate({
                        field: getattr(row, field) for field in PUBLIC_ACTIVITY_TERMINAL_FIELDS
                    }).model_dump(mode="json")
                    response = self._response(value, actor=actor, activity_id=activity_id,
                        command=command, status=status, links=links)
                    completed = await self.idempotency.complete(reservation=reservation, response_status=200,
                        response_payload={"binding": binding, "data": response}, now=self.service._now())
                    self._record(completed, binding=binding, instant=self.service._now())
                    if (
                        completed.id != reserved_id or completed.status != "completed"
                        or type(completed.response_status) is not int or completed.response_status != 200
                    ):
                        raise ConstructionActivityTerminalOperationError("Completed activity reservation is invalid.")
                    stored = self.idempotency.response_codec.decrypt(completed.response_ciphertext)
                    if self._snapshot(stored, binding=binding, actor=actor, activity_id=activity_id,
                        command=command, status=status, links=links) != response:
                        raise ConstructionActivityTerminalOperationError("Completed activity snapshot is invalid.")
                _, final_links = await self._current_links(actor, context, activity_id)
                if final_links != links:
                    raise ConstructionRowVersionConflictError()
            await self.session.commit()
            return response
        except asyncio.CancelledError:
            await self._rollback()
            raise
        except Exception as error:
            await self._rollback()
            if isinstance(error, (ApiIdempotencyKeyReusedError, ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionActivityValidationError, ConstructionActivityTransitionError, ConstructionRowVersionConflictError,
                ConstructionActivityTerminalNotFoundError, ConstructionActivityTerminalOperationError)):
                raise
            raise ConstructionActivityTerminalOperationError("Activity terminal workflow failed.") from None

    async def complete_activity(self, *, actor, access_context, activity_id, expected_row_version,
                                result=ACTIVITY_RESULT_UNSET, idempotency_key=None, trace_id=None):
        return await self._finalize(actor=actor, access_context=access_context, activity_id=activity_id,
            expected_row_version=expected_row_version, result=result, idempotency_key=idempotency_key,
            trace_id=trace_id, status="completed", operation=ACTIVITY_COMPLETE_OPERATION)

    async def cancel_activity(self, *, actor, access_context, activity_id, expected_row_version,
                              result=ACTIVITY_RESULT_UNSET, idempotency_key=None, trace_id=None):
        return await self._finalize(actor=actor, access_context=access_context, activity_id=activity_id,
            expected_row_version=expected_row_version, result=result, idempotency_key=idempotency_key,
            trace_id=trace_id, status="cancelled", operation=ACTIVITY_CANCEL_OPERATION)


def build_construction_activity_terminal_workflow(*, session, encryption_key, now_provider=None):
    return ConstructionActivityTerminalWorkflow(session=session,
        activity_service=build_construction_activity_terminal_service(session=session, now_provider=now_provider),
        idempotency_service=build_api_idempotency_service(session, encryption_key=encryption_key))
