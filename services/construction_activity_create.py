from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from services.construction_audit_client_correlation import construction_audit_client_details

from services.construction_activity_rules import (
    ConstructionActivityLinks, ConstructionActivitySchedule, ConstructionActivityValidationError,
    normalize_construction_activity_type,
)
from services.construction_audit import ConstructionAuditLogger
from services.construction_grants import ConstructionAccessContextResolver
from services.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext, ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError, require_construction_access, require_construction_role_scope,
)


ACTIVITY_CREATE_PERMISSION = "construction.projects.create"


class ConstructionActivityCreateNotFoundError(Exception):
    pass


class ConstructionActivityCreateOperationError(Exception):
    pass


@dataclass(frozen=True)
class ConstructionActivityCreateCommand:
    activity_type: str
    links: ConstructionActivityLinks
    schedule: ConstructionActivitySchedule
    responsible_user_id: UUID | None = None
    comment: str | None = None

    def __post_init__(self):
        kind = normalize_construction_activity_type(self.activity_type)
        if not isinstance(self.links, ConstructionActivityLinks) or not isinstance(self.schedule, ConstructionActivitySchedule):
            raise ConstructionActivityValidationError("Activity requires validated links and schedule.")
        links = ConstructionActivityLinks(self.links.lead_id, self.links.client_id, self.links.project_id)
        schedule = ConstructionActivitySchedule(self.schedule.due_at, self.schedule.timezone)
        if self.responsible_user_id is not None and not isinstance(self.responsible_user_id, UUID):
            raise ConstructionActivityValidationError("Activity responsible user must be a UUID.")
        if self.comment is not None and not isinstance(self.comment, str):
            raise ConstructionActivityValidationError("Activity comment must be text.")
        object.__setattr__(self, "activity_type", kind)
        object.__setattr__(self, "links", links)
        object.__setattr__(self, "schedule", schedule)
        object.__setattr__(self, "comment", self.comment.strip() or None if self.comment is not None else None)


