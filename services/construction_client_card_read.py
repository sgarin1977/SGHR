from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Literal
from types import SimpleNamespace
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from services.construction_client_projects_read import (
    ClientProjectReadResponse, ConstructionClientProjectsReadCursor, ConstructionClientProjectsReadPage,
)
from services.construction_client_leads_read import (
    ClientLeadReadResponse, ConstructionClientLeadsReadCursor, ConstructionClientLeadsReadPage,
)
from services.construction_client_activities_read import (
    ConstructionClientActivitiesReadCursor, ConstructionClientActivitiesReadPage,
)
from services.construction_activity_read import ActivityPlannedReadResponse
from services.construction_client_history_read import (
    ClientHistoryReadResponse, ConstructionClientHistoryReadCursor, ConstructionClientHistoryReadPage,
)
from services.construction_client_history_contract import (
    construction_history_event_belongs_to_client, ConstructionHistoryClientOnlyActivityEvidence,
)
from services.construction_clients import (
    ConstructionClientNotFoundError, ConstructionClientValidationError, ConstructionClientOperationError,
)
from services.construction_grants import ConstructionAccessContextResolver
from services.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext, ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError, require_construction_access, require_construction_role_scope,
)


CLIENT_CARD_READ_PERMISSION = 'construction.projects.read'


class ConstructionClientCardReadOperationError(ConstructionClientOperationError):
    pass


