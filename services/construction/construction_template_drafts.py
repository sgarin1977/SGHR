"""Authorized draft creation; published versions and root metadata stay intact."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import re
from uuid import UUID, uuid4

from services.construction.construction_audit import ConstructionAuditChange
from services.construction.construction_foundation import require_expected_row_version
from services.construction.construction_onboarding import (
    ConstructionOnboardingValidationError as Validation,
    ConstructionOnboardingOperationError as Operation,
    ConstructionOnboardingStateConflictError,
    _version,
)
from services.construction.construction_organization_templates import (
    ConstructionOrganizationTemplateService,
    OrganizationTemplateVersionState,
    SECTION_FIELDS, UUID_QUESTION_FIELDS,
)
from services.construction.construction_organization_template_definitions import (
    validate_organization_template_definition,
)

MAX_NUMBER = 2**31 - 1


class ConstructionTemplateDraftNotFound(ConstructionOnboardingStateConflictError):
    pass


@dataclass(frozen=True)
class ConstructionTemplateDraftResult:
    tenant_id: UUID
    template_id: UUID
    template_row_version: int
    base_version_id: UUID
    version_id: UUID
    version: int
    version_row_version: int
    status: str
    source_starter_version_id: UUID | None
    source_language: str
    section_count: int
    question_count: int
    created_by: UUID
    created_at: datetime


def _aware(value):
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _root(row, *, tenant_id, template_id):
    try:
        if (
            not isinstance(row, dict)
            or set(row) != {
                "id", "tenant_id", "code", "active", "row_version",
                "created_by", "created_at", "updated_at",
            }
            or row["id"] != template_id or row["tenant_id"] != tenant_id
            or any(not isinstance(row[key], UUID)
                   for key in ("id", "tenant_id", "created_by"))
            or type(row["active"]) is not bool
            or not isinstance(row["code"], str)
            or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", row["code"]) is None
            or not all(_aware(row[key]) for key in ("created_at", "updated_at"))
        ):
            raise ValueError()
        _version(row["row_version"])
        return deepcopy(row)
    except (ValueError, TypeError, KeyError):
        raise Operation("Stored template identity is invalid.") from None


def _plan(*, actor, root, source, number, instant):
    checked = validate_organization_template_definition(
        source.definition.canonical_json,
        source_language=source.source_language)
    if checked.content_hash != source.content_hash:
        raise Operation("Base template definition differs.")
    payload = checked.payload()
    reserved = {
        actor.tenant_id, actor.user_id, root["id"], source.version_id,
    }
    for section in source.sections:
        reserved.add(section.id)
        reserved.update(question.id for question in section.questions)
    if source.source_starter_version_id is not None:
        reserved.add(source.source_starter_version_id)

    def identity():
        value = uuid4()
        if not isinstance(value, UUID) or value in reserved:
            raise Operation("New draft identity is invalid.")
        reserved.add(value)
        return value

    version_id = identity()
    version = dict(
        id=version_id, tenant_id=actor.tenant_id, template_id=root["id"],
        version=number, status="DRAFT",
        source_starter_version_id=source.source_starter_version_id,
        definition_schema_version=1, source_language=source.source_language,
        title_key=payload["title_key"], description_key=payload["description_key"],
        labels=deepcopy(payload["labels"]), content_hash=None,
        row_version=1, created_by=actor.user_id,
        created_at=instant, updated_at=instant, published_at=None)
    sections, questions = [], []
    for section in payload["sections"]:
        section_id = identity()
        sections.append(
            {name: deepcopy(section[name]) for name in SECTION_FIELDS}
            | dict(
                id=section_id, tenant_id=actor.tenant_id,
                template_version_id=version_id, row_version=1,
                created_at=instant, updated_at=instant))
        for question in section["questions"]:
            fields = deepcopy(question)
            for name in UUID_QUESTION_FIELDS:
                if fields[name] is not None:
                    fields[name] = UUID(fields[name])
            questions.append(fields | dict(
                id=identity(), tenant_id=actor.tenant_id,
                template_version_id=version_id,
                template_section_id=section_id, row_version=1,
                created_at=instant, updated_at=instant))
    return dict(
        template=deepcopy(root), version=version,
        sections=tuple(sections), questions=tuple(questions))


def _verify(snapshot, *, plan):
    try:
        if (
            not isinstance(snapshot, dict) or set(snapshot) != set(plan)
            or snapshot["template"] != plan["template"]
            or snapshot["version"] != plan["version"]
        ):
            raise ValueError()
        for name in ("sections", "questions"):
            rows = snapshot[name]
            if not isinstance(rows, (tuple, list)) or len(rows) != len(plan[name]):
                raise ValueError()
            expected = {row["id"]: row for row in plan[name]}
            actual = {row["id"]: row for row in rows}
            if len(actual) != len(rows) or actual != expected:
                raise ValueError()
        for row in (
            snapshot["version"], *snapshot["sections"], *snapshot["questions"],
        ):
            if type(row["row_version"]) is not int or row["row_version"] != 1:
                raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise Operation("Stored draft differs from its creation plan.") from None


class ConstructionTemplateDraftService:
    def __init__(self, *, session, repository, template_service):
        if (
            not isinstance(template_service, ConstructionOrganizationTemplateService)
            or template_service.session is not session
            or getattr(repository, "session", None) is not session
            or any(not callable(getattr(repository, name, None)) for name in (
                "get_template_for_update", "latest_version_number",
                "create_version", "insert_content", "get_copy",
            ))
        ):
            raise ValueError("Draft creation requires shared-session collaborators.")
        self.session, self.repository = session, repository
        self.templates = template_service
        self.authorizer = template_service.authorizer

    async def create_draft(
        self, *, actor, template_id, base_version_id,
        expected_template_row_version, trace_id=None,
        manage_transaction=True,
    ):
        if (
            not isinstance(template_id, UUID)
            or not isinstance(base_version_id, UUID)
            or type(manage_transaction) is not bool
        ):
            raise Validation("Invalid template draft request.")
        expected = _version(expected_template_row_version)

        async with self.authorizer._operation(actor, write=True) as transaction:
            row = await self.repository.get_template_for_update(
                tenant_id=actor.tenant_id, template_id=template_id)
            self.authorizer._same_transaction(transaction)
            await self.authorizer._authorize(actor)
            self.authorizer._same_transaction(transaction)
            if row is None:
                raise ConstructionTemplateDraftNotFound(
                    "Organization template is unavailable.")
            root = _root(
                row, tenant_id=actor.tenant_id, template_id=template_id)
            require_expected_row_version(
                actual_row_version=root["row_version"],
                expected_row_version=expected)

            source = await self.templates.get_version(
                actor=actor, version_id=base_version_id)
            self.authorizer._same_transaction(transaction)
            if (
                not isinstance(source, OrganizationTemplateVersionState)
                or source.tenant_id != actor.tenant_id
                or source.version_id != base_version_id
                or source.template_id != template_id
            ):
                raise ConstructionTemplateDraftNotFound(
                    "Organization template is unavailable.")

            maximum = await self.repository.latest_version_number(
                tenant_id=actor.tenant_id, template_id=template_id)
            self.authorizer._same_transaction(transaction)
            if (
                type(maximum) is not int
                or not source.version <= maximum < MAX_NUMBER
            ):
                raise Operation("Template version allocation is unavailable.")
            await self.authorizer._authorize(actor)
            self.authorizer._same_transaction(transaction)

            plan = _plan(
                actor=actor, root=root, source=source,
                number=maximum + 1, instant=self.authorizer._now())
            version_id = plan["version"]["id"]
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
                tenant_id=actor.tenant_id, template_id=template_id,
                version_id=version_id)
            stored = await self.repository.get_copy(**arguments)
            self.authorizer._same_transaction(transaction)
            _verify(stored, plan=plan)
            await self.authorizer.audit.record_operation(
                actor=actor,
                event_type="construction_measurement_template_draft_created",
                entity_type="construction_measurement_template",
                entity_id=template_id, operation="create_draft",
                trace_id=trace_id,
                changes=(ConstructionAuditChange(
                    "template_version_id", None, str(version_id)),),
                details={
                    "base_version_id": str(base_version_id),
                    "base_content_hash": source.content_hash,
                    "template_version_id": str(version_id),
                    "version": maximum + 1,
                    "template_row_version": root["row_version"],
                    "version_row_version": 1,
                    "section_count": len(plan["sections"]),
                    "question_count": len(plan["questions"]),
                })
            self.authorizer._same_transaction(transaction)
            await self.authorizer._authorize(actor)
            self.authorizer._same_transaction(transaction)
            stored = await self.repository.get_copy(**arguments)
            self.authorizer._same_transaction(transaction)
            _verify(stored, plan=plan)

            result = ConstructionTemplateDraftResult(
                tenant_id=actor.tenant_id, template_id=template_id,
                template_row_version=root["row_version"],
                base_version_id=base_version_id, version_id=version_id,
                version=maximum + 1, version_row_version=1, status="DRAFT",
                source_starter_version_id=source.source_starter_version_id,
                source_language=source.source_language,
                section_count=len(plan["sections"]),
                question_count=len(plan["questions"]),
                created_by=actor.user_id, created_at=plan["version"]["created_at"])
            if manage_transaction:
                await self.session.commit()
            return result


def build_construction_template_draft_service(*, session, now_provider=None):
    from database.repositories.construction.construction_template_drafts import (
        ConstructionTemplateDraftRepository,
    )
    from services.construction.construction_organization_templates import (
        build_construction_organization_template_service,
    )
    return ConstructionTemplateDraftService(
        session=session, repository=ConstructionTemplateDraftRepository(session),
        template_service=build_construction_organization_template_service(
            session=session, now_provider=now_provider))
