"""Settings-authorized organization reads; exact versions never follow the source."""
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import re
from uuid import UUID

from services.construction.construction_onboarding import (
    ConstructionOnboardingService,
    ConstructionOnboardingValidationError,
    ConstructionOnboardingOperationError,
    ConstructionOnboardingStateConflictError,
)
from services.construction.construction_starter_template_definitions import (
    MAX_BYTES, _key, _language,
)
from services.construction.construction_organization_template_definitions import (
    OrganizationQuestion, OrganizationSection,
    ValidatedOrganizationTemplateDefinition,
    validate_organization_template_definition,
)

MAX_VERSION = 2**63 - 1
HEADER_FIELDS = frozenset({
    "version_id", "tenant_id", "template_id", "code", "active",
    "template_row_version", "version", "version_row_version", "status",
    "source_starter_version_id", "definition_schema_version", "source_language",
    "title_key", "description_key", "labels", "content_hash", "created_by",
    "created_at", "updated_at", "published_at",
})
SECTION_FIELDS = tuple(
    name for name in OrganizationSection.model_fields if name != "questions")
QUESTION_FIELDS = tuple(OrganizationQuestion.model_fields)
UUID_QUESTION_FIELDS = ("work_type_id", "pinned_work_type_version_id")
SECTION_COLUMNS = frozenset(SECTION_FIELDS) | {
    "id", "tenant_id", "template_version_id", "row_version", "created_at", "updated_at",
}
QUESTION_COLUMNS = frozenset(QUESTION_FIELDS) | {
    "id", "tenant_id", "template_version_id", "template_section_id",
    "row_version", "created_at", "updated_at",
}


class ConstructionOrganizationTemplateValidationError(ConstructionOnboardingValidationError):
    pass


class ConstructionOrganizationTemplateOperationError(ConstructionOnboardingOperationError):
    pass


class ConstructionOrganizationTemplateNotFoundError(ConstructionOnboardingStateConflictError):
    pass


