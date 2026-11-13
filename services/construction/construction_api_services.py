"""Service composition and navigation over canonical Construction policies."""
from database.repositories.construction import (
    ConstructionAccessRepository, ConstructionClientRepository,
    ConstructionOrganizationRepository, ConstructionProjectRepository,
)
from services.construction.construction_clients import ConstructionClientService
from services.construction.construction_projects import ConstructionProjectService
from services.construction.construction_foundation import (
    ConstructionOrganizationService, ConstructionOrganizationUnavailableError,
)
from services.construction.construction_lead_read import ConstructionLeadReadService
from services.construction.construction_client_catalog_read import ConstructionClientCatalogReadService
from services.construction.construction_project_catalog_read import ConstructionProjectCatalogReadService
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
    require_construction_access,
)


def build_client_patch_service(session):
    return ConstructionClientService(
        session=session, repository=ConstructionClientRepository(session),
    )


def build_project_patch_service(session):
    return ConstructionProjectService(
        session=session, repository=ConstructionProjectRepository(session),
        client_repository=ConstructionClientRepository(session),
        access_repository=ConstructionAccessRepository(session),
    )


def build_navigation_access(session):
    return ConstructionAccessRepository(session)


def build_navigation_organization(session):
    return ConstructionOrganizationService(
        repository=ConstructionOrganizationRepository(session),
    )


async def read_construction_navigation(*, actor, context, access, organizations):
    if (
        actor.status != "active"
        or context.user_id != actor.user_id
        or context.tenant_id != actor.tenant_id
        or not context.roles
        or not context.grants
        or await access.has_active_platform_membership(
            user_id=actor.user_id, tenant_id=actor.tenant_id
        ) is not True
    ):
        raise ConstructionPermissionDeniedError()

    organization = await organizations.require_current(actor=actor)
    if organization.tenant_id != actor.tenant_id:
        raise ConstructionOrganizationUnavailableError()

    can_read_leads = True
    try:
        ConstructionLeadReadService._authorize(actor, context)
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        can_read_leads = False

    can_read_projects = True
    try:
        ConstructionProjectCatalogReadService._scope(actor, context)
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        can_read_projects = False

    can_read_clients = True
    try:
        ConstructionClientCatalogReadService._authorize(actor, context)
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        can_read_clients = False

    can_create_project = True
    try:
        require_construction_access(
            actor=actor, access_context=context,
            permission_code="construction.projects.create",
        )
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        can_create_project = False
    can_manage_team = True
    try:
        require_construction_access(
            actor=actor, access_context=context,
            permission_code="construction.team.manage",
        )
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        can_manage_team = False

    can_edit_tax_profiles = True
    try:
        require_construction_access(
            actor=actor, access_context=context,
            permission_code="construction.tax.edit",
        )
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        can_edit_tax_profiles = False

    can_edit_pricing_profiles = True
    try:
        require_construction_access(
            actor=actor, access_context=context,
            permission_code="construction.pricing.edit",
        )
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        can_edit_pricing_profiles = False

    return {
        "tenant_id": str(actor.tenant_id),
        "name": organization.name,
        "currency": organization.default_currency,
        "roles": list(context.roles),
        "can_read_leads": can_read_leads,
        "can_read_clients": can_read_clients,
        "can_read_projects": can_read_projects,
        "can_create_project": can_create_project,
        "can_manage_team": can_manage_team,
        "can_edit_tax_profiles": can_edit_tax_profiles,
        "can_edit_pricing_profiles": can_edit_pricing_profiles,
    }
