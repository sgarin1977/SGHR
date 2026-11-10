"""Canonical draft reads and versioned header edits; no publication."""
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from services.construction.construction_audit import ConstructionAuditChange
from services.construction.construction_foundation import (
    ConstructionRowVersionConflictError, require_expected_row_version,
)
from services.construction.construction_onboarding import (
    ConstructionOnboardingValidationError as Validation,
    ConstructionOnboardingOperationError as Operation,
    _version, MAX_VERSION,
)
from services.construction.construction_template_drafts import (
    ConstructionTemplateDraftService,
    ConstructionTemplateDraftNotFound as NotFound,
    _root, _aware,
)
from services.construction.construction_organization_templates import (
    OrganizationTemplateSectionReference,
    assemble_organization_template_content,
)
from services.construction.construction_organization_template_definitions import (
    ValidatedOrganizationTemplateDefinition,
    OrganizationTemplateDefinitionError,
    validate_organization_template_definition,
)

HEADER_FIELDS = frozenset({
    "source_language", "title_key", "description_key", "labels",
})
VERSION_FIELDS = frozenset({
    "id", "tenant_id", "template_id", "version", "status",
    "source_starter_version_id", "definition_schema_version",
    "source_language", "title_key", "description_key", "labels",
    "content_hash", "row_version", "created_by", "created_at",
    "updated_at", "published_at",
})


@dataclass(frozen=True)
class ConstructionTemplateDraftState:
    tenant_id: UUID
    template_id: UUID
    code: str
    active: bool
    template_row_version: int
    version_id: UUID
    version: int
    version_row_version: int
    source_starter_version_id: UUID | None
    definition_schema_version: int
    source_language: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    definition: ValidatedOrganizationTemplateDefinition
    sections: tuple[OrganizationTemplateSectionReference, ...]

    @property
    def status(self):
        return "DRAFT"

    @property
    def title(self):
        payload = self.definition.payload()
        return payload["labels"][self.source_language][payload["title_key"]]

    @property
    def description(self):
        payload = self.definition.payload()
        key = payload["description_key"]
        return None if key is None else payload["labels"][self.source_language][key]


@dataclass(frozen=True)
class ConstructionTemplateDraftEditResult:
    draft: ConstructionTemplateDraftState
    changed: bool


def _state(snapshot, *, actor, template_id, version_id):
    try:
        if (
            not isinstance(snapshot, dict)
            or set(snapshot) != {"template", "version", "sections", "questions"}
        ):
            raise ValueError()
        root = _root(
            snapshot["template"], tenant_id=actor.tenant_id,
            template_id=template_id)
        row = snapshot["version"]
        if (
            not isinstance(row, dict) or set(row) != VERSION_FIELDS
            or row["tenant_id"] != actor.tenant_id
            or row["template_id"] != template_id or row["id"] != version_id
            or any(not isinstance(row[name], UUID)
                   for name in ("tenant_id", "template_id", "id", "created_by"))
            or row["source_starter_version_id"] is not None
                and not isinstance(row["source_starter_version_id"], UUID)
            or row["status"] != "DRAFT"
            or row["published_at"] is not None or row["content_hash"] is not None
            or type(row["version"]) is not int
            or not 1 <= row["version"] <= 2**31 - 1
            or type(row["definition_schema_version"]) is not int
            or row["definition_schema_version"] != 1
            or not _aware(row["created_at"]) or not _aware(row["updated_at"])
        ):
            raise ValueError()
        version = _version(row["row_version"])
        definition, references = assemble_organization_template_content(
            row,
            {"sections": snapshot["sections"], "questions": snapshot["questions"]},
            tenant_id=actor.tenant_id, version_id=version_id,
            source_language=row["source_language"])
        return ConstructionTemplateDraftState(
            tenant_id=actor.tenant_id, template_id=template_id,
            code=root["code"], active=root["active"],
            template_row_version=root["row_version"],
            version_id=version_id, version=row["version"],
            version_row_version=version,
            source_starter_version_id=row["source_starter_version_id"],
            definition_schema_version=1, source_language=row["source_language"],
            created_by=row["created_by"], created_at=row["created_at"],
            updated_at=row["updated_at"],
            definition=definition, sections=references)
    except (ValueError, TypeError, KeyError):
        raise Operation("Stored draft content is invalid.") from None


