from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from services.construction.construction_audit_client_correlation import construction_audit_client_details

from services.construction.construction_audit import ConstructionAuditChange, ConstructionAuditLogger
from services.construction.construction_clients import ConstructionClientService
from services.construction.construction_foundation import (
    ConstructionRowVersionConflictError, require_expected_row_version,
)
from services.construction.construction_grants import ConstructionAccessContextResolver
from services.construction.construction_lead_conversion_rules import (
    ConstructionLeadConversionSelection, inspect_construction_lead_conversion,
)
from services.construction.construction_lead_rules import ConstructionLeadInput, ConstructionLeadValidationError, ConstructionLeadTransitionError
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
    require_construction_access, require_construction_role_scope,
)
from services.construction.construction_projects import ConstructionProjectService


class ConstructionLeadConversionNotFoundError(Exception):
    pass


class ConstructionLeadConversionOperationError(Exception):
    pass


@dataclass(frozen=True)
class ConstructionLeadConversionCommand:
    expected_row_version: int
    selection: ConstructionLeadConversionSelection
    client_type: str | None = None
    project_name: str | None = None
    project_address_raw: str | None = None

    def __post_init__(self):
        if type(self.expected_row_version) is not int or not 0 < self.expected_row_version < 2**63:
            raise ConstructionLeadValidationError("Conversion requires a positive BIGINT version.")
        if not isinstance(self.selection, ConstructionLeadConversionSelection):
            raise ConstructionLeadValidationError("Conversion requires a validated selection.")
        for field in ("client_type", "project_name", "project_address_raw"):
            value = getattr(self, field)
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise ConstructionLeadValidationError("Conversion creation fields must be non-empty text.")
                object.__setattr__(self, field, value.strip().lower() if field == "client_type" else value.strip())
        if self.selection.creates_client:
            if self.client_type not in ("person", "company"):
                raise ConstructionLeadValidationError("New client requires person or company client_type.")
        elif self.client_type is not None:
            raise ConstructionLeadValidationError("An existing client is not edited by conversion.")
        if self.selection.creates_project:
            if self.project_name is None:
                raise ConstructionLeadValidationError("New project name is required.")
        elif self.project_name is not None or self.project_address_raw is not None:
            raise ConstructionLeadValidationError("An existing project is not edited by conversion.")


@dataclass(frozen=True)
class ConstructionLeadConversionResult:
    lead: object
    client: object
    project: object
    is_replay: bool


class _ConversionCreationAudit:
    """Collect the normal create-service substeps for one final conversion audit."""
    def __init__(self, *, session, actor, trace_id, client_id=None):
        self.session, self.actor, self.trace_id = session, actor, trace_id
        self.events = []
        self.related_client_id = client_id

    async def create_event(self, **event):
        expected = {
            "construction_client_created": "construction_client",
            "construction_project_created": "construction_project",
        }
        kind = event.get("event_type")
        related = event.get("entity_id") if kind == "construction_client_created" else self.related_client_id
        if (
            kind not in expected
            or event.get("tenant_id") != self.actor.tenant_id
            or event.get("user_id") != self.actor.user_id
            or event.get("entity_type") != expected[kind]
            or not isinstance(event.get("entity_id"), UUID)
            or event.get("platform") != "api"
            or event.get("trace_id") != self.trace_id
            or not isinstance(related, UUID)
            or event.get("payload") != {"operation": "create", "changes": [], "reason": None, "related_client_id": str(related)}
            or any(item["event_type"] == kind for item in self.events)
        ):
            raise ConstructionLeadConversionOperationError("Conversion creation audit is invalid.")
        self.events.append(event)
        if kind == "construction_client_created":
            self.related_client_id = event["entity_id"]


