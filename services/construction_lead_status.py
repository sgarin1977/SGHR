from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from services.construction_audit_client_correlation import construction_audit_client_details

from services.construction_audit import ConstructionAuditChange, ConstructionAuditLogger
from services.construction_foundation import ConstructionRowVersionConflictError, require_expected_row_version
from services.construction_grants import ConstructionAccessContextResolver
from services.construction_lead_rules import (
    ConstructionLeadValidationError, ConstructionLeadTransitionError,
    require_construction_lead_status, require_construction_lead_transition,
)
from services.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext, ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError, require_construction_access, require_construction_role_scope,
)


LEAD_STATUS_PERMISSION = "construction.projects.edit"
LEAD_STATUS_PRESERVED_FIELDS = (
    "source", "name", "work_type_description", "phone", "email", "address_raw",
    "responsible_user_id", "comment", "converted_client_id", "converted_project_id", "created_at",
)


class ConstructionLeadStatusNotFoundError(Exception):
    pass


class ConstructionLeadStatusOperationError(Exception):
    pass


@dataclass(frozen=True)
class ConstructionLeadStatusCommand:
    expected_row_version: int
    next_status: str

    def __post_init__(self):
        if type(self.expected_row_version) is not int or not 0 < self.expected_row_version < 2**63:
            raise ConstructionLeadValidationError("Lead status requires a positive BIGINT version.")
        require_construction_lead_status(self.next_status)


@dataclass(frozen=True)
class ConstructionLeadStatusResult:
    lead: object
    changed: bool


