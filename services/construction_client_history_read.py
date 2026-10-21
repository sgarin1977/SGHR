from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, StrictStr

from database.repositories.construction_client_history import ConstructionClientHistoryCursor, ConstructionClientHistoryRecord
from services.construction_client_history_contract import construction_history_event_belongs_to_client
from services.construction_clients import ConstructionClientNotFoundError, ConstructionClientValidationError, ConstructionClientOperationError
from services.construction_grants import ConstructionAccessContextResolver
from services.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext, ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError, require_construction_access, require_construction_role_scope,
)


CLIENT_HISTORY_READ_PERMISSION = 'construction.projects.read'
_CHANGE_FIELDS = {
    'construction_client': frozenset({'display_name', 'client_type', 'phone', 'email', 'notes', 'deleted_at'}),
    'construction_project': frozenset({'name', 'client_id', 'responsible_user_id', 'comment', 'address_raw',
        'address_formatted', 'country_code', 'region', 'city', 'postal_code', 'latitude', 'longitude',
        'address_provider', 'provider_place_id', 'address_verification_status', 'status'}),
    'construction_lead': frozenset({'status', 'converted_client_id', 'converted_project_id'}),
    'construction_activity': frozenset({'status', 'completed_at', 'cancelled_at'}),
}


class ConstructionClientHistoryReadOperationError(ConstructionClientOperationError):
    pass


class ClientHistoryReadResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: UUID
    tenant_id: UUID
    user_id: UUID | None
    event_type: StrictStr
    entity_type: StrictStr
    entity_id: UUID
    payload: dict
    created_at: AwareDatetime


PUBLIC_CLIENT_HISTORY_FIELDS = frozenset(ClientHistoryReadResponse.model_fields)


@dataclass(frozen=True)
class ConstructionClientHistoryReadCursor:
    tenant_id: UUID
    user_id: UUID
    client_id: UUID
    created_at: datetime
    event_id: UUID

    def __post_init__(self):
        if (not isinstance(self.user_id, UUID) or not isinstance(self.created_at, datetime)
            or self.created_at.tzinfo is None or self.created_at.utcoffset() is None):
            raise ConstructionClientValidationError('History read cursor requires actor UUID and aware timestamp.')
        try:
            checked = ConstructionClientHistoryCursor(self.tenant_id, self.client_id, self.created_at, self.event_id)
        except ValueError:
            raise ConstructionClientValidationError('History read cursor is invalid.') from None
        object.__setattr__(self, 'created_at', checked.created_at)


@dataclass(frozen=True)
class ConstructionClientHistoryReadPage:
    items: tuple
    next_cursor: ConstructionClientHistoryReadCursor | None
    legacy_activity_evidence: tuple = field(default=(), repr=False)


def _scalar(value):
    if value is None or type(value) in (str, int, bool) or (type(value) is float and isfinite(value)):
        return value
    raise ConstructionClientHistoryReadOperationError('History change value is invalid.')


def _payload(record):
    source = record.payload
    reason = source.get('reason')
    if reason is not None and not isinstance(reason, str):
        raise ConstructionClientHistoryReadOperationError('History reason is invalid.')
    changes = [dict(field=item['field'], old_value=_scalar(item['old_value']), new_value=_scalar(item['new_value']))
        for item in source['changes'] if item['field'] in _CHANGE_FIELDS[record.entity_type]]
    result = dict(operation=source['operation'], changes=changes, reason=reason)
    if 'related_client_id' in source:
        result['related_client_id'] = source['related_client_id']
    if record.event_type == 'construction_lead_converted':
        for key in ('created_client_id', 'created_project_id'):
            if key in source:
                value = source[key]
                if value is not None:
                    try:
                        valid = isinstance(value, str) and str(UUID(value)) == value
                    except (ValueError, TypeError, AttributeError):
                        valid = False
                    if not valid:
                        raise ConstructionClientHistoryReadOperationError('History conversion detail is invalid.')
                result[key] = value
    if record.event_type in ('construction_activity_completed', 'construction_activity_cancelled') and 'result_changed' in source:
        if type(source['result_changed']) is not bool:
            raise ConstructionClientHistoryReadOperationError('History activity detail is invalid.')
        result['result_changed'] = source['result_changed']
    return result


