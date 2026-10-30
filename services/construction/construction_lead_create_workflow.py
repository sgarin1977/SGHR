import hashlib
from datetime import datetime, timezone
from uuid import UUID

from services.api_idempotency import ApiIdempotencyKeyReusedError, build_api_idempotency_service, hash_request_payload
from services.construction.construction_lead_create import (
    LEAD_INPUT_FIELDS, ConstructionLeadOperationError, build_construction_lead_create_service,
)
from services.construction.construction_lead_response import LeadCreatedResponse, PUBLIC_LEAD_CREATED_FIELDS, public_created_lead
from services.construction.construction_lead_rules import ConstructionLeadInput, ConstructionLeadValidationError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError

LEAD_CREATE_OPERATION = "construction.lead.create"


class ConstructionLeadIdempotencyInProgressError(ConstructionLeadOperationError):
    status_code = 409
    code = "idempotency_in_progress"


class ConstructionLeadReplayUnavailableError(ConstructionLeadOperationError):
    status_code = 404
    code = "not_found"


class ConstructionLeadCreateWorkflow:
    def __init__(self, *, session, lead_service, idempotency_service):
        collaborators = (
            lead_service, getattr(lead_service, "repository", None),
            getattr(lead_service, "access_repository", None),
            getattr(getattr(lead_service, "audit_logger", None), "event_repository", None),
            getattr(idempotency_service, "repository", None),
        )
        codec = getattr(idempotency_service, "response_codec", None)
        if (
            session is None or any(getattr(item, "session", None) is not session for item in collaborators)
            or any(not callable(getattr(session, name, None)) for name in ("commit", "rollback", "in_transaction"))
            or any(not callable(getattr(idempotency_service, name, None)) for name in ("reserve", "complete"))
            or any(not callable(getattr(codec, name, None)) for name in ("encrypt", "decrypt"))
            or not callable(getattr(lead_service, "create_lead", None))
            or not callable(getattr(lead_service.repository, "get_active_lead", None))
        ):
            raise ValueError("Lead workflow requires shared-session wiring and a response codec.")
        self.session, self.service, self.idempotency = session, lead_service, idempotency_service

    async def _current_access(self, actor, context):
        self.service._authorize(actor, context)
        if await self.service.access_repository.has_active_platform_membership(
            tenant_id=actor.tenant_id, user_id=actor.user_id,
        ) is not True:
            raise ConstructionPermissionDeniedError("Lead creation membership is not available.")
        context = await self.service.resolver.resolve(actor=actor)
        self.service._authorize(actor, context)
        return context

    @staticmethod
    def _record_context(record, binding, instant):
        if (
            not isinstance(getattr(record, "id", None), UUID)
            or getattr(record, "tenant_id", None) != UUID(binding["tenant_id"])
            or getattr(record, "principal_id", None) != UUID(binding["principal_id"])
            or any(getattr(record, field, None) != binding[field]
                   for field in ("principal_type", "operation", "key_hash", "request_hash"))
            or not isinstance(getattr(record, "expires_at", None), datetime)
            or record.expires_at.tzinfo is None or record.expires_at.utcoffset() is None
            or record.expires_at <= instant
        ):
            raise ConstructionLeadOperationError("Lead reservation context is invalid.")

    @staticmethod
    def _response(response, *, actor, data):
        if not isinstance(response, dict) or set(response) != PUBLIC_LEAD_CREATED_FIELDS:
            raise ConstructionLeadOperationError("Lead response fields are invalid.")
        item = LeadCreatedResponse.model_validate(response)
        checked = item.model_dump(mode="json")
        expected = {field: getattr(data, field) for field in LEAD_INPUT_FIELDS}
        expected["responsible_user_id"] = str(data.responsible_user_id) if data.responsible_user_id else None
        if item.tenant_id != actor.tenant_id or any(checked[field] != value for field, value in expected.items()):
            raise ConstructionLeadOperationError("Lead response binding is invalid.")
        return checked

    def _snapshot(self, value, *, binding, actor, data):
        if not isinstance(value, dict) or set(value) != {"binding", "data"} or value["binding"] != binding:
            raise ConstructionLeadOperationError("Stored lead response context is invalid.")
        return self._response(value["data"], actor=actor, data=data)

    async def create_lead(
        self, *, actor, access_context, source, name, work_type_description,
        phone=None, email=None, address_raw=None, comment=None, responsible_user_id=None,
        idempotency_key=None, trace_id=None,
    ):
        try:
            self.service._authorize(actor, access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionLeadOperationError("Lead workflow requires an active transaction.")
            if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 255:
                raise ConstructionLeadValidationError("Lead creation requires a valid Idempotency-Key.")
            data = ConstructionLeadInput(
                source=source, name=name, work_type_description=work_type_description,
                phone=phone, email=email, address_raw=address_raw, comment=comment,
                responsible_user_id=responsible_user_id,
            )
            # Hash the supplied command values; normalization belongs to the input/service.
            payload = dict(source=source, name=name, work_type_description=work_type_description,
                phone=phone, email=email, address_raw=address_raw, comment=comment,
                responsible_user_id=str(responsible_user_id) if responsible_user_id else None)
            context = await self._current_access(actor, access_context)
            instant = self.service._now()
            binding = dict(tenant_id=str(actor.tenant_id), principal_type="user", principal_id=str(actor.user_id),
                operation=LEAD_CREATE_OPERATION,
                key_hash=hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest(),
                request_hash=hash_request_payload(payload))
            reservation = await self.idempotency.reserve(
                tenant_id=actor.tenant_id, principal_type="user", principal_id=actor.user_id,
                operation=LEAD_CREATE_OPERATION, idempotency_key=idempotency_key, payload=payload, now=instant,
            )
            record = reservation.record
            self._record_context(record, binding, instant)
            if type(reservation.is_replay) is not bool:
                raise ConstructionLeadOperationError("Lead reservation mode is invalid.")
            if reservation.is_replay:
                if record.status == "processing" and reservation.response_status is None:
                    raise ConstructionLeadIdempotencyInProgressError("Lead creation is processing.")
                if record.status != "completed" or reservation.response_status != 201:
                    raise ConstructionLeadOperationError("Stored lead response state is invalid.")
                response = self._snapshot(reservation.response_payload, binding=binding, actor=actor, data=data)
                context = await self._current_access(actor, context)
                lead_id = UUID(response["id"])
                lead = await self.service.repository.get_active_lead(tenant_id=actor.tenant_id, lead_id=lead_id)
                if (
                    lead is None or getattr(lead, "id", None) != lead_id
                    or getattr(lead, "tenant_id", None) != actor.tenant_id
                    or getattr(lead, "deleted_at", "missing") is not None
                ):
                    raise ConstructionLeadReplayUnavailableError("Created lead is not available.")
            else:
                if record.status != "processing":
                    raise ConstructionLeadOperationError("Lead reservation state is invalid.")
                lead = await self.service.create_lead(
                    actor=actor, access_context=context, data=data, trace_id=trace_id,
                )
                response = self._response(
                    public_created_lead(lead, tenant_id=actor.tenant_id, lead_id=lead.id), actor=actor, data=data,
                )
                completed = await self.idempotency.complete(
                    reservation=reservation, response_status=201,
                    response_payload={"binding": binding, "data": response}, now=self.service._now(),
                )
                self._record_context(completed, binding, instant)
                if completed.id != record.id or completed.status != "completed" or completed.response_status != 201:
                    raise ConstructionLeadOperationError("Completed lead reservation is invalid.")
                stored = self.idempotency.response_codec.decrypt(completed.response_ciphertext)
                if self._snapshot(stored, binding=binding, actor=actor, data=data) != response:
                    raise ConstructionLeadOperationError("Completed lead response is invalid.")
            await self.session.commit()
            return response
        except Exception as error:
            await self.session.rollback()
            if isinstance(error, (
                ApiIdempotencyKeyReusedError, ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionLeadValidationError, ConstructionLeadOperationError,
            )):
                raise
            raise ConstructionLeadOperationError("Lead creation workflow failed.") from None


def build_construction_lead_create_workflow(*, session, encryption_key, now_provider=None):
    return ConstructionLeadCreateWorkflow(
        session=session,
        lead_service=build_construction_lead_create_service(session=session, now_provider=now_provider),
        idempotency_service=build_api_idempotency_service(session, encryption_key=encryption_key),
    )
