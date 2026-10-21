from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_validator

from database.repositories.construction_activities import ConstructionActivityCursor
from services.construction_activity_rules import ConstructionActivityLinks, ConstructionActivityValidationError, normalize_construction_activity_schedule
from services.construction_grants import ConstructionAccessContextResolver
from services.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext, ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError, require_construction_access, require_construction_role_scope,
)


ACTIVITY_READ_PERMISSION="construction.projects.read"


class ConstructionActivityReadNotFoundError(Exception):
    pass


class ConstructionActivityReadOperationError(Exception):
    pass


class ActivityPlannedReadResponse(BaseModel):
    model_config=ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    type: Literal["call", "message", "meeting", "site_visit", "follow_up", "task", "other"]
    status: Literal["planned"]
    lead_id: UUID | None
    client_id: UUID | None
    project_id: UUID | None
    due_at: AwareDatetime
    timezone: StrictStr = Field(min_length=1)
    responsible_user_id: UUID | None
    comment: StrictStr | None
    result: StrictStr | None
    completed_at: None
    cancelled_at: None
    created_by_user_id: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
    row_version: StrictInt = Field(ge=1, le=2**63-1)

    @field_validator("due_at", "created_at", "updated_at")
    @classmethod
    def utc_timestamp(cls, value):
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def domain_values(self):
        ConstructionActivityLinks(self.lead_id, self.client_id, self.project_id)
        schedule=normalize_construction_activity_schedule(due_at=self.due_at, timezone_code=self.timezone)
        if self.timezone != schedule.timezone:
            raise ValueError("Stored activity timezone is not canonical.")
        return self


PUBLIC_ACTIVITY_PLANNED_FIELDS=frozenset(ActivityPlannedReadResponse.model_fields)


@dataclass(frozen=True)
class ConstructionActivityReadCursor:
    tenant_id: UUID
    user_id: UUID
    filters: tuple
    due_at: datetime
    activity_id: UUID

    def __post_init__(self):
        if (
            not isinstance(self.tenant_id, UUID) or not isinstance(self.user_id, UUID)
            or not isinstance(self.filters, tuple) or len(self.filters)!=4
            or any(value is not None and not isinstance(value, UUID) for value in self.filters)
        ):
            raise ConstructionActivityValidationError("Activity cursor requires trusted context and UUID filters.")
        try:
            checked=ConstructionActivityCursor(self.tenant_id, self.due_at, self.activity_id)
        except ValueError:
            raise ConstructionActivityValidationError("Activity cursor position is invalid.") from None
        object.__setattr__(self, "due_at", checked.due_at)


@dataclass(frozen=True)
class ConstructionActivityReadPage:
    items: tuple
    next_cursor: ConstructionActivityReadCursor | None


