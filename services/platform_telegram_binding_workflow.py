"""Atomic Platform binding initiation/consume with secret-free idempotency.

The caller opens the Platform transaction. This workflow commits business data,
mandatory audit and the shared idempotency record together. HTTP/bot transports
are separate adapters; no public route is registered by this module.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
from uuid import UUID, uuid4

from services.api_identity import ApiActorContext, ApiIdentityAccessError, ApiIdentityService
from services.api_idempotency import build_api_idempotency_service, hash_request_payload
from services.platform_telegram_binding_consume import (
    PlatformTelegramBindingOperationError, PlatformTelegramBindingResult,
    build_platform_telegram_binding_consume_service,
)
from services.platform_telegram_binding_replay import PlatformTelegramBindingTokenDeriver
from services.platform_telegram_binding_tokens import PlatformTelegramBindingTokenDeniedError


INITIATE = 'platform.telegram.binding.initiate'
CONSUME = 'platform.telegram.binding.consume'


class PlatformTelegramBindingIdempotencyInProgressError(PlatformTelegramBindingOperationError):
    status_code = 409
    code = 'idempotency_in_progress'


@dataclass(frozen=True)
class PlatformTelegramBindingInitiationResult:
    binding_id: UUID
    user_id: UUID
    tenant_id: UUID
    issued_at: datetime
    expires_at: datetime
    row_version: int
    token: str = field(repr=False)


def _error():
    return PlatformTelegramBindingOperationError('Telegram binding workflow state is invalid.')


def _uuid(value):
    if not isinstance(value, str): raise _error()
    try: identifier = UUID(value)
    except (ValueError, TypeError, AttributeError): raise _error() from None
    if str(identifier) != value: raise _error()
    return identifier


def _time(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None: raise _error()
    return value.astimezone(timezone.utc)


def _stored_time(value):
    if not isinstance(value, str): raise _error()
    try: instant = _time(datetime.fromisoformat(value))
    except (ValueError, TypeError): raise _error() from None
    if instant.isoformat() != value: raise _error()
    return instant


class PlatformTelegramBindingWorkflow:
    def __init__(self, *, session, consume_service, identity_service, idempotency_service, token_deriver, now_provider=None):
        if (getattr(consume_service, 'session', None) is not session
            or getattr(identity_service, 'session', None) is not session
            or getattr(getattr(idempotency_service, 'repository', None), 'session', None) is not session
            or not callable(getattr(identity_service, 'require_actor', None))
            or not callable(getattr(getattr(idempotency_service, 'repository', None), 'reserve_record', None))
            or getattr(idempotency_service, 'response_codec', None) is None
            or not isinstance(token_deriver, PlatformTelegramBindingTokenDeriver)):
            raise ValueError('Binding workflow requires shared-session Platform services and atomic idempotency.')
        self.session, self.consume = session, consume_service
        self.identity, self.idempotency, self.deriver = identity_service, idempotency_service, token_deriver
        self.tokens, self.events = consume_service.tokens, consume_service.events
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def _now(self): return _time(self.now_provider())

    def _request(self, key, trace_id):
        if self.session.in_transaction() is not True:
            raise PlatformTelegramBindingOperationError('Binding workflow requires an active caller transaction.')
        if not isinstance(key, str) or not key.strip() or len(key) > 255:
            raise ValueError('Telegram binding requires a valid Idempotency-Key.')
        if trace_id is not None and (not isinstance(trace_id, str) or not trace_id.strip() or len(trace_id) > 128):
            raise ValueError('Binding trace identifier is invalid.')

    async def _actor(self, actor):
        if (not isinstance(actor, ApiActorContext) or actor.status != 'active'
            or not isinstance(actor.user_id, UUID) or not isinstance(actor.tenant_id, UUID)):
            raise ApiIdentityAccessError('API actor is not available.')
        current = await self.identity.require_actor(user_id=actor.user_id, tenant_id=actor.tenant_id)
        if (not isinstance(current, ApiActorContext) or current.status != 'active'
            or current.user_id != actor.user_id or current.tenant_id != actor.tenant_id):
            raise ApiIdentityAccessError('API actor is not available.')
        return current

    @staticmethod
    def _record(record, binding, now):
        if (not isinstance(getattr(record, 'id', None), UUID)
            or getattr(record, 'tenant_id', None) != _uuid(binding['tenant_id'])
            or getattr(record, 'principal_id', None) != _uuid(binding['principal_id'])
            or any(getattr(record, name, None) != binding[name] for name in
                ('principal_type', 'operation', 'key_hash', 'request_hash'))
            or _time(getattr(record, 'expires_at', None)) <= now):
            raise _error()

    async def _reserve(self, *, tenant_id, user_id, operation, key, payload):
        now = self._now()
        binding = dict(tenant_id=str(tenant_id), principal_type='user', principal_id=str(user_id),
            operation=operation, key_hash=hashlib.sha256(key.encode('utf-8')).hexdigest(),
            request_hash=hash_request_payload(payload))
        reservation = await self.idempotency.reserve(tenant_id=tenant_id, principal_type='user', principal_id=user_id,
            operation=operation, idempotency_key=key, payload=payload, now=now)
        self._record(reservation.record, binding, self._now())
        if type(reservation.is_replay) is not bool: raise _error()
        if reservation.is_replay:
            if reservation.record.status == 'processing' and reservation.response_status is None:
                raise PlatformTelegramBindingIdempotencyInProgressError('Telegram binding operation is processing.')
            expected_status = 201 if operation == INITIATE else 200
            if reservation.record.status != 'completed' or type(reservation.response_status) is not int or reservation.response_status != expected_status:
                raise _error()
        elif reservation.record.status != 'processing': raise _error()
        return reservation, binding

    @staticmethod
    def _snapshot(snapshot, binding):
        if not isinstance(snapshot, dict) or set(snapshot) != {'binding', 'data'} or snapshot['binding'] != binding:
            raise _error()
        if not isinstance(snapshot['data'], dict): raise _error()
        return snapshot['data']

    async def _complete(self, reservation, binding, data, status):
        reserved_id = reservation.record.id
        row = await self.idempotency.complete(reservation=reservation, response_status=status,
            response_payload=dict(binding=binding, data=data), now=self._now())
        self._record(row, binding, self._now())
        if row.id != reserved_id or row.status != 'completed' or type(row.response_status) is not int or row.response_status != status:
            raise _error()
        snapshot = self.idempotency.response_codec.decrypt(row.response_ciphertext)
        if self._snapshot(snapshot, binding) != data: raise _error()

    @staticmethod
    def _token(row, *, binding_id, owner_id, tenant_id):
        if (row is None or getattr(row, 'id', None) != binding_id or getattr(row, 'user_id', None) != owner_id
            or getattr(row, 'tenant_id', None) != tenant_id
            or not isinstance(getattr(row, 'initiated_by_user_id', None), UUID)
            or type(getattr(row, 'row_version', None)) is not int or row.row_version < 1):
            raise _error()

    def _initiation(self, data, row, *, actor):
        if set(data) != {'binding_id', 'user_id', 'tenant_id', 'issued_at', 'expires_at', 'row_version'}: raise _error()
        identifier = _uuid(data['binding_id'])
        self._token(row, binding_id=identifier, owner_id=actor.user_id, tenant_id=actor.tenant_id)
        issued, expires = _stored_time(data['issued_at']), _stored_time(data['expires_at'])
        if (_uuid(data['user_id']) != actor.user_id or _uuid(data['tenant_id']) != actor.tenant_id
            or row.initiated_by_user_id != actor.user_id or type(data['row_version']) is not int or data['row_version'] != 1
            or _time(row.issued_at) != issued or _time(row.expires_at) != expires): raise _error()
        material = self.deriver.issue(binding_id=identifier, user_id=actor.user_id, tenant_id=actor.tenant_id,
            initiated_by_user_id=actor.user_id, issued_at=issued)
        if material.expires_at != expires or not hmac.compare_digest(material.token_hash, row.token_hash): raise _error()
        return PlatformTelegramBindingInitiationResult(identifier, actor.user_id, actor.tenant_id, issued, expires, 1, material.token)

    async def initiate_binding(self, *, actor, idempotency_key=None, trace_id=None):
        try:
            self._request(idempotency_key, trace_id)
            actor = await self._actor(actor)
            with self.session.no_autoflush:
                if await self.tokens.lock_active_user(user_id=actor.user_id) != actor.user_id:
                    raise ApiIdentityAccessError('API actor is not available.')
                actor = await self._actor(actor)
                reservation, binding = await self._reserve(tenant_id=actor.tenant_id, user_id=actor.user_id,
                    operation=INITIATE, key=idempotency_key, payload={})
                if reservation.is_replay:
                    data = self._snapshot(reservation.response_payload, binding)
                    identifier = _uuid(data.get('binding_id'))
                    row = await self.tokens.get_token_for_update(token_id=identifier, user_id=actor.user_id, tenant_id=actor.tenant_id)
                else:
                    identifier = uuid4()
                    material = self.deriver.issue(binding_id=identifier, user_id=actor.user_id, tenant_id=actor.tenant_id,
                        initiated_by_user_id=actor.user_id, issued_at=self._now())
                    row = await self.tokens.create_token(token_id=identifier, user_id=actor.user_id,
                        tenant_id=actor.tenant_id, initiated_by_user_id=actor.user_id, token_hash=material.token_hash,
                        issued_at=material.issued_at, expires_at=material.expires_at)
                    if (getattr(row, 'row_version', None) != 1 or getattr(row, 'consumed_at', 'missing') is not None
                        or getattr(row, 'revoked_at', 'missing') is not None): raise _error()
                    data = dict(binding_id=str(identifier), user_id=str(actor.user_id), tenant_id=str(actor.tenant_id),
                        issued_at=material.issued_at.isoformat(), expires_at=material.expires_at.isoformat(), row_version=1)
                    self._initiation(data, row, actor=actor)
                    payload = dict(operation=INITIATE, changes=[
                        dict(field='issued_at', old_value=None, new_value=data['issued_at']),
                        dict(field='expires_at', old_value=None, new_value=data['expires_at'])])
                    audit = await self.events.create_event(event_type='platform_telegram_binding_initiated',
                        tenant_id=actor.tenant_id, user_id=actor.user_id, entity_type='platform_telegram_binding_token',
                        entity_id=identifier, payload=payload, platform='api', trace_id=trace_id)
                    if (not isinstance(getattr(audit, 'id', None), UUID) or audit.tenant_id != actor.tenant_id
                        or audit.user_id != actor.user_id or audit.entity_id != identifier or audit.payload != payload
                        or audit.event_type != 'platform_telegram_binding_initiated'
                        or audit.entity_type != 'platform_telegram_binding_token'): raise _error()
                    await self._complete(reservation, binding, data, 201)
                result = self._initiation(data, row, actor=actor)
                await self._actor(actor)
            await self.session.commit()
            return result
        except BaseException:
            await self.session.rollback()
            raise

    @staticmethod
    def _consumed(data, row, *, binding_id, owner_id, tenant_id):
        if set(data) != {'binding_id', 'account_id', 'user_id', 'tenant_id', 'consumed_at', 'row_version', 'account_created'}:
            raise _error()
        if (_uuid(data['binding_id']) != binding_id or _uuid(data['user_id']) != owner_id
            or _uuid(data['tenant_id']) != tenant_id or type(data['row_version']) is not int
            or data['row_version'] < 2 or type(data['account_created']) is not bool
            or _time(getattr(row, 'consumed_at', None)) != _stored_time(data['consumed_at'])
            or row.row_version < data['row_version']): raise _error()
        return PlatformTelegramBindingResult(binding_id, _uuid(data['account_id']), owner_id,
            _stored_time(data['consumed_at']), data['row_version'], data['account_created'], tenant_id)

    async def consume_binding(self, *, token, telegram_init_data, idempotency_key=None, trace_id=None):
        from services.platform_telegram_binding_tokens import hash_platform_telegram_binding_token
        try:
            self._request(idempotency_key, trace_id)
            identity = self.consume._identity(telegram_init_data, self._now())
            digest = hash_platform_telegram_binding_token(token)
            with self.session.no_autoflush:
                locator = await self.tokens.get_token_locator(token_hash=digest)
                if not isinstance(locator, tuple) or len(locator) != 3 or not all(isinstance(value, UUID) for value in locator):
                    raise PlatformTelegramBindingTokenDeniedError('Telegram binding token is not available.')
                identifier, owner, tenant = locator
                if await self.tokens.lock_active_user(user_id=owner) != owner:
                    raise PlatformTelegramBindingTokenDeniedError('Telegram binding token is not available.')
                current = self.consume._identity(telegram_init_data, self._now())
                if current.platform_user_id != identity.platform_user_id: raise _error()
                reservation, binding = await self._reserve(tenant_id=tenant, user_id=owner,
                    operation=CONSUME, key=idempotency_key,
                    payload=dict(token_hash=digest, telegram_user_id=str(identity.platform_user_id)))
                row = await self.tokens.get_token_for_update(token_id=identifier, user_id=owner, tenant_id=tenant)
                self._token(row, binding_id=identifier, owner_id=owner, tenant_id=tenant)
                if not hmac.compare_digest(row.token_hash, digest): raise _error()
                if reservation.is_replay:
                    data = self._snapshot(reservation.response_payload, binding)
                else:
                    result = await self.consume.consume_token(token=token, telegram_init_data=telegram_init_data, trace_id=trace_id)
                    if (not isinstance(result, PlatformTelegramBindingResult) or result.binding_id != identifier
                        or result.user_id != owner or result.tenant_id != tenant): raise _error()
                    data = dict(binding_id=str(identifier), account_id=str(result.account_id), user_id=str(owner),
                        tenant_id=str(tenant), consumed_at=result.consumed_at.isoformat(),
                        row_version=result.row_version, account_created=result.account_created)
                    self._consumed(data, row, binding_id=identifier, owner_id=owner, tenant_id=tenant)
                    await self._complete(reservation, binding, data, 200)
                result = self._consumed(data, row, binding_id=identifier, owner_id=owner, tenant_id=tenant)
                current = self.consume._identity(telegram_init_data, self._now())
                if current.platform_user_id != identity.platform_user_id: raise _error()
            await self.session.commit()
            return result
        except BaseException:
            await self.session.rollback()
            raise


def build_platform_telegram_binding_workflow(*, session, encryption_key, telegram_verifier, now_provider=None):
    return PlatformTelegramBindingWorkflow(session=session,
        consume_service=build_platform_telegram_binding_consume_service(session=session,
            telegram_verifier=telegram_verifier, now_provider=now_provider),
        identity_service=ApiIdentityService(session),
        idempotency_service=build_api_idempotency_service(session, encryption_key=encryption_key),
        token_deriver=PlatformTelegramBindingTokenDeriver(encryption_key=encryption_key), now_provider=now_provider)
