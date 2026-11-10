"""Versioned edits of existing draft sections/questions; no publication."""
from copy import deepcopy
from dataclasses import replace
from uuid import UUID

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
    ConstructionTemplateDraftEditResult,
    _state,
)
from services.construction.construction_organization_templates import (
    SECTION_FIELDS, QUESTION_FIELDS, UUID_QUESTION_FIELDS,
)
from services.construction.construction_organization_template_definitions import (
    OrganizationTemplateDefinitionError,
    validate_organization_template_definition,
)

UNSET = object()


def _locate(state, payload, *, kind, item_id):
    for index, reference in enumerate(state.sections):
        section = payload["sections"][index]
        if kind == "SECTION" and reference.id == item_id:
            return section, reference
        if kind == "QUESTION":
            for question, child in zip(section["questions"], reference.questions):
                if child.id == item_id:
                    return question, child
    raise NotFound("Organization draft item is unavailable.")


def _normalized_item(payload, *, kind, code):
    for section in payload["sections"]:
        if kind == "SECTION" and section["code"] == code:
            return section
        if kind == "QUESTION":
            for question in section["questions"]:
                if question["code"] == code:
                    return question
    raise Operation("Normalized draft item is unavailable.")


def _references(before, payload, *, kind, item_id, changed, new_code):
    sections = {}
    questions = {}
    for section in before.sections:
        current = section
        if kind == "SECTION" and section.id == item_id and changed:
            current = replace(
                section, code=new_code, row_version=section.row_version + 1)
        sections[current.code] = current
        for question in section.questions:
            child = question
            if kind == "QUESTION" and question.id == item_id and changed:
                child = replace(
                    question, code=new_code, row_version=question.row_version + 1)
            questions[child.code] = child
    return tuple(
        replace(
            sections[section["code"]],
            questions=tuple(questions[q["code"]] for q in section["questions"]),
        )
        for section in payload["sections"]
    )


