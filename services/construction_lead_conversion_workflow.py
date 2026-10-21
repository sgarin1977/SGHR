import hashlib
from datetime import datetime
from uuid import UUID

from services.api_idempotency import (
    ApiIdempotencyKeyReusedError, build_api_idempotency_service, hash_request_payload,
)
from services.construction_foundation import ConstructionRowVersionConflictError
from services.construction_lead_conversion import (
    ConstructionLeadConversionCommand, ConstructionLeadConversionService,
    ConstructionLeadConversionNotFoundError, ConstructionLeadConversionOperationError,
    build_construction_lead_conversion_service,
)
from services.construction_lead_conversion_rules import ConstructionLeadConversionSelection
from services.construction_lead_rules import ConstructionLeadTransitionError, ConstructionLeadValidationError
from services.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError

LEAD_CONVERT_OPERATION = "construction.lead.convert"
CONVERTED_RESPONSE_FIELDS = frozenset({
    "lead_id", "tenant_id", "client_id", "project_id", "status", "row_version",
})


class ConstructionLeadConversionInProgressError(ConstructionLeadConversionOperationError):
    status_code = 409
    code = "idempotency_in_progress"


def _response(value, *, tenant_id, lead_id):
    if not isinstance(value, dict) or set(value) != CONVERTED_RESPONSE_FIELDS:
        raise ConstructionLeadConversionOperationError("Conversion response fields are invalid.")
    checked = dict(value)
    for field in ("lead_id", "tenant_id", "client_id", "project_id"):
        item = checked[field]
        if not isinstance(item, str):
            raise ConstructionLeadConversionOperationError("Conversion response identifier is invalid.")
        try:
            parsed = UUID(item)
        except (ValueError, AttributeError):
            raise ConstructionLeadConversionOperationError("Conversion response identifier is invalid.") from None
        if str(parsed) != item:
            raise ConstructionLeadConversionOperationError("Conversion response identifier is not canonical.")
    if (
        checked["tenant_id"] != str(tenant_id) or checked["lead_id"] != str(lead_id)
        or checked["status"] != "won"
        or type(checked["row_version"]) is not int or not 0 < checked["row_version"] < 2**63
    ):
        raise ConstructionLeadConversionOperationError("Conversion response binding is invalid.")
    return checked