class ConstructionClientHistoryReadService:
    """Read a domain client history; caller owns transaction and RLS context."""

    def __init__(self, *, session, repository, access_repository, now_provider=None):
        protocols = ((repository, ('list_history_for_client', 'get_client_identity_for_history')),
            (access_repository, ('has_active_platform_membership', 'list_active_grants')))
        if (session is None or not callable(getattr(session, 'in_transaction', None))
            or not hasattr(session, 'no_autoflush')
            or any(getattr(repo, 'session', None) is not session
                or any(not callable(getattr(repo, name, None)) for name in methods) for repo, methods in protocols)
            or (now_provider is not None and not callable(now_provider))):
            raise ValueError('History read requires shared-session repositories.')
        self.session, self.repository, self.access_repository = session, repository, access_repository
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(repository=access_repository, now_provider=self._now)

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionClientHistoryReadOperationError('History read clock must be aware.')
        return value.astimezone(timezone.utc)

    @staticmethod
    def _authorize(actor, context):
        if (getattr(actor, 'status', None) != 'active'
            or not isinstance(getattr(actor, 'user_id', None), UUID)
            or not isinstance(getattr(actor, 'tenant_id', None), UUID)
            or not isinstance(context, ConstructionAccessContext)
            or context.user_id != actor.user_id or context.tenant_id != actor.tenant_id):
            raise ConstructionPermissionDeniedError('Client history access is not available.')
        for grant in context.grants:
            if not isinstance(grant, ConstructionGrantContext) or not isinstance(grant.scope_id, UUID):
                raise ConstructionScopeDeniedError('Client history scope is invalid.')
            require_construction_role_scope(role=grant.role, scope_type=grant.scope_type)
        require_construction_access(actor=actor, access_context=context, permission_code=CLIENT_HISTORY_READ_PERMISSION)

    async def _context(self, actor):
        if await self.access_repository.has_active_platform_membership(tenant_id=actor.tenant_id, user_id=actor.user_id) is not True:
            raise ConstructionPermissionDeniedError('Client history membership is not available.')
        context = await self.resolver.resolve(actor=actor)
        self._authorize(actor, context)
        return context

    async def _client(self, actor, client_id):
        identity = await self.repository.get_client_identity_for_history(tenant_id=actor.tenant_id, client_id=client_id)
        if (not isinstance(identity, dict) or set(identity) != {'id', 'tenant_id'}
            or identity['id'] != client_id or identity['tenant_id'] != actor.tenant_id):
            raise ConstructionClientNotFoundError('Client history is not available in the authorized tenant.')

    @staticmethod
    def _project_scope(actor, context, model):
        if model.entity_type == 'construction_project':
            require_construction_access(actor=actor, access_context=context,
                permission_code=CLIENT_HISTORY_READ_PERMISSION, project_id=model.entity_id)

    @classmethod
    def _item(cls, record, *, actor, client_id, context):
        if (not isinstance(record, ConstructionClientHistoryRecord)
            or not construction_history_event_belongs_to_client(record, tenant_id=actor.tenant_id, client_id=client_id)
            or (record.user_id is not None and not isinstance(record.user_id, UUID))):
            raise ConstructionClientHistoryReadOperationError('Client history result context is invalid.')
        position = ConstructionClientHistoryCursor(actor.tenant_id, client_id, record.created_at, record.id)
        model = ClientHistoryReadResponse(id=record.id, tenant_id=record.tenant_id, user_id=record.user_id,
            event_type=record.event_type, entity_type=record.entity_type, entity_id=record.entity_id,
            payload=_payload(record), created_at=position.created_at)
        cls._project_scope(actor, context, model)
        return model

    async def list_history_for_client(self, *, actor, access_context, client_id, limit=20, after=None):
        try:
            if not isinstance(client_id, UUID) or type(limit) is not int or not 1 <= limit <= 100:
                raise ConstructionClientValidationError('History read requires client UUID and page limit between 1 and 100.')
            self._authorize(actor, access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionClientHistoryReadOperationError('History read requires an active caller transaction.')
            position = None
            if after is not None:
                if not isinstance(after, ConstructionClientHistoryReadCursor):
                    raise ConstructionClientValidationError('History read cursor is invalid.')
                after = ConstructionClientHistoryReadCursor(after.tenant_id, after.user_id, after.client_id, after.created_at, after.event_id)
                if after.tenant_id != actor.tenant_id or after.user_id != actor.user_id or after.client_id != client_id:
                    raise ConstructionClientValidationError('History read cursor does not match this actor/client/tenant.')
                position = ConstructionClientHistoryCursor(after.tenant_id, after.client_id, after.created_at, after.event_id)
            with self.session.no_autoflush:
                await self._context(actor)
                await self._client(actor, client_id)
                context = await self._context(actor)
                rows = await self.repository.list_history_for_client(tenant_id=actor.tenant_id, client_id=client_id, limit=limit, after=position)
                if not isinstance(rows, list) or len(rows) > limit + 1:
                    raise ConstructionClientHistoryReadOperationError('History read page result is invalid.')
                models = [self._item(row, actor=actor, client_id=client_id, context=context) for row in rows]
                previous = (position.created_at, position.event_id) if position is not None else None
                seen = set()
                for model in models:
                    key = (model.created_at, model.id)
                    if model.id in seen or (previous is not None and key >= previous):
                        raise ConstructionClientHistoryReadOperationError('History read page order is invalid.')
                    previous = key
                    seen.add(model.id)
                items = tuple(model.model_dump(mode='json') for model in models[:limit])
                cursor = None
                if len(models) > limit:
                    last = models[limit - 1]
                    cursor = ConstructionClientHistoryReadCursor(actor.tenant_id, actor.user_id, client_id, last.created_at, last.id)
                await self._client(actor, client_id)
                current = await self._context(actor)
                for model in models:
                    self._project_scope(actor, current, model)
                # Keep proof outside public event payload. The card composer
                # can revalidate attribution without inventing a legacy marker.
                evidence = tuple((row.id, row.activity_evidence) for row in rows[:limit]
                    if row.activity_evidence is not None and 'related_client_id' not in row.payload
                    and row.entity_type == 'construction_activity')
                return ConstructionClientHistoryReadPage(items, cursor, evidence)
        except (ConstructionClientValidationError, ConstructionClientNotFoundError, ConstructionClientHistoryReadOperationError,
            ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
            raise
        except Exception:
            raise ConstructionClientHistoryReadOperationError('Client history read failed.') from None


def build_construction_client_history_read_service(*, session, now_provider=None):
    from database.repositories.construction_client_history import ConstructionClientHistoryRepository
    from database.repositories.construction import ConstructionAccessRepository
    return ConstructionClientHistoryReadService(session=session, repository=ConstructionClientHistoryRepository(session),
        access_repository=ConstructionAccessRepository(session), now_provider=now_provider)