class ConstructionTemplateDraftItemService:
    def __init__(self, *, session, repository, content_service):
        if (
            not isinstance(content_service, ConstructionTemplateDraftContentService)
            or content_service.session is not session
            or getattr(repository, "session", None) is not session
            or any(not callable(getattr(repository, name, None)) for name in (
                "advance_draft", "update_item", "get_copy"))
        ):
            raise ValueError("Draft item edits require shared-session collaborators.")
        self.session, self.repository = session, repository
        self.content = content_service
        self.authorizer = content_service.authorizer

    async def edit_item(
        self, *, actor, template_id, version_id, kind, item_id,
        expected_row_version, fields, labels=UNSET,
        trace_id=None, manage_transaction=True,
    ):
        self.content._ids(template_id, version_id)
        expected = _version(expected_row_version)
        allowed = (
            set(SECTION_FIELDS) if kind == "SECTION"
            else set(QUESTION_FIELDS) if kind == "QUESTION"
            else None
        )
        if (
            allowed is None or not isinstance(item_id, UUID)
            or not isinstance(fields, dict)
            or not set(fields).issubset(allowed)
            or type(manage_transaction) is not bool
        ):
            raise Validation("Invalid draft item update.")
        requested = deepcopy(fields)

        async with self.authorizer._operation(actor, write=True) as transaction:
            before = await self.content._locked(
                actor=actor, template_id=template_id, version_id=version_id,
                write=True, transaction=transaction)
            require_expected_row_version(
                actual_row_version=before.version_row_version,
                expected_row_version=expected)
            candidate = before.definition.payload()
            target, reference = _locate(
                before, candidate, kind=kind, item_id=item_id)
            original_item = deepcopy(target)
            target.update(requested)
            new_code = target["code"]
            if labels is not UNSET:
                candidate["labels"] = deepcopy(labels)
            try:
                for section in candidate["sections"]:
                    section["questions"].sort(key=lambda row: row["sort_order"])
                candidate["sections"].sort(key=lambda row: row["sort_order"])
                checked = validate_organization_template_definition(
                    candidate, source_language=before.source_language)
            except (OrganizationTemplateDefinitionError, TypeError, KeyError):
                raise Validation("Draft structure or labels are invalid.") from None
            normalized = checked.payload()
            normalized_item = _normalized_item(
                normalized, kind=kind, code=new_code)
            item_changes = {
                name: normalized_item[name] for name in allowed
                if normalized_item[name] != original_item[name]
            }
            old_labels = before.definition.payload()["labels"]
            label_changes = (
                {} if normalized["labels"] == old_labels
                else {"labels": normalized["labels"]}
            )
            if not item_changes and not label_changes:
                await self.authorizer._authorize(actor)
                self.authorizer._same_transaction(transaction)
                if manage_transaction:
                    await self.session.commit()
                return ConstructionTemplateDraftEditResult(before, False)
            if (
                before.version_row_version == MAX_VERSION
                or item_changes and reference.row_version == MAX_VERSION
            ):
                raise Operation("Draft or item row version is exhausted.")

            arguments = dict(
                tenant_id=actor.tenant_id, template_id=template_id,
                version_id=version_id)
            as_of = self.authorizer._now()
            advanced = await self.repository.advance_draft(
                **arguments,
                expected_row_version=before.version_row_version,
                labels=label_changes, as_of=as_of)
            self.authorizer._same_transaction(transaction)
            if advanced is None:
                raise ConstructionRowVersionConflictError()

            if item_changes:
                values = deepcopy(item_changes)
                if kind == "QUESTION":
                    for name in UUID_QUESTION_FIELDS:
                        if name in values and values[name] is not None:
                            values[name] = UUID(values[name])
                saved = await self.repository.update_item(
                    **arguments, kind=kind, item_id=item_id,
                    expected_item_row_version=reference.row_version,
                    expected_version_row_version=before.version_row_version + 1,
                    fields=values, as_of=as_of)
                self.authorizer._same_transaction(transaction)
                if saved is None:
                    raise ConstructionRowVersionConflictError()

            snapshot = await self.repository.get_copy(**arguments)
            self.authorizer._same_transaction(transaction)
            after = _state(
                snapshot, actor=actor, template_id=template_id, version_id=version_id)
            expected_references = _references(
                before, normalized, kind=kind, item_id=item_id,
                changed=bool(item_changes), new_code=new_code)
            if (
                after.definition != checked
                or after.sections != expected_references
                or after.version_row_version != before.version_row_version + 1
                or replace(
                    after,
                    version_row_version=before.version_row_version,
                    updated_at=before.updated_at,
                    definition=before.definition,
                    sections=before.sections,
                ) != before
            ):
                raise Operation("Saved draft structure differs.")

            changes = [
                ConstructionAuditChange(
                    kind.lower() + "." + name, original_item[name], value)
                for name, value in sorted(item_changes.items())
            ]
            if label_changes:
                changes.append(ConstructionAuditChange(
                    "labels", old_labels, normalized["labels"]))
            await self.authorizer.audit.record_operation(
                actor=actor,
                event_type="construction_measurement_template_draft_updated",
                entity_type="construction_measurement_template",
                entity_id=template_id,
                operation="edit_draft_" + kind.lower(),
                trace_id=trace_id, changes=tuple(changes),
                details={
                    "template_version_id": str(version_id),
                    "item_id": str(item_id),
                    "item_kind": kind,
                    "previous_row_version": before.version_row_version,
                    "row_version": after.version_row_version,
                    "previous_item_row_version": reference.row_version,
                    "item_row_version": reference.row_version + bool(item_changes),
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


def build_construction_template_draft_item_service(*, session, now_provider=None):
    from database.repositories.construction.construction_template_draft_items import (
        ConstructionTemplateDraftItemRepository,
    )
    from services.construction.construction_template_draft_content import (
        build_construction_template_draft_content_service,
    )
    return ConstructionTemplateDraftItemService(
        session=session,
        repository=ConstructionTemplateDraftItemRepository(session),
        content_service=build_construction_template_draft_content_service(
            session=session, now_provider=now_provider))
