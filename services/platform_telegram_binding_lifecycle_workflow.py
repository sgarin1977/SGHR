"""Commit authorized token lifecycle changes, audit and replay atomically.

Resend issues a new secret and uses the initiation replay contract. Versioned
revoke accepts an optional key; absence of a key does not waive expected version.
No HTTP routes, canonical-account unlink or auth-session policy are introduced.
"""
import hmac
from uuid import UUID

from services.api_identity import ApiIdentityAccessError
from services.platform_telegram_binding_consume import PlatformTelegramBindingOperationError
from services.platform_telegram_binding_lifecycle import (
    PlatformTelegramBindingLifecycleService, PlatformTelegramBindingResendResult,
    PlatformTelegramBindingRevocationResult, _version,
)
from services.platform_telegram_binding_workflow import (
    PlatformTelegramBindingWorkflow, build_platform_telegram_binding_workflow,
    _uuid, _stored_time, _time,
)


RESEND = 'platform.telegram.binding.resend'
REVOKE = 'platform.telegram.binding.revoke'
_ISSUED_FIELDS = frozenset({'binding_id', 'user_id', 'tenant_id', 'issued_at', 'expires_at', 'row_version'})
_SOURCE_FIELDS = frozenset({'source_binding_id', 'source_row_version', 'source_revoked_at'})
_REVOKED_FIELDS = frozenset({'binding_id', 'user_id', 'tenant_id', 'row_version', 'revoked_at'})


def _error():
    return PlatformTelegramBindingOperationError('Telegram binding lifecycle workflow state is invalid.')


