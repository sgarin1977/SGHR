"""Local definition/catalog checks. Native RLS and immutable triggers are separate."""
import asyncio
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import CheckConstraint, JSON, insert, select, update
from sqlalchemy.dialects import postgresql

from database.models import (
    ConstructionStarterMeasurementTemplate as Template,
    ConstructionStarterMeasurementTemplateVersion as Version,
)
from database.repositories.construction.construction_starter_templates import ConstructionStarterTemplateRepository
from services.construction.construction_onboarding import ConstructionOnboardingOperationError
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_starter_template_definitions import (
    validate_starter_definition, StarterTemplateDefinitionError, QUESTION_TYPES,
)
from services.construction.construction_starter_templates import (
    ConstructionStarterTemplateService as Service,
    ConstructionStarterTemplateNotFoundError as NotFound,
    ConstructionStarterTemplateOperationError as Operation,
    ConstructionStarterTemplateValidationError as Validation,
    ConstructionStarterTemplateCursor as Cursor,
)
from test_construction_02_tax_profiles import case, LocalUTC
from test_construction_02_onboarding_progress import onboarding


def definition():
    def gate(code):
        return dict(
            code=code, question_type="SINGLE_CHOICE", label_key="gate",
            required=True, active=True, sort_order=10,
            options=[
                {"value": value, "label_key": key}
                for value, key in (
                    ("NO", "no"), ("YES", "yes"),
                    ("NEEDS_CLARIFICATION", "unclear"),
                )
            ])
    return dict(
        title_key="title", description_key="description",
        sections=[
            dict(
                code="DEMO", label_key="demo", work_phase_code="DEMOLITION",
                active=True, sort_order=10,
                questions=[
                    gate("DEMO_GATE"),
                    dict(
                        code="AREA", question_type="NUMBER", label_key="area",
                        required=True, active=True, sort_order=20,
                        unit_code="M2", formula_type="DIRECT",
                        input_schema={"type": "string", "format": "decimal"},
                        validation_schema={"minimum": "0"},
                        show_if={
                            "question_code": "DEMO_GATE",
                            "operator": "EQ", "value": "YES",
                        }),
                ]),
            dict(
                code="PAINT", label_key="paint", work_phase_code="PAINT_FINISH",
                active=True, sort_order=20, questions=[gate("PAINT_GATE")]),
        ],
        labels={"en": {
            "title": "Starter", "description": "Structure without prices",
            "demo": "Demolition", "paint": "Painting", "gate": "Any work in this phase?",
            "area": "Measured area", "no": "No", "yes": "Yes",
            "unclear": "Needs clarification",
        }},
    )


@pytest.mark.parametrize("kind", QUESTION_TYPES)
def test_question_types_are_data_not_executable_logic(kind):
    value = definition()
    extra = dict(
        code="EXTRA", question_type=kind, label_key="extra",
        required=False, active=True, sort_order=30)
    value["labels"]["en"]["extra"] = "Additional fact"
    if kind in ("SINGLE_CHOICE", "MULTI_CHOICE"):
        extra["options"] = [{"value": "A", "label_key": "yes"}]
    if kind == "PHOTO":
        extra["photo_policy"] = "OPTIONAL"
    if kind == "CALCULATED":
        extra.update(formula_type="RECTANGLE", unit_code="M2")
    value["sections"][0]["questions"].append(extra)
    parsed = validate_starter_definition(value, source_language="en")
    assert parsed.payload()["sections"][0]["questions"][-1]["question_type"] == kind


@pytest.mark.parametrize("bad", [
    "extra_price", "phase_order", "duplicate_question", "unit", "formula",
    "forward_ref", "self_ref", "commercial_schema", "commercial_default",
    "organization_binding", "partial_binding", "missing_label",
    "missing_gate_option", "numeric_nan", "float", "string_active",
])
def test_invalid_or_commercial_definitions_are_rejected(bad):
    value = definition()
    question = value["sections"][0]["questions"][1]
    if bad == "extra_price":
        value["unit_price"] = "10"
    elif bad == "phase_order":
        value["sections"][0]["work_phase_code"] = "PAINT_FINISH"
        value["sections"][1]["work_phase_code"] = "DEMOLITION"
    elif bad == "duplicate_question":
        question["code"] = "DEMO_GATE"
    elif bad == "unit":
        question["unit_code"] = "USD"
    elif bad == "formula":
        question["formula_type"] = "__import__('os')"
    elif bad in ("forward_ref", "self_ref"):
        question["show_if"]["question_code"] = "PAINT_GATE" if bad == "forward_ref" else "AREA"
    elif bad == "commercial_schema":
        question["input_schema"] = {
            "type": "object", "properties": {"unit_price": {"type": "string"}}}
    elif bad == "commercial_default":
        question.update(default_source="PROJECT_VALUE", default_value={"field": "project.unit_price"})
    elif bad == "organization_binding":
        question["work_type_source"] = "ORGANIZATION"
    elif bad == "partial_binding":
        question.update(work_type_source="MASTER", work_type_id=str(uuid4()),
                        work_type_version_policy="PINNED")
    elif bad == "missing_label":
        del value["labels"]["en"]["area"]
    elif bad == "missing_gate_option":
        value["sections"][0]["questions"][0]["options"].pop()
    elif bad == "numeric_nan":
        question["validation_schema"]["minimum"] = "NaN"
    elif bad == "float":
        question["validation_schema"]["minimum"] = 0.1
    else:
        question["active"] = "true"
    with pytest.raises(StarterTemplateDefinitionError):
        validate_starter_definition(value, source_language="en")


