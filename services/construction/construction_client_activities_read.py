from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from database.repositories.construction.construction_client_activities import (
    ConstructionClientActivitiesCursor, ConstructionClientActivityRecord,
)
from services.construction.construction_activity_read import ActivityPlannedReadResponse, PUBLIC_ACTIVITY_PLANNED_FIELDS
from services.construction.construction_clients import (
    ConstructionClientNotFoundError, ConstructionClientValidationError, ConstructionClientOperationError,
)
from services.construction.construction_grants import ConstructionAccessContextResolver
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext, ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError, require_construction_access, require_construction_role_scope,
)


CLIENT_ACTIVITIES_READ_PERMISSION = 'construction.projects.read'


class ConstructionClientActivitiesReadOperationError(ConstructionClientOperationError):
    pass


@dataclass(frozen=True)
class ConstructionClientActivitiesReadCursor:
    tenant_id: UUID
    user_id: UUID
    client_id: UUID
    due_at: datetime
    activity_id: UUID

    def __post_init__(self):
        if not isinstance(self.user_id, UUID):
            raise ConstructionClientValidationError('Client activities cursor requires an actor UUID.')
        try:
            checked = ConstructionClientActivitiesCursor(self.tenant_id, self.client_id, self.due_at, self.activity_id)
        except ValueError:
            raise ConstructionClientValidationError('Client activities cursor is invalid.') from None
        object.__setattr__(self, 'due_at', checked.due_at)


@dataclass(frozen=True)
class ConstructionClientActivitiesReadPage:
    items: tuple
    next_cursor: ConstructionClientActivitiesReadCursor | None