class ConstructionTemplateDraftContentService:
    def __init__(self, *, session, repository, draft_service):
        if (
            not isinstance(draft_service, ConstructionTemplateDraftService)
            or draft_service.session is not session
            or getattr(repository, "session", None) is not session
            or any(not callable(getattr(repository, name, None)) for name in (
                "lock_template", "lock_draft", "get_copy", "update_header"))
        ):
            raise ValueError("Draft content requires shared-session collaborators.")
        self.session, self.repository = session, repository
        self.authorizer = draft_service.authorizer

    @staticmethod
    def _ids(template_id, version_id):
        if not isinstance(template_id, UUID) or not isinstance(version_id, UUID):
            raise Validation("Template and draft UUIDs are required.")

    async def _locked(self, *, actor, template_id, version_id, write, transaction):
        arguments = dict(tenant_id=actor.tenant_id, template_id=template_id)
        root = await self.repository.lock_template(**arguments, write=write)
        self.authorizer._same_transaction(transaction)
        await self.authorizer._authorize(actor)
        self.authorizer._same_transaction(transaction)
        if root is None:
            raise NotFound("Organization draft is unavailable.")
        _root(root, tenant_id=actor.tenant_id, template_id=template_id)

        header = await self.repository.lock_draft(
            **arguments, version_id=version_id, write=write)
        self.authorizer._same_transaction(transaction)
        await self.authorizer._authorize(actor)
        self.authorizer._same_transaction(transaction)
        if header is None:
            raise NotFound("Organization draft is unavailable.")

        snapshot = await self.repository.get_copy(
            **arguments, version_id=version_id)
        self.authorizer._same_transaction(transaction)
        if snapshot["template"] != root or snapshot["version"] != header:
            raise Operation("Locked draft context differs.")
        return _state(
            snapshot, actor=actor, template_id=template_id, version_id=version_id)

    async def read_draft(self, *, actor, template_id, version_id):
        self._ids(template_id, version_id)
        async with self.authorizer._operation(actor, write=False) as transaction:
            state = await self._locked(
                actor=actor, template_id=template_id, version_id=version_id,
                write=False, transaction=transaction)
            await self.authorizer._authorize(actor)
            self.authorizer._same_transaction(transaction)
            return state

    async def edit_header(
        self, *, actor, template_id, version_id, expected_row_version,
        fields, trace_id=None, manage_transaction=True,
    ):
        self._ids(template_id, version_id)
        expected = _version(expected_row_version)
        if (
            not isinstance(fields, dict)
            or not set(fields).issubset(HEADER_FIELDS)
            or type(manage_transaction) is not bool
        ):
            raise Validation("Only supported draft header fields are accepted.")
        requested = deepcopy(fields)

        async with self.authorizer._operation(actor, write=True) as transaction:
            before = await self._locked(
                actor=actor, template_id=template_id, version_id=version_id,
                write=True, transaction=transaction)
            require_expected_row_version(
                actual_row_version=before.version_row_version,
                expected_row_version=expected)
            payload = before.definition.payload()
            language = requested.get("source_language", before.source_language)
            candidate = deepcopy(payload)
            candidate.update({
                name: value for name, value in requested.items()
                if name != "source_language"})
            try:
                checked = validate_organization_template_definition(
                    candidate, source_language=language)
            except OrganizationTemplateDefinitionError:
                raise Validation("Draft labels or source language are invalid.") from None

            normalized = checked.payload()
            old_values = {
                "source_language": before.source_language,
                **{name: payload[name] for name in (
                    "title_key", "description_key", "labels")},
            }
            new_values = {
                "source_language": language,
                **{name: normalized[name] for name in (
                    "title_key", "description_key", "labels")},
            }
            changes = {
                name: value for name, value in new_values.items()
                if value != old_values[name]}
            if not changes:
                await self.authorizer._authorize(actor)
                self.authorizer._same_transaction(transaction)
                if manage_transaction:
                    await self.session.commit()
                return ConstructionTemplateDraftEditResult(before, False)
            if before.version_row_version == MAX_VERSION:
                raise Operation("Draft row version is exhausted.")

            arguments = dict(
                tenant_id=actor.tenant_id, template_id=template_id,
                version_id=version_id)
            saved = await self.repository.update_header(
                **arguments, expected_row_version=before.version_row_version,
                fields=changes, as_of=self.authorizer._now())
            self.authorizer._same_transaction(transaction)
            if saved is None:
                raise ConstructionRowVersionConflictError()
            snapshot = await self.repository.get_copy(**arguments)
            self.authorizer._same_transaction(transaction)
            after = _state(
                snapshot, actor=actor, template_id=template_id, version_id=version_id)
            if (
                after.version_row_version != before.version_row_version + 1
                or after.definition != checked
                or replace(
                    after,
                    version_row_version=before.version_row_version,
                    source_language=before.source_language,
                    updated_at=before.updated_at,
                    definition=before.definition,
                ) != before
            ):
                raise Operation("Saved draft header differs.")

            await self.authorizer.audit.record_operation(
                actor=actor,
                event_type="construction_measurement_template_draft_updated",
                entity_type="construction_measurement_template",
                entity_id=template_id, operation="edit_draft_header",
                trace_id=trace_id,
                changes=tuple(
                    ConstructionAuditChange(name, old_values[name], value)
                    for name, value in changes.items()),
                details={
                    "template_version_id": str(version_id),
                    "version": after.version,
                    "previous_row_version": before.version_row_version,
                    "row_version": after.version_row_version,
                })
            self.authorizer._same_transaction(transaction)
            await self.authorizer._authorize(actor)
            self.authorizer._same_transaction(transaction)
            final = await self.repository.get_copy(**arguments)
            self.authorizer._same_transaction(transaction)
            if _state(
                final, actor=actor, template_id=template_id, version_id=version_id
            ) != after:
                raise Operation("Draft changed during audit.")
            if manage_transaction:
                await self.session.commit()
            return ConstructionTemplateDraftEditResult(after, True)


def build_construction_template_draft_content_service(*, session, now_provider=None):
    from database.repositories.construction.construction_template_draft_content import (
        ConstructionTemplateDraftContentRepository,
    )
    from services.construction.construction_template_drafts import (
        build_construction_template_draft_service,
    )
    return ConstructionTemplateDraftContentService(
        session=session,
        repository=ConstructionTemplateDraftContentRepository(session),
        draft_service=build_construction_template_draft_service(
            session=session, now_provider=now_provider))
