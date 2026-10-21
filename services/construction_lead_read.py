"""Read a CRM lead and its computed next action using existing access policy.

No next_action column is stored. Caller owns the shared read transaction and
existing Platform authentication. This increment registers no HTTP routes.
"""
from datetime import datetime, timezone
from uuid import UUID

from services.construction_activity_read import (
    ActivityPlannedReadResponse, ConstructionActivityReadNotFoundError,
    build_construction_activity_read_service,
)
from services.construction_client_leads_read import ClientLeadReadResponse
from services.construction_grants import ConstructionAccessContextResolver
from services.construction_lead_rules import ConstructionLeadValidationError
from services.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext, ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError, require_construction_access, require_construction_role_scope,
)

LEAD_READ_PERMISSION = 'construction.projects.read'


class ConstructionLeadReadNotFoundError(Exception):
    pass


class ConstructionLeadReadOperationError(Exception):
    pass


class ConstructionLeadReadResponse(ClientLeadReadResponse):
    # Unlike a client's converted-lead list, ordinary CRM reads also include
    # unconverted leads. The remaining existing lead projection stays identical.
    converted_client_id: UUID | None
    next_action: ActivityPlannedReadResponse | None


PUBLIC_LEAD_DETAIL_FIELDS = frozenset(ConstructionLeadReadResponse.model_fields)
STORED_LEAD_READ_FIELDS = PUBLIC_LEAD_DETAIL_FIELDS - {'next_action'}


class ConstructionLeadReadService:
    def __init__(self, *, session, repository, activity_service, access_repository, now_provider=None):
        if (session is None or not callable(getattr(session, 'in_transaction', None))
                or not hasattr(session, 'no_autoflush')
                or getattr(repository, 'session', None) is not session
                or not callable(getattr(repository, 'get_active_lead', None))
                or getattr(activity_service, 'session', None) is not session
                or not callable(getattr(activity_service, 'get_next_action_for_lead', None))
                or getattr(access_repository, 'session', None) is not session
                or any(not callable(getattr(access_repository, name, None))
                    for name in ('has_active_platform_membership', 'list_active_grants'))
                or (now_provider is not None and not callable(now_provider))):
            raise ValueError('Lead read requires shared-session repositories and activity reads.')
        self.session, self.repository = session, repository
        self.activities, self.access = activity_service, access_repository
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(repository=access_repository, now_provider=self._now)

    def _now(self):
        instant = self.now_provider()
        if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
            raise ConstructionLeadReadOperationError('Lead read clock must be aware.')
        return instant.astimezone(timezone.utc)

    @staticmethod
    def _authorize(actor, context):
        if (getattr(actor, 'status', None) != 'active'
                or not isinstance(getattr(actor, 'user_id', None), UUID)
                or not isinstance(getattr(actor, 'tenant_id', None), UUID)
                or not isinstance(context, ConstructionAccessContext)):
            raise ConstructionPermissionDeniedError('Lead read access is not available.')
        for grant in context.grants:
            if not isinstance(grant, ConstructionGrantContext) or not isinstance(grant.scope_id, UUID):
                raise ConstructionScopeDeniedError('Lead read scope is invalid.')
            require_construction_role_scope(role=grant.role, scope_type=grant.scope_type)
        # Apply the existing tenant CRM read boundary; assignment/identity-link
        # is not an alternative source of access to an unconverted lead.
        require_construction_access(actor=actor, access_context=context, permission_code=LEAD_READ_PERMISSION)

    async def _context(self, actor):
        if await self.access.has_active_platform_membership(user_id=actor.user_id, tenant_id=actor.tenant_id) is not True:
            raise ConstructionPermissionDeniedError('Lead read membership is not available.')
        context = await self.resolver.resolve(actor=actor)
        self._authorize(actor, context)
        return context

    @staticmethod
    def _lead(row, *, actor, lead_id):
        if (row is None or getattr(row, 'id', None) != lead_id
                or getattr(row, 'tenant_id', None) != actor.tenant_id
                or getattr(row, 'deleted_at', 'missing') is not None):
            raise ConstructionLeadReadNotFoundError('Lead is not available.')
        return ConstructionLeadReadResponse.model_validate({
            **{name:getattr(row, name) for name in STORED_LEAD_READ_FIELDS}, 'next_action':None,
        })

    @staticmethod
    def _project_access(actor, context, lead, next_action=None):
        project_ids = [lead.converted_project_id]
        if next_action is not None:
            project_ids.append(next_action.project_id)
        for project_id in project_ids:
            if project_id is not None:
                require_construction_access(actor=actor, access_context=context,
                    permission_code=LEAD_READ_PERMISSION, project_id=project_id)

    async def get_lead(self, *, actor, access_context, lead_id):
        try:
            if not isinstance(lead_id, UUID):
                raise ConstructionLeadValidationError('Lead read requires a UUID.')
            self._authorize(actor, access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionLeadReadOperationError('Lead read requires an active caller transaction.')
            with self.session.no_autoflush:
                context = await self._context(actor)
                lead = self._lead(await self.repository.get_active_lead(
                    tenant_id=actor.tenant_id, lead_id=lead_id), actor=actor, lead_id=lead_id)
                self._project_access(actor, context, lead)
                context = await self._context(actor)
                action = await self.activities.get_next_action_for_lead(
                    actor=actor, access_context=context, lead_id=lead_id)
                if action is not None:
                    if not isinstance(action, dict):
                        raise ConstructionLeadReadOperationError('Lead next action result is invalid.')
                    action = ActivityPlannedReadResponse.model_validate(action)
                    if action.tenant_id != actor.tenant_id or action.lead_id != lead_id:
                        raise ConstructionLeadReadOperationError('Lead next action context is invalid.')
                current = self._lead(await self.repository.get_active_lead(
                    tenant_id=actor.tenant_id, lead_id=lead_id), actor=actor, lead_id=lead_id)
                if current != lead:
                    raise ConstructionLeadReadOperationError('Lead changed during the read; retry the read transaction.')
                context = await self._context(actor)
                self._project_access(actor, context, lead, action)
                return lead.model_copy(update={'next_action':action}).model_dump(mode='json')
        except (ConstructionLeadValidationError, ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionLeadReadNotFoundError, ConstructionLeadReadOperationError):
            raise
        except ConstructionActivityReadNotFoundError:
            raise ConstructionLeadReadNotFoundError('Lead is not available.') from None
        except Exception:
            raise ConstructionLeadReadOperationError('Lead read failed.') from None


def build_construction_lead_read_service(*, session, now_provider=None):
    from database.repositories.construction import ConstructionAccessRepository
    from database.repositories.construction_leads import ConstructionLeadRepository
    return ConstructionLeadReadService(session=session, repository=ConstructionLeadRepository(session),
        activity_service=build_construction_activity_read_service(session=session, now_provider=now_provider),
        access_repository=ConstructionAccessRepository(session), now_provider=now_provider)