class ConstructionActivityReadService:
    """Materialize authorized planned activities; caller owns the read transaction."""

    def __init__(self, *, session, repository, lead_repository, client_repository,
                 project_repository, access_repository, now_provider=None):
        protocols=(
            (repository, ("list_visible_planned_activities", "get_next_action_for_lead")),
            (lead_repository, ("get_active_lead",)), (client_repository, ("get_active_client",)),
            (project_repository, ("get_active_project",)),
            (access_repository, ("has_active_platform_membership", "list_active_grants")),
        )
        if (
            session is None or not callable(getattr(session, "in_transaction", None)) or not hasattr(session, "no_autoflush")
            or any(getattr(repo, "session", None) is not session
                or any(not callable(getattr(repo, name, None)) for name in names) for repo, names in protocols)
            or (now_provider is not None and not callable(now_provider))
        ):
            raise ValueError("Activity read service requires shared-session repositories.")
        self.session,self.repository=session,repository
        self.lead_repository,self.client_repository,self.project_repository=lead_repository,client_repository,project_repository
        self.access_repository=access_repository
        self.now_provider=now_provider or (lambda: datetime.now(timezone.utc))
        self.resolver=ConstructionAccessContextResolver(repository=access_repository, now_provider=self._now)

    def _now(self):
        value=self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionActivityReadOperationError("Activity read clock must be aware.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _scope(actor, context):
        if (
            getattr(actor, "status", None)!="active" or not isinstance(getattr(actor, "user_id", None), UUID)
            or not isinstance(getattr(actor, "tenant_id", None), UUID) or not isinstance(context, ConstructionAccessContext)
            or context.user_id!=actor.user_id or context.tenant_id!=actor.tenant_id
        ):
            raise ConstructionPermissionDeniedError("Activity read access is not available.")
        for grant in context.grants:
            if not isinstance(grant, ConstructionGrantContext) or not isinstance(grant.scope_id, UUID):
                raise ConstructionScopeDeniedError("Activity read scope is invalid.")
            require_construction_role_scope(role=grant.role, scope_type=grant.scope_type)
        try:
            require_construction_access(actor=actor, access_context=context, permission_code=ACTIVITY_READ_PERMISSION)
            return None
        except ConstructionScopeDeniedError:
            pass
        projects=set()
        for grant in context.grants:
            if grant.scope_type=="project":
                try:
                    require_construction_access(actor=actor, access_context=context,
                        permission_code=ACTIVITY_READ_PERMISSION, project_id=grant.scope_id)
                    projects.add(grant.scope_id)
                except ConstructionScopeDeniedError:
                    pass
        if not projects:
            raise ConstructionScopeDeniedError("Activity read scope is not available.")
        return tuple(sorted(projects, key=str))

    @classmethod
    def _authorize(cls, actor, context, *, lead_id=None, client_id=None, project_id=None):
        cls._scope(actor, context)
        if lead_id is not None or client_id is not None:
            require_construction_access(actor=actor, access_context=context, permission_code=ACTIVITY_READ_PERMISSION)
        if project_id is not None:
            require_construction_access(actor=actor, access_context=context, permission_code=ACTIVITY_READ_PERMISSION, project_id=project_id)

    async def _context(self, actor):
        if await self.access_repository.has_active_platform_membership(tenant_id=actor.tenant_id, user_id=actor.user_id) is not True:
            raise ConstructionPermissionDeniedError("Activity read membership is not available.")
        return await self.resolver.resolve(actor=actor)

    async def _parents(self, actor, filters):
        for repo, method, field, resource_id in (
            (self.lead_repository, "get_active_lead", "lead_id", filters[0]),
            (self.client_repository, "get_active_client", "client_id", filters[1]),
            (self.project_repository, "get_active_project", "project_id", filters[2]),
        ):
            if resource_id is not None:
                row=await getattr(repo, method)(tenant_id=actor.tenant_id, **{field:resource_id})
                if row is None or getattr(row, "id", None)!=resource_id or getattr(row, "tenant_id", None)!=actor.tenant_id or getattr(row, "deleted_at", "missing") is not None:
                    raise ConstructionActivityReadNotFoundError("Activity parent is not available.")

    def _item(self, row, *, actor, context, filters):
        if row is None or getattr(row, "tenant_id", None)!=actor.tenant_id or getattr(row, "deleted_at", "missing") is not None:
            raise ConstructionActivityReadOperationError("Activity read result context is invalid.")
        model=ActivityPlannedReadResponse.model_validate({field:getattr(row, field) for field in PUBLIC_ACTIVITY_PLANNED_FIELDS})
        for field, value in zip(("lead_id", "client_id", "project_id", "responsible_user_id"), filters):
            if value is not None and getattr(model, field)!=value:
                raise ConstructionActivityReadOperationError("Activity read result does not match its filters.")
        self._authorize(actor, context, lead_id=model.lead_id, client_id=model.client_id, project_id=model.project_id)
        return model

    @staticmethod
    def _inputs(filters, limit):
        if any(value is not None and not isinstance(value, UUID) for value in filters):
            raise ConstructionActivityValidationError("Activity filters require UUID references.")
        if type(limit) is not int or not 1<=limit<=100:
            raise ConstructionActivityValidationError("Activity page limit must be between 1 and 100.")

    async def _finish(self, actor, filters, initial_scope, models):
        await self._parents(actor, filters)
        context=await self._context(actor)
        final_scope=self._scope(actor, context)
        if final_scope is not None and (initial_scope is None or not set(initial_scope)<=set(final_scope)):
            raise ConstructionScopeDeniedError("Activity read scope changed during the query.")
        self._authorize(actor, context, lead_id=filters[0], client_id=filters[1], project_id=filters[2])
        for model in models:
            self._authorize(actor, context, lead_id=model.lead_id, client_id=model.client_id, project_id=model.project_id)
        return context

    async def list_planned_activities(self, *, actor, access_context, lead_id=None, client_id=None,
                                      project_id=None, responsible_user_id=None, limit=20, after=None):
        try:
            filters=(lead_id,client_id,project_id,responsible_user_id)
            self._inputs(filters, limit)
            self._authorize(actor, access_context, lead_id=lead_id, client_id=client_id, project_id=project_id)
            if self.session.in_transaction() is not True:
                raise ConstructionActivityReadOperationError("Activity reads require an active caller transaction.")
            position=None
            if after is not None:
                if not isinstance(after, ConstructionActivityReadCursor):
                    raise ConstructionActivityValidationError("Activity read cursor is invalid.")
                after=ConstructionActivityReadCursor(after.tenant_id, after.user_id, after.filters, after.due_at, after.activity_id)
                if after.tenant_id!=actor.tenant_id or after.user_id!=actor.user_id or after.filters!=filters:
                    raise ConstructionActivityValidationError("Activity cursor does not match the current query.")
                position=ConstructionActivityCursor(after.tenant_id, after.due_at, after.activity_id)
            with self.session.no_autoflush:
                context=await self._context(actor)
                self._authorize(actor, context, lead_id=lead_id, client_id=client_id, project_id=project_id)
                scope=self._scope(actor, context)
                await self._parents(actor, filters)
                rows=await self.repository.list_visible_planned_activities(tenant_id=actor.tenant_id,
                    allowed_project_ids=scope, lead_id=lead_id, client_id=client_id, project_id=project_id,
                    responsible_user_id=responsible_user_id, limit=limit, after=position)
                if not isinstance(rows, list) or len(rows)>limit+1:
                    raise ConstructionActivityReadOperationError("Activity page result is invalid.")
                models=[self._item(row, actor=actor, context=context, filters=filters) for row in rows]
                previous=(position.due_at,position.activity_id) if position is not None else None
                ids=set()
                for model in models:
                    key=(model.due_at,model.id)
                    if model.id in ids or (previous is not None and key<=previous):
                        raise ConstructionActivityReadOperationError("Activity page order is invalid.")
                    previous=key;ids.add(model.id)
                public=tuple(model.model_dump(mode="json") for model in models[:limit])
                next_cursor=None
                if len(models)>limit:
                    last=models[limit-1]
                    next_cursor=ConstructionActivityReadCursor(actor.tenant_id,actor.user_id,filters,last.due_at,last.id)
                await self._finish(actor, filters, scope, models)
                return ConstructionActivityReadPage(public,next_cursor)
        except (ConstructionActivityValidationError, ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionActivityReadNotFoundError, ConstructionActivityReadOperationError):
            raise
        except Exception:
            raise ConstructionActivityReadOperationError("Activity read operation failed.") from None

    async def get_next_action_for_lead(self, *, actor, access_context, lead_id):
        try:
            if not isinstance(lead_id, UUID):
                raise ConstructionActivityValidationError("Next action requires a lead UUID.")
            filters=(lead_id,None,None,None)
            self._authorize(actor,access_context,lead_id=lead_id)
            if self.session.in_transaction() is not True:
                raise ConstructionActivityReadOperationError("Activity reads require an active caller transaction.")
            with self.session.no_autoflush:
                context=await self._context(actor)
                self._authorize(actor,context,lead_id=lead_id)
                scope=self._scope(actor,context)
                await self._parents(actor,filters)
                row=await self.repository.get_next_action_for_lead(tenant_id=actor.tenant_id,lead_id=lead_id)
                models=[] if row is None else [self._item(row,actor=actor,context=context,filters=filters)]
                public=models[0].model_dump(mode="json") if models else None
                await self._finish(actor,filters,scope,models)
                return public
        except (ConstructionActivityValidationError, ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionActivityReadNotFoundError, ConstructionActivityReadOperationError):
            raise
        except Exception:
            raise ConstructionActivityReadOperationError("Activity next action read failed.") from None


def build_construction_activity_read_service(*, session, now_provider=None):
    from database.repositories.construction_activity_reads import ConstructionActivityReadRepository
    from database.repositories.construction_leads import ConstructionLeadRepository
    from database.repositories.construction import ConstructionClientRepository, ConstructionProjectRepository, ConstructionAccessRepository
    return ConstructionActivityReadService(session=session,repository=ConstructionActivityReadRepository(session),
        lead_repository=ConstructionLeadRepository(session),client_repository=ConstructionClientRepository(session),
        project_repository=ConstructionProjectRepository(session),access_repository=ConstructionAccessRepository(session),now_provider=now_provider)