class ConstructionLeadStatusService:
    """Caller owns the transaction and must roll back every failed operation."""

    def __init__(self, *, session, repository, access_repository, event_repository, now_provider=None):
        methods = (
            (repository, ("get_active_lead_for_update", "transition_status")),
            (access_repository, ("has_active_platform_membership", "list_active_grants")),
            (event_repository, ("create_event",)),
        )
        if (
            session is None or not callable(getattr(session, "in_transaction", None))
            or not hasattr(session, "no_autoflush")
            or any(getattr(repo, "session", None) is not session
                   or any(not callable(getattr(repo, name, None)) for name in names)
                   for repo, names in methods)
            or (now_provider is not None and not callable(now_provider))
        ):
            raise ValueError("Lead status requires shared-session repository wiring.")
        self.session, self.repository = session, repository
        self.access_repository, self.event_repository = access_repository, event_repository
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(repository=access_repository, now_provider=self._now)
        self.audit_logger = ConstructionAuditLogger(event_repository=event_repository)

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionLeadStatusOperationError("Lead status clock must be aware.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _authorize(actor, context):
        if (
            getattr(actor, "status", None) != "active"
            or not isinstance(getattr(actor, "user_id", None), UUID)
            or not isinstance(getattr(actor, "tenant_id", None), UUID)
            or not isinstance(context, ConstructionAccessContext)
        ):
            raise ConstructionPermissionDeniedError("Lead status access is not available.")
        for grant in context.grants:
            if not isinstance(grant, ConstructionGrantContext) or not isinstance(grant.scope_id, UUID):
                raise ConstructionScopeDeniedError("Lead status scope is invalid.")
            require_construction_role_scope(role=grant.role, scope_type=grant.scope_type)
        require_construction_access(actor=actor, access_context=context, permission_code=LEAD_STATUS_PERMISSION)

    async def _context(self, actor):
        if await self.access_repository.has_active_platform_membership(
            tenant_id=actor.tenant_id, user_id=actor.user_id,
        ) is not True:
            raise ConstructionPermissionDeniedError("Lead status membership is not available.")
        return await self.resolver.resolve(actor=actor)

    @staticmethod
    def _resource(lead, *, actor, lead_id):
        if (
            lead is None or getattr(lead, "id", None) != lead_id
            or getattr(lead, "tenant_id", None) != actor.tenant_id
            or getattr(lead, "deleted_at", "missing") is not None
        ):
            raise ConstructionLeadStatusNotFoundError("Lead is not available.")
        return lead

    async def transition_status(self, *, actor, access_context, lead_id, command, trace_id=None):
        try:
            self._authorize(actor, access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionLeadStatusOperationError("Lead status requires an active caller transaction.")
            if not isinstance(lead_id, UUID) or not isinstance(command, ConstructionLeadStatusCommand):
                raise ConstructionLeadValidationError("Lead status requires a UUID and validated command.")
            command = ConstructionLeadStatusCommand(
                expected_row_version=command.expected_row_version, next_status=command.next_status,
            )
            with self.session.no_autoflush:
                self._authorize(actor, await self._context(actor))
                lead = self._resource(await self.repository.get_active_lead_for_update(
                    tenant_id=actor.tenant_id, lead_id=lead_id,
                ), actor=actor, lead_id=lead_id)
                self._authorize(actor, await self._context(actor))
                version, old_status = getattr(lead, "row_version", None), getattr(lead, "status", None)
                if type(version) is not int or not 0 < version < 2**63:
                    raise ConstructionLeadStatusOperationError("Stored lead version is invalid.")
                try:
                    require_construction_lead_status(old_status)
                except ConstructionLeadValidationError:
                    raise ConstructionLeadStatusOperationError("Stored lead status is invalid.") from None
                require_expected_row_version(actual_row_version=version,
                                             expected_row_version=command.expected_row_version)
                if old_status == command.next_status:
                    return ConstructionLeadStatusResult(lead, False)
                require_construction_lead_transition(current_status=old_status, next_status=command.next_status)
                if version == 2**63-1:
                    raise ConstructionLeadValidationError("Lead version cannot be incremented.")
                missing = object()
                preserved = {field: getattr(lead, field, missing) for field in LEAD_STATUS_PRESERVED_FIELDS}
                if any(value is missing for value in preserved.values()):
                    raise ConstructionLeadStatusOperationError("Stored lead fields are incomplete.")
                instant = self._now()
                updated = await self.repository.transition_status(
                    tenant_id=actor.tenant_id, lead_id=lead_id, expected_row_version=version,
                    current_status=old_status, next_status=command.next_status, as_of=instant,
                )
                if updated is None:
                    raise ConstructionRowVersionConflictError()
                self._resource(updated, actor=actor, lead_id=lead_id)
                if (
                    getattr(updated, "status", None) != command.next_status
                    or type(getattr(updated, "row_version", None)) is not int
                    or updated.row_version != version+1
                    or getattr(updated, "updated_at", None) != instant
                    or any(getattr(updated, field, missing) != value for field, value in preserved.items())
                ):
                    raise ConstructionLeadStatusOperationError("Conditional lead status result is invalid.")
                await self.audit_logger.record_operation(
                    actor=actor, event_type="construction_lead_status_changed",
                    entity_type="construction_lead", entity_id=lead_id, operation="transition_status",
                    changes=(ConstructionAuditChange("status", old_status, command.next_status),),
                    trace_id=trace_id,
                    details=construction_audit_client_details(tenant_id=actor.tenant_id, entity_type="construction_lead", entity=updated),
                )
                return ConstructionLeadStatusResult(updated, True)
        except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionLeadValidationError, ConstructionLeadTransitionError,
                ConstructionRowVersionConflictError, ConstructionLeadStatusNotFoundError,
                ConstructionLeadStatusOperationError):
            raise
        except Exception:
            raise ConstructionLeadStatusOperationError("Lead status change failed.") from None


def build_construction_lead_status_service(*, session, now_provider=None):
    from database.repositories.construction import ConstructionAccessRepository
    from database.repositories.construction_leads import ConstructionLeadRepository
    from database.repositories.event import EventRepository
    return ConstructionLeadStatusService(session=session, repository=ConstructionLeadRepository(session),
        access_repository=ConstructionAccessRepository(session), event_repository=EventRepository(session),
        now_provider=now_provider)
