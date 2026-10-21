"""Confirm a pending client link using an already authenticated Platform actor.

actor comes from the trusted Platform authentication boundary, never a body
user_id or a confirmation boolean. Its selected/home tenant is not membership
in the client tenant. Routing IDs are only hints: the locked request, recipient
and token hash must all match. The caller binds Construction RLS to the trusted
request tenant and owns idempotency and the final shared transaction commit.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hmac
from uuid import UUID

from sqlalchemy import select

from database.models import User
from database.repositories.construction_client_link_requests import (
    ConstructionClientLinkRequestRepository, ConstructionClientLinkRequestState, MAX_VERSION,
)
from database.repositories.construction_client_user_links import (
    ConstructionClientUserLinkRepository, ConstructionClientUserLinkState,
    ConstructionClientUserLinkMutation, MAX_CLIENT_ROW_VERSION,
)
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.construction_audit import ConstructionAuditLogger, ConstructionAuditChange
from services.construction_client_link_request_issuance import (
    PlatformClientLinkIdentityReader, hash_client_link_confirmation_token,
)
from services.construction_clients import ConstructionClientOperationError, ConstructionClientValidationError
from services.construction_foundation import ConstructionRowVersionConflictError, require_expected_row_version
from services.construction_grants import ConstructionAccessContextResolver
from services.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError, require_construction_access,
)

CONFIRM_EVENT = 'construction_client_user_link_confirmed'
REQUEST_ENTITY = 'construction_client_user_link_request'


class ConstructionClientLinkConfirmationDeniedError(ConstructionClientValidationError):
    def __init__(self):
        super().__init__('Client link confirmation is not available.')


class PlatformClientLinkConfirmationIdentityReader(PlatformClientLinkIdentityReader):
    async def require_active_user(self, *, user_id):
        if not isinstance(user_id, UUID):
            raise ApiIdentityAccessError('Platform identity is not available.')
        statement = select(User.id, User.status).where(User.id == user_id, User.status == 'active').execution_options(autoflush=False)
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
        row = result.one_or_none()
        if row is None or tuple(row) != (user_id, 'active'):
            raise ApiIdentityAccessError('Platform identity is not available.')
        return user_id


@dataclass(frozen=True)
class _ClientLinkPrincipal:
    # Domain audit/authorization reference made only from the verified stored
    # request, not a new login identity or proof of an authenticated session.
    user_id: UUID
    tenant_id: UUID


@dataclass(frozen=True)
class ConstructionClientLinkConfirmationResult:
    request_id: UUID
    tenant_id: UUID
    client_id: UUID
    linked_user_id: UUID
    client_row_version: int
    request_row_version: int
    consumed_at: datetime
    client_changed: bool
    request_audit_id: UUID
    client_audit_id: UUID | None


class ConstructionClientLinkConfirmationService:
    def __init__(self, *, session, request_repository, client_repository, access_repository,
            event_repository, identity_reader, now_provider=None):
        methods = ((request_repository, ('get_request_for_update', 'consume_request')),
            (client_repository, ('get_state_for_update', 'change_linked_user')),
            (access_repository, ('has_active_platform_membership', 'list_active_grants')),
            (event_repository, ('create_event',)))
        if (session is None or not callable(getattr(session, 'in_transaction', None))
                or not callable(getattr(session, 'rollback', None)) or not hasattr(session, 'no_autoflush')
                or any(getattr(repo, 'session', None) is not session
                    or any(not callable(getattr(repo, name, None)) for name in names) for repo, names in methods)
                or getattr(identity_reader, 'session', None) is None or identity_reader.session is session
                or not callable(getattr(identity_reader, 'require_active_user', None))
                or (now_provider is not None and not callable(now_provider))):
            raise ValueError('Confirmation requires shared Construction writes and separate canonical Platform reads.')
        self.session, self.requests, self.clients = session, request_repository, client_repository
        self.access, self.identity = access_repository, identity_reader
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.audit = ConstructionAuditLogger(event_repository=event_repository)
        self.resolver = ConstructionAccessContextResolver(repository=access_repository, now_provider=self._now)

    def _now(self):
        now = self.now_provider()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ConstructionClientOperationError('Client confirmation clock is invalid.')
        return now.astimezone(timezone.utc)

    async def _active(self, user_id):
        if await self.identity.require_active_user(user_id=user_id) != user_id:
            raise ApiIdentityAccessError('Platform identity is not available.')

    async def _authorize_operator(self, request):
        principal = _ClientLinkPrincipal(request.initiated_by_user_id, request.tenant_id)
        await self._active(principal.user_id)
        if await self.access.has_active_platform_membership(user_id=principal.user_id, tenant_id=principal.tenant_id) is not True:
            raise ConstructionPermissionDeniedError('Client edit membership is not available.')
        context = await self.resolver.resolve(actor=principal)
        require_construction_access(actor=principal, access_context=context, permission_code='construction.projects.edit')

    @staticmethod
    def _client_state(value, tenant_id, client_id):
        if value is None:
            raise ConstructionClientLinkConfirmationDeniedError()
        if (not isinstance(value, ConstructionClientUserLinkState) or value.tenant_id != tenant_id
                or value.client_id != client_id or type(value.row_version) is not int
                or not 1 <= value.row_version <= MAX_CLIENT_ROW_VERSION
                or (value.linked_user_id is not None and not isinstance(value.linked_user_id, UUID))):
            raise ConstructionClientOperationError('Client confirmation base is invalid.')
        return value

    async def _audit(self, *, principal, event_type, entity_type, entity_id, changes, details, trace_id):
        event = await self.audit.record_operation(actor=principal, event_type=event_type,
            entity_type=entity_type, entity_id=entity_id, operation='update', changes=changes,
            details=details, trace_id=trace_id)
        payload = {'operation': 'update', 'changes': [c.as_payload() for c in changes], 'reason': None, **details}
        if (not isinstance(getattr(event, 'id', None), UUID)
                or getattr(event, 'tenant_id', None) != principal.tenant_id
                or getattr(event, 'user_id', None) != principal.user_id
                or getattr(event, 'entity_id', None) != entity_id
                or getattr(event, 'entity_type', None) != entity_type
                or getattr(event, 'event_type', None) != event_type
                or getattr(event, 'platform', None) != 'api' or getattr(event, 'payload', None) != payload
                or (trace_id is not None and getattr(event, 'trace_id', None) != trace_id)):
            raise ConstructionClientOperationError('Mandatory client confirmation audit receipt is invalid.')
        return event.id

    async def confirm_request(self, *, actor, tenant_id, client_id, request_id, token,
            expected_request_row_version, trace_id=None):
        try:
            # Do not call the ordinary Construction actor/membership gate for
            # the confirming user. Only the saved initiator needs edit access.
            if (not isinstance(actor, ApiActorContext) or actor.status != 'active'
                    or not isinstance(actor.user_id, UUID)):
                raise ApiIdentityAccessError('Authenticated Platform recipient is not available.')
            recipient_id = actor.user_id
            if (not all(isinstance(value, UUID) for value in (tenant_id, client_id, request_id))
                    or type(expected_request_row_version) is not int or not 1 <= expected_request_row_version <= MAX_VERSION
                    or (trace_id is not None and (not isinstance(trace_id, str) or not trace_id.strip() or len(trace_id) > 128))):
                raise ConstructionClientValidationError('Client confirmation context is invalid.')
            if self.session.in_transaction() is not True:
                raise ConstructionClientOperationError('Confirmation requires an existing caller transaction.')
            try:
                digest = hash_client_link_confirmation_token(token)
            except ConstructionClientValidationError:
                raise ConstructionClientLinkConfirmationDeniedError() from None
            with self.session.no_autoflush:
                await self._active(recipient_id)
                # Same lock order as issuance and other client mutations.
                before = self._client_state(await self.clients.get_state_for_update(
                    tenant_id=tenant_id, client_id=client_id), tenant_id, client_id)
                request = await self.requests.get_request_for_update(tenant_id=tenant_id, request_id=request_id,
                    target_user_id=recipient_id, token_hash=digest)
                if request is None:
                    raise ConstructionClientLinkConfirmationDeniedError()
                if not isinstance(request, ConstructionClientLinkRequestState):
                    raise ConstructionClientOperationError('Stored confirmation context is invalid.')
                if (request.request_id != request_id or request.tenant_id != tenant_id or request.client_id != client_id
                        or request.target_user_id != recipient_id or not hmac.compare_digest(request.token_hash, digest)):
                    raise ConstructionClientLinkConfirmationDeniedError()
                if request.consumed_at is not None or request.invalidated_at is not None or not request.issued_at <= self._now() < request.expires_at:
                    raise ConstructionClientLinkConfirmationDeniedError()
                require_expected_row_version(actual_row_version=request.row_version, expected_row_version=expected_request_row_version)
                require_expected_row_version(actual_row_version=before.row_version, expected_row_version=request.expected_client_row_version)
                if before.linked_user_id != request.expected_linked_user_id:
                    raise ConstructionRowVersionConflictError()
                if (request.row_version == MAX_VERSION
                        or (before.linked_user_id != recipient_id and before.row_version == MAX_CLIENT_ROW_VERSION)):
                    raise ConstructionClientValidationError('Client confirmation version is exhausted.')
                await self._authorize_operator(request)
                await self._active(recipient_id)
                as_of = self._now()
                if not request.issued_at <= as_of < request.expires_at:
                    raise ConstructionClientLinkConfirmationDeniedError()
                consumed = await self.requests.consume_request(state=request, as_of=as_of)
                if consumed is None:
                    raise ConstructionRowVersionConflictError()
                if (not isinstance(consumed, ConstructionClientLinkRequestState)
                        or consumed != replace(request, consumed_at=as_of, updated_at=as_of, row_version=request.row_version+1)):
                    raise ConstructionClientOperationError('Client confirmation consumption receipt is invalid.')
                changed = await self.clients.change_linked_user(tenant_id=tenant_id, client_id=client_id,
                    expected_row_version=before.row_version, expected_linked_user_id=before.linked_user_id,
                    linked_user_id=recipient_id, as_of=as_of)
                if changed is None:
                    raise ConstructionRowVersionConflictError()
                wanted_change = before.linked_user_id != recipient_id
                if (not isinstance(changed, ConstructionClientUserLinkMutation) or changed.changed is not wanted_change
                        or changed.state != ConstructionClientUserLinkState(client_id, tenant_id, recipient_id,
                            before.row_version+int(wanted_change))):
                    raise ConstructionClientOperationError('Confirmed client link receipt is invalid.')
                await self._authorize_operator(request)
                await self._active(recipient_id)
                principal = _ClientLinkPrincipal(recipient_id, request.tenant_id)
                details = {'related_client_id': str(client_id), 'confirmation_request_id': str(request_id),
                    'initiated_by_user_id': str(request.initiated_by_user_id),
                    'confirmation_method': 'authenticated_target_and_one_time_token',
                    'identity_link_action': 'operator_replacement' if request.expected_linked_user_id not in (None,recipient_id) else 'operator_link'}
                request_audit_id = await self._audit(principal=principal, event_type=CONFIRM_EVENT,
                    entity_type=REQUEST_ENTITY, entity_id=request_id, changes=(
                        ConstructionAuditChange('consumed_at', None, as_of.isoformat()),
                        ConstructionAuditChange('row_version', request.row_version, consumed.row_version)),
                    details={**details, 'client_changed': wanted_change}, trace_id=trace_id)
                client_audit_id = None
                if wanted_change:
                    client_audit_id = await self._audit(principal=principal, event_type='construction_client_updated',
                        entity_type='construction_client', entity_id=client_id, changes=(
                            ConstructionAuditChange('linked_user_id', str(before.linked_user_id) if before.linked_user_id else None, str(recipient_id)),
                            ConstructionAuditChange('row_version', before.row_version, changed.state.row_version)),
                        details=details, trace_id=trace_id)
                await self._authorize_operator(request)
                await self._active(recipient_id)
                if self.session.in_transaction() is not True:
                    raise ConstructionClientOperationError('Client confirmation transaction ended prematurely.')
                return ConstructionClientLinkConfirmationResult(request_id,tenant_id,client_id,recipient_id,
                    changed.state.row_version,consumed.row_version,as_of,wanted_change,request_audit_id,client_audit_id)
        except (ConstructionClientValidationError, ConstructionClientOperationError, ApiIdentityAccessError,
                ConstructionPermissionDeniedError, ConstructionScopeDeniedError, ConstructionRowVersionConflictError):
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise ConstructionClientOperationError('Client link confirmation failed.') from None
        except BaseException:
            await self.session.rollback()
            raise


def build_construction_client_link_confirmation_service(*, session, platform_session, now_provider=None):
    from database.repositories.construction import ConstructionAccessRepository
    from database.repositories.event import EventRepository
    return ConstructionClientLinkConfirmationService(session=session,
        request_repository=ConstructionClientLinkRequestRepository(session),
        client_repository=ConstructionClientUserLinkRepository(session), access_repository=ConstructionAccessRepository(session),
        event_repository=EventRepository(session), identity_reader=PlatformClientLinkConfirmationIdentityReader(platform_session),
        now_provider=now_provider)