class ConstructionLeadConversionService:
    """No commit/rollback: caller must roll back every failure, including final audit."""
    def __init__(self, *, session, repository, client_repository, project_repository,
                 access_repository, event_repository, now_provider=None):
        methods = (
            (repository, ("get_active_lead_for_update", "complete_conversion")),
            (client_repository, ("get_active_client_for_update", "get_active_client", "create_client")),
            (project_repository, ("get_active_project_for_update", "create_project")),
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
            raise ValueError("Conversion requires shared-session repository wiring.")
        self.session, self.repository = session, repository
        self.client_repository, self.project_repository = client_repository, project_repository
        self.access_repository, self.event_repository = access_repository, event_repository
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(repository=access_repository, now_provider=self._now)
        self.audit_logger = ConstructionAuditLogger(event_repository=event_repository)

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionLeadConversionOperationError("Conversion clock must be aware.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _authorize(actor, context, permission, project_id=None):
        if (
            getattr(actor, "status", None) != "active"
            or not isinstance(getattr(actor, "user_id", None), UUID)
            or not isinstance(getattr(actor, "tenant_id", None), UUID)
            or not isinstance(context, ConstructionAccessContext)
        ):
            raise ConstructionPermissionDeniedError("Conversion access is not available.")
        for grant in context.grants:
            if not isinstance(grant, ConstructionGrantContext) or not isinstance(grant.scope_id, UUID):
                raise ConstructionScopeDeniedError("Conversion scope is invalid.")
            require_construction_role_scope(role=grant.role, scope_type=grant.scope_type)
        require_construction_access(actor=actor, access_context=context,
                                    permission_code=permission, project_id=project_id)

    async def _context(self, actor):
        if await self.access_repository.has_active_platform_membership(
            tenant_id=actor.tenant_id, user_id=actor.user_id,
        ) is not True:
            raise ConstructionPermissionDeniedError("Conversion membership is not available.")
        return await self.resolver.resolve(actor=actor)

    @staticmethod
    def _resource(resource, *, tenant_id, resource_id, label):
        if resource is None:
            raise ConstructionLeadConversionNotFoundError(label + " is not available.")
        if (
            getattr(resource, "id", None) != resource_id
            or getattr(resource, "tenant_id", None) != tenant_id
            or getattr(resource, "deleted_at", "missing") is not None
        ):
            raise ConstructionLeadConversionNotFoundError(label + " is not available.")
        return resource

    async def _targets(self, *, actor, context, client_id, project_id):
        self._authorize(actor, context, "construction.projects.read", project_id)
        client = self._resource(await self.client_repository.get_active_client_for_update(
            tenant_id=actor.tenant_id, client_id=client_id,
        ), tenant_id=actor.tenant_id, resource_id=client_id, label="Conversion client")
        project = None
        if project_id is not None:
            project = self._resource(await self.project_repository.get_active_project_for_update(
                tenant_id=actor.tenant_id, project_id=project_id,
            ), tenant_id=actor.tenant_id, resource_id=project_id, label="Conversion project")
            if getattr(project, "client_id", None) != client.id:
                raise ConstructionLeadValidationError("Selected project does not belong to the selected client.")
        return client, project

    async def convert_lead(self, *, actor, access_context, lead_id, command, trace_id=None):
        try:
            self._authorize(actor, access_context, "construction.projects.edit")
            if self.session.in_transaction() is not True:
                raise ConstructionLeadConversionOperationError("Conversion requires an active caller transaction.")
            if not isinstance(lead_id, UUID) or not isinstance(command, ConstructionLeadConversionCommand):
                raise ConstructionLeadValidationError("Conversion requires a UUID and validated command.")
            # Revalidate frozen input before using it; only server-side context selects tenant/user.
            selection = ConstructionLeadConversionSelection(
                client_id=command.selection.client_id, project_id=command.selection.project_id)
            command = ConstructionLeadConversionCommand(
                selection=selection, **{
                    field: getattr(command, field) for field in (
                        "expected_row_version", "client_type", "project_name", "project_address_raw",
                    )
                })
            context = await self._context(actor)
            self._authorize(actor, context, "construction.projects.edit")
            with self.session.no_autoflush:
                lead = self._resource(await self.repository.get_active_lead_for_update(
                    tenant_id=actor.tenant_id, lead_id=lead_id,
                ), tenant_id=actor.tenant_id, resource_id=lead_id, label="Conversion lead")
                state = inspect_construction_lead_conversion(
                    status=lead.status, converted_client_id=lead.converted_client_id,
                    converted_project_id=lead.converted_project_id,
                )
                if type(getattr(lead, "row_version", None)) is not int or not 0 < lead.row_version < 2**63:
                    raise ConstructionLeadConversionOperationError("Conversion lead version is invalid.")
                if state.is_replay:
                    if (
                        command.selection.client_id not in (None, state.converted_client_id)
                        or command.selection.project_id not in (None, state.converted_project_id)
                    ):
                        raise ConstructionLeadValidationError("Conversion already has different recorded targets.")
                    client, project = await self._targets(actor=actor, context=context,
                        client_id=state.converted_client_id, project_id=state.converted_project_id)
                    context = await self._context(actor)
                    self._authorize(actor, context, "construction.projects.edit")
                    self._authorize(actor, context, "construction.projects.read", project.id)
                    return ConstructionLeadConversionResult(lead, client, project, True)
                require_expected_row_version(actual_row_version=lead.row_version,
                                             expected_row_version=command.expected_row_version)
                if lead.row_version == 2**63 - 1:
                    raise ConstructionLeadValidationError("Conversion lead version cannot be incremented.")
                old_version, old_status = lead.row_version, lead.status
                preserved = {
                    field: getattr(lead, field) for field in (
                        "source", "name", "work_type_description", "phone", "email",
                        "address_raw", "comment", "responsible_user_id",
                    )
                }
                data = ConstructionLeadInput(**preserved)
                selection = command.selection
                if selection.creates_client or selection.creates_project:
                    self._authorize(actor, context, "construction.projects.create")
                client, project = None, None
                if selection.client_id is not None:
                    client, project = await self._targets(actor=actor, context=context,
                        client_id=selection.client_id, project_id=selection.project_id)
                address = command.project_address_raw or data.address_raw
                if selection.creates_project:
                    if not address:
                        raise ConstructionLeadValidationError("New project address is required.")
                    if data.responsible_user_id is not None and await self.access_repository.has_active_platform_membership(
                        tenant_id=actor.tenant_id, user_id=data.responsible_user_id,
                    ) is not True:
                        raise ConstructionLeadValidationError("Project responsible user is not available.")
                context = await self._context(actor)
                self._authorize(actor, context, "construction.projects.edit")
                if selection.creates_client or selection.creates_project:
                    self._authorize(actor, context, "construction.projects.create")
                elif project is not None:
                    self._authorize(actor, context, "construction.projects.read", project.id)
                captured = _ConversionCreationAudit(session=self.session, actor=actor, trace_id=trace_id, client_id=client.id if client is not None else None)
                if selection.creates_client:
                    service = ConstructionClientService(session=self.session,
                        repository=self.client_repository, event_repository=captured)
                    client = await service.create_client(actor=actor, access_context=context,
                        display_name=data.name, client_type=command.client_type,
                        phone=data.phone, email=data.email, notes=None,
                        trace_id=trace_id, manage_transaction=False)
                project_comment = "\n\n".join(value for value in (data.work_type_description, data.comment) if value)
                if selection.creates_project:
                    service = ConstructionProjectService(session=self.session,
                        repository=self.project_repository, client_repository=self.client_repository,
                        access_repository=self.access_repository, event_repository=captured)
                    project = await service.create_project(actor=actor, access_context=context,
                        name=command.project_name, address_raw=address, client_id=client.id,
                        responsible_user_id=data.responsible_user_id, comment=project_comment or None,
                        trace_id=trace_id, manage_transaction=False)
                    if (
                        not isinstance(getattr(project, "id", None), UUID)
                        or getattr(project, "tenant_id", None) != actor.tenant_id
                        or getattr(project, "deleted_at", "missing") is not None
                        or getattr(project, "client_id", None) != client.id
                        or getattr(project, "name", None) != command.project_name
                        or getattr(project, "address_raw", None) != address
                        or getattr(project, "responsible_user_id", "missing") != data.responsible_user_id
                        or getattr(project, "comment", None) != (project_comment or None)
                        or getattr(project, "created_by", None) != actor.user_id
                        or getattr(project, "status", None) != "draft"
                        or type(getattr(project, "row_version", None)) is not int or project.row_version != 1
                    ):
                        raise ConstructionLeadConversionOperationError("Created conversion project is invalid.")
                expected_events = []
                if selection.creates_client:
                    expected_events.append(("construction_client_created", client.id))
                if selection.creates_project:
                    expected_events.append(("construction_project_created", project.id))
                if [(event["event_type"], event["entity_id"]) for event in captured.events] != expected_events:
                    raise ConstructionLeadConversionOperationError("Conversion creation audit is incomplete.")
                context = await self._context(actor)
                self._authorize(actor, context, "construction.projects.edit")
                self._authorize(actor, context,
                    "construction.projects.create" if selection.creates_project else "construction.projects.read",
                    None if selection.creates_project else project.id)
                instant = self._now()
                completed = await self.repository.complete_conversion(
                    tenant_id=actor.tenant_id, lead_id=lead.id, expected_row_version=old_version,
                    client_id=client.id, project_id=project.id, as_of=instant)
                if (
                    completed is None or getattr(completed, "id", None) != lead_id
                    or getattr(completed, "tenant_id", None) != actor.tenant_id
                    or getattr(completed, "deleted_at", "missing") is not None
                    or getattr(completed, "status", None) != "won"
                    or getattr(completed, "converted_client_id", None) != client.id
                    or getattr(completed, "converted_project_id", None) != project.id
                    or type(getattr(completed, "row_version", None)) is not int
                    or completed.row_version != old_version + 1
                    or getattr(completed, "updated_at", None) != instant
                    or any(getattr(completed, field, object()) != value for field, value in preserved.items())
                ):
                    raise ConstructionLeadConversionOperationError("Conditional conversion did not complete.")
                await self.audit_logger.record_operation(actor=actor,
                    event_type="construction_lead_converted", entity_type="construction_lead",
                    entity_id=lead_id, operation="convert", trace_id=trace_id,
                    changes=(
                        ConstructionAuditChange("status", old_status, "won"),
                        ConstructionAuditChange("converted_client_id", None, str(client.id)),
                        ConstructionAuditChange("converted_project_id", None, str(project.id)),
                    ), details={
                        **construction_audit_client_details(tenant_id=actor.tenant_id, entity_type="construction_lead", entity=completed),
                        "created_client_id": str(client.id) if selection.creates_client else None,
                        "created_project_id": str(project.id) if selection.creates_project else None,
                    })
                return ConstructionLeadConversionResult(completed, client, project, False)
        except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionLeadValidationError, ConstructionLeadTransitionError,
                ConstructionRowVersionConflictError, ConstructionLeadConversionNotFoundError,
                ConstructionLeadConversionOperationError):
            raise
        except Exception:
            raise ConstructionLeadConversionOperationError("Lead conversion failed.") from None


def build_construction_lead_conversion_service(*, session, now_provider=None):
    from database.repositories.construction import (
        ConstructionAccessRepository, ConstructionClientRepository, ConstructionProjectRepository,
    )
    from database.repositories.construction.construction_leads import ConstructionLeadRepository
    from database.repositories.event import EventRepository
    return ConstructionLeadConversionService(session=session, repository=ConstructionLeadRepository(session),
        client_repository=ConstructionClientRepository(session), project_repository=ConstructionProjectRepository(session),
        access_repository=ConstructionAccessRepository(session), event_repository=EventRepository(session),
        now_provider=now_provider)
