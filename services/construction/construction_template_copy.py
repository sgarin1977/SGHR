"""Authorized exact-version copying; no HTTP or idempotency activation."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import re
from uuid import UUID, uuid4

from services.construction.construction_audit import ConstructionAuditChange
from services.construction.construction_onboarding import (
    ConstructionOnboardingValidationError,
    ConstructionOnboardingOperationError,
    ConstructionOnboardingStateConflictError,
)
from services.construction.construction_starter_template_definitions import (
    Question, validate_starter_definition,
)
from services.construction.construction_starter_templates import (
    ConstructionStarterTemplateService,
    ConstructionStarterTemplateVersionState,
    build_construction_starter_template_service,
)

QUESTION_FIELDS = tuple(Question.model_fields)
SECTION_FIELDS = ("code", "label_key", "work_phase_code", "sort_order", "active")
UUID_QUESTION_FIELDS = ("work_type_id", "pinned_work_type_version_id")


class ConstructionTemplateCopyValidationError(ConstructionOnboardingValidationError):
    pass


class ConstructionTemplateCopyConflictError(ConstructionOnboardingStateConflictError):
    pass


class ConstructionTemplateCopyOperationError(ConstructionOnboardingOperationError):
    pass


class ConstructionTemplateCopyBindingUnavailable(ConstructionTemplateCopyConflictError):
    pass


@dataclass(frozen=True)
class ConstructionTemplateCopyResult:
    tenant_id: UUID
    template_id: UUID
    code: str
    template_row_version: int
    template_version_id: UUID
    version: int
    version_row_version: int
    source_starter_version_id: UUID
    definition_schema_version: int
    source_language: str
    content_hash: str
    section_count: int
    question_count: int
    created_by: UUID
    created_at: datetime
    published_at: datetime


def _aware(value):
    return (
        isinstance(value, datetime) and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _plan(*, actor, source, code, instant):
    payload = source.definition.payload()
    reserved = {
        actor.tenant_id, actor.user_id, source.template_id, source.version_id,
    }

    def identity():
        value = uuid4()
        if not isinstance(value, UUID) or value in reserved:
            raise ConstructionTemplateCopyOperationError("New template identity is invalid.")
        reserved.add(value)
        return value

    template_id, version_id = identity(), identity()
    template = dict(
        id=template_id, tenant_id=actor.tenant_id, code=code, active=True,
        row_version=1, created_by=actor.user_id,
        created_at=instant, updated_at=instant)
    version = dict(
        id=version_id, tenant_id=actor.tenant_id, template_id=template_id,
        version=1, status="DRAFT", source_starter_version_id=source.version_id,
        definition_schema_version=source.definition_schema_version,
        source_language=source.source_language,
        title_key=payload["title_key"], description_key=payload["description_key"],
        labels=deepcopy(payload["labels"]), content_hash=None,
        row_version=1, created_by=actor.user_id, created_at=instant,
        updated_at=instant, published_at=None)
    sections, questions = [], []
    for section in payload["sections"]:
        section_id = identity()
        sections.append(
            {key: section[key] for key in SECTION_FIELDS}
            | dict(
                id=section_id, tenant_id=actor.tenant_id,
                template_version_id=version_id, row_version=1,
                created_at=instant, updated_at=instant))
        for question in section["questions"]:
            fields = deepcopy(question)
            for key in UUID_QUESTION_FIELDS:
                if fields[key] is not None:
                    fields[key] = UUID(fields[key])
            questions.append(fields | dict(
                id=identity(), tenant_id=actor.tenant_id,
                template_version_id=version_id, template_section_id=section_id,
                row_version=1, created_at=instant, updated_at=instant))
    return dict(
        template=template, version=version,
        sections=tuple(sections), questions=tuple(questions))


def _verify(snapshot, *, plan, source, published):
    try:
        if not isinstance(snapshot, dict) or set(snapshot) != {
            "template", "version", "sections", "questions",
        }:
            raise ValueError()
        if snapshot["template"] != plan["template"]:
            raise ValueError()
        if snapshot["template"]["active"] is not True:
            raise ValueError()
        version = snapshot["version"]
        expected = deepcopy(plan["version"])
        if published:
            if (
                not isinstance(version, dict)
                or not _aware(version.get("published_at"))
                or version.get("updated_at") != version["published_at"]
            ):
                raise ValueError()
            expected.update(
                status="PUBLISHED", content_hash=source.content_hash,
                row_version=2, published_at=version["published_at"],
                updated_at=version["updated_at"])
        if version != expected:
            raise ValueError()
        if (
            type(version["version"]) is not int
            or type(version["definition_schema_version"]) is not int
        ):
            raise ValueError()
        all_rows = [snapshot["template"], version]
        for name in ("sections", "questions"):
            rows = snapshot[name]
            expected_rows = plan[name]
            if not isinstance(rows, (list, tuple)) or len(rows) != len(expected_rows):
                raise ValueError()
            actual_by_id = {row["id"]: row for row in rows}
            expected_by_id = {row["id"]: row for row in expected_rows}
            if len(actual_by_id) != len(rows) or actual_by_id != expected_by_id:
                raise ValueError()
            all_rows.extend(rows)
        if any(
            type(row["row_version"]) is not int
            or not _aware(row["created_at"]) or not _aware(row["updated_at"])
            for row in all_rows
        ):
            raise ValueError()

        actual_sections = {row["id"]: row for row in snapshot["sections"]}
        actual_questions = {row["id"]: row for row in snapshot["questions"]}
        definition_sections = []
        for planned_section in plan["sections"]:
            section = actual_sections[planned_section["id"]]
            questions = []
            for planned_question in plan["questions"]:
                if planned_question["template_section_id"] != section["id"]:
                    continue
                stored = actual_questions[planned_question["id"]]
                question = {key: stored[key] for key in QUESTION_FIELDS}
                for key in UUID_QUESTION_FIELDS:
                    if question[key] is not None:
                        question[key] = str(question[key])
                questions.append(question)
            definition_sections.append(
                {key: section[key] for key in SECTION_FIELDS}
                | {"questions": questions})
        definition = validate_starter_definition({
            "title_key": version["title_key"],
            "description_key": version["description_key"],
            "labels": version["labels"], "sections": definition_sections,
        }, source_language=version["source_language"])
        if definition.canonical_json != source.definition.canonical_json:
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ConstructionTemplateCopyOperationError(
            "Stored template copy differs from its exact source.") from None


class ConstructionTemplateCopyService:
    def __init__(
        self, *, session, repository, starter_service, binding_validator=None,
    ):
        if (
            not isinstance(starter_service, ConstructionStarterTemplateService)
            or starter_service.session is not session
            or getattr(repository, "session", None) is not session
            or any(not callable(getattr(repository, name, None)) for name in (
                "create_template", "create_version", "insert_content",
                "publish", "get_copy"))
            or binding_validator is not None and (
                getattr(binding_validator, "session", None) is not session
                or not callable(getattr(
                    binding_validator, "validate_template_bindings", None)))
        ):
            raise ValueError("Template copying requires shared-session collaborators.")
        self.session, self.repository = session, repository
        self.starters, self.authorizer = starter_service, starter_service.authorizer
        self.binding_validator = binding_validator

    async def copy_template(
        self, *, actor, source_version_id, code=None,
        trace_id=None, manage_transaction=True,
    ):
        if (
            not isinstance(source_version_id, UUID)
            or type(manage_transaction) is not bool
            or code is not None and (
                not isinstance(code, str)
                or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code) is None)
        ):
            raise ConstructionTemplateCopyValidationError("Invalid template copy request.")

        async with self.authorizer._operation(actor, write=True) as transaction:
            source = await self.starters.get_version(
                actor=actor, version_id=source_version_id)
            self.authorizer._same_transaction(transaction)
            if (
                not isinstance(source, ConstructionStarterTemplateVersionState)
                or source.version_id != source_version_id
            ):
                raise ConstructionTemplateCopyOperationError("Source version context differs.")

            has_bindings = any(
                question["work_type_source"] is not None
                for section in source.definition.payload()["sections"]
                for question in section["questions"])
            if has_bindings:
                if self.binding_validator is None:
                    raise ConstructionTemplateCopyBindingUnavailable(
                        "Canonical work-type binding validation is unavailable.")
                accepted = await self.binding_validator.validate_template_bindings(
                    actor=actor, definition=source.definition)
                self.authorizer._same_transaction(transaction)
                if accepted is not True:
                    raise ConstructionTemplateCopyBindingUnavailable(
                        "Canonical work-type bindings were not confirmed.")
                await self.authorizer._authorize(actor)
                self.authorizer._same_transaction(transaction)

            plan = _plan(
                actor=actor, source=source, code=source.code if code is None else code,
                instant=self.authorizer._now())
            template_id = plan["template"]["id"]
            version_id = plan["version"]["id"]
            identity = await self.repository.create_template(
                tenant_id=actor.tenant_id, row=deepcopy(plan["template"]))
            self.authorizer._same_transaction(transaction)
            await self.authorizer._authorize(actor)
            self.authorizer._same_transaction(transaction)
            if identity is None:
                raise ConstructionTemplateCopyConflictError(
                    "Template code already exists in this organization.")

            await self.repository.create_version(
                tenant_id=actor.tenant_id, template_id=template_id,
                row=deepcopy(plan["version"]))
            self.authorizer._same_transaction(transaction)
            await self.repository.insert_content(
                tenant_id=actor.tenant_id, version_id=version_id,
                sections=deepcopy(plan["sections"]),
                questions=deepcopy(plan["questions"]))
            self.authorizer._same_transaction(transaction)
            await self.authorizer._authorize(actor)
            self.authorizer._same_transaction(transaction)

            arguments = dict(
                tenant_id=actor.tenant_id,
                template_id=template_id, version_id=version_id)
            draft = await self.repository.get_copy(**arguments)
            self.authorizer._same_transaction(transaction)
            _verify(draft, plan=plan, source=source, published=False)
            saved = await self.repository.publish(
                **arguments, content_hash=source.content_hash,
                as_of=self.authorizer._now())
            self.authorizer._same_transaction(transaction)
            if saved is None:
                raise ConstructionTemplateCopyOperationError("Draft publication failed.")
            stored = await self.repository.get_copy(**arguments)
            self.authorizer._same_transaction(transaction)
            _verify(stored, plan=plan, source=source, published=True)

            await self.authorizer.audit.record_operation(
                actor=actor, event_type="construction_measurement_template_copied",
                entity_type="construction_measurement_template",
                entity_id=template_id, operation="copy_starter", trace_id=trace_id,
                changes=(
                    ConstructionAuditChange("template_id", None, str(template_id)),
                    ConstructionAuditChange("template_version_id", None, str(version_id)),
                    ConstructionAuditChange("code", None, plan["template"]["code"]),
                ),
                details={
                    "source_starter_template_id": str(source.template_id),
                    "source_starter_version_id": str(source.version_id),
                    "source_content_hash": source.content_hash,
                    "template_version_id": str(version_id),
                    "version": 1,
                    "template_row_version": 1,
                    "version_row_version": 2,
                    "section_count": len(plan["sections"]),
                    "question_count": len(plan["questions"]),
                    "section_ids": [str(row["id"]) for row in plan["sections"]],
                    "question_ids": [str(row["id"]) for row in plan["questions"]],
                })
            self.authorizer._same_transaction(transaction)
            await self.authorizer._authorize(actor)
            self.authorizer._same_transaction(transaction)
            fresh_source = await self.starters.get_version(
                actor=actor, version_id=source_version_id)
            self.authorizer._same_transaction(transaction)
            if fresh_source != source:
                raise ConstructionTemplateCopyConflictError("Source version changed.")

            result = ConstructionTemplateCopyResult(
                tenant_id=actor.tenant_id, template_id=template_id,
                code=plan["template"]["code"], template_row_version=1,
                template_version_id=version_id, version=1, version_row_version=2,
                source_starter_version_id=source.version_id,
                definition_schema_version=source.definition_schema_version,
                source_language=source.source_language, content_hash=source.content_hash,
                section_count=len(plan["sections"]), question_count=len(plan["questions"]),
                created_by=actor.user_id, created_at=plan["template"]["created_at"],
                published_at=stored["version"]["published_at"])
            if manage_transaction:
                await self.session.commit()
            return result


def build_construction_template_copy_service(
    *, session, now_provider=None, binding_validator=None,
):
    from database.repositories.construction.construction_template_copy import (
        ConstructionTemplateCopyRepository,
    )
    return ConstructionTemplateCopyService(
        session=session, repository=ConstructionTemplateCopyRepository(session),
        starter_service=build_construction_starter_template_service(
            session=session, now_provider=now_provider),
        binding_validator=binding_validator)
