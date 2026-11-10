"""CON-O001 template readiness from an organization-owned published version."""
from collections.abc import Mapping
from uuid import UUID

from services.construction.construction_onboarding import (
    ConstructionOnboardingService,
    ConstructionOnboardingStep as Step,
    ConstructionOnboardingOperationError as Operation,
    ConstructionOnboardingCompletionUnavailable as Unavailable,
    _step,
)
from services.construction.construction_organization_template_definitions import (
    ValidatedOrganizationTemplateDefinition,
)
from services.construction.construction_organization_templates import (
    ConstructionOrganizationTemplateService,
    OrganizationTemplateVersionState,
)


class ConstructionOnboardingTemplateVerifier:
    def __init__(self, *, session, repository, template_service):
        if (
            not isinstance(template_service, ConstructionOrganizationTemplateService)
            or template_service.session is not session
            or getattr(repository, "session", None) is not session
            or not callable(getattr(repository, "get_active_published_candidate", None))
        ):
            raise ValueError("Template verification requires the shared progress session.")
        self.session = session
        self.repository = repository
        self.templates = template_service

    async def verify_step(self, *, actor, step):
        ConstructionOnboardingService._actor(actor)
        if _step(step) is not Step.MEASUREMENT_TEMPLATES:
            raise Unavailable("This verifier handles only measurement templates.")
        authorizer = self.templates.authorizer
        async with authorizer._operation(actor, write=False) as transaction:
            candidate = await self.repository.get_active_published_candidate(
                tenant_id=actor.tenant_id, as_of=authorizer._now())
            authorizer._same_transaction(transaction)
            await authorizer._authorize(actor)
            authorizer._same_transaction(transaction)
            if candidate is None:
                return False
            if (
                not isinstance(candidate, Mapping)
                or set(candidate) != {"tenant_id", "template_id", "version_id"}
                or any(not isinstance(candidate[name], UUID) for name in candidate)
                or candidate["tenant_id"] != actor.tenant_id
            ):
                raise Operation("Canonical template candidate context differs.")

            state = await self.templates.get_version(
                actor=actor, version_id=candidate["version_id"])
            authorizer._same_transaction(transaction)
            if (
                not isinstance(state, OrganizationTemplateVersionState)
                or state.tenant_id != actor.tenant_id
                or state.template_id != candidate["template_id"]
                or state.version_id != candidate["version_id"]
                or state.active is not True
                or not isinstance(state.definition, ValidatedOrganizationTemplateDefinition)
                or state.definition.content_hash != state.content_hash
                or state.definition.source_language != state.source_language
            ):
                raise Operation("Canonical published template differs.")
            # The shared reader validates sections, questions, references,
            # definition schema and canonical content hash. It never follows
            # a newer starter source or creates commercial data.
            await authorizer._authorize(actor)
            authorizer._same_transaction(transaction)
            return True


def build_construction_onboarding_template_verifier(*, session, now_provider=None):
    from database.repositories.construction.construction_onboarding_templates import (
        ConstructionOnboardingTemplateRepository,
    )
    from services.construction.construction_organization_templates import (
        build_construction_organization_template_service,
    )

    return ConstructionOnboardingTemplateVerifier(
        session=session,
        repository=ConstructionOnboardingTemplateRepository(session),
        template_service=build_construction_organization_template_service(
            session=session, now_provider=now_provider))
