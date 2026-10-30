from datetime import datetime, timezone
from uuid import UUID

from services.construction.construction_audit_client_correlation import construction_audit_client_details

from services.construction.construction_audit import ConstructionAuditLogger
from services.construction.construction_grants import ConstructionAccessContextResolver
from services.construction.construction_lead_rules import ConstructionLeadInput, ConstructionLeadValidationError
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
    require_construction_access, require_construction_role_scope,
)

LEAD_CREATE_PERMISSION = "construction.projects.create"
LEAD_INPUT_FIELDS = (
    "source", "name", "work_type_description", "phone", "email",
    "address_raw", "comment", "responsible_user_id",
)


class ConstructionLeadOperationError(Exception):
    pass


class ConstructionLeadCreateService:
    """Prepare the lead and audit; the caller owns commit and rollback."""

    def __init__(self, *, session, repository, access_repository, event_repository, now_provider=None):
        if (
            session is None
            or any(getattr(item, "session", None) is not session
                   for item in (repository, access_repository, event_repository))
            or not callable(getattr(session, "in_transaction", None))
            or not callable(getattr(repository, "create_lead", None))
            or any(not callable(getattr(access_repository, name, None))
                   for name in ("has_active_platform_membership", "list_active_grants"))
            or not callable(getattr(event_repository, "create_event", None))
            or (now_provider is not None and not callable(now_provider))
        ):
            raise ValueError("Lead creation requires shared-session wiring.")
        self.session = session
        self.repository = repository
        self.access_repository = access_repository
        self.event_repository = event_repository
        self.audit_logger = ConstructionAuditLogger(event_repository=event_repository)
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.resolver = ConstructionAccessContextResolver(
            repository=access_repository, now_provider=self._now,
        )

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionLeadOperationError("Lead creation clock must be aware.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _authorize(actor, context):
        if (
            getattr(actor, "status", None) != "active"
            or not isinstance(getattr(actor, "user_id", None), UUID)
            or not isinstance(getattr(actor, "tenant_id", None), UUID)
            or not isinstance(context, ConstructionAccessContext)
        ):
            raise ConstructionPermissionDeniedError("Lead creation is not available.")
        require_construction_access(
            actor=actor, access_context=context, permission_code=LEAD_CREATE_PERMISSION,
        )
        for grant in context.grants:
            if not isinstance(grant, ConstructionGrantContext) or not isinstance(grant.scope_id, UUID):
                raise ConstructionScopeDeniedError("Lead creation scope is invalid.")
            if grant.role in context.roles and LEAD_CREATE_PERMISSION in CONSTRUCTION_ROLE_PERMISSIONS.get(grant.role, ()):
                require_construction_role_scope(role=grant.role, scope_type=grant.scope_type)

    @staticmethod
    def _check_created(lead, *, actor, data, instant):
        if (
            not isinstance(getattr(lead, "id", None), UUID)
            or getattr(lead, "tenant_id", None) != actor.tenant_id
            or getattr(lead, "status", None) != "new"
            or type(getattr(lead, "row_version", None)) is not int or lead.row_version != 1
            or getattr(lead, "deleted_at", "missing") is not None
            or getattr(lead, "converted_client_id", "missing") is not None
            or getattr(lead, "converted_project_id", "missing") is not None
            or getattr(lead, "created_at", None) != instant
            or getattr(lead, "updated_at", None) != instant
            or any(getattr(lead, field, object()) != getattr(data, field) for field in LEAD_INPUT_FIELDS)
        ):
            raise ConstructionLeadOperationError("Created lead state is invalid.")

    async def create_lead(self, *, actor, access_context, data, trace_id=None):
        try:
            self._authorize(actor, access_context)
            if self.session.in_transaction() is not True:
                raise ConstructionLeadOperationError("Lead creation requires an active caller transaction.")
            if not isinstance(data, ConstructionLeadInput):
                raise ConstructionLeadValidationError("Lead creation requires validated input.")
            data = ConstructionLeadInput(**{field: getattr(data, field) for field in LEAD_INPUT_FIELDS})
            if await self.access_repository.has_active_platform_membership(
                tenant_id=actor.tenant_id, user_id=actor.user_id,
            ) is not True:
                raise ConstructionPermissionDeniedError("Lead creation membership is not available.")
            if data.responsible_user_id is not None and await self.access_repository.has_active_platform_membership(
                tenant_id=actor.tenant_id, user_id=data.responsible_user_id,
            ) is not True:
                raise ConstructionLeadValidationError("Lead responsible user is not available.")
            # Re-resolve effective grants after awaited membership checks.
            context = await self.resolver.resolve(actor=actor)
            self._authorize(actor, context)
            instant = self._now()
            lead = await self.repository.create_lead(
                tenant_id=actor.tenant_id, data=data, as_of=instant,
            )
            self._check_created(lead, actor=actor, data=data, instant=instant)
            await self.audit_logger.record_operation(
                actor=actor, event_type="construction_lead_created",
                entity_type="construction_lead", entity_id=lead.id,
                operation="create", trace_id=trace_id,
                details=construction_audit_client_details(tenant_id=actor.tenant_id, entity_type="construction_lead", entity=lead),
            )
            return lead
        except (
            ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
            ConstructionLeadValidationError, ConstructionLeadOperationError,
        ):
            raise
        except Exception:
            raise ConstructionLeadOperationError("Lead creation failed.") from None


def build_construction_lead_create_service(*, session, now_provider=None):
    from database.repositories.construction import ConstructionAccessRepository
    from database.repositories.construction.construction_leads import ConstructionLeadRepository
    from database.repositories.event import EventRepository
    return ConstructionLeadCreateService(
        session=session, repository=ConstructionLeadRepository(session),
        access_repository=ConstructionAccessRepository(session),
        event_repository=EventRepository(session), now_provider=now_provider,
    )
