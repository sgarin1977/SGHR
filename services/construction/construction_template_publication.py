"""Canonical publication readiness and atomic immutable-version publication."""
from copy import deepcopy
from dataclasses import dataclass
from uuid import UUID

from services.construction.construction_audit import ConstructionAuditChange
from services.construction.construction_foundation import (
    ConstructionRowVersionConflictError, require_expected_row_version,
)
from services.construction.construction_onboarding import (
    ConstructionOnboardingOperationError as Operation,
    ConstructionOnboardingStateConflictError,
    ConstructionOnboardingValidationError as Validation,
    MAX_VERSION, _version,
)
from services.construction.construction_template_draft_content import (
    ConstructionTemplateDraftContentService,
    _state,
)
from services.construction.construction_organization_templates import (
    ConstructionOrganizationTemplateService,
    OrganizationTemplateVersionState,
)
from services.construction.construction_organization_template_definitions import (
    validate_organization_template_definition,
)


class ConstructionTemplateBindingUnavailable(ConstructionOnboardingStateConflictError):
    pass


@dataclass(frozen=True)
class ConstructionTemplatePublicationReadiness:
    tenant_id: UUID
    template_id: UUID
    version_id: UUID
    version: int
    version_row_version: int
    template_row_version: int
    active: bool
    definition_hash: str
    section_count: int
    question_count: int
    binding_count: int


UNCHANGED_FIELDS = (
    "tenant_id", "template_id", "code", "active", "template_row_version",
    "version_id", "version", "source_starter_version_id",
    "definition_schema_version", "source_language",
    "created_by", "created_at", "definition", "sections",
)


def _verify_snapshot(snapshot, *, baseline, published):
    try:
        if (
            not isinstance(snapshot, dict)
            or set(snapshot) != {"template", "version", "sections", "questions"}
            or snapshot["template"] != baseline["template"]
            or snapshot["sections"] != baseline["sections"]
            or snapshot["questions"] != baseline["questions"]
        ):
            raise ValueError()
        expected = deepcopy(baseline["version"])
        expected.update(
            status="PUBLISHED",
            row_version=published.version_row_version,
            content_hash=published.content_hash,
            published_at=published.published_at,
            updated_at=published.updated_at,
        )
        if snapshot["version"] != expected:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise Operation("Published snapshot differs from the locked draft.") from None