def test_canonical_hash_and_detached_payload():
    value = definition()
    original = validate_starter_definition(value, source_language="en")
    again = validate_starter_definition(original.canonical_json, source_language="en")
    assert again == original
    changed = original.payload()
    changed["labels"]["en"]["title"] = "Changed"
    assert original.payload()["labels"]["en"]["title"] == "Starter"
    assert validate_starter_definition(changed, source_language="en").content_hash != original.content_hash
    with pytest.raises(StarterTemplateDefinitionError):
        validate_starter_definition('{"title_key":"a","title_key":"b"}', source_language="en")


@pytest.fixture
def catalog_case(onboarding):
    c = onboarding
    c.sync.rollback()

    def sqlite_translate(value, source, target):
        if value is None or source is None or target is None:
            return None
        replacements = {}
        for index, character in enumerate(source):
            if character not in replacements:
                replacements[character] = target[index] if index < len(target) else ""
        return "".join(replacements.get(character, character) for character in value)

    c.sync.connection().connection.driver_connection.create_function(
        "translate", 3, sqlite_translate, deterministic=True
    )
    c.sync.rollback()
    c.catalog_templates = Template.__table__.to_metadata(c.table.metadata)
    c.catalog_versions = Version.__table__.to_metadata(c.table.metadata)
    c.catalog_versions.c.definition.type = JSON(none_as_null=True)
    for table in (c.catalog_templates, c.catalog_versions):
        for name in ("created_at", "updated_at", "published_at"):
            if name in table.c:
                table.c[name].type = LocalUTC()
        for constraint in list(table.constraints):
            if constraint.name in {
                "ck_con_starter_templates_code", "ck_con_starter_templates_time",
                "ck_con_starter_versions_time", "ck_con_starter_versions_definition",
            }:
                table.constraints.remove(constraint)
        table.create(c.sync.get_bind())
    c.session.get_bind = c.sync.get_bind
    c.catalog_repository = ConstructionStarterTemplateRepository(c.session)
    c.catalog_repository.templates = c.catalog_templates
    c.catalog_repository.versions = c.catalog_versions
    c.catalog = Service(
        session=c.session, repository=c.catalog_repository, authorizer=c.progress)
    c.sync.begin()
    yield c


def begin(c):
    if not c.sync.in_transaction():
        c.sync.begin()


def snapshot(c):
    return tuple(
        tuple(dict(row) for row in c.sync.execute(
            select(table).order_by(table.c[0])).mappings())
        for table in (c.catalog_templates, c.catalog_versions, c.settings, c.audits))


def put(c):
    c.sync.execute(insert(c.catalog_templates), [
        dict(
            id=UUID(int=number), code=code, active=active, row_version=1,
            created_at=c.now, updated_at=c.now)
        for number, code, active in (
            (101, "BASE", True), (202, "SECOND", True), (303, "HIDDEN", False))
    ])
    for number, template, version, future in (
        (1001, 101, 1, False), (1002, 101, 2, False), (1003, 101, 3, True),
        (2001, 202, 1, False), (3001, 303, 1, False),
    ):
        data = validate_starter_definition(definition(), source_language="en")
        c.sync.execute(insert(c.catalog_versions).values(
            id=UUID(int=number), template_id=UUID(int=template),
            version=version, definition_schema_version=1, source_language="en",
            definition=data.payload(), content_hash=data.content_hash,
            published_at=c.now + timedelta(days=1 if future else -1),
            created_at=c.now))
    c.sync.commit()


def test_orm_matches_catalog_storage():
    assert len(Template.__table__.c) == 6 and len(Template.__table__.constraints) == 5
    assert len(Version.__table__.c) == 9 and len(Version.__table__.constraints) == 10
    assert Template.__mapper__.version_id_col is Template.__table__.c.row_version
    assert Version.__mapper__.version_id_col is None
    assert Version.__table__.c.definition.type.compile(dialect=postgresql.dialect()) == "JSONB"
    assert next(iter(Version.__table__.foreign_key_constraints)).ondelete == "RESTRICT"
    assert Version.__table__.c.published_at.server_default is None
    assert Template.__table__.c.active.server_default is None


