"""Commit canonical unlink, mandatory audit and shared idempotency together."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from uuid import UUID

from database.repositories.platform_telegram_accounts import PlatformTelegramIdentityUnavailableError
from services.api_identity import ApiIdentityAccessError
from services.api_idempotency import build_api_idempotency_service, hash_request_payload
from services.platform_telegram_account_unlink import (
    UNLINK_OPERATION, PlatformTelegramAccountUnlinkResult, PlatformTelegramAccountUnlinkService,
    build_platform_telegram_account_unlink_service,
)
from services.platform_telegram_binding_consume import PlatformTelegramBindingOperationError
from services.platform_telegram_binding_workflow import PlatformTelegramBindingIdempotencyInProgressError


_DATA_FIELDS = frozenset({'account_id', 'user_id', 'tenant_id', 'status', 'row_version', 'changed'})


def _error():
    return PlatformTelegramBindingOperationError('Telegram unlink workflow state is invalid.')


def _uuid(value):
    if not isinstance(value, str): raise _error()
    try: identifier = UUID(value)
    except (ValueError, TypeError, AttributeError): raise _error() from None
    if str(identifier) != value: raise _error()
    return identifier


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None: raise _error()
    return value.astimezone(timezone.utc)


class PlatformTelegramAccountUnlinkWorkflow:
    def __init__(self, *, unlink_service, idempotency_service, now_provider=None):
        if (not isinstance(unlink_service, PlatformTelegramAccountUnlinkService)
            or getattr(getattr(idempotency_service, 'repository', None), 'session', None) is not unlink_service.session
            or not callable(getattr(getattr(idempotency_service, 'repository', None), 'reserve_record', None))
            or not callable(getattr(idempotency_service, 'reserve', None))
            or not callable(getattr(idempotency_service, 'complete', None))
            or not callable(getattr(getattr(idempotency_service, 'response_codec', None), 'encrypt', None))
            or not callable(getattr(getattr(idempotency_service, 'response_codec', None), 'decrypt', None))):
            raise ValueError('Unlink workflow requires shared-session unlink, audit and atomic idempotency.')
        self.service, self.idempotency, self.session = unlink_service, idempotency_service, unlink_service.session
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def _now(self): return _utc(self.now_provider())

    def _request(self, *, account_id, expected_row_version, idempotency_key, trace_id):
        if self.session.in_transaction() is not True:
            raise PlatformTelegramBindingOperationError('Unlink workflow requires an active caller transaction.')
        if not isinstance(account_id, UUID): raise ValueError('Account identifier is invalid.')
        if type(expected_row_version) is not int or not 1 <= expected_row_version < 2**63-1:
            raise ValueError('Account expected row version is invalid.')
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 255:
            raise ValueError('Telegram unlink requires a valid Idempotency-Key.')
        if trace_id is not None and (not isinstance(trace_id, str) or not trace_id.strip() or len(trace_id) > 128):
            raise ValueError('Account trace identifier is invalid.')

    async def _owner(self, actor, account_id):
        actor = await self.service._actor(actor)
        try:
            row = await self.service.accounts.get_owned_account_for_update(account_id=account_id, user_id=actor.user_id)
        except PlatformTelegramIdentityUnavailableError:
            raise ApiIdentityAccessError('API actor is not available.') from None
        if row is None: raise ApiIdentityAccessError('Telegram account is not available.')
        row = self.service._account(row, account_id=account_id, user_id=actor.user_id)
        return await self.service._actor(actor), row

    def _record(self, record, binding):
        if (not isinstance(getattr(record, 'id', None), UUID)
            or getattr(record, 'tenant_id', None) != _uuid(binding['tenant_id'])
            or getattr(record, 'principal_id', None) != _uuid(binding['principal_id'])
            or any(getattr(record, name, None) != binding[name]
                for name in ('principal_type', 'operation', 'key_hash', 'request_hash'))
            or _utc(getattr(record, 'expires_at', None)) <= self._now()): raise _error()

    async def _reserve(self, *, actor, account_id, expected_row_version, key):
        payload = dict(account_id=str(account_id), expected_row_version=expected_row_version)
        binding = dict(tenant_id=str(actor.tenant_id), principal_type='user', principal_id=str(actor.user_id),
            operation=UNLINK_OPERATION, key_hash=hashlib.sha256(key.encode('utf-8')).hexdigest(),
            request_hash=hash_request_payload(payload))
        reservation = await self.idempotency.reserve(tenant_id=actor.tenant_id, principal_type='user',
            principal_id=actor.user_id, operation=UNLINK_OPERATION, idempotency_key=key, payload=payload, now=self._now())
        self._record(reservation.record, binding)
        if type(reservation.is_replay) is not bool: raise _error()
        if reservation.is_replay:
            if reservation.record.status == 'processing' and reservation.response_status is None:
                raise PlatformTelegramBindingIdempotencyInProgressError('Telegram unlink operation is processing.')
            if (reservation.record.status != 'completed' or type(reservation.response_status) is not int
                or reservation.response_status != 200): raise _error()
        elif reservation.record.status != 'processing': raise _error()
        return reservation, binding

    @staticmethod
    def _snapshot(snapshot, binding):
        if (not isinstance(snapshot, dict) or set(snapshot) != {'binding', 'data'}
            or snapshot['binding'] != binding or not isinstance(snapshot['data'], dict)): raise _error()
        return snapshot['data']

    @staticmethod
    def _result(data, *, actor, account_id, expected_row_version):
        if not isinstance(data, dict) or set(data) != _DATA_FIELDS: raise _error()
        if (_uuid(data['account_id']) != account_id or _uuid(data['user_id']) != actor.user_id
            or _uuid(data['tenant_id']) != actor.tenant_id or data['status'] != 'unlinked'
            or type(data['changed']) is not bool or type(data['row_version']) is not int
            or data['row_version'] != expected_row_version + int(data['changed'])): raise _error()
        return PlatformTelegramAccountUnlinkResult(account_id, actor.user_id, actor.tenant_id,
            'unlinked', data['row_version'], data['changed'])

    async def _complete(self, reservation, binding, data):
        identifier = reservation.record.id
        snapshot = dict(binding=binding, data=data)
        row = await self.idempotency.complete(reservation=reservation, response_status=200,
            response_payload=deepcopy(snapshot), now=self._now())
        self._record(row, binding)
        if (row.id != identifier or row.status != 'completed' or type(row.response_status) is not int
            or row.response_status != 200): raise _error()
        checked = self.idempotency.response_codec.decrypt(row.response_ciphertext)
        if self._snapshot(checked, binding) != data: raise _error()

    async def unlink_account(self, *, actor, account_id, expected_row_version, idempotency_key=None, trace_id=None):
        try:
            self._request(account_id=account_id, expected_row_version=expected_row_version,
                idempotency_key=idempotency_key, trace_id=trace_id)
            with self.session.no_autoflush:
                # User -> account -> idempotency lock order. Replays retain
                # current Platform authorization and account ownership checks.
                actor, row = await self._owner(actor, account_id)
                reservation, binding = await self._reserve(actor=actor, account_id=account_id,
                    expected_row_version=expected_row_version, key=idempotency_key)
                if reservation.is_replay:
                    data = self._snapshot(reservation.response_payload, binding)
                    result = self._result(data, actor=actor, account_id=account_id, expected_row_version=expected_row_version)
                    # A later proof-based relink may have advanced the account.
                    # Return the original result without unlinking it again.
                    if row.row_version < result.row_version or (
                        row.row_version == result.row_version and row.status != 'unlinked'): raise _error()
                else:
                    changed = await self.service.unlink_account(actor=actor, account_id=account_id,
                        expected_row_version=expected_row_version, trace_id=trace_id)
                    if not isinstance(changed, PlatformTelegramAccountUnlinkResult): raise _error()
                    if not all(isinstance(value, UUID) for value in (changed.account_id, changed.user_id, changed.tenant_id)):
                        raise _error()
                    data = dict(account_id=str(changed.account_id), user_id=str(changed.user_id),
                        tenant_id=str(changed.tenant_id), status=changed.status,
                        row_version=changed.row_version, changed=changed.changed)
                    result = self._result(data, actor=actor, account_id=account_id, expected_row_version=expected_row_version)
                    await self._complete(reservation, binding, data)
                await self.service._actor(actor)
            await self.session.commit()
            return result
        except BaseException:
            await self.session.rollback()
            raise


def build_platform_telegram_account_unlink_workflow(*, session, encryption_key, now_provider=None):
    return PlatformTelegramAccountUnlinkWorkflow(
        unlink_service=build_platform_telegram_account_unlink_service(session=session),
        idempotency_service=build_api_idempotency_service(session,encryption_key=encryption_key),
        now_provider=now_provider)