class ConstructionTemplatePublicationService:
    def __init__(
        self, *, session, repository, content_service,
        published_service, binding_validator=None,
    ):
        if (
            not isinstance(content_service, ConstructionTemplateDraftContentService)
            or not isinstance(published_service, ConstructionOrganizationTemplateService)
            or content_service.session is not session
            or published_service.session is not session
            or getattr(repository, "session", None) is not session
            or any(not callable(getattr(repository, name, None)) for name in (
                "publish_draft", "get_copy"))
            or binding_validator is not None and (
                getattr(binding_validator, "session", None) is not session
                or not callable(getattr(
                    binding_validator, "validate_template_bindings", None)))
        ):
            raise ValueError("Publication requires shared-session collaborators.")
        self.session, self.repository = session, repository
        self.content, self.published = content_service, published_service
        self.authorizer = content_service.authorizer
        self.binding_validator = binding_validator

    async def _bindings(self, *, actor, definition, transaction):
        count = sum(
            question["work_type_source"] is not None
            for section in definition.payload()["sections"]
            for question in section["questions"])
        if count:
            if self.binding_validator is None:
                raise ConstructionTemplateBindingUnavailable(
                    "Canonical work-type binding validation is unavailable.")
            accepted = await self.binding_validator.validate_template_bindings(
                actor=actor, definition=definition)
            self.authorizer._same_transaction(transaction)
            if accepted is not True:
                raise ConstructionTemplateBindingUnavailable(
                    "Canonical work-type bindings were not confirmed.")
        await self.authorizer._authorize(actor)
        self.authorizer._same_transaction(transaction)
        return count

    async def _ready(self, *, actor, state, transaction):
        if state.version_row_version == MAX_VERSION:
            raise Operation("Template row version is exhausted.")
        checked = validate_organization_template_definition(
            state.definition.canonical_json,
            source_language=state.source_language)
        if checked != state.definition:
            raise Operation("Canonical draft definition differs.")
        count = await self._bindings(
            actor=actor, definition=checked, transaction=transaction)
        return ConstructionTemplatePublicationReadiness(
            tenant_id=state.tenant_id,
            template_id=state.template_id,
            version_id=state.version_id,
            version=state.version,
            version_row_version=state.version_row_version,
            template_row_version=state.template_row_version,
            active=state.active,
            definition_hash=checked.content_hash,
            section_count=len(state.sections),
            question_count=sum(len(section.questions) for section in state.sections),
            binding_count=count)

    async def check_draft(
        self, *, actor, template_id, version_id, expected_row_version,
    ):
        self.content._ids(template_id, version_id)
        expected = _version(expected_row_version)
        async with self.authorizer._operation(actor, write=False) as transaction:
            state = await self.content._locked(
                actor=actor, template_id=template_id, version_id=version_id,
                write=False, transaction=transaction)
            require_expected_row_version(
                actual_row_version=state.version_row_version,
                expected_row_version=expected)
            return await self._ready(
                actor=actor, state=state, transaction=transaction)

    async def publish_draft(
        self, *, actor, template_id, version_id, expected_row_version,
        trace_id=None, manage_transaction=True,
    ):
        self.content._ids(template_id, version_id)
        expected = _version(expected_row_version)
        if type(manage_transaction) is not bool:
            raise Validation("Transaction mode is invalid.")
        async with self.authorizer._operation(actor, write=True) as transaction:
            before = await self.content._locked(
                actor=actor, template_id=template_id, version_id=version_id,
                write=True, transaction=transaction)
            require_expected_row_version(
                actual_row_version=before.version_row_version,
                expected_row_version=expected)
            ready = await self._ready(
                actor=actor, state=before, transaction=transaction)
            arguments = dict(
                tenant_id=actor.tenant_id, template_id=template_id,
                version_id=version_id)
            baseline = await self.repository.get_copy(**arguments)
            self.authorizer._same_transaction(transaction)
            if _state(
                baseline, actor=actor, template_id=template_id, version_id=version_id
            ) != before:
                raise Operation("Draft changed before publication.")

            saved = await self.repository.publish_draft(
                **arguments,
                expected_row_version=before.version_row_version,
                content_hash=ready.definition_hash,
                as_of=self.authorizer._now())
            self.authorizer._same_transaction(transaction)
            if saved is None:
                raise ConstructionRowVersionConflictError()
            published = await self.published.get_version(
                actor=actor, version_id=version_id)
            self.authorizer._same_transaction(transaction)
            if (
                not isinstance(published, OrganizationTemplateVersionState)
                or any(getattr(published, name) != getattr(before, name)
                       for name in UNCHANGED_FIELDS)
                or published.version_row_version != before.version_row_version + 1
                or published.content_hash != ready.definition_hash
                or published.updated_at != published.published_at
            ):
                raise Operation("Published version differs.")
            snapshot = await self.repository.get_copy(**arguments)
            self.authorizer._same_transaction(transaction)
            _verify_snapshot(snapshot, baseline=baseline, published=published)

            await self.authorizer.audit.record_operation(
                actor=actor,
                event_type="construction_measurement_template_published",
                entity_type="construction_measurement_template",
                entity_id=template_id, operation="publish_template_version",
                trace_id=trace_id,
                changes=(
                    ConstructionAuditChange("status", "DRAFT", "PUBLISHED"),
                    ConstructionAuditChange(
                        "content_hash", None, published.content_hash),
                ),
                details={
                    "template_version_id": str(version_id),
                    "version": published.version,
                    "previous_row_version": before.version_row_version,
                    "row_version": published.version_row_version,
                    "section_count": ready.section_count,
                    "question_count": ready.question_count,
                    "binding_count": ready.binding_count,
                })
            self.authorizer._same_transaction(transaction)
            # The trusted validator uses the same transaction and must retain
            # canonical work identity/version guarantees until the caller commits.
            await self._bindings(
                actor=actor, definition=before.definition, transaction=transaction)
            final = await self.repository.get_copy(**arguments)
            self.authorizer._same_transaction(transaction)
            _verify_snapshot(final, baseline=baseline, published=published)
            await self.authorizer._authorize(actor)
            self.authorizer._same_transaction(transaction)
            if manage_transaction:
                await self.session.commit()
            return published


def build_construction_template_publication_service(
    *, session, now_provider=None, binding_validator=None,
):
    from database.repositories.construction.construction_template_publication import (
        ConstructionTemplatePublicationRepository,
    )
    from services.construction.construction_template_draft_content import (
        build_construction_template_draft_content_service,
    )
    from services.construction.construction_organization_templates import (
        build_construction_organization_template_service,
    )
    return ConstructionTemplatePublicationService(
        session=session,
        repository=ConstructionTemplatePublicationRepository(session),
        content_service=build_construction_template_draft_content_service(
            session=session, now_provider=now_provider),
        published_service=build_construction_organization_template_service(
            session=session, now_provider=now_provider),
        binding_validator=binding_validator)