def _time(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Aware template timestamp required.")
    return value.astimezone(timezone.utc)


def _counter(value):
    if type(value) is not int or not 1 <= value <= MAX_VERSION:
        raise ValueError("Positive BIGINT template counter required.")
    return value


@dataclass(frozen=True)
class OrganizationTemplateCursor:
    tenant_id: UUID
    user_id: UUID
    after_template_id: UUID

    def __post_init__(self):
        if not all(isinstance(value, UUID) for value in (
            self.tenant_id, self.user_id, self.after_template_id,
        )):
            raise ConstructionOrganizationTemplateValidationError("Invalid template cursor.")


@dataclass(frozen=True)
class OrganizationTemplateSummary:
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
    title: str
    description: str | None
    content_hash: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    published_at: datetime


@dataclass(frozen=True)
class OrganizationTemplateQuestionReference:
    id: UUID
    code: str
    row_version: int


@dataclass(frozen=True)
class OrganizationTemplateSectionReference:
    id: UUID
    code: str
    row_version: int
    questions: tuple[OrganizationTemplateQuestionReference, ...]


@dataclass(frozen=True)
class OrganizationTemplateVersionState(OrganizationTemplateSummary):
    definition: ValidatedOrganizationTemplateDefinition
    sections: tuple[OrganizationTemplateSectionReference, ...]


@dataclass(frozen=True)
class OrganizationTemplatePage:
    items: tuple[OrganizationTemplateSummary, ...]
    next_cursor: OrganizationTemplateCursor | None


def _summary(row, *, tenant_id, as_of, requested_id=None):
    try:
        if (
            not isinstance(row, Mapping) or set(row) != HEADER_FIELDS
            or not all(isinstance(row[name], UUID) for name in (
                "version_id", "tenant_id", "template_id", "created_by"))
            or row["tenant_id"] != tenant_id
            or requested_id is not None and row["version_id"] != requested_id
            or row["source_starter_version_id"] is not None
                and not isinstance(row["source_starter_version_id"], UUID)
            or type(row["active"]) is not bool
            or row["status"] != "PUBLISHED"
            or not isinstance(row["code"], str)
            or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", row["code"]) is None
            or type(row["version"]) is not int
            or not 1 <= row["version"] <= 2**31 - 1
            or type(row["definition_schema_version"]) is not int
            or row["definition_schema_version"] != 1
            or not isinstance(row["content_hash"], str)
            or re.fullmatch(r"[0-9a-f]{64}", row["content_hash"]) is None
            or _time(row["published_at"]) > as_of
        ):
            raise ValueError()
        _counter(row["template_row_version"])
        _counter(row["version_row_version"])
        _language(row["source_language"])
        _key(row["title_key"])
        if row["description_key"] is not None:
            _key(row["description_key"])
        labels = row["labels"]
        if not isinstance(labels, dict) or not 1 <= len(labels) <= 64:
            raise ValueError()
        count = 0
        for language, values in labels.items():
            _language(language)
            if not isinstance(values, dict):
                raise ValueError()
            count += len(values)
            if count > 20000:
                raise ValueError()
            for key, value in values.items():
                _key(key)
                if (
                    not isinstance(value, str) or not value or value != value.strip()
                    or len(value) > 120
                    or any(ord(character) < 32 or ord(character) == 127 for character in value)
                ):
                    raise ValueError()
        if len(json.dumps(labels, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
            raise ValueError()
        source = labels[row["source_language"]]
        title = source[row["title_key"]]
        description = (
            None if row["description_key"] is None
            else source[row["description_key"]])
        return OrganizationTemplateSummary(
            tenant_id=row["tenant_id"], template_id=row["template_id"],
            code=row["code"], active=row["active"],
            template_row_version=row["template_row_version"],
            version_id=row["version_id"], version=row["version"],
            version_row_version=row["version_row_version"],
            source_starter_version_id=row["source_starter_version_id"],
            definition_schema_version=1, source_language=row["source_language"],
            title=title, description=description, content_hash=row["content_hash"],
            created_by=row["created_by"], created_at=_time(row["created_at"]),
            updated_at=_time(row["updated_at"]), published_at=_time(row["published_at"]))
    except (ValueError, TypeError, KeyError):
        raise ConstructionOrganizationTemplateOperationError(
            "Stored organization template header is invalid.") from None


def _rows(rows, *, fields, maximum, summary):
    if not isinstance(rows, (list, tuple)) or not 1 <= len(rows) <= maximum:
        raise ValueError()
    seen = set()
    for row in rows:
        if (
            not isinstance(row, Mapping) or set(row) != fields
            or not isinstance(row["id"], UUID) or row["id"] in seen
            or row["tenant_id"] != summary.tenant_id
            or row["template_version_id"] != summary.version_id
            or type(row["sort_order"]) is not int or row["sort_order"] < 0
        ):
            raise ValueError()
        _counter(row["row_version"])
        _time(row["created_at"])
        _time(row["updated_at"])
        seen.add(row["id"])


@dataclass(frozen=True)
class _OrganizationTemplateContentContext:
    tenant_id: UUID
    version_id: UUID
    source_language: str


def assemble_organization_template_content(
    header, content, *, tenant_id, version_id, source_language,
):
    summary = _OrganizationTemplateContentContext(
        tenant_id, version_id, source_language)
    try:
        if not isinstance(content, Mapping) or set(content) != {"sections", "questions"}:
            raise ValueError()
        sections, questions = content["sections"], content["questions"]
        _rows(sections, fields=SECTION_COLUMNS, maximum=100, summary=summary)
        _rows(questions, fields=QUESTION_COLUMNS, maximum=2000, summary=summary)
        if list(sections) != sorted(sections, key=lambda row: (row["sort_order"], row["id"])):
            raise ValueError()
        grouped = {row["id"]: [] for row in sections}
        for row in questions:
            section_id = row["template_section_id"]
            if not isinstance(section_id, UUID) or section_id not in grouped:
                raise ValueError()
            for name in UUID_QUESTION_FIELDS:
                if row[name] is not None and not isinstance(row[name], UUID):
                    raise ValueError()
            grouped[section_id].append(row)
        definition_sections, references = [], []
        for section in sections:
            children = sorted(
                grouped[section["id"]], key=lambda row: (row["sort_order"], row["id"]))
            if not children:
                raise ValueError()
            data_questions, question_refs = [], []
            for row in children:
                data = {name: row[name] for name in QUESTION_FIELDS}
                for name in UUID_QUESTION_FIELDS:
                    if data[name] is not None:
                        data[name] = str(data[name])
                data_questions.append(data)
                question_refs.append(OrganizationTemplateQuestionReference(
                    row["id"], row["code"], row["row_version"]))
            definition_sections.append(
                {name: section[name] for name in SECTION_FIELDS}
                | {"questions": data_questions})
            references.append(OrganizationTemplateSectionReference(
                section["id"], section["code"], section["row_version"],
                tuple(question_refs)))
        definition = validate_organization_template_definition({
            "title_key": header["title_key"], "description_key": header["description_key"],
            "labels": header["labels"], "sections": definition_sections,
        }, source_language=summary.source_language)
        return definition, tuple(references)
    except (ValueError, TypeError, KeyError):
        raise ConstructionOrganizationTemplateOperationError(
            "Stored organization template content is invalid.") from None


def _assemble(header, content, *, summary):
    definition, references = assemble_organization_template_content(
        header, content, tenant_id=summary.tenant_id,
        version_id=summary.version_id, source_language=summary.source_language)
    if definition.content_hash != summary.content_hash:
        raise ConstructionOrganizationTemplateOperationError(
            "Stored organization template content is invalid.")
    return OrganizationTemplateVersionState(
        **asdict(summary), definition=definition, sections=references)


class ConstructionOrganizationTemplateService:
    def __init__(self, *, session, repository, authorizer):
        if (
            not isinstance(authorizer, ConstructionOnboardingService)
            or authorizer.session is not session
            or getattr(repository, "session", None) is not session
            or any(not callable(getattr(repository, name, None)) for name in (
                "get_version_header", "list_latest", "get_content"))
        ):
            raise ValueError("Organization reads require shared-session settings authority.")
        self.session, self.repository, self.authorizer = session, repository, authorizer

    async def get_version(self, *, actor, version_id):
        if not isinstance(version_id, UUID):
            raise ConstructionOrganizationTemplateValidationError("A version UUID is required.")
        async with self.authorizer._operation(actor, write=False) as transaction:
            as_of = self.authorizer._now()
            header = await self.repository.get_version_header(
                tenant_id=actor.tenant_id, version_id=version_id, as_of=as_of)
            self.authorizer._same_transaction(transaction)
            if header is None:
                await self.authorizer._authorize(actor)
                self.authorizer._same_transaction(transaction)
                raise ConstructionOrganizationTemplateNotFoundError(
                    "Organization template version is unavailable.")
            summary = _summary(
                header, tenant_id=actor.tenant_id, as_of=as_of, requested_id=version_id)
            content = await self.repository.get_content(
                tenant_id=actor.tenant_id, version_id=version_id)
            self.authorizer._same_transaction(transaction)
            result = _assemble(header, content, summary=summary)
            await self.authorizer._authorize(actor)
            self.authorizer._same_transaction(transaction)
            return result

    async def list_templates(self, *, actor, limit=20, after=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ConstructionOrganizationTemplateValidationError("Template limit is invalid.")
        if after is not None and (
            not isinstance(after, OrganizationTemplateCursor)
            or (after.tenant_id, after.user_id) != (actor.tenant_id, actor.user_id)
        ):
            raise ConstructionOrganizationTemplateValidationError("Template cursor context differs.")
        async with self.authorizer._operation(actor, write=False) as transaction:
            as_of = self.authorizer._now()
            rows = await self.repository.list_latest(
                tenant_id=actor.tenant_id, limit=limit + 1, as_of=as_of,
                after_template_id=None if after is None else after.after_template_id)
            self.authorizer._same_transaction(transaction)
            if not isinstance(rows, (list, tuple)) or len(rows) > limit + 1:
                raise ConstructionOrganizationTemplateOperationError("Template page is invalid.")
            states, codes = [], set()
            previous = None if after is None else after.after_template_id
            for row in rows:
                state = _summary(row, tenant_id=actor.tenant_id, as_of=as_of)
                if previous is not None and state.template_id <= previous or state.code in codes:
                    raise ConstructionOrganizationTemplateOperationError("Template page order differs.")
                states.append(state)
                codes.add(state.code)
                previous = state.template_id
            await self.authorizer._authorize(actor)
            self.authorizer._same_transaction(transaction)
            items = tuple(states[:limit])
            cursor = None
            if len(states) > limit:
                cursor = OrganizationTemplateCursor(
                    actor.tenant_id, actor.user_id, items[-1].template_id)
            return OrganizationTemplatePage(items, cursor)


def build_construction_organization_template_service(*, session, now_provider=None):
    from database.repositories.construction.construction_organization_templates import (
        ConstructionOrganizationTemplateRepository,
    )
    from services.construction.construction_starter_templates import (
        build_construction_starter_template_service,
    )
    # Reuse its bare settings authorizer; no starter catalog query is made.
    authorizer = build_construction_starter_template_service(
        session=session, now_provider=now_provider).authorizer
    return ConstructionOrganizationTemplateService(
        session=session, repository=ConstructionOrganizationTemplateRepository(session),
        authorizer=authorizer)
