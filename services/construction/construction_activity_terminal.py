from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from services.construction.construction_audit_client_correlation import construction_audit_client_details

from services.construction.construction_activity_rules import CONSTRUCTION_ACTIVITY_STATUSES, ConstructionActivityLinks, ConstructionActivityValidationError, ConstructionActivityTransitionError, require_construction_activity_transition, ACTIVITY_RESULT_UNSET
from services.construction.construction_audit import ConstructionAuditChange, ConstructionAuditLogger
from services.construction.construction_foundation import ConstructionRowVersionConflictError, require_expected_row_version
from services.construction.construction_grants import ConstructionAccessContextResolver
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext, ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError, require_construction_access, require_construction_role_scope,
)

ACTIVITY_EDIT_PERMISSION = "construction.projects.edit"
ACTIVITY_TERMINAL_PRESERVED_FIELDS = (
    "type", "lead_id", "client_id", "project_id", "due_at", "timezone", "responsible_user_id",
    "comment", "created_by_user_id", "created_at",
)


class ConstructionActivityTerminalNotFoundError(Exception):
    pass


class ConstructionActivityTerminalOperationError(Exception):
    pass


@dataclass(frozen=True)
class ConstructionActivityTerminalCommand:
    expected_row_version: int
    result: str | None | object = ACTIVITY_RESULT_UNSET

    def __post_init__(self):
        if type(self.expected_row_version) is not int or not 0 < self.expected_row_version < 2**63:
            raise ConstructionActivityValidationError("Activity operation requires a positive BIGINT version.")
        if self.result is not ACTIVITY_RESULT_UNSET:
            if self.result is not None and not isinstance(self.result, str):
                raise ConstructionActivityValidationError("Activity result must be text.")
            object.__setattr__(self, "result", self.result.strip() or None if self.result is not None else None)

    @property
    def result_provided(self):
        return self.result is not ACTIVITY_RESULT_UNSET


