"""Authorized binding-token lifecycle; caller owns commit and idempotency.

These internal operations revoke unused challenges, not canonical Telegram
accounts or authentication sessions. Public transports must use the approved
workflow/idempotency contract before exposing them.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4

from database.repositories.event import EventRepository
from database.repositories.platform_telegram_binding_tokens import PlatformTelegramBindingTokenRepository
from services.api_identity import ApiActorContext, ApiIdentityAccessError, ApiIdentityService
from services.platform_telegram_binding_consume import PlatformTelegramBindingOperationError, PlatformTelegramBindingVersionConflictError
from services.platform_telegram_binding_replay import PlatformTelegramBindingTokenDeriver
from services.platform_telegram_binding_tokens import PLATFORM_TELEGRAM_BINDING_TOKEN_TTL, PlatformTelegramBindingTokenDeniedError


@dataclass(frozen=True)
class PlatformTelegramBindingRevocationResult:
    binding_id: UUID
    user_id: UUID
    tenant_id: UUID
    row_version: int
    revoked_at: datetime


@dataclass(frozen=True)
class PlatformTelegramBindingResendResult:
    source_binding_id: UUID
    source_row_version: int
    source_revoked_at: datetime
    binding_id: UUID
    user_id: UUID
    tenant_id: UUID
    row_version: int
    issued_at: datetime
    expires_at: datetime
    token: str = field(repr=False)


def _error():
    return PlatformTelegramBindingOperationError('Telegram binding lifecycle state is invalid.')


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None: raise _error()
    return value.astimezone(timezone.utc)


def _version(value):
    if type(value) is not int or not 1 <= value < 2**63-1:
        raise ValueError('Binding expected row version is invalid.')
    return value


class PlatformTelegramBindingLifecycleService:
    def __init__(self, *, session, token_repository, event_repository, identity_service, token_deriver, now_provider=None):
        if (session is None or not callable(getattr(session, 'in_transaction', None))
            or any(getattr(repo, 'session', None) is not session for repo in (token_repository, event_repository, identity_service))
            or not isinstance(token_deriver, PlatformTelegramBindingTokenDeriver)):
            raise ValueError('Binding lifecycle requires shared-session Platform services.')
        self.session, self.tokens, self.events, self.identity = session, token_repository, event_repository, identity_service
        self.deriver = token_deriver
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    async def _actor(self, actor):
        if (not isinstance(actor, ApiActorContext) or actor.status != 'active'
            or not isinstance(actor.user_id, UUID) or not isinstance(actor.tenant_id, UUID)):
            raise ApiIdentityAccessError('API actor is not available.')
        current = await self.identity.require_actor(user_id=actor.user_id, tenant_id=actor.tenant_id)
        if (not isinstance(current, ApiActorContext) or current.status != 'active'
            or current.user_id != actor.user_id or current.tenant_id != actor.tenant_id):
            raise ApiIdentityAccessError('API actor is not available.')
        return current

    async def _source(self, *, actor, binding_id, expected_row_version, trace_id):
        if self.session.in_transaction() is not True:
            raise PlatformTelegramBindingOperationError('Binding lifecycle requires an active caller transaction.')
        if not isinstance(binding_id, UUID): raise ValueError('Binding identifier is invalid.')
        _version(expected_row_version)
        if trace_id is not None and (not isinstance(trace_id, str) or not trace_id.strip() or len(trace_id) > 128):
            raise ValueError('Binding trace identifier is invalid.')
        actor = await self._actor(actor)
        if await self.tokens.lock_active_user(user_id=actor.user_id) != actor.user_id:
            raise ApiIdentityAccessError('API actor is not available.')
        actor = await self._actor(actor)
        row = await self.tokens.get_token_for_update(token_id=binding_id, user_id=actor.user_id, tenant_id=actor.tenant_id)
        if row is None:
            raise PlatformTelegramBindingTokenDeniedError('Telegram binding token is not available.')
        if (getattr(row, 'id', None) != binding_id or getattr(row, 'user_id', None) != actor.user_id
            or getattr(row, 'tenant_id', None) != actor.tenant_id
            or not isinstance(getattr(row, 'initiated_by_user_id', None), UUID)
            or type(getattr(row, 'row_version', None)) is not int or not 1 <= row.row_version < 2**63-1): raise _error()
        if row.row_version != expected_row_version: raise PlatformTelegramBindingVersionConflictError()
        now = _utc(self.now_provider())
        issued, expires = _utc(row.issued_at), _utc(row.expires_at)
        if (getattr(row, 'consumed_at', 'missing') is not None or getattr(row, 'revoked_at', 'missing') is not None
            or not issued <= now < expires):
            raise PlatformTelegramBindingTokenDeniedError('Telegram binding token is not available.')
        if (expires-issued != PLATFORM_TELEGRAM_BINDING_TOKEN_TTL or not isinstance(row.token_hash, str)
            or len(row.token_hash) != 64 or any(value not in '0123456789abcdef' for value in row.token_hash)): raise _error()
        return actor, row, now

    @staticmethod
    def _immutable(row):
        return (row.id, row.user_id, row.tenant_id, row.initiated_by_user_id, row.token_hash,
            _utc(row.issued_at), _utc(row.expires_at))

    async def _revoke(self, row, *, expected_row_version, now):
        original = self._immutable(row)
        changed = await self.tokens.revoke_token(token_id=row.id, user_id=row.user_id, tenant_id=row.tenant_id,
            expected_row_version=expected_row_version, as_of=now)
        if changed is None: raise PlatformTelegramBindingVersionConflictError()
        if (self._immutable(changed) != original or type(changed.row_version) is not int
            or changed.row_version != expected_row_version+1 or changed.revoked_at != now
            or changed.consumed_at is not None): raise _error()
        return changed

    async def _audit(self, *, actor, binding_id, event_type, payload, trace_id):
        row = await self.events.create_event(event_type=event_type, tenant_id=actor.tenant_id, user_id=actor.user_id,
            entity_type='platform_telegram_binding_token', entity_id=binding_id, payload=payload, platform='api', trace_id=trace_id)
        if (not isinstance(getattr(row, 'id', None), UUID) or getattr(row, 'tenant_id', None) != actor.tenant_id
            or getattr(row, 'user_id', None) != actor.user_id or getattr(row, 'entity_id', None) != binding_id
            or getattr(row, 'entity_type', None) != 'platform_telegram_binding_token'
            or getattr(row, 'event_type', None) != event_type or getattr(row, 'payload', None) != payload): raise _error()

    async def revoke_binding(self, *, actor, binding_id, expected_row_version, trace_id=None):
        try:
            with self.session.no_autoflush:
                actor, row, now = await self._source(actor=actor, binding_id=binding_id,
                    expected_row_version=expected_row_version, trace_id=trace_id)
                row = await self._revoke(row, expected_row_version=expected_row_version, now=now)
                result = PlatformTelegramBindingRevocationResult(binding_id, actor.user_id, actor.tenant_id, expected_row_version+1, now)
                await self._audit(actor=actor, binding_id=binding_id, event_type='platform_telegram_binding_revoked',
                    payload=dict(operation='platform.telegram.binding.revoke', changes=[
                        dict(field='revoked_at', old_value=None, new_value=now.isoformat()),
                        dict(field='row_version', old_value=expected_row_version, new_value=expected_row_version+1)]), trace_id=trace_id)
                await self._actor(actor)
                return result
        except BaseException:
            await self.session.rollback()
            raise

    async def resend_binding(self, *, actor, binding_id, expected_row_version, trace_id=None):
        try:
            with self.session.no_autoflush:
                actor, source, now = await self._source(actor=actor, binding_id=binding_id,
                    expected_row_version=expected_row_version, trace_id=trace_id)
                source = await self._revoke(source, expected_row_version=expected_row_version, now=now)
                identifier = uuid4()
                material = self.deriver.issue(binding_id=identifier, user_id=actor.user_id, tenant_id=actor.tenant_id,
                    initiated_by_user_id=actor.user_id, issued_at=now)
                row = await self.tokens.create_token(token_id=identifier, user_id=actor.user_id, tenant_id=actor.tenant_id,
                    initiated_by_user_id=actor.user_id, token_hash=material.token_hash,
                    issued_at=material.issued_at, expires_at=material.expires_at)
                expected = (identifier, actor.user_id, actor.tenant_id, actor.user_id, material.token_hash, now, material.expires_at)
                if (self._immutable(row) != expected or type(row.row_version) is not int or row.row_version != 1
                    or row.consumed_at is not None or row.revoked_at is not None): raise _error()
                result = PlatformTelegramBindingResendResult(binding_id, expected_row_version+1, now, identifier,
                    actor.user_id, actor.tenant_id, 1, now, material.expires_at, material.token)
                await self._audit(actor=actor, binding_id=identifier, event_type='platform_telegram_binding_resent',
                    payload=dict(operation='platform.telegram.binding.resend', source_binding_id=str(binding_id),
                        new_binding_id=str(identifier), changes=[
                            dict(field='source_revoked_at', old_value=None, new_value=now.isoformat()),
                            dict(field='source_row_version', old_value=expected_row_version, new_value=expected_row_version+1),
                            dict(field='issued_at', old_value=None, new_value=now.isoformat()),
                            dict(field='expires_at', old_value=None, new_value=material.expires_at.isoformat())]), trace_id=trace_id)
                await self._actor(actor)
                return result
        except BaseException:
            await self.session.rollback()
            raise


def build_platform_telegram_binding_lifecycle_service(*, session, encryption_key, now_provider=None):
    return PlatformTelegramBindingLifecycleService(session=session,
        token_repository=PlatformTelegramBindingTokenRepository(session), event_repository=EventRepository(session),
        identity_service=ApiIdentityService(session), token_deriver=PlatformTelegramBindingTokenDeriver(encryption_key=encryption_key),
        now_provider=now_provider)
