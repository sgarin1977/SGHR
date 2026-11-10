"""Append validated draft structure with server UUIDs and atomic audit."""
from copy import deepcopy
from dataclasses import dataclass, replace
from uuid import UUID, uuid4

from services.construction.construction_audit import ConstructionAuditChange
from services.construction.construction_foundation import (
    ConstructionRowVersionConflictError, require_expected_row_version,
)
from services.construction.construction_onboarding import (
    ConstructionOnboardingValidationError as Validation,
    ConstructionOnboardingOperationError as Operation,
    MAX_VERSION, _version,
)
from services.construction.construction_template_drafts import (
    ConstructionTemplateDraftNotFound as NotFound,
)
from services.construction.construction_template_draft_content import (
    ConstructionTemplateDraftContentService,
    ConstructionTemplateDraftState,
    _state,
)
from services.construction.construction_organization_templates import (
    OrganizationTemplateSectionReference as SectionReference,
    OrganizationTemplateQuestionReference as QuestionReference,
    SECTION_FIELDS, UUID_QUESTION_FIELDS,
)
from services.construction.construction_organization_template_definitions import (
    OrganizationTemplateDefinitionError,
    validate_organization_template_definition,
)

UNSET = object()
MAX_ORDER = 2**31 - 1


@dataclass(frozen=True)
class ConstructionTemplateDraftAppendResult:
    draft: ConstructionTemplateDraftState
    section_ids: tuple[UUID, ...]
    question_ids: tuple[UUID, ...]


def _section(state, section_id):
    for reference in state.sections:
        if reference.id == section_id:
            return reference
    raise NotFound("Organization draft section is unavailable.")


def _plan(before, payload, *, actor, kind, section_id, code, instant):
    reserved = {
        actor.user_id, before.tenant_id, before.template_id,
        before.version_id, before.created_by,
    }
    if before.source_starter_version_id is not None:
        reserved.add(before.source_starter_version_id)
    for reference in before.sections:
        reserved.add(reference.id)
        reserved.update(question.id for question in reference.questions)

    def identity():
        value = uuid4()
        if not isinstance(value, UUID) or value in reserved:
            raise Operation("New draft item identity is invalid.")
        reserved.add(value)
        return value

    sections, questions = [], []
    section_refs = {reference.code: reference for reference in before.sections}
    question_refs = {
        question.code: question
        for reference in before.sections for question in reference.questions
    }

    if kind == "SECTION":
        definition = next(row for row in payload["sections"] if row["code"] == code)
        new_section_id = identity()
        sections.append({
            **{name: deepcopy(definition[name]) for name in SECTION_FIELDS},
            "id": new_section_id,
            "tenant_id": before.tenant_id,
            "template_version_id": before.version_id,
            "row_version": 1,
            "created_at": instant,
            "updated_at": instant,
        })
        section_refs[code] = SectionReference(new_section_id, code, 1, ())
        definitions = definition["questions"]
        parent_id = new_section_id
    else:
        reference = _section(before, section_id)
        definition = next(
            row for row in payload["sections"] if row["code"] == reference.code)
        definitions = [next(row for row in definition["questions"] if row["code"] == code)]
        parent_id = section_id

    for question in definitions:
        values = deepcopy(question)
        for name in UUID_QUESTION_FIELDS:
            if values[name] is not None:
                values[name] = UUID(values[name])
        new_id = identity()
        questions.append(values | {
            "id": new_id,
            "tenant_id": before.tenant_id,
            "template_version_id": before.version_id,
            "template_section_id": parent_id,
            "row_version": 1,
            "created_at": instant,
            "updated_at": instant,
        })
        question_refs[question["code"]] = QuestionReference(
            new_id, question["code"], 1)

    if any(row["sort_order"] > MAX_ORDER for row in (*sections, *questions)):
        raise Validation("Draft ordering exceeds the storage range.")

    references = tuple(
        replace(
            section_refs[section["code"]],
            questions=tuple(
                question_refs[question["code"]]
                for question in section["questions"]),
        )
        for section in payload["sections"]
    )
    return tuple(sections), tuple(questions), references


def _verify_rows(snapshot, *, baseline, sections, questions):
    try:
        if snapshot["template"] != baseline["template"]:
            raise ValueError()
        for name, additions in (("sections", sections), ("questions", questions)):
            expected = {row["id"]: row for row in baseline[name]}
            for row in additions:
                if row["id"] in expected:
                    raise ValueError()
                expected[row["id"]] = row
            actual_rows = snapshot[name]
            actual = {row["id"]: row for row in actual_rows}
            if len(actual) != len(actual_rows) or actual != expected:
                raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise Operation("Stored appended structure differs.") from None