class ConstructionClientActivitiesReadService:
    """Read the client card's planned activities; caller owns the read transaction."""

    def __init__(self, *, session, repository, client_repository, access_repository, now_provider=None):
        protocols = ((repository, ('list_planned_activities_for_client',)),
            (client_repository, ('get_active_client',)),
            (access_repository, ('has_active_platform_membership', 'list_active_grants')))
        if (session is None or not callable(getattr(session, 'in_transaction', None))
            or not hasattr(session, 'no_autoflush')
            or any(getattr(repo, 'session', None) is not session
                or any(not callable(getattr(repo, name, None)) for name in methods) for repo, methods in protocols)
            or (now_provider is not None and not callable(now_provider))):
            raise ValueError('Client activities read requires shared-session repositories.')
        self.session, self.repository = session, repository
        self.client_repository, self.access_repository = client_repository, access_repository
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(repository=access_repository, now_provider=self._now)

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionClientActivitiesReadOperationError('Client activities clock must be aware.')
        return value.astimezone(timezone.utc)

    @staticmethod
    def _authorize(actor, context):
        if (getattr(actor, 'status', None) != 'active'
            or not isinstance(getattr(actor, 'user_id', None), UUID)
            or not isinstance(getattr(actor, 'tenant_id', None), UUID)
            or not isinstance(context, ConstructionAccessContext)
            or context.user_id != actor.user_id or context.tenant_id != actor.tenant_id):
            raise ConstructionPermissionDeniedError('Client activities access is not available.')
        for grant in context.grants:
            if not isinstance(grant, ConstructionGrantContext) or not isinstance(grant.scope_id, UUID):
                raise ConstructionScopeDeniedError('Client activities scope is invalid.')
            require_construction_role_scope(role=grant.role, scope_type=grant.scope_type)
        # Client cards keep the existing tenant CRM boundary. Assigned project,
        # responsible user or creator identity cannot authorize a client card.
        require_construction_access(actor=actor, access_context=context, permission_code=CLIENT_ACTIVITIES_READ_PERMISSION)

    async def _context(self, actor):
        if await self.access_repository.has_active_platform_membership(
            tenant_id=actor.tenant_id, user_id=actor.user_id) is not True:
            raise ConstructionPermissionDeniedError('Client activities membership is not available.')
        context = await self.resolver.resolve(actor=actor)
        self._authorize(actor, context)
        return context

    async def _client(self, actor, client_id):
        row = await self.client_repository.get_active_client(tenant_id=actor.tenant_id, client_id=client_id)
        if (row is None or getattr(row, 'id', None) != client_id or getattr(row, 'tenant_id', None) != actor.tenant_id
            or getattr(row, 'deleted_at', 'missing') is not None):
            raise ConstructionClientNotFoundError('Client is not available in the authorized tenant.')

    @staticmethod
    def _item(record, *, actor, client_id, context):
        if not isinstance(record, ConstructionClientActivityRecord):
            raise ConstructionClientActivitiesReadOperationError('Client activity evidence is invalid.')
        row = record.activity
        if (row is None or not isinstance(getattr(row, 'id', None), UUID)
            or getattr(row, 'tenant_id', None) != actor.tenant_id or getattr(row, 'deleted_at', 'missing') is not None):
            raise ConstructionClientActivitiesReadOperationError('Client activity result context is invalid.')
        related = []
        for field, resource_id, tenant_id, parent_client in (
            ('lead_id', record.linked_lead_id, record.linked_lead_tenant_id, record.linked_lead_client_id),
            ('project_id', record.linked_project_id, record.linked_project_tenant_id, record.linked_project_client_id)):
            link = getattr(row, field, 'missing')
            if link is None:
                if any(value is not None for value in (resource_id, tenant_id, parent_client)):
                    raise ConstructionClientActivitiesReadOperationError('Unexpected client activity parent evidence.')
            elif (not isinstance(link, UUID) or resource_id != link or tenant_id != actor.tenant_id
                or (parent_client is not None and not isinstance(parent_client, UUID))):
                raise ConstructionClientActivitiesReadOperationError('Client activity parent evidence is invalid.')
            related.append(parent_client)
        direct = getattr(row, 'client_id', 'missing')
        if direct is not None and not isinstance(direct, UUID):
            raise ConstructionClientActivitiesReadOperationError('Client activity direct reference is invalid.')
        if client_id not in (direct, *related):
            raise ConstructionClientActivitiesReadOperationError('Activity does not belong to this client.')
        model = ActivityPlannedReadResponse.model_validate({field: getattr(row, field) for field in PUBLIC_ACTIVITY_PLANNED_FIELDS})
        if model.project_id is not None:
            require_construction_access(actor=actor, access_context=context,
                permission_code=CLIENT_ACTIVITIES_READ_PERMISSION, project_id=model.project_id)
        return model

    async def list_planned_activities_for_client(self, *, actor, access_context, client_id, limit=20, after=None):
        try:
            if not isinstance(client_id, UUID) or type(limit) is not int or not 1 <= limit <= 100:
                raise ConstructionClientValidationError('Client activities require a UUID and page limit between 1 and 100.')
            self._authorize(actor, access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionClientActivitiesReadOperationError('Client activities require an active caller transaction.')
            position = None
            if after is not None:
                if not isinstance(after, ConstructionClientActivitiesReadCursor):
                    raise ConstructionClientValidationError('Client activities read cursor is invalid.')
                after = ConstructionClientActivitiesReadCursor(after.tenant_id, after.user_id, after.client_id, after.due_at, after.activity_id)
                if after.tenant_id != actor.tenant_id or after.user_id != actor.user_id or after.client_id != client_id:
                    raise ConstructionClientValidationError('Client activities cursor does not match this actor/client/tenant.')
                position = ConstructionClientActivitiesCursor(after.tenant_id, after.client_id, after.due_at, after.activity_id)
            with self.session.no_autoflush:
                await self._context(actor)
                await self._client(actor, client_id)
                context = await self._context(actor)
                rows = await self.repository.list_planned_activities_for_client(
                    tenant_id=actor.tenant_id, client_id=client_id, limit=limit, after=position)
                if not isinstance(rows, list) or len(rows) > limit + 1:
                    raise ConstructionClientActivitiesReadOperationError('Client activities page result is invalid.')
                models = [self._item(row, actor=actor, client_id=client_id, context=context) for row in rows]
                previous = (position.due_at, position.activity_id) if position is not None else None
                seen = set()
                for model in models:
                    key = (model.due_at, model.id)
                    if model.id in seen or (previous is not None and key <= previous):
                        raise ConstructionClientActivitiesReadOperationError('Client activities page order is invalid.')
                    previous = key
                    seen.add(model.id)
                items = tuple(model.model_dump(mode='json') for model in models[:limit])
                cursor = None
                if len(models) > limit:
                    last = models[limit - 1]
                    cursor = ConstructionClientActivitiesReadCursor(actor.tenant_id, actor.user_id, client_id, last.due_at, last.id)
                await self._client(actor, client_id)
                current = await self._context(actor)
                for model in models:
                    if model.project_id is not None:
                        require_construction_access(actor=actor, access_context=current,
                            permission_code=CLIENT_ACTIVITIES_READ_PERMISSION, project_id=model.project_id)
                return ConstructionClientActivitiesReadPage(items, cursor)
        except (ConstructionClientValidationError, ConstructionClientNotFoundError, ConstructionClientActivitiesReadOperationError,
            ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
            raise
        except Exception:
            raise ConstructionClientActivitiesReadOperationError('Client activities read failed.') from None


def build_construction_client_activities_read_service(*, session, now_provider=None):
    from database.repositories.construction.construction_client_activities import ConstructionClientActivitiesRepository
    from database.repositories.construction import ConstructionClientRepository, ConstructionAccessRepository
    return ConstructionClientActivitiesReadService(session=session, repository=ConstructionClientActivitiesRepository(session),
        client_repository=ConstructionClientRepository(session), access_repository=ConstructionAccessRepository(session), now_provider=now_provider)
