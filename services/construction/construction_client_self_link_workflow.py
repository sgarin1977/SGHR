"""Atomic authenticated self-link with optional encrypted receipt replay.

The target is always the authenticated Platform actor. Every request, including
replay, refreshes canonical identity, membership and CRM edit authority. Replay
returns an historical receipt and never reapplies a link to a newer client state.
Public HTTP paths and Idempotency-Key requiredness are outside this workflow.
"""
from datetime import datetime, timezone
import hashlib
from uuid import UUID

from database.construction import set_construction_tenant_context
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.api_idempotency import (
    ApiIdempotencyReservation, ApiIdempotencyKeyReusedError,
    build_api_idempotency_service, hash_request_payload,
)
from services.construction.construction_client_link_confirmation_workflow import ConstructionClientLinkIdempotencyInProgressError
from services.construction.construction_client_self_link import (
    ConstructionClientSelfLinkService, ConstructionClientSelfLinkResult,
    build_construction_client_self_link_service,
)
from services.construction.construction_clients import (
    ConstructionClientValidationError, ConstructionClientNotFoundError, ConstructionClientOperationError,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError

SELF_LINK = 'construction.client_link.self'


def _error():
    return ConstructionClientOperationError('Client self-link workflow state is invalid.')


def _uuid(value):
    if not isinstance(value, str):
        raise _error()
    try:
        identifier = UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise _error() from None
    if str(identifier) != value:
        raise _error()
    return identifier


def _time(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _error()
    return value.astimezone(timezone.utc)


class ConstructionClientSelfLinkWorkflow:
    def __init__(self, *, session, self_link_service, idempotency_service, now_provider=None):
        if (not isinstance(self_link_service, ConstructionClientSelfLinkService)
                or self_link_service.session is not session
                or (idempotency_service is not None and (
                    getattr(getattr(idempotency_service, 'repository', None), 'session', None) is not session
                    or not callable(getattr(getattr(idempotency_service, 'repository', None), 'reserve_record', None))
                    or not callable(getattr(getattr(idempotency_service, 'response_codec', None), 'decrypt', None))))
                or not all(callable(getattr(session, name, None)) for name in ('execute', 'in_transaction', 'commit', 'rollback'))
                or not hasattr(session, 'no_autoflush')
                or (now_provider is not None and not callable(now_provider))):
            raise ValueError('Self-link workflow requires shared writes and atomic encrypted idempotency.')
        self.session, self.service, self.idempotency = session, self_link_service, idempotency_service
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def _now(self):
        return _time(self.now_provider())

    @staticmethod
    def _snapshot(result):
        if not isinstance(result, ConstructionClientSelfLinkResult):
            raise _error()
        return {name: str(value) if isinstance(value, UUID) else value
            for name in result.__dataclass_fields__ for value in (getattr(result, name),)}

    @staticmethod
    def _result(data, *, actor, client_id, expected_row_version, replacement):
        if not isinstance(data, dict) or set(data) != set(ConstructionClientSelfLinkResult.__dataclass_fields__):
            raise _error()
        fields = dict(data)
        for name in ('client_id', 'tenant_id', 'linked_user_id'):
            fields[name] = _uuid(data[name])
        fields['audit_id'] = _uuid(data['audit_id']) if data['audit_id'] is not None else None
        if (fields['client_id'] != client_id or fields['tenant_id'] != actor.tenant_id
                or fields['linked_user_id'] != actor.user_id
                or type(data['changed']) is not bool or type(data['row_version']) is not int
                or not 1 <= data['row_version'] < 2**63
                or data['row_version'] != expected_row_version + int(data['changed'])
                or (fields['audit_id'] is not None) != data['changed']
                or (replacement and not data['changed'])):
            raise _error()
        return ConstructionClientSelfLinkResult(**fields)

    def _record(self, row, binding):
        if (not isinstance(getattr(row, 'id', None), UUID)
                or getattr(row, 'tenant_id', None) != _uuid(binding['tenant_id'])
                or getattr(row, 'principal_id', None) != _uuid(binding['principal_id'])
                or any(getattr(row, name, None) != binding[name]
                    for name in ('principal_type', 'operation', 'key_hash', 'request_hash'))
                or _time(getattr(row, 'created_at', None)) > self._now()
                or _time(getattr(row, 'expires_at', None)) <= self._now()):
            raise _error()

    async def link_self(self, *, actor, access_context, client_id, expected_row_version,
            idempotency_key=None, trace_id=None):
        return await self._change(actor=actor, access_context=access_context, client_id=client_id,
            expected_row_version=expected_row_version, previous_user_id=None, replacement=False,
            idempotency_key=idempotency_key, trace_id=trace_id)

    async def replace_with_self(self, *, actor, access_context, client_id, previous_user_id,
            expected_row_version, idempotency_key=None, trace_id=None):
        return await self._change(actor=actor, access_context=access_context, client_id=client_id,
            expected_row_version=expected_row_version, previous_user_id=previous_user_id, replacement=True,
            idempotency_key=idempotency_key, trace_id=trace_id)

    async def _change(self, *, actor, access_context, client_id, expected_row_version,
            previous_user_id, replacement, idempotency_key, trace_id):
        try:
            if (not isinstance(actor, ApiActorContext) or actor.status != 'active'
                    or not isinstance(actor.user_id, UUID) or not isinstance(actor.tenant_id, UUID)):
                raise ApiIdentityAccessError('Authenticated Platform actor is not available.')
            self.service._scope(actor, access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionClientOperationError('Self-link workflow requires a caller transaction.')
            if (not isinstance(client_id, UUID) or type(expected_row_version) is not int
                    or not 1 <= expected_row_version < 2**63
                    or (replacement and (not isinstance(previous_user_id, UUID) or previous_user_id == actor.user_id))
                    or (idempotency_key is not None and (not isinstance(idempotency_key, str)
                        or not idempotency_key.strip() or len(idempotency_key) > 255))
                    or (trace_id is not None and (not isinstance(trace_id, str) or not trace_id.strip() or len(trace_id) > 128))):
                raise ConstructionClientValidationError('Client self-link request is invalid.')
            if idempotency_key is not None and self.idempotency is None:
                raise ConstructionClientOperationError('Client self-link replay is not configured.')
            with self.session.no_autoflush:
                # Bind the authenticated server tenant before membership/grant SQL.
                await set_construction_tenant_context(self.session, tenant_id=actor.tenant_id)
                await self.service._authorize(actor)
                reservation = None
                validation = dict(actor=actor, client_id=client_id,
                    expected_row_version=expected_row_version, replacement=replacement)
                if idempotency_key is not None:
                    payload = dict(client_id=str(client_id), expected_row_version=expected_row_version,
                        replacement=replacement, previous_user_id=str(previous_user_id) if replacement else None)
                    binding = dict(tenant_id=str(actor.tenant_id), principal_type='user', principal_id=str(actor.user_id),
                        operation=SELF_LINK, key_hash=hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest(),
                        request_hash=hash_request_payload(payload))
                    reservation = await self.idempotency.reserve(tenant_id=actor.tenant_id, principal_type='user',
                        principal_id=actor.user_id, operation=SELF_LINK, idempotency_key=idempotency_key,
                        payload=payload, now=self._now())
                    if not isinstance(reservation, ApiIdempotencyReservation) or type(reservation.is_replay) is not bool:
                        raise _error()
                    self._record(reservation.record, binding)
                if reservation is not None and reservation.is_replay:
                    if reservation.record.status == 'processing' and reservation.response_status is None:
                        raise ConstructionClientLinkIdempotencyInProgressError('Client self-link is still processing.')
                    snapshot = reservation.response_payload
                    if (reservation.record.status != 'completed' or type(reservation.response_status) is not int
                            or reservation.response_status != 200 or not isinstance(snapshot, dict)
                            or set(snapshot) != {'binding', 'data'} or snapshot['binding'] != binding):
                        raise _error()
                    result = self._result(snapshot['data'], **validation)
                else:
                    if reservation is not None and reservation.record.status != 'processing':
                        raise _error()
                    common = dict(actor=actor, access_context=access_context, client_id=client_id,
                        expected_row_version=expected_row_version, trace_id=trace_id)
                    result = await (self.service.replace_with_self(**common, previous_user_id=previous_user_id)
                        if replacement else self.service.link_self(**common))
                    data = self._snapshot(result)
                    result = self._result(data, **validation)
                    if reservation is not None:
                        snapshot = dict(binding=binding, data=data)
                        reserved_id = reservation.record.id
                        row = await self.idempotency.complete(reservation=reservation, response_status=200,
                            response_payload=snapshot, now=self._now())
                        self._record(row, binding)
                        if (row.id != reserved_id or row.status != 'completed' or type(row.response_status) is not int
                                or row.response_status != 200
                                or self.idempotency.response_codec.decrypt(row.response_ciphertext) != snapshot):
                            raise _error()
                await self.service._authorize(actor)
            await self.session.commit()
            return result
        except (ConstructionClientValidationError, ConstructionClientOperationError, ConstructionClientNotFoundError,
                ApiIdentityAccessError, ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionRowVersionConflictError, ApiIdempotencyKeyReusedError):
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise ConstructionClientOperationError('Client self-link workflow failed.') from None
        except BaseException:
            await self.session.rollback()
            raise


def build_construction_client_self_link_workflow(*, session, platform_session, encryption_key=None, now_provider=None):
    return ConstructionClientSelfLinkWorkflow(session=session,
        self_link_service=build_construction_client_self_link_service(
            session=session, platform_session=platform_session, now_provider=now_provider),
        idempotency_service=(build_api_idempotency_service(session, encryption_key=encryption_key)
            if encryption_key is not None else None), now_provider=now_provider)