class PlatformTelegramBindingLifecycleWorkflow:
    def __init__(self, *, binding_workflow, lifecycle_service):
        if (not isinstance(binding_workflow, PlatformTelegramBindingWorkflow)
            or not isinstance(lifecycle_service, PlatformTelegramBindingLifecycleService)
            or lifecycle_service.session is not binding_workflow.session
            or any(getattr(lifecycle_service, name) is not getattr(binding_workflow, name)
                for name in ('tokens', 'events', 'identity', 'deriver'))):
            raise ValueError('Binding lifecycle workflow requires shared Platform services.')
        self.base, self.service, self.session = binding_workflow, lifecycle_service, binding_workflow.session

    @staticmethod
    def _command(binding_id, expected_row_version):
        if not isinstance(binding_id, UUID): raise ValueError('Binding identifier is invalid.')
        _version(expected_row_version)

    async def _owner(self, actor):
        actor = await self.base._actor(actor)
        if await self.base.tokens.lock_active_user(user_id=actor.user_id) != actor.user_id:
            raise ApiIdentityAccessError('API actor is not available.')
        return await self.base._actor(actor)

    async def _row(self, identifier, actor):
        row = await self.base.tokens.get_token_for_update(token_id=identifier,
            user_id=actor.user_id, tenant_id=actor.tenant_id)
        self.base._token(row, binding_id=identifier, owner_id=actor.user_id, tenant_id=actor.tenant_id)
        return row

    @staticmethod
    def _revoked(data, row, *, actor, binding_id, expected_row_version):
        if not isinstance(data, dict) or set(data) != _REVOKED_FIELDS: raise _error()
        if (_uuid(data['binding_id']) != binding_id or _uuid(data['user_id']) != actor.user_id
            or _uuid(data['tenant_id']) != actor.tenant_id
            or type(data['row_version']) is not int or data['row_version'] != expected_row_version+1
            or row.id != binding_id or row.user_id != actor.user_id or row.tenant_id != actor.tenant_id
            or row.row_version < data['row_version'] or row.consumed_at is not None
            or _time(row.revoked_at) != _stored_time(data['revoked_at'])): raise _error()
        return PlatformTelegramBindingRevocationResult(binding_id, actor.user_id, actor.tenant_id,
            data['row_version'], _stored_time(data['revoked_at']))

    def _resent(self, data, source, row, *, actor, binding_id, expected_row_version):
        if not isinstance(data, dict) or set(data) != _ISSUED_FIELDS | _SOURCE_FIELDS: raise _error()
        source_data = dict(binding_id=data['source_binding_id'], user_id=data['user_id'], tenant_id=data['tenant_id'],
            row_version=data['source_row_version'], revoked_at=data['source_revoked_at'])
        revoked = self._revoked(source_data, source, actor=actor, binding_id=binding_id, expected_row_version=expected_row_version)
        issued_data = {name: data[name] for name in _ISSUED_FIELDS}
        issued = self.base._initiation(issued_data, row, actor=actor)
        if issued.binding_id == binding_id or issued.issued_at != revoked.revoked_at: raise _error()
        return PlatformTelegramBindingResendResult(binding_id, revoked.row_version, revoked.revoked_at,
            issued.binding_id, actor.user_id, actor.tenant_id, issued.row_version,
            issued.issued_at, issued.expires_at, issued.token)

    async def resend_binding(self, *, actor, binding_id, expected_row_version, idempotency_key=None, trace_id=None):
        try:
            self.base._request(idempotency_key, trace_id)
            self._command(binding_id, expected_row_version)
            with self.session.no_autoflush:
                actor = await self._owner(actor)
                reservation, binding = await self.base._reserve(tenant_id=actor.tenant_id, user_id=actor.user_id,
                    operation=RESEND, key=idempotency_key,
                    payload=dict(binding_id=str(binding_id), expected_row_version=expected_row_version))
                if reservation.is_replay:
                    data = self.base._snapshot(reservation.response_payload, binding)
                    if set(data) != _ISSUED_FIELDS | _SOURCE_FIELDS: raise _error()
                else:
                    result = await self.service.resend_binding(actor=actor, binding_id=binding_id,
                        expected_row_version=expected_row_version, trace_id=trace_id)
                    if (not isinstance(result, PlatformTelegramBindingResendResult)
                        or not isinstance(result.binding_id, UUID) or not isinstance(result.source_binding_id, UUID)
                        or not isinstance(result.user_id, UUID) or not isinstance(result.tenant_id, UUID)): raise _error()
                    data = dict(source_binding_id=str(result.source_binding_id), source_row_version=result.source_row_version,
                        source_revoked_at=_time(result.source_revoked_at).isoformat(), binding_id=str(result.binding_id),
                        user_id=str(result.user_id), tenant_id=str(result.tenant_id), row_version=result.row_version,
                        issued_at=_time(result.issued_at).isoformat(), expires_at=_time(result.expires_at).isoformat())
                source = await self._row(binding_id, actor)
                row = await self._row(_uuid(data['binding_id']), actor)
                checked = self._resent(data, source, row, actor=actor, binding_id=binding_id, expected_row_version=expected_row_version)
                if not reservation.is_replay:
                    if not isinstance(result.token, str) or not hmac.compare_digest(result.token, checked.token): raise _error()
                    # The shared codec receives immutable metadata only, never
                    # the code/deep-link secret or an encrypted secret snapshot.
                    await self.base._complete(reservation, binding, data, 200)
                await self.base._actor(actor)
            await self.session.commit()
            return checked
        except BaseException:
            await self.session.rollback()
            raise

    async def revoke_binding(self, *, actor, binding_id, expected_row_version, idempotency_key=None, trace_id=None):
        try:
            self._command(binding_id, expected_row_version)
            if self.session.in_transaction() is not True:
                raise PlatformTelegramBindingOperationError('Binding lifecycle workflow requires an active transaction.')
            if idempotency_key is not None: self.base._request(idempotency_key, trace_id)
            elif trace_id is not None and (not isinstance(trace_id, str) or not trace_id.strip() or len(trace_id) > 128):
                raise ValueError('Binding trace identifier is invalid.')
            with self.session.no_autoflush:
                actor = await self._owner(actor)
                reservation = binding = None
                if idempotency_key is not None:
                    reservation, binding = await self.base._reserve(tenant_id=actor.tenant_id, user_id=actor.user_id,
                        operation=REVOKE, key=idempotency_key,
                        payload=dict(binding_id=str(binding_id), expected_row_version=expected_row_version))
                if reservation is not None and reservation.is_replay:
                    data = self.base._snapshot(reservation.response_payload, binding)
                else:
                    result = await self.service.revoke_binding(actor=actor, binding_id=binding_id,
                        expected_row_version=expected_row_version, trace_id=trace_id)
                    if (not isinstance(result, PlatformTelegramBindingRevocationResult)
                        or not isinstance(result.binding_id, UUID) or not isinstance(result.user_id, UUID)
                        or not isinstance(result.tenant_id, UUID)): raise _error()
                    data = dict(binding_id=str(result.binding_id), user_id=str(result.user_id), tenant_id=str(result.tenant_id),
                        row_version=result.row_version, revoked_at=_time(result.revoked_at).isoformat())
                row = await self._row(binding_id, actor)
                checked = self._revoked(data, row, actor=actor, binding_id=binding_id, expected_row_version=expected_row_version)
                if reservation is not None and not reservation.is_replay:
                    await self.base._complete(reservation, binding, data, 200)
                await self.base._actor(actor)
            await self.session.commit()
            return checked
        except BaseException:
            await self.session.rollback()
            raise


def build_platform_telegram_binding_lifecycle_workflow(*, session, encryption_key, telegram_verifier, now_provider=None):
    base = build_platform_telegram_binding_workflow(session=session, encryption_key=encryption_key,
        telegram_verifier=telegram_verifier, now_provider=now_provider)
    service = PlatformTelegramBindingLifecycleService(session=session, token_repository=base.tokens,
        event_repository=base.events, identity_service=base.identity, token_deriver=base.deriver, now_provider=base.now_provider)
    return PlatformTelegramBindingLifecycleWorkflow(binding_workflow=base, lifecycle_service=service)