class ConstructionActivityCreateService:
    """Caller owns commit/rollback of activity and mandatory audit together."""

    def __init__(self, *, session, repository, lead_repository, client_repository,
                 project_repository, access_repository, event_repository, now_provider=None):
        protocols = (
            (repository, ("create_activity",)),
            (lead_repository, ("get_active_lead_for_update",)),
            (client_repository, ("get_active_client_for_update",)),
            (project_repository, ("get_active_project_for_update",)),
            (access_repository, ("has_active_platform_membership", "list_active_grants")),
            (event_repository, ("create_event",)),
        )
        if (
            session is None or not callable(getattr(session, "in_transaction", None))
            or not hasattr(session, "no_autoflush")
            or any(getattr(repo, "session", None) is not session
                   or any(not callable(getattr(repo, name, None)) for name in names)
                   for repo, names in protocols)
            or (now_provider is not None and not callable(now_provider))
        ):
            raise ValueError("Activity creation requires shared-session repository wiring.")
        self.session, self.repository = session, repository
        self.lead_repository, self.client_repository = lead_repository, client_repository
        self.project_repository, self.access_repository = project_repository, access_repository
        self.event_repository = event_repository
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(repository=access_repository, now_provider=self._now)
        self.audit_logger = ConstructionAuditLogger(event_repository=event_repository)

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionActivityCreateOperationError("Activity creation clock must be aware.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _identity(actor, context):
        if (
            getattr(actor, "status", None) != "active"
            or not isinstance(getattr(actor, "tenant_id", None), UUID)
            or not isinstance(getattr(actor, "user_id", None), UUID)
            or not isinstance(context, ConstructionAccessContext)
            or context.tenant_id != actor.tenant_id or context.user_id != actor.user_id
        ):
            raise ConstructionPermissionDeniedError("Activity creation access is not available.")

    @classmethod
    def _authorize(cls, actor, context, links):
        cls._identity(actor, context)
        for grant in context.grants:
            if not isinstance(grant, ConstructionGrantContext) or not isinstance(grant.scope_id, UUID):
                raise ConstructionScopeDeniedError("Activity creation scope is invalid.")
            require_construction_role_scope(role=grant.role, scope_type=grant.scope_type)
        # A project grant cannot authorize unrelated tenant-level CRM resources.
        if links.lead_id is not None or links.client_id is not None:
            require_construction_access(actor=actor, access_context=context, permission_code=ACTIVITY_CREATE_PERMISSION)
        if links.project_id is not None:
            require_construction_access(actor=actor, access_context=context,
                permission_code=ACTIVITY_CREATE_PERMISSION, project_id=links.project_id)

    async def _context(self, actor):
        if await self.access_repository.has_active_platform_membership(
            tenant_id=actor.tenant_id, user_id=actor.user_id,
        ) is not True:
            raise ConstructionPermissionDeniedError("Activity creation membership is not available.")
        return await self.resolver.resolve(actor=actor)

    async def _responsible(self, actor, command):
        if command.responsible_user_id is not None and await self.access_repository.has_active_platform_membership(
            tenant_id=actor.tenant_id, user_id=command.responsible_user_id,
        ) is not True:
            raise ConstructionActivityValidationError("Activity responsible user is not available.")

    @staticmethod
    def _resource(value, *, tenant_id, resource_id):
        if (
            value is None or getattr(value, "id", None) != resource_id
            or getattr(value, "tenant_id", None) != tenant_id
            or getattr(value, "deleted_at", "missing") is not None
        ):
            raise ConstructionActivityCreateNotFoundError("Activity linked resource is not available.")
        return value

    @staticmethod
    def _check_created(activity, *, actor, command, instant):
        fields = dict(tenant_id=actor.tenant_id, type=command.activity_type, status="planned",
            lead_id=command.links.lead_id, client_id=command.links.client_id, project_id=command.links.project_id,
            due_at=command.schedule.due_at, timezone=command.schedule.timezone,
            responsible_user_id=command.responsible_user_id, comment=command.comment,
            result=None, completed_at=None, cancelled_at=None, created_by_user_id=actor.user_id,
            created_at=instant, updated_at=instant, deleted_at=None)
        if (
            not isinstance(getattr(activity, "id", None), UUID)
            or type(getattr(activity, "row_version", None)) is not int or activity.row_version != 1
            or any(getattr(activity, field, object()) != value for field, value in fields.items())
        ):
            raise ConstructionActivityCreateOperationError("Created activity state is invalid.")

    async def create_activity(self, *, actor, access_context, command, trace_id=None):
        try:
            self._identity(actor, access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionActivityCreateOperationError("Activity creation requires an active caller transaction.")
            if not isinstance(command, ConstructionActivityCreateCommand):
                raise ConstructionActivityValidationError("Activity creation requires a validated command.")
            command = ConstructionActivityCreateCommand(
                activity_type=command.activity_type, links=command.links, schedule=command.schedule,
                responsible_user_id=command.responsible_user_id, comment=command.comment,
            )
            self._authorize(actor, access_context, command.links)
            with self.session.no_autoflush:
                self._authorize(actor, await self._context(actor), command.links)
                await self._responsible(actor, command)
                # Match conversion lock order: lead, then client, then project.
                correlation_parents = {}
                for repo, method, field in (
                    (self.lead_repository, "get_active_lead_for_update", "lead_id"),
                    (self.client_repository, "get_active_client_for_update", "client_id"),
                    (self.project_repository, "get_active_project_for_update", "project_id"),
                ):
                    resource_id = getattr(command.links, field)
                    if resource_id is not None:
                        correlation_parents[field] = self._resource(await getattr(repo, method)(tenant_id=actor.tenant_id, **{field: resource_id}),
                                       tenant_id=actor.tenant_id, resource_id=resource_id)
                await self._responsible(actor, command)
                self._authorize(actor, await self._context(actor), command.links)
                instant = self._now()
                activity = await self.repository.create_activity(
                    tenant_id=actor.tenant_id, activity_type=command.activity_type,
                    links=command.links, schedule=command.schedule, created_by_user_id=actor.user_id,
                    responsible_user_id=command.responsible_user_id, comment=command.comment, as_of=instant,
                )
                self._check_created(activity, actor=actor, command=command, instant=instant)
                await self.audit_logger.record_operation(
                    actor=actor, event_type="construction_activity_created", entity_type="construction_activity",
                    entity_id=activity.id, operation="create", trace_id=trace_id,
                    details=construction_audit_client_details(
                        tenant_id=actor.tenant_id, entity_type="construction_activity", entity=activity,
                        lead=correlation_parents.get("lead_id"), client=correlation_parents.get("client_id"),
                        project=correlation_parents.get("project_id")),
                )
                return activity
        except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionActivityValidationError, ConstructionActivityCreateNotFoundError,
                ConstructionActivityCreateOperationError):
            raise
        except Exception:
            raise ConstructionActivityCreateOperationError("Activity creation failed.") from None


def build_construction_activity_create_service(*, session, now_provider=None):
    from database.repositories.construction import (
        ConstructionAccessRepository, ConstructionClientRepository, ConstructionProjectRepository,
    )
    from database.repositories.construction_activities import ConstructionActivityRepository
    from database.repositories.construction_leads import ConstructionLeadRepository
    from database.repositories.event import EventRepository
    return ConstructionActivityCreateService(session=session, repository=ConstructionActivityRepository(session),
        lead_repository=ConstructionLeadRepository(session), client_repository=ConstructionClientRepository(session),
        project_repository=ConstructionProjectRepository(session), access_repository=ConstructionAccessRepository(session),
        event_repository=EventRepository(session), now_provider=now_provider)
