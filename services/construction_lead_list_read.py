"""Authorized tenant CRM lead list with bound cursor and computed next actions.

Uses the existing lead filters, sorting and access policy. The caller owns the
read transaction. No HTTP paths, mutable next_action or new schema are defined.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from database.repositories.construction_leads import ConstructionLeadCursor
from services.construction_activity_read import ActivityPlannedReadResponse
from services.construction_grants import ConstructionAccessContextResolver
from services.construction_lead_read import ConstructionLeadReadService, ConstructionLeadReadNotFoundError, ConstructionLeadReadOperationError
from services.construction_lead_rules import ConstructionLeadValidationError, normalize_construction_lead_source, require_construction_lead_status
from services.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError


class ConstructionLeadListReadOperationError(ConstructionLeadReadOperationError):
    pass


@dataclass(frozen=True)
class ConstructionLeadListFilters:
    status: str | None = None
    source: str | None = None
    responsible_user_id: UUID | None = None
    converted_client_id: UUID | None = None
    converted_project_id: UUID | None = None

    def __post_init__(self):
        if self.status is not None:
            require_construction_lead_status(self.status)
        if self.source is not None:
            object.__setattr__(self, 'source', normalize_construction_lead_source(self.source))
        if any(value is not None and not isinstance(value, UUID) for value in (
                self.responsible_user_id, self.converted_client_id, self.converted_project_id)):
            raise ConstructionLeadValidationError('Lead list filters require UUID references.')

    def values(self):
        return {name:getattr(self,name) for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class ConstructionLeadListReadCursor:
    tenant_id: UUID
    user_id: UUID
    filters: ConstructionLeadListFilters
    updated_at: datetime
    lead_id: UUID

    def __post_init__(self):
        if not isinstance(self.user_id, UUID) or not isinstance(self.filters, ConstructionLeadListFilters):
            raise ConstructionLeadValidationError('Lead list cursor requires actor and filter context.')
        object.__setattr__(self, 'filters', ConstructionLeadListFilters(**self.filters.values()))
        try:
            checked = ConstructionLeadCursor(self.tenant_id, self.updated_at, self.lead_id)
        except ValueError:
            raise ConstructionLeadValidationError('Lead list cursor position is invalid.') from None
        object.__setattr__(self, 'updated_at', checked.updated_at)


@dataclass(frozen=True)
class ConstructionLeadListReadPage:
    items: tuple
    next_cursor: ConstructionLeadListReadCursor | None


class ConstructionLeadListReadService:
    def __init__(self, *, session, repository, access_repository, now_provider=None):
        if (session is None or not callable(getattr(session,'in_transaction',None))
                or not hasattr(session,'no_autoflush')
                or getattr(repository,'session',None) is not session
                or any(not callable(getattr(repository,name,None)) for name in ('list_active_leads','list_next_actions_for_leads'))
                or getattr(access_repository,'session',None) is not session
                or any(not callable(getattr(access_repository,name,None)) for name in ('has_active_platform_membership','list_active_grants'))
                or (now_provider is not None and not callable(now_provider))):
            raise ValueError('Lead list requires shared-session read repositories.')
        self.session, self.repository, self.access = session, repository, access_repository
        self.now_provider = now_provider or (lambda:datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(repository=access_repository,now_provider=self._now)

    def _now(self):
        value = self.now_provider()
        if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionLeadListReadOperationError('Lead list clock must be aware.')
        return value.astimezone(timezone.utc)

    async def _context(self, actor):
        if await self.access.has_active_platform_membership(user_id=actor.user_id,tenant_id=actor.tenant_id) is not True:
            raise ConstructionPermissionDeniedError('Lead list membership is not available.')
        context = await self.resolver.resolve(actor=actor)
        ConstructionLeadReadService._authorize(actor,context)
        return context

    async def list_leads(self, *, actor, access_context, filters=None, limit=20, after=None):
        try:
            if type(limit) is not int or not 1<=limit<=100:
                raise ConstructionLeadValidationError('Lead list limit must be between 1 and 100.')
            if filters is not None and not isinstance(filters,ConstructionLeadListFilters):
                raise ConstructionLeadValidationError('Lead list requires typed filters.')
            filters = ConstructionLeadListFilters(**filters.values()) if filters is not None else ConstructionLeadListFilters()
            ConstructionLeadReadService._authorize(actor,access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionLeadListReadOperationError('Lead list requires an active caller transaction.')
            position = None
            if after is not None:
                if not isinstance(after,ConstructionLeadListReadCursor):
                    raise ConstructionLeadValidationError('Lead list requires a typed cursor.')
                after = ConstructionLeadListReadCursor(after.tenant_id,after.user_id,after.filters,after.updated_at,after.lead_id)
                if after.tenant_id!=actor.tenant_id or after.user_id!=actor.user_id or after.filters!=filters:
                    raise ConstructionLeadValidationError('Lead list cursor does not match this actor, tenant and filters.')
                position = ConstructionLeadCursor(after.tenant_id,after.updated_at,after.lead_id)
            with self.session.no_autoflush:
                context = await self._context(actor)
                rows = await self.repository.list_active_leads(tenant_id=actor.tenant_id,
                    limit=limit,after=position,**filters.values())
                if not isinstance(rows,list) or len(rows)>limit+1:
                    raise ConstructionLeadListReadOperationError('Lead list repository page is invalid.')
                models, identifiers = [], set()
                previous = (position.updated_at,position.lead_id) if position is not None else None
                for row in rows:
                    if not isinstance(getattr(row,'id',None),UUID):
                        raise ConstructionLeadListReadOperationError('Lead list identifier is invalid.')
                    model = ConstructionLeadReadService._lead(row,actor=actor,lead_id=row.id)
                    for name, value in filters.values().items():
                        if value is not None and getattr(model,name)!=value:
                            raise ConstructionLeadListReadOperationError('Lead list row does not match its filters.')
                    key = (model.updated_at,model.id)
                    if model.id in identifiers or (previous is not None and key>=previous):
                        raise ConstructionLeadListReadOperationError('Lead list order is invalid.')
                    ConstructionLeadReadService._project_access(actor,context,model)
                    previous = key
                    identifiers.add(model.id)
                    models.append(model)
                visible = models[:limit]
                context = await self._context(actor)
                for model in models:
                    ConstructionLeadReadService._project_access(actor,context,model)
                action_rows = await self.repository.list_next_actions_for_leads(tenant_id=actor.tenant_id,
                    lead_ids=tuple(model.id for model in visible)) if visible else []
                if not isinstance(action_rows,list) or len(action_rows)>len(visible):
                    raise ConstructionLeadListReadOperationError('Lead next-action batch is invalid.')
                actions, action_ids = {}, set()
                visible_ids = {model.id for model in visible}
                for row in action_rows:
                    if getattr(row,'deleted_at','missing') is not None:
                        raise ConstructionLeadListReadOperationError('Lead next action is unavailable.')
                    action = ActivityPlannedReadResponse.model_validate({name:getattr(row,name)
                        for name in ActivityPlannedReadResponse.model_fields})
                    if (action.tenant_id!=actor.tenant_id or action.lead_id not in visible_ids
                            or action.lead_id in actions or action.id in action_ids):
                        raise ConstructionLeadListReadOperationError('Lead next-action batch context is invalid.')
                    actions[action.lead_id] = action
                    action_ids.add(action.id)
                context = await self._context(actor)
                for model in models:
                    ConstructionLeadReadService._project_access(actor,context,model,actions.get(model.id))
                items = tuple(model.model_copy(update={'next_action':actions.get(model.id)}).model_dump(mode='json')
                    for model in visible)
                cursor = None
                if len(models)>limit:
                    last = visible[-1]
                    cursor = ConstructionLeadListReadCursor(actor.tenant_id,actor.user_id,filters,last.updated_at,last.id)
                return ConstructionLeadListReadPage(items,cursor)
        except (ConstructionLeadValidationError,ConstructionPermissionDeniedError,ConstructionScopeDeniedError,
                ConstructionLeadListReadOperationError):
            raise
        except ConstructionLeadReadNotFoundError:
            raise ConstructionLeadListReadOperationError('Lead list row is unavailable in this tenant.') from None
        except Exception:
            raise ConstructionLeadListReadOperationError('Lead list read failed.') from None


def build_construction_lead_list_read_service(*, session, now_provider=None):
    from database.repositories.construction import ConstructionAccessRepository
    from database.repositories.construction_lead_list_read import ConstructionLeadListReadRepository
    return ConstructionLeadListReadService(session=session,repository=ConstructionLeadListReadRepository(session),
        access_repository=ConstructionAccessRepository(session),now_provider=now_provider)
