"""Authorized explicit self-link; operator confirmation is a separate flow.

actor is the existing authenticated server Platform context, never a request
user_id. This service only targets actor.user_id. The caller later commits the
client mutation and mandatory audit together. No public route is registered here.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from database.models import User
from database.repositories.construction_client_user_links import (
    ConstructionClientUserLinkMutation, ConstructionClientUserLinkRepository,
    ConstructionClientUserLinkState, MAX_CLIENT_ROW_VERSION,
)
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.construction_audit import ConstructionAuditChange, ConstructionAuditLogger
from services.construction_clients import (
    ConstructionClientNotFoundError, ConstructionClientOperationError, ConstructionClientValidationError,
)
from services.construction_foundation import ConstructionRowVersionConflictError, require_expected_row_version
from services.construction_grants import ConstructionAccessContextResolver
from services.construction_permissions import (
    ConstructionAccessContext, ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
    require_construction_access,
)


class ConstructionClientSelfLinkValidationError(ConstructionClientValidationError):
    pass


class PlatformClientSelfIdentityReader:
    """Refresh only canonical id/status using the existing Platform connection.

    This is not an authenticator: the caller already obtained actor through the
    normal Platform session/auth boundary. No new credential or Telegram proof
    is accepted, and no organization membership is inferred from the identity.
    """
    def __init__(self, session):
        if session is None or not callable(getattr(session, 'execute', None)) or not hasattr(session, 'no_autoflush'):
            raise ValueError('Canonical self identity requires the Platform read session.')
        self.session = session

    async def require_active_actor(self, *, actor):
        if (not isinstance(actor, ApiActorContext) or actor.status != 'active'
                or not isinstance(actor.user_id, UUID) or not isinstance(actor.tenant_id, UUID)):
            raise ApiIdentityAccessError('Authenticated Platform actor is not available.')
        statement = select(User.id, User.status).where(
            User.id == actor.user_id, User.status == 'active',
        ).execution_options(autoflush=False)
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
        row = result.one_or_none()
        if row is None or tuple(row) != (actor.user_id, 'active'):
            raise ApiIdentityAccessError('Authenticated Platform actor is not available.')
        return actor


@dataclass(frozen=True)
class ConstructionClientSelfLinkResult:
    client_id: UUID
    tenant_id: UUID
    linked_user_id: UUID
    row_version: int
    changed: bool
    audit_id: UUID | None


class ConstructionClientSelfLinkService:
    def __init__(self, *, session, repository, access_repository, event_repository, identity_reader, now_provider=None):
        methods = (
            (repository, ('get_state_for_update', 'change_linked_user')),
            (access_repository, ('has_active_platform_membership', 'list_active_grants')),
            (event_repository, ('create_event',)),
        )
        if (session is None or not callable(getattr(session, 'in_transaction', None))
                or not callable(getattr(session, 'rollback', None)) or not hasattr(session, 'no_autoflush')
                or any(getattr(repo, 'session', None) is not session
                    or any(not callable(getattr(repo, name, None)) for name in names) for repo, names in methods)
                or getattr(identity_reader, 'session', None) is None
                or getattr(identity_reader, 'session', None) is session
                or not callable(getattr(identity_reader, 'require_active_actor', None))
                or (now_provider is not None and not callable(now_provider))):
            raise ValueError('Self-link requires shared Construction repositories and a separate canonical Platform identity reader.')
        self.session, self.repository, self.access = session, repository, access_repository
        self.identity, self.events = identity_reader, event_repository
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(repository=access_repository, now_provider=self._now)
        self.audit = ConstructionAuditLogger(event_repository=event_repository)

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionClientOperationError('Client link clock is invalid.')
        return value.astimezone(timezone.utc)

    @staticmethod
    def _scope(actor, context):
        if (not isinstance(actor, ApiActorContext) or actor.status != 'active'
                or not isinstance(actor.user_id, UUID) or not isinstance(actor.tenant_id, UUID)
                or not isinstance(context, ConstructionAccessContext)):
            raise ConstructionPermissionDeniedError('Client edit authorization is not available.')
        # Existing CRM client-edit policy; no permission is inferred from the link.
        require_construction_access(actor=actor, access_context=context, permission_code='construction.projects.edit')

    async def _authorize(self, actor):
        current = await self.identity.require_active_actor(actor=actor)
        if (not isinstance(current, ApiActorContext) or current.status != 'active'
                or current.user_id != actor.user_id or current.tenant_id != actor.tenant_id):
            raise ApiIdentityAccessError('Authenticated Platform actor is not available.')
        if await self.access.has_active_platform_membership(user_id=current.user_id, tenant_id=current.tenant_id) is not True:
            raise ConstructionPermissionDeniedError('Client edit membership is not available.')
        context = await self.resolver.resolve(actor=current)
        self._scope(current, context)
        return current

    @staticmethod
    def _state(value, actor, client_id):
        if value is None:
            raise ConstructionClientNotFoundError('Construction client is not available.')
        if (not isinstance(value, ConstructionClientUserLinkState)
                or value.client_id != client_id or value.tenant_id != actor.tenant_id
                or (value.linked_user_id is not None and not isinstance(value.linked_user_id, UUID))
                or type(value.row_version) is not int or not 1 <= value.row_version <= MAX_CLIENT_ROW_VERSION):
            raise ConstructionClientOperationError('Client link state is invalid.')
        return value

    async def link_self(self, *, actor, access_context, client_id, expected_row_version, trace_id=None):
        return await self._change(actor=actor, access_context=access_context, client_id=client_id,
            expected_row_version=expected_row_version, previous_user_id=None, replacement=False, trace_id=trace_id)

    async def replace_with_self(self, *, actor, access_context, client_id, previous_user_id, expected_row_version, trace_id=None):
        return await self._change(actor=actor, access_context=access_context, client_id=client_id,
            expected_row_version=expected_row_version, previous_user_id=previous_user_id, replacement=True, trace_id=trace_id)

    async def _change(self, *, actor, access_context, client_id, expected_row_version, previous_user_id, replacement, trace_id):
        try:
            self._scope(actor, access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionClientOperationError('Self-link requires an existing caller transaction.')
            if (not isinstance(client_id, UUID) or type(expected_row_version) is not int
                    or not 1 <= expected_row_version <= MAX_CLIENT_ROW_VERSION):
                raise ConstructionClientSelfLinkValidationError('Self-link requires a client UUID and positive BIGINT version.')
            if replacement and (not isinstance(previous_user_id, UUID) or previous_user_id == actor.user_id):
                raise ConstructionClientSelfLinkValidationError('Explicit replacement requires the previous different linked user.')
            if trace_id is not None and (not isinstance(trace_id, str) or not trace_id.strip() or len(trace_id) > 128):
                raise ConstructionClientSelfLinkValidationError('Client link trace identifier is invalid.')
            with self.session.no_autoflush:
                actor = await self._authorize(actor)
                before = self._state(await self.repository.get_state_for_update(
                    tenant_id=actor.tenant_id, client_id=client_id), actor, client_id)
                require_expected_row_version(actual_row_version=before.row_version, expected_row_version=expected_row_version)
                if replacement:
                    if before.linked_user_id != previous_user_id:
                        raise ConstructionRowVersionConflictError()
                elif before.linked_user_id not in (None, actor.user_id):
                    raise ConstructionClientSelfLinkValidationError('Existing user link requires explicit replacement.')
                actor = await self._authorize(actor)
                if before.linked_user_id == actor.user_id:
                    return ConstructionClientSelfLinkResult(client_id, actor.tenant_id, actor.user_id,
                        before.row_version, False, None)
                if before.row_version == MAX_CLIENT_ROW_VERSION:
                    raise ConstructionClientSelfLinkValidationError('Client link version is exhausted.')
                changed = await self.repository.change_linked_user(tenant_id=actor.tenant_id, client_id=client_id,
                    expected_row_version=before.row_version, expected_linked_user_id=before.linked_user_id,
                    linked_user_id=actor.user_id, as_of=self._now())
                if changed is None:
                    raise ConstructionRowVersionConflictError()
                if not isinstance(changed, ConstructionClientUserLinkMutation) or changed.changed is not True:
                    raise ConstructionClientOperationError('Client link write receipt is invalid.')
                after = self._state(changed.state, actor, client_id)
                if after.linked_user_id != actor.user_id or after.row_version != before.row_version + 1:
                    raise ConstructionClientOperationError('Client link write receipt is invalid.')
                actor = await self._authorize(actor)
                action = 'replace_with_self' if replacement else 'self_link'
                changes = (
                    ConstructionAuditChange('linked_user_id', str(before.linked_user_id) if before.linked_user_id else None, str(actor.user_id)),
                    ConstructionAuditChange('row_version', before.row_version, after.row_version),
                )
                payload = {'operation': 'update', 'changes': [c.as_payload() for c in changes], 'reason': None,
                    'related_client_id': str(client_id), 'confirmation_method': 'authenticated_self', 'identity_link_action': action}
                event = await self.audit.record_operation(actor=actor, event_type='construction_client_updated',
                    entity_type='construction_client', entity_id=client_id, operation='update', changes=changes,
                    details={'related_client_id': str(client_id), 'confirmation_method': 'authenticated_self',
                        'identity_link_action': action}, trace_id=trace_id)
                if (not isinstance(getattr(event, 'id', None), UUID)
                        or getattr(event, 'tenant_id', None) != actor.tenant_id
                        or getattr(event, 'user_id', None) != actor.user_id
                        or getattr(event, 'entity_id', None) != client_id
                        or getattr(event, 'entity_type', None) != 'construction_client'
                        or getattr(event, 'event_type', None) != 'construction_client_updated'
                        or getattr(event, 'platform', None) != 'api'
                        or getattr(event, 'payload', None) != payload
                        or (trace_id is not None and getattr(event, 'trace_id', None) != trace_id)):
                    raise ConstructionClientOperationError('Mandatory client link audit receipt is invalid.')
                await self._authorize(actor)
                return ConstructionClientSelfLinkResult(client_id, actor.tenant_id, actor.user_id,
                    after.row_version, True, event.id)
        except (ConstructionClientValidationError, ConstructionClientNotFoundError, ConstructionClientOperationError,
                ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionRowVersionConflictError, ApiIdentityAccessError):
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise ConstructionClientOperationError('Client self-link operation failed.') from None
        except BaseException:
            await self.session.rollback()
            raise


def build_construction_client_self_link_service(*, session, platform_session, now_provider=None):
    from database.repositories.construction import ConstructionAccessRepository
    from database.repositories.event import EventRepository
    return ConstructionClientSelfLinkService(session=session,
        repository=ConstructionClientUserLinkRepository(session),
        access_repository=ConstructionAccessRepository(session), event_repository=EventRepository(session),
        identity_reader=PlatformClientSelfIdentityReader(platform_session), now_provider=now_provider)