class ConstructionTemplateDraftAppendService:
    def __init__(self, *, session, repository, content_service):
        if (
            not isinstance(content_service, ConstructionTemplateDraftContentService)
            or content_service.session is not session
            or getattr(repository, "session", None) is not session
            or any(not callable(getattr(repository, name, None)) for name in (
                "advance_draft", "insert_content", "get_copy"))
        ):
            raise ValueError("Draft additions require shared-session collaborators.")
        self.session, self.repository = session, repository
        self.content = content_service
        self.authorizer = content_service.authorizer

    async def append_item(
        self, *, actor, template_id, version_id, expected_row_version,
        kind, body, section_id=None, labels=UNSET,
        trace_id=None, manage_transaction=True,
    ):
        self.content._ids(template_id, version_id)
        expected = _version(expected_row_version)
        if (
            kind not in ("SECTION", "QUESTION")
            or not isinstance(body, dict)
            or kind == "SECTION" and section_id is not None
            or kind == "QUESTION" and not isinstance(section_id, UUID)
            or type(manage_transaction) is not bool
        ):
            raise Validation("Invalid draft addition.")
        requested = deepcopy(body)

        async with self.authorizer._operation(actor, write=True) as transaction:
            before = await self.content._locked(
                actor=actor, template_id=template_id, version_id=version_id,
                write=True, transaction=transaction)
            require_expected_row_version(
                actual_row_version=before.version_row_version,
                expected_row_version=expected)
            if before.version_row_version == MAX_VERSION:
                raise Operation("Draft row version is exhausted.")

            candidate = before.definition.payload()
            if kind == "SECTION":
                candidate["sections"].append(requested)
            else:
                reference = _section(before, section_id)
                parent = next(
                    row for row in candidate["sections"]
                    if row["code"] == reference.code)
                parent["questions"].append(requested)
            if labels is not UNSET:
                candidate["labels"] = deepcopy(labels)
            try:
                for section in candidate["sections"]:
                    section["questions"].sort(key=lambda row: row["sort_order"])
                candidate["sections"].sort(key=lambda row: row["sort_order"])
                checked = validate_organization_template_definition(
                    candidate, source_language=before.source_language)
            except (OrganizationTemplateDefinitionError, TypeError, KeyError):
                raise Validation("New draft structure or labels are invalid.") from None

            normalized = checked.payload()
            sections, questions, references = _plan(
                before, normalized, actor=actor, kind=kind,
                section_id=section_id, code=requested["code"],
                instant=self.authorizer._now())
            arguments = dict(
                tenant_id=actor.tenant_id, template_id=template_id,
                version_id=version_id)
            baseline = await self.repository.get_copy(**arguments)
            self.authorizer._same_transaction(transaction)
            if _state(
                baseline, actor=actor, template_id=template_id, version_id=version_id
            ) != before:
                raise Operation("Draft changed before append.")
            old_labels = before.definition.payload()["labels"]
            label_update = (
                {} if normalized["labels"] == old_labels
                else {"labels": normalized["labels"]}
            )
            advanced = await self.repository.advance_draft(
                **arguments,
                expected_row_version=before.version_row_version,
                labels=label_update, as_of=self.authorizer._now())
            self.authorizer._same_transaction(transaction)
            if advanced is None:
                raise ConstructionRowVersionConflictError()

            await self.repository.insert_content(
                tenant_id=actor.tenant_id, version_id=version_id,
                sections=sections, questions=questions)
            self.authorizer._same_transaction(transaction)
            await self.authorizer._authorize(actor)
            self.authorizer._same_transaction(transaction)
            snapshot = await self.repository.get_copy(**arguments)
            self.authorizer._same_transaction(transaction)
            _verify_rows(
                snapshot, baseline=baseline, sections=sections, questions=questions)
            after = _state(
                snapshot, actor=actor, template_id=template_id, version_id=version_id)
            if (
                after.definition != checked or after.sections != references
                or after.version_row_version != before.version_row_version + 1
                or replace(
                    after,
                    version_row_version=before.version_row_version,
                    updated_at=before.updated_at,
                    definition=before.definition,
                    sections=before.sections,
                ) != before
            ):
                raise Operation("Saved draft addition differs.")

            section_ids = tuple(row["id"] for row in sections)
            question_ids = tuple(row["id"] for row in questions)
            await self.authorizer.audit.record_operation(
                actor=actor,
                event_type="construction_measurement_template_draft_updated",
                entity_type="construction_measurement_template",
                entity_id=template_id,
                operation="add_draft_" + kind.lower(),
                trace_id=trace_id,
                changes=tuple(
                    ConstructionAuditChange(name, None, str(identity))
                    for name, identities in (
                        ("section_id", section_ids), ("question_id", question_ids))
                    for identity in identities),
                details={
                    "template_version_id": str(version_id),
                    "parent_section_id": None if section_id is None else str(section_id),
                    "new_section_ids": [str(value) for value in section_ids],
                    "new_question_ids": [str(value) for value in question_ids],
                    "previous_row_version": before.version_row_version,
                    "row_version": after.version_row_version,
                    "definition_hash_before": before.definition.content_hash,
                    "definition_hash_after": after.definition.content_hash,
                })
            self.authorizer._same_transaction(transaction)
            await self.authorizer._authorize(actor)
            self.authorizer._same_transaction(transaction)
            final = await self.repository.get_copy(**arguments)
            self.authorizer._same_transaction(transaction)
            _verify_rows(
                final, baseline=baseline, sections=sections, questions=questions)
            if _state(
                final, actor=actor, template_id=template_id, version_id=version_id
            ) != after:
                raise Operation("Draft changed during append audit.")
            if manage_transaction:
                await self.session.commit()
            return ConstructionTemplateDraftAppendResult(
                after, section_ids, question_ids)


def build_construction_template_draft_append_service(*, session, now_provider=None):
    from database.repositories.construction.construction_template_draft_items import (
        ConstructionTemplateDraftItemRepository,
    )
    from services.construction.construction_template_draft_content import (
        build_construction_template_draft_content_service,
    )
    return ConstructionTemplateDraftAppendService(
        session=session,
        repository=ConstructionTemplateDraftItemRepository(session),
        content_service=build_construction_template_draft_content_service(
            session=session, now_provider=now_provider))