class ConstructionLeadConversionWorkflow:
    """Reserve, convert, encrypt completion and commit in one caller-opened transaction."""
    def __init__(self, *, session, conversion_service, idempotency_service):
        collaborators = (
            conversion_service, getattr(conversion_service, "repository", None),
            getattr(conversion_service, "client_repository", None),
            getattr(conversion_service, "project_repository", None),
            getattr(conversion_service, "access_repository", None),
            getattr(conversion_service, "event_repository", None),
            getattr(idempotency_service, "repository", None),
        )
        codec = getattr(idempotency_service, "response_codec", None)
        if (
            session is None or any(getattr(item, "session", None) is not session for item in collaborators)
            or not isinstance(conversion_service, ConstructionLeadConversionService)
            or any(not callable(getattr(session, name, None)) for name in ("commit", "rollback", "in_transaction"))
            or any(not callable(getattr(idempotency_service, name, None)) for name in ("reserve", "complete"))
            or any(not callable(getattr(codec, name, None)) for name in ("encrypt", "decrypt"))
        ):
            raise ValueError("Conversion workflow requires shared-session wiring and an encrypted response codec.")
        self.session, self.service, self.idempotency = session, conversion_service, idempotency_service

    async def _current_access(self, actor, context):
        self.service._authorize(actor, context, "construction.projects.edit")
        context = await self.service._context(actor)
        self.service._authorize(actor, context, "construction.projects.edit")
        return context

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
            raise ConstructionLeadConversionOperationError("Conversion reservation context is invalid.")

    @staticmethod
    def _snapshot(value, *, binding, actor, lead_id):
        if not isinstance(value, dict) or set(value) != {"binding", "data"} or value["binding"] != binding:
            raise ConstructionLeadConversionOperationError("Stored conversion snapshot binding is invalid.")
        return _response(value["data"], tenant_id=actor.tenant_id, lead_id=lead_id)

    @staticmethod
    def _project_result(result, *, actor, lead_id):
        if type(getattr(result, "is_replay", None)) is not bool:
            raise ConstructionLeadConversionOperationError("Conversion result mode is invalid.")
        lead, client, project = result.lead, result.client, result.project
        for item in (lead, client, project):
            if (
                not isinstance(getattr(item, "id", None), UUID)
                or getattr(item, "tenant_id", None) != actor.tenant_id
                or getattr(item, "deleted_at", "missing") is not None
            ):
                raise ConstructionLeadConversionNotFoundError("Conversion resources are not available.")
        if (
            lead.id != lead_id or lead.status != "won" or lead.converted_client_id != client.id
            or lead.converted_project_id != project.id or project.client_id != client.id
        ):
            raise ConstructionLeadConversionOperationError("Conversion result relationships are invalid.")
        return _response(dict(lead_id=str(lead.id), tenant_id=str(actor.tenant_id),
            client_id=str(client.id), project_id=str(project.id), status="won", row_version=lead.row_version),
            tenant_id=actor.tenant_id, lead_id=lead_id)

    async def convert_lead(
        self, *, actor, access_context, lead_id, expected_row_version,
        client_id=None, project_id=None, client_type=None, project_name=None,
        project_address_raw=None, idempotency_key=None, trace_id=None,
    ):
        try:
            self.service._authorize(actor, access_context, "construction.projects.edit")
            if self.session.in_transaction() is not True:
                raise ConstructionLeadConversionOperationError("Conversion workflow requires an active transaction.")
            if not isinstance(lead_id, UUID):
                raise ConstructionLeadValidationError("Lead identifier must be a UUID.")
            if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 255:
                raise ConstructionLeadValidationError("Conversion requires a valid Idempotency-Key.")
            command = ConstructionLeadConversionCommand(
                expected_row_version=expected_row_version,
                selection=ConstructionLeadConversionSelection(client_id=client_id, project_id=project_id),
                client_type=client_type, project_name=project_name, project_address_raw=project_address_raw,
            )
            # Bind the raw command, before service normalization, including path and version.
            payload = dict(lead_id=str(lead_id), expected_row_version=expected_row_version,
                client_id=str(client_id) if client_id is not None else None,
                project_id=str(project_id) if project_id is not None else None,
                client_type=client_type, project_name=project_name, project_address_raw=project_address_raw)
            context = await self._current_access(actor, access_context)
            instant = self.service._now()
            binding = dict(tenant_id=str(actor.tenant_id), principal_type="user", principal_id=str(actor.user_id),
                operation=LEAD_CONVERT_OPERATION, key_hash=hashlib.sha256(idempotency_key.encode()).hexdigest(),
                request_hash=hash_request_payload(payload))
            reservation = await self.idempotency.reserve(
                tenant_id=actor.tenant_id, principal_type="user", principal_id=actor.user_id,
                operation=LEAD_CONVERT_OPERATION, idempotency_key=idempotency_key, payload=payload, now=instant)
            record = reservation.record
            self._record(record, binding=binding, instant=instant)
            reserved_id = record.id
            if type(reservation.is_replay) is not bool:
                raise ConstructionLeadConversionOperationError("Conversion reservation mode is invalid.")
            if reservation.is_replay:
                if record.status == "processing" and reservation.response_status is None:
                    raise ConstructionLeadConversionInProgressError("Lead conversion is processing.")
                if record.status != "completed" or type(reservation.response_status) is not int or reservation.response_status != 200:
                    raise ConstructionLeadConversionOperationError("Stored conversion state is invalid.")
                response = self._snapshot(reservation.response_payload, binding=binding, actor=actor, lead_id=lead_id)
                context = await self._current_access(actor, context)
                # Hold the lead lock before calling the domain service: replay must never create targets.
                with self.session.no_autoflush:
                    lead = await self.service.repository.get_active_lead_for_update(
                        tenant_id=actor.tenant_id, lead_id=lead_id)
                    if (
                        lead is None or getattr(lead, "id", None) != lead_id
                        or getattr(lead, "tenant_id", None) != actor.tenant_id
                        or getattr(lead, "deleted_at", "missing") is not None
                    ):
                        raise ConstructionLeadConversionNotFoundError("Converted lead is not available.")
                    if (
                        getattr(lead, "status", None) != "won"
                        or getattr(lead, "converted_client_id", None) != UUID(response["client_id"])
                        or getattr(lead, "converted_project_id", None) != UUID(response["project_id"])
                        or type(getattr(lead, "row_version", None)) is not int
                        or lead.row_version < response["row_version"]
                    ):
                        raise ConstructionLeadConversionOperationError("Recorded conversion no longer matches the lead.")
                    result = await self.service.convert_lead(actor=actor, access_context=context,
                        lead_id=lead_id, command=command, trace_id=trace_id)
                    checked = self._project_result(result, actor=actor, lead_id=lead_id)
                    if (
                        result.is_replay is not True
                        or any(checked[field] != response[field] for field in CONVERTED_RESPONSE_FIELDS - {"row_version"})
                        or checked["row_version"] < response["row_version"]
                    ):
                        raise ConstructionLeadConversionOperationError("Conversion replay is inconsistent.")
            else:
                if record.status != "processing":
                    raise ConstructionLeadConversionOperationError("Conversion reservation state is invalid.")
                context = await self._current_access(actor, context)
                result = await self.service.convert_lead(actor=actor, access_context=context,
                    lead_id=lead_id, command=command, trace_id=trace_id)
                response = self._project_result(result, actor=actor, lead_id=lead_id)
                completed = await self.idempotency.complete(reservation=reservation,
                    response_status=200, response_payload={"binding": binding, "data": response},
                    now=self.service._now())
                self._record(completed, binding=binding, instant=instant)
                if (
                    completed.id != reserved_id or completed.status != "completed"
                    or type(completed.response_status) is not int or completed.response_status != 200
                ):
                    raise ConstructionLeadConversionOperationError("Completed conversion reservation is invalid.")
                stored = self.idempotency.response_codec.decrypt(completed.response_ciphertext)
                if self._snapshot(stored, binding=binding, actor=actor, lead_id=lead_id) != response:
                    raise ConstructionLeadConversionOperationError("Completed conversion snapshot is invalid.")
            await self.session.commit()
            return response
        except Exception as error:
            await self.session.rollback()
            if isinstance(error, (
                ApiIdempotencyKeyReusedError, ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionLeadValidationError, ConstructionLeadTransitionError,
                ConstructionRowVersionConflictError, ConstructionLeadConversionNotFoundError,
                ConstructionLeadConversionOperationError,
            )):
                raise
            raise ConstructionLeadConversionOperationError("Lead conversion workflow failed.") from None


def build_construction_lead_conversion_workflow(*, session, encryption_key, now_provider=None):
    return ConstructionLeadConversionWorkflow(
        session=session,
        conversion_service=build_construction_lead_conversion_service(session=session, now_provider=now_provider),
        idempotency_service=build_api_idempotency_service(session, encryption_key=encryption_key),
    )