@pytest.mark.asyncio
async def test_empty_catalog_does_not_seed_or_modify_onboarding(catalog_case):
    c = catalog_case
    before = snapshot(c)
    page = await c.catalog.list_latest(actor=c.actor)
    assert page.items == () and page.next_cursor is None
    assert snapshot(c) == before and c.session.commits == 0
    c.events.create_event.assert_not_awaited()
    c.serialization.lock_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_latest_visible_versions_and_exact_old_version_are_read_only(catalog_case):
    c = catalog_case
    put(c)
    before = snapshot(c)
    begin(c)
    page = await c.catalog.list_latest(actor=c.actor, limit=1)
    assert len(page.items) == 1
    assert page.items[0].version_id == UUID(int=1002)
    assert page.next_cursor.after_template_id == UUID(int=101)
    second = await c.catalog.list_latest(actor=c.actor, limit=1, after=page.next_cursor)
    assert second.items[0].version_id == UUID(int=2001) and second.next_cursor is None
    old = await c.catalog.get_version(actor=c.actor, version_id=UUID(int=1001))
    assert old.version == 1 and old.definition.payload()["title_key"] == "title"
    for hidden in (1003, 3001, 9999):
        begin(c)
        with pytest.raises(NotFound):
            await c.catalog.get_version(actor=c.actor, version_id=UUID(int=hidden))
    assert snapshot(c) == before and c.session.commits == 0
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_existing_settings_authority_reads_catalog(catalog_case, role):
    c = catalog_case
    c.grant.role = role
    assert (await c.catalog.list_latest(actor=c.actor)).items == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "revoked", "scope"])
async def test_authority_cannot_be_replaced_by_a_source_id(catalog_case, failure):
    c = catalog_case
    put(c)
    if failure == "membership":
        c.membership = False
    elif failure == "revoked":
        c.grant.status = "revoked"
    else:
        c.grant.role, c.grant.scope_type = "OBSERVER", "project"
    begin(c)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await c.catalog.get_version(actor=c.actor, version_id=UUID(int=1001))


@pytest.mark.asyncio
async def test_fresh_authority_after_repository_wait(catalog_case):
    c = catalog_case
    put(c)
    original = c.catalog_repository.get_version
    async def revoked(**values):
        row = await original(**values)
        c.membership = False
        return row
    c.catalog_repository.get_version = revoked
    begin(c)
    with pytest.raises(ConstructionPermissionDeniedError):
        await c.catalog.get_version(actor=c.actor, version_id=UUID(int=1001))


@pytest.mark.asyncio
async def test_corrupt_hash_including_lookahead_fails_closed(catalog_case):
    c = catalog_case
    put(c)
    c.sync.execute(update(c.catalog_versions).where(
        c.catalog_versions.c.id == UUID(int=2001)
    ).values(content_hash="0" * 64))
    c.sync.commit()
    begin(c)
    with pytest.raises(Operation):
        await c.catalog.list_latest(actor=c.actor, limit=1)


@pytest.mark.asyncio
async def test_cursor_is_position_not_authority(catalog_case):
    c = catalog_case
    before = snapshot(c)
    with pytest.raises(Validation):
        await c.catalog.list_latest(
            actor=c.actor, after=Cursor(c.foreign, c.user, UUID(int=1)))
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_cancellation_rolls_back_read_transaction(catalog_case):
    c = catalog_case
    c.catalog_repository.list_latest = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await c.catalog.list_latest(actor=c.actor)
    assert not c.sync.in_transaction()


@pytest.mark.asyncio
async def test_repository_committing_is_rejected(catalog_case):
    c = catalog_case
    original = c.catalog_repository.list_latest
    async def committed(**values):
        rows = await original(**values)
        await c.session.commit()
        return rows
    c.catalog_repository.list_latest = committed
    with pytest.raises(ConstructionOnboardingOperationError):
        await c.catalog.list_latest(actor=c.actor)


@pytest.mark.asyncio
async def test_repository_sql_is_bounded_joined_and_has_no_writer():
    statements = []
    class Capture:
        async def execute(self, statement):
            statements.append(statement)
            return NS(mappings=lambda: NS(all=lambda: [], one_or_none=lambda: None))
    from datetime import datetime, timezone
    repo = ConstructionStarterTemplateRepository(Capture())
    await repo.list_latest(limit=2, as_of=datetime.now(timezone.utc))
    compiled = statements[-1].compile(dialect=postgresql.dialect())
    assert "row_number()" in str(compiled)
    assert "JOIN" in str(compiled) and "published_at <=" in str(compiled)
    assert 2 in compiled.params.values()
    assert not any(hasattr(repo, name) for name in (
        "create_template", "publish_version", "update_version", "delete_version"))