class ConstructionActivityTerminalService:
    """Caller must commit or roll back terminal mutation and mandatory audit together."""

    def __init__(self, *, session, repository, lead_repository, client_repository,
                 project_repository, access_repository, event_repository, now_provider=None):
        methods = (
            (repository, ("find_active_activity_links", "get_active_activity_for_update_by_links", "complete_activity", "cancel_activity")),
            (lead_repository, ("get_active_lead_for_update",)),
            (client_repository, ("get_active_client_for_update",)),
            (project_repository, ("get_active_project_for_update",)),
            (access_repository, ("has_active_platform_membership", "list_active_grants")),
            (event_repository, ("create_event",)),
        )
        if (
            session is None or not callable(getattr(session, "in_transaction", None)) or not hasattr(session, "no_autoflush")
            or any(getattr(repo, "session", None) is not session
                   or any(not callable(getattr(repo, name, None)) for name in names) for repo, names in methods)
            or (now_provider is not None and not callable(now_provider))
        ):
            raise ValueError("Activity terminal service requires shared-session repository wiring.")
        self.session, self.repository = session, repository
        self.lead_repository, self.client_repository, self.project_repository = lead_repository, client_repository, project_repository
        self.access_repository, self.event_repository = access_repository, event_repository
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(repository=access_repository, now_provider=self._now)
        self.audit_logger = ConstructionAuditLogger(event_repository=event_repository)

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionActivityTerminalOperationError("Activity clock must be aware.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _scope(actor, context):
        if (
            getattr(actor, "status", None) != "active" or not isinstance(getattr(actor, "tenant_id", None), UUID)
            or not isinstance(getattr(actor, "user_id", None), UUID) or not isinstance(context, ConstructionAccessContext)
            or context.user_id != actor.user_id or context.tenant_id != actor.tenant_id
        ):
            raise ConstructionPermissionDeniedError("Activity edit access is not available.")
        for grant in context.grants:
            if not isinstance(grant, ConstructionGrantContext) or not isinstance(grant.scope_id, UUID):
                raise ConstructionScopeDeniedError("Activity edit scope is invalid.")
            require_construction_role_scope(role=grant.role, scope_type=grant.scope_type)
        # Probe the shared engine rather than granting permissions from role names.
        try:
            require_construction_access(actor=actor, access_context=context, permission_code=ACTIVITY_EDIT_PERMISSION)
            return None
        except ConstructionScopeDeniedError:
            pass
        allowed = set()
        for grant in context.grants:
            if grant.scope_type == "project":
                try:
                    require_construction_access(actor=actor, access_context=context,
                        permission_code=ACTIVITY_EDIT_PERMISSION, project_id=grant.scope_id)
                    allowed.add(grant.scope_id)
                except ConstructionScopeDeniedError:
                    pass
        if not allowed:
            raise ConstructionScopeDeniedError("Activity edit scope is not available.")
        return tuple(sorted(allowed, key=str))

    @classmethod
    def _authorize_links(cls, actor, context, links):
        cls._scope(actor, context)
        if links.lead_id is not None or links.client_id is not None:
            require_construction_access(actor=actor, access_context=context, permission_code=ACTIVITY_EDIT_PERMISSION)
        if links.project_id is not None:
            require_construction_access(actor=actor, access_context=context,
                permission_code=ACTIVITY_EDIT_PERMISSION, project_id=links.project_id)

    async def _context(self, actor):
        if await self.access_repository.has_active_platform_membership(tenant_id=actor.tenant_id, user_id=actor.user_id) is not True:
            raise ConstructionPermissionDeniedError("Activity edit membership is not available.")
        return await self.resolver.resolve(actor=actor)

    @staticmethod
    def _resource(row, *, tenant_id, resource_id):
        if row is None or getattr(row, "id", None) != resource_id or getattr(row, "tenant_id", None) != tenant_id or getattr(row, "deleted_at", "missing") is not None:
            raise ConstructionActivityTerminalNotFoundError("Activity resource is not available.")
        return row

    @staticmethod
    def _links(value, activity_id):
        if not isinstance(value, dict) or set(value) != {"id", "lead_id", "client_id", "project_id"} or value["id"] != activity_id:
            raise ConstructionActivityTerminalOperationError("Activity scope metadata is invalid.")
        try:
            return ConstructionActivityLinks(value["lead_id"], value["client_id"], value["project_id"])
        except ConstructionActivityValidationError:
            raise ConstructionActivityTerminalOperationError("Stored activity links are invalid.") from None

    async def _finalize(self, *, actor, access_context, activity_id, command, next_status, trace_id):
        try:
            self._scope(actor, access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionActivityTerminalOperationError("Activity operation requires an active caller transaction.")
            if not isinstance(activity_id, UUID) or not isinstance(command, ConstructionActivityTerminalCommand):
                raise ConstructionActivityValidationError("Activity operation requires a UUID and validated command.")
            command = ConstructionActivityTerminalCommand(command.expected_row_version, command.result)
            with self.session.no_autoflush:
                context = await self._context(actor)
                allowed = self._scope(actor, context)
                metadata = await self.repository.find_active_activity_links(
                    tenant_id=actor.tenant_id, activity_id=activity_id, allowed_project_ids=allowed)
                if metadata is None:
                    raise ConstructionActivityTerminalNotFoundError("Activity is not available in the authorized scope.")
                links = self._links(metadata, activity_id)
                self._authorize_links(actor, context, links)
                correlation_parents = {}
                for repo, method, field in (
                    (self.lead_repository, "get_active_lead_for_update", "lead_id"),
                    (self.client_repository, "get_active_client_for_update", "client_id"),
                    (self.project_repository, "get_active_project_for_update", "project_id"),
                ):
                    reference = getattr(links, field)
                    if reference is not None:
                        correlation_parents[field] = self._resource(await getattr(repo, method)(tenant_id=actor.tenant_id, **{field: reference}),
                                       tenant_id=actor.tenant_id, resource_id=reference)
                context = await self._context(actor)
                self._authorize_links(actor, context, links)
                row = await self.repository.get_active_activity_for_update_by_links(
                    tenant_id=actor.tenant_id, activity_id=activity_id, links=links)
                if row is None:
                    raise ConstructionRowVersionConflictError()
                self._resource(row, tenant_id=actor.tenant_id, resource_id=activity_id)
                if any(getattr(row, field, "missing") != getattr(links, field) for field in ("lead_id", "client_id", "project_id")):
                    raise ConstructionRowVersionConflictError()
                context = await self._context(actor)
                self._authorize_links(actor, context, links)
                version, old_status = getattr(row, "row_version", None), getattr(row, "status", None)
                if type(version) is not int or not 0 < version < 2**63 or old_status not in CONSTRUCTION_ACTIVITY_STATUSES:
                    raise ConstructionActivityTerminalOperationError("Stored activity state is invalid.")
                require_expected_row_version(actual_row_version=version, expected_row_version=command.expected_row_version)
                require_construction_activity_transition(current_status=old_status, next_status=next_status)
                if version == 2**63-1:
                    raise ConstructionActivityValidationError("Activity version cannot be incremented.")
                if getattr(row, "completed_at", "missing") is not None or getattr(row, "cancelled_at", "missing") is not None:
                    raise ConstructionActivityTerminalOperationError("Planned activity has invalid terminal timestamps.")
                missing = object()
                preserved = {field: getattr(row, field, missing) for field in ACTIVITY_TERMINAL_PRESERVED_FIELDS}
                old_result = getattr(row, "result", missing)
                if any(value is missing for value in preserved.values()) or (old_result is not None and not isinstance(old_result, str)):
                    raise ConstructionActivityTerminalOperationError("Stored activity fields are invalid.")
                effective_result = command.result if command.result_provided else old_result
                instant = self._now()
                method = self.repository.complete_activity if next_status == "completed" else self.repository.cancel_activity
                updated = await method(tenant_id=actor.tenant_id, activity_id=activity_id,
                    expected_row_version=version, as_of=instant, result=command.result)
                if updated is None:
                    raise ConstructionRowVersionConflictError()
                self._resource(updated, tenant_id=actor.tenant_id, resource_id=activity_id)
                terminal_field = "completed_at" if next_status == "completed" else "cancelled_at"
                other_field = "cancelled_at" if next_status == "completed" else "completed_at"
                if (
                    getattr(updated, "status", None) != next_status or getattr(updated, "result", missing) != effective_result
                    or type(getattr(updated, "row_version", None)) is not int or updated.row_version != version+1
                    or getattr(updated, terminal_field, None) != instant or getattr(updated, other_field, missing) is not None
                    or getattr(updated, "updated_at", None) != instant
                    or any(getattr(updated, field, missing) != value for field, value in preserved.items())
                ):
                    raise ConstructionActivityTerminalOperationError("Conditional activity operation result is invalid.")
                await self.audit_logger.record_operation(actor=actor,
                    event_type="construction_activity_completed" if next_status == "completed" else "construction_activity_cancelled",
                    entity_type="construction_activity", entity_id=activity_id,
                    operation="complete" if next_status == "completed" else "cancel", trace_id=trace_id,
                    changes=(ConstructionAuditChange("status", old_status, next_status),
                             ConstructionAuditChange(terminal_field, None, instant.isoformat())),
                    details={"result_changed": old_result != effective_result,
                        **construction_audit_client_details(
                        tenant_id=actor.tenant_id, entity_type="construction_activity", entity=updated,
                        lead=correlation_parents.get("lead_id"), client=correlation_parents.get("client_id"),
                        project=correlation_parents.get("project_id"))})
                return updated
        except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionActivityValidationError, ConstructionActivityTransitionError,
                ConstructionRowVersionConflictError, ConstructionActivityTerminalNotFoundError,
                ConstructionActivityTerminalOperationError):
            raise
        except Exception:
            raise ConstructionActivityTerminalOperationError("Activity terminal operation failed.") from None

    async def complete_activity(self, *, actor, access_context, activity_id, command, trace_id=None):
        return await self._finalize(actor=actor, access_context=access_context, activity_id=activity_id,
                                    command=command, next_status="completed", trace_id=trace_id)

    async def cancel_activity(self, *, actor, access_context, activity_id, command, trace_id=None):
        return await self._finalize(actor=actor, access_context=access_context, activity_id=activity_id,
                                    command=command, next_status="cancelled", trace_id=trace_id)


def build_construction_activity_terminal_service(*, session, now_provider=None):
    from database.repositories.construction import ConstructionAccessRepository, ConstructionClientRepository, ConstructionProjectRepository
    from database.repositories.construction.construction_activities import ConstructionActivityRepository
    from database.repositories.construction.construction_leads import ConstructionLeadRepository
    from database.repositories.event import EventRepository
    return ConstructionActivityTerminalService(session=session, repository=ConstructionActivityRepository(session),
        lead_repository=ConstructionLeadRepository(session), client_repository=ConstructionClientRepository(session),
        project_repository=ConstructionProjectRepository(session), access_repository=ConstructionAccessRepository(session),
        event_repository=EventRepository(session), now_provider=now_provider)
