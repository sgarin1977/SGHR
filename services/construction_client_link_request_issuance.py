"""Authorized pending client identity-link requests; no target consent is implied.

Operator confirmation expires 24 hours after its server-issued creation time.
The caller owns idempotency, delivery and commit. Request insertion and mandatory audit share one Construction
transaction, including invalidation of earlier live requests for the same
client/target pair. Canonical users use the separate Platform connection.
"""
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import secrets
from uuid import UUID, uuid4

from sqlalchemy import select

from database.models import User
from database.repositories.construction_client_link_requests import (
    ConstructionClientLinkRequestRepository, ConstructionClientLinkRequestState, MAX_VERSION,
)
from database.repositories.construction_client_user_links import (
    ConstructionClientUserLinkRepository, ConstructionClientUserLinkState, MAX_CLIENT_ROW_VERSION,
)
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.construction_audit import ConstructionAuditLogger
from services.construction_client_self_link import PlatformClientSelfIdentityReader
from services.construction_clients import (
    ConstructionClientNotFoundError, ConstructionClientOperationError, ConstructionClientValidationError,
)
from services.construction_foundation import ConstructionRowVersionConflictError, require_expected_row_version
from services.construction_grants import ConstructionAccessContextResolver
from services.construction_permissions import (
    ConstructionAccessContext, ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
    require_construction_access,
)

CLIENT_LINK_CONFIRMATION_TTL = timedelta(hours=24)

REQUEST_EVENT = 'construction_client_user_link_requested'
REQUEST_ENTITY = 'construction_client_user_link_request'


class ConstructionClientLinkRequestValidationError(ConstructionClientValidationError):
    pass