class ClientCardClientResponse(BaseModel):
    """The same client projection as the existing client GET."""
    model_config = ConfigDict(extra='forbid')
    id: UUID
    tenant_id: UUID
    display_name: StrictStr
    client_type: Literal['person', 'company']
    phone: StrictStr | None
    email: StrictStr | None
    notes: StrictStr | None
    linked_user_id: UUID | None
    row_version: StrictInt = Field(ge=1, le=2**63-1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator('created_at', 'updated_at')
    @classmethod
    def utc_timestamp(cls, value):
        return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ConstructionClientCardReadResult:
    client: dict
    projects: ConstructionClientProjectsReadPage
    leads: ConstructionClientLeadsReadPage
    activities: ConstructionClientActivitiesReadPage
    history: ConstructionClientHistoryReadPage


_SECTIONS = {
    'projects': (ClientProjectReadResponse, ConstructionClientProjectsReadPage,
        ConstructionClientProjectsReadCursor, 'updated_at', 'project_id', 'list_projects_for_client'),
    'leads': (ClientLeadReadResponse, ConstructionClientLeadsReadPage,
        ConstructionClientLeadsReadCursor, 'updated_at', 'lead_id', 'list_leads_for_client'),
    'activities': (ActivityPlannedReadResponse, ConstructionClientActivitiesReadPage,
        ConstructionClientActivitiesReadCursor, 'due_at', 'activity_id', 'list_planned_activities_for_client'),
    'history': (ClientHistoryReadResponse, ConstructionClientHistoryReadPage,
        ConstructionClientHistoryReadCursor, 'created_at', 'event_id', 'list_history_for_client'),
}


class ConstructionClientCardReadService:
    """Compose the existing read services in one caller-owned transaction."""

    def __init__(self, *, session, client_service, access_repository, projects_service,
        leads_service, activities_service, history_service, now_provider=None):
        sections = dict(projects=projects_service, leads=leads_service,
            activities=activities_service, history=history_service)
        if (session is None or not callable(getattr(session, 'in_transaction', None))
            or not hasattr(session, 'no_autoflush')
            or getattr(getattr(client_service, 'repository', None), 'session', None) is not session
            or not callable(getattr(client_service, 'get_client', None))
            or getattr(access_repository, 'session', None) is not session
            or any(not callable(getattr(access_repository, name, None)) for name in
                ('has_active_platform_membership', 'list_active_grants'))
            or any(getattr(service, 'session', None) is not session
                or not callable(getattr(service, _SECTIONS[name][-1], None)) for name, service in sections.items())
            or (now_provider is not None and not callable(now_provider))):
            raise ValueError('Client card requires shared-session read services and access repository.')
        self.session, self.client_service, self.access_repository = session, client_service, access_repository
        self.sections = sections
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(repository=access_repository, now_provider=self._now)

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionClientCardReadOperationError('Client card clock must be aware.')
        return value.astimezone(timezone.utc)

    @staticmethod
    def _authorize(actor, context):
        if (getattr(actor, 'status', None) != 'active'
            or not isinstance(getattr(actor, 'user_id', None), UUID)
            or not isinstance(getattr(actor, 'tenant_id', None), UUID)
            or not isinstance(context, ConstructionAccessContext)
            or context.user_id != actor.user_id or context.tenant_id != actor.tenant_id):
            raise ConstructionPermissionDeniedError('Client card access is not available.')
        for grant in context.grants:
            if not isinstance(grant, ConstructionGrantContext) or not isinstance(grant.scope_id, UUID):
                raise ConstructionScopeDeniedError('Client card scope is invalid.')
            require_construction_role_scope(role=grant.role, scope_type=grant.scope_type)
        require_construction_access(actor=actor, access_context=context, permission_code=CLIENT_CARD_READ_PERMISSION)

    async def _context(self, actor):
        if await self.access_repository.has_active_platform_membership(
            tenant_id=actor.tenant_id, user_id=actor.user_id) is not True:
            raise ConstructionPermissionDeniedError('Client card membership is not available.')
        context = await self.resolver.resolve(actor=actor)
        self._authorize(actor, context)
        return context

    @staticmethod
    def _request(name, limit, after, *, actor, client_id):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ConstructionClientValidationError('Client card list limits must be between 1 and 100.')
        if after is not None:
            if not isinstance(after, _SECTIONS[name][2]):
                raise ConstructionClientValidationError('Client card cursor type is invalid.')
            try:
                after = replace(after)
            except (ValueError, TypeError, AttributeError):
                raise ConstructionClientValidationError('Client card cursor is invalid.') from None
            if after.tenant_id != actor.tenant_id or after.user_id != actor.user_id or after.client_id != client_id:
                raise ConstructionClientValidationError('Client card cursor does not match actor/client/tenant.')
        return limit, after

    async def _client(self, *, actor, context, client_id):
        row = await self.client_service.get_client(actor=actor, access_context=context, client_id=client_id)
        if (getattr(row, 'id', None) != client_id or getattr(row, 'tenant_id', None) != actor.tenant_id
            or getattr(row, 'deleted_at', 'missing') is not None):
            raise ConstructionClientNotFoundError('Client card is not available in the authorized tenant.')
        return ClientCardClientResponse.model_validate({field: getattr(row, field)
            for field in ClientCardClientResponse.model_fields}).model_dump(mode='json')

    @staticmethod
    def _page(name, page, *, actor, client_id, limit, after):
        schema, page_type, cursor_type, time_field, id_field, _ = _SECTIONS[name]
        if not isinstance(page, page_type) or not isinstance(page.items, tuple) or len(page.items) > limit:
            raise ConstructionClientCardReadOperationError('Client card section page is invalid.')
        evidence = {}
        if name == 'history':
            if not isinstance(page.legacy_activity_evidence, tuple):
                raise ConstructionClientCardReadOperationError('Client card history evidence is invalid.')
            for entry in page.legacy_activity_evidence:
                if (not isinstance(entry, tuple) or len(entry) != 2 or not isinstance(entry[0], UUID)
                    or entry[0] in evidence or not isinstance(entry[1], ConstructionHistoryClientOnlyActivityEvidence)):
                    raise ConstructionClientCardReadOperationError('Client card history evidence is invalid.')
                evidence[entry[0]] = entry[1]
        models = []
        previous = (getattr(after, time_field), getattr(after, id_field)) if after is not None else None
        seen = set()
        for item in page.items:
            if not isinstance(item, dict):
                raise ConstructionClientCardReadOperationError('Client card section item is invalid.')
            model = schema.model_validate(item)
            if model.tenant_id != actor.tenant_id:
                raise ConstructionClientCardReadOperationError('Client card section tenant is invalid.')
            if name == 'projects' and model.client_id != client_id or name == 'leads' and model.converted_client_id != client_id:
                raise ConstructionClientCardReadOperationError('Client card section client is invalid.')
            if name == 'history' and not construction_history_event_belongs_to_client(
                SimpleNamespace(**model.model_dump(), activity_evidence=evidence.get(model.id)),
                tenant_id=actor.tenant_id, client_id=client_id):
                raise ConstructionClientCardReadOperationError('Client card history attribution is invalid.')
            if model.id in evidence and (model.entity_type != 'construction_activity' or 'related_client_id' in model.payload):
                raise ConstructionClientCardReadOperationError('Unexpected client card legacy history evidence.')
            key = (getattr(model, time_field), model.id)
            if model.id in seen or previous is not None and (key <= previous if name == 'activities' else key >= previous):
                raise ConstructionClientCardReadOperationError('Client card section ordering is invalid.')
            seen.add(model.id)
            previous = key
            models.append(model)
        if set(evidence) - seen:
            raise ConstructionClientCardReadOperationError('Unused client card history evidence.')
        cursor = page.next_cursor
        if cursor is not None:
            if not isinstance(cursor, cursor_type) or len(models) != limit:
                raise ConstructionClientCardReadOperationError('Client card section cursor is invalid.')
            cursor = replace(cursor)
            if (cursor.tenant_id != actor.tenant_id or cursor.user_id != actor.user_id or cursor.client_id != client_id
                or (getattr(cursor, time_field), getattr(cursor, id_field)) != previous):
                raise ConstructionClientCardReadOperationError('Client card section cursor context is invalid.')
        items = tuple(model.model_dump(mode='json') for model in models)
        if name == 'history':
            return page_type(items, cursor, tuple(evidence.items()))
        return page_type(items, cursor)

    @staticmethod
    def _scope(name, page, *, actor, context):
        for item in page.items:
            project = item['id'] if name == 'projects' else item['converted_project_id'] if name == 'leads' else (
                item['project_id'] if name == 'activities' else item['entity_id'] if item['entity_type'] == 'construction_project' else None)
            if project is not None:
                require_construction_access(actor=actor, access_context=context,
                    permission_code=CLIENT_CARD_READ_PERMISSION, project_id=UUID(project))

    async def get_client_card(self, *, actor, access_context, client_id,
        projects_limit=20, projects_after=None, leads_limit=20, leads_after=None,
        activities_limit=20, activities_after=None, history_limit=20, history_after=None):
        try:
            if not isinstance(client_id, UUID):
                raise ConstructionClientValidationError('Client card requires a client UUID.')
            self._authorize(actor, access_context)
            requests = {name: self._request(name, limit, after, actor=actor, client_id=client_id)
                for name, limit, after in (('projects', projects_limit, projects_after), ('leads', leads_limit, leads_after),
                    ('activities', activities_limit, activities_after), ('history', history_limit, history_after))}
            if self.session.in_transaction() is not True:
                raise ConstructionClientCardReadOperationError('Client card requires an active caller transaction.')
            with self.session.no_autoflush:
                context = await self._context(actor)
                client = await self._client(actor=actor, context=context, client_id=client_id)
                pages = {}
                # AsyncSession is shared: read sections sequentially, never gather.
                for name, service in self.sections.items():
                    context = await self._context(actor)
                    limit, after = requests[name]
                    page = await getattr(service, _SECTIONS[name][-1])(
                        actor=actor, access_context=context, client_id=client_id, limit=limit, after=after)
                    pages[name] = self._page(name, page, actor=actor, client_id=client_id, limit=limit, after=after)
                # Client availability and permissions may have changed during any
                # section read. No awaited operation follows the final access check.
                await self._client(actor=actor, context=context, client_id=client_id)
                current = await self._context(actor)
                for name, page in pages.items():
                    self._scope(name, page, actor=actor, context=current)
                return ConstructionClientCardReadResult(client, **pages)
        except (ConstructionClientValidationError, ConstructionClientNotFoundError,
            ConstructionClientCardReadOperationError, ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
            raise
        except Exception:
            raise ConstructionClientCardReadOperationError('Client card read failed.') from None


def build_construction_client_card_read_service(*, session, now_provider=None):
    from database.repositories.construction import ConstructionAccessRepository
    from services.construction_client_read import build_construction_client_read_service
    from services.construction_client_projects_read import build_construction_client_projects_read_service
    from services.construction_client_leads_read import build_construction_client_leads_read_service
    from services.construction_client_activities_read import build_construction_client_activities_read_service
    from services.construction_client_history_read import build_construction_client_history_read_service
    return ConstructionClientCardReadService(session=session,
        client_service=build_construction_client_read_service(session), access_repository=ConstructionAccessRepository(session),
        projects_service=build_construction_client_projects_read_service(session=session, now_provider=now_provider),
        leads_service=build_construction_client_leads_read_service(session=session, now_provider=now_provider),
        activities_service=build_construction_client_activities_read_service(session=session, now_provider=now_provider),
        history_service=build_construction_client_history_read_service(session=session, now_provider=now_provider), now_provider=now_provider)