def hash_client_link_confirmation_token(token):
    """Purpose-prefixed opaque 256-bit secret; never accept a caller user identity."""
    if (not isinstance(token, str) or len(token) != 48 or not token.startswith('ccl1_')
            or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_' for c in token[5:])):
        raise ConstructionClientLinkRequestValidationError('Client confirmation token format is invalid.')
    raw = urlsafe_b64decode(token[5:] + '=')
    if len(raw) != 32 or urlsafe_b64encode(raw).decode('ascii').rstrip('=') != token[5:]:
        raise ConstructionClientLinkRequestValidationError('Client confirmation token format is invalid.')
    return sha256(token.encode('ascii')).hexdigest()


class PlatformClientLinkIdentityReader(PlatformClientSelfIdentityReader):
    """Existence only for the target: no tenant membership or Telegram prerequisite."""
    async def require_existing_user(self, *, user_id):
        if not isinstance(user_id, UUID):
            raise ConstructionClientLinkRequestValidationError('Target Platform user is invalid.')
        statement = select(User.id).where(User.id == user_id).execution_options(autoflush=False)
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
        if result.scalar_one_or_none() != user_id:
            raise ConstructionClientLinkRequestValidationError('Target Platform user is not available.')
        return user_id


@dataclass(frozen=True)
class ConstructionClientLinkIssuanceResult:
    request: ConstructionClientLinkRequestState
    token: str = field(repr=False)
    audit_id: UUID


class ConstructionClientLinkRequestIssuanceService:
    def __init__(self, *, session, request_repository, client_repository, access_repository,
            event_repository, identity_reader, confirmation_lifetime=CLIENT_LINK_CONFIRMATION_TTL, now_provider=None, token_factory=None):
        methods = ((request_repository, ('create_request', 'list_active_requests_for_update', 'invalidate_request')),
            (client_repository, ('get_state_for_update',)),
            (access_repository, ('has_active_platform_membership', 'list_active_grants')),
            (event_repository, ('create_event',)))
        if (session is None or not callable(getattr(session, 'in_transaction', None))
                or not callable(getattr(session, 'rollback', None)) or not hasattr(session, 'no_autoflush')
                or any(getattr(repo, 'session', None) is not session
                    or any(not callable(getattr(repo, name, None)) for name in names) for repo, names in methods)
                or getattr(identity_reader, 'session', None) is None or identity_reader.session is session
                or any(not callable(getattr(identity_reader, name, None))
                    for name in ('require_active_actor', 'require_existing_user'))
                or not isinstance(confirmation_lifetime, timedelta) or confirmation_lifetime != CLIENT_LINK_CONFIRMATION_TTL
                or (now_provider is not None and not callable(now_provider))
                or (token_factory is not None and not callable(token_factory))):
            raise ValueError('Issuance requires shared Construction repositories, separate Platform reads and the approved 24-hour confirmation lifetime.')
        self.session, self.requests, self.clients = session, request_repository, client_repository
        self.access, self.identity = access_repository, identity_reader
        self.audit = ConstructionAuditLogger(event_repository=event_repository)
        self.confirmation_lifetime = confirmation_lifetime
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.token_factory = token_factory or (lambda: 'ccl1_' + secrets.token_urlsafe(32))
        self.resolver = ConstructionAccessContextResolver(repository=access_repository, now_provider=self._now)

    def _now(self):
        now = self.now_provider()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ConstructionClientOperationError('Client confirmation clock is invalid.')
        return now.astimezone(timezone.utc)

    @staticmethod
    def _scope(actor, context):
        if (not isinstance(actor, ApiActorContext) or actor.status != 'active'
                or not isinstance(actor.user_id, UUID) or not isinstance(actor.tenant_id, UUID)
                or not isinstance(context, ConstructionAccessContext)):
            raise ConstructionPermissionDeniedError('Client edit authorization is not available.')
        require_construction_access(actor=actor, access_context=context, permission_code='construction.projects.edit')

    async def _authorize(self, actor):
        current = await self.identity.require_active_actor(actor=actor)
        if (not isinstance(current, ApiActorContext) or current.status != 'active'
                or current.user_id != actor.user_id or current.tenant_id != actor.tenant_id):
            raise ApiIdentityAccessError('Authenticated Platform actor is not available.')
        if await self.access.has_active_platform_membership(user_id=current.user_id, tenant_id=current.tenant_id) is not True:
            raise ConstructionPermissionDeniedError('Client edit membership is not available.')
        self._scope(current, await self.resolver.resolve(actor=current))
        return current

    async def _target(self, target_user_id):
        if await self.identity.require_existing_user(user_id=target_user_id) != target_user_id:
            raise ConstructionClientOperationError('Canonical target identity receipt is invalid.')

    async def request_link(self, *, actor, access_context, client_id, target_user_id,
            expected_row_version, trace_id=None):
        return await self._issue(actor=actor, access_context=access_context, client_id=client_id,
            target_user_id=target_user_id, expected_row_version=expected_row_version,
            previous_user_id=None, replacement=False, trace_id=trace_id)

    async def request_replacement(self, *, actor, access_context, client_id, target_user_id,
            previous_user_id, expected_row_version, trace_id=None):
        return await self._issue(actor=actor, access_context=access_context, client_id=client_id,
            target_user_id=target_user_id, expected_row_version=expected_row_version,
            previous_user_id=previous_user_id, replacement=True, trace_id=trace_id)

    async def _invalidate_active_requests(self, *, tenant_id, client_id, target_user_id, as_of):
        # _issue already holds the authorized live client lock. Confirm uses
        # the same client-before-request order, so it cannot race this change.
        states = await self.requests.list_active_requests_for_update(
            tenant_id=tenant_id, client_id=client_id, target_user_id=target_user_id, as_of=as_of)
        if (not isinstance(states, tuple)
                or any(not isinstance(state, ConstructionClientLinkRequestState)
                    or state.tenant_id != tenant_id or state.client_id != client_id
                    or state.target_user_id != target_user_id or state.consumed_at is not None
                    or state.invalidated_at is not None or not state.issued_at <= as_of < state.expires_at
                    for state in states)
                or len({state.request_id for state in states}) != len(states)):
            raise ConstructionClientOperationError('Active client confirmation context is invalid.')
        if any(state.row_version == MAX_VERSION for state in states):
            raise ConstructionClientLinkRequestValidationError('Client confirmation version is exhausted.')
        invalidated_ids = []
        for state in sorted(states, key=lambda value: (value.issued_at, value.request_id)):
            saved = await self.requests.invalidate_request(state=state, as_of=as_of)
            if saved is None:
                raise ConstructionRowVersionConflictError()
            if (not isinstance(saved, ConstructionClientLinkRequestState)
                    or saved != replace(state, invalidated_at=as_of, updated_at=as_of,
                        row_version=state.row_version+1)):
                raise ConstructionClientOperationError('Client confirmation invalidation receipt is invalid.')
            invalidated_ids.append(str(state.request_id))
        return invalidated_ids


    async def _issue(self, *, actor, access_context, client_id, target_user_id, expected_row_version,
            previous_user_id, replacement, trace_id):
        try:
            self._scope(actor, access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionClientOperationError('Issuance requires an existing caller transaction.')
            if (not isinstance(client_id, UUID) or not isinstance(target_user_id, UUID)
                    or type(expected_row_version) is not int or not 1 <= expected_row_version <= MAX_CLIENT_ROW_VERSION):
                raise ConstructionClientLinkRequestValidationError('Client confirmation requires UUID identifiers and a positive BIGINT version.')
            if target_user_id == actor.user_id:
                raise ConstructionClientLinkRequestValidationError('Use explicit authenticated self-link for the initiating user.')
            if replacement and (not isinstance(previous_user_id, UUID) or previous_user_id == target_user_id):
                raise ConstructionClientLinkRequestValidationError('Replacement requires the previous different linked user.')
            if trace_id is not None and (not isinstance(trace_id, str) or not trace_id.strip() or len(trace_id) > 128):
                raise ConstructionClientLinkRequestValidationError('Client confirmation trace identifier is invalid.')
            with self.session.no_autoflush:
                actor = await self._authorize(actor)
                before = await self.clients.get_state_for_update(tenant_id=actor.tenant_id, client_id=client_id)
                if before is None:
                    raise ConstructionClientNotFoundError('Construction client is not available.')
                if (not isinstance(before, ConstructionClientUserLinkState) or before.client_id != client_id
                        or before.tenant_id != actor.tenant_id or type(before.row_version) is not int
                        or not 1 <= before.row_version <= MAX_CLIENT_ROW_VERSION
                        or (before.linked_user_id is not None and not isinstance(before.linked_user_id, UUID))):
                    raise ConstructionClientOperationError('Client confirmation base is invalid.')
                require_expected_row_version(actual_row_version=before.row_version, expected_row_version=expected_row_version)
                if replacement:
                    if before.linked_user_id != previous_user_id:
                        raise ConstructionRowVersionConflictError()
                elif before.linked_user_id not in (None, target_user_id):
                    raise ConstructionClientLinkRequestValidationError('Existing client link requires explicit replacement.')
                if before.row_version == MAX_CLIENT_ROW_VERSION:
                    raise ConstructionClientLinkRequestValidationError('Client link version is exhausted.')
                await self._target(target_user_id)
                actor = await self._authorize(actor)
                issued_at = self._now()
                expires_at = issued_at + CLIENT_LINK_CONFIRMATION_TTL
                token = self.token_factory()
                expected = ConstructionClientLinkRequestState(uuid4(), actor.tenant_id, client_id,
                    actor.user_id, target_user_id, before.linked_user_id, before.row_version,
                    hash_client_link_confirmation_token(token), issued_at, expires_at, None, issued_at, 1)
                invalidated_ids = await self._invalidate_active_requests(tenant_id=actor.tenant_id,
                    client_id=client_id, target_user_id=target_user_id, as_of=issued_at)
                saved = await self.requests.create_request(
                    **{name:getattr(expected, name) for name in ('request_id', 'tenant_id', 'client_id',
                        'initiated_by_user_id', 'target_user_id', 'expected_linked_user_id',
                        'expected_client_row_version', 'token_hash', 'issued_at', 'expires_at')})
                if saved is None:
                    raise ConstructionRowVersionConflictError()
                if not isinstance(saved, ConstructionClientLinkRequestState) or saved != expected:
                    raise ConstructionClientOperationError('Client confirmation write receipt is invalid.')
                actor = await self._authorize(actor)
                await self._target(target_user_id)
                details = {'related_client_id': str(client_id), 'target_user_id': str(target_user_id),
                    'invalidated_request_ids': invalidated_ids,
                    'expected_client_row_version': before.row_version,
                    'expected_linked_user_id': str(before.linked_user_id) if before.linked_user_id else None,
                    'expires_at': expires_at.isoformat(), 'identity_link_action': 'request_replacement' if replacement else 'request_link'}
                event = await self.audit.record_operation(actor=actor, event_type=REQUEST_EVENT,
                    entity_type=REQUEST_ENTITY, entity_id=saved.request_id, operation='create', details=details, trace_id=trace_id)
                if (not isinstance(getattr(event, 'id', None), UUID)
                        or getattr(event, 'tenant_id', None) != actor.tenant_id
                        or getattr(event, 'user_id', None) != actor.user_id
                        or getattr(event, 'entity_id', None) != saved.request_id
                        or getattr(event, 'entity_type', None) != REQUEST_ENTITY
                        or getattr(event, 'event_type', None) != REQUEST_EVENT
                        or getattr(event, 'platform', None) != 'api'
                        or getattr(event, 'payload', None) != {'operation': 'create', 'changes': [], 'reason': None, **details}
                        or (trace_id is not None and getattr(event, 'trace_id', None) != trace_id)):
                    raise ConstructionClientOperationError('Mandatory client confirmation audit receipt is invalid.')
                await self._authorize(actor)
                await self._target(target_user_id)
                if not issued_at <= self._now() < expires_at or self.session.in_transaction() is not True:
                    raise ConstructionClientOperationError('Client confirmation issuance expired before completion.')
                return ConstructionClientLinkIssuanceResult(saved, token, event.id)
        except (ConstructionClientValidationError, ConstructionClientNotFoundError, ConstructionClientOperationError,
                ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionRowVersionConflictError, ApiIdentityAccessError):
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise ConstructionClientOperationError('Client confirmation issuance failed.') from None
        except BaseException:
            await self.session.rollback()
            raise


def build_construction_client_link_request_issuance_service(*, session, platform_session,
        confirmation_lifetime=CLIENT_LINK_CONFIRMATION_TTL, now_provider=None, token_factory=None):
    from database.repositories.construction import ConstructionAccessRepository
    from database.repositories.event import EventRepository
    return ConstructionClientLinkRequestIssuanceService(session=session,
        request_repository=ConstructionClientLinkRequestRepository(session),
        client_repository=ConstructionClientUserLinkRepository(session),
        access_repository=ConstructionAccessRepository(session), event_repository=EventRepository(session),
        identity_reader=PlatformClientLinkIdentityReader(platform_session),
        confirmation_lifetime=confirmation_lifetime, now_provider=now_provider, token_factory=token_factory)
