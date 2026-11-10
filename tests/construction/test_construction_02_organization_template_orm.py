"""ORM contract and local FK/version checks; native guards and RLS are separate."""
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import (
    BigInteger, CheckConstraint, Column, FetchedValue, JSON, MetaData,
    Table, Uuid, create_engine, event, insert,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.schema import CreateTable

from database.models import (
    ConstructionMeasurementTemplate as Template,
    ConstructionMeasurementTemplateVersion as Version,
    ConstructionMeasurementTemplateSection as Section,
    ConstructionMeasurementTemplateQuestion as Question,
)

MODELS = (Template, Version, Section, Question)


@pytest.mark.parametrize("model,count", [
    (Template, 8), (Version, 17), (Section, 11), (Question, 29),
])
def test_mapping_preserves_storage_shape_and_server_generated_metadata(model, count):
    table = model.__table__
    assert len(table.c) == count
    assert isinstance(table.c.row_version.type, BigInteger)
    assert table.c.row_version.nullable is False
    assert str(table.c.row_version.server_default.arg) == "1"
    assert model.__mapper__.version_id_col is table.c.row_version
    assert model.__mapper__.eager_defaults is True
    assert isinstance(table.c.updated_at.server_onupdate, FetchedValue)
    assert table.c.updated_at.onupdate is None
    assert table.c.created_at.type.timezone and table.c.updated_at.type.timezone
    sql = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "ON DELETE RESTRICT" in sql
    assert "row_version BIGINT" in sql


def test_provenance_and_section_reference_cannot_cross_version_context():
    source = next(
        constraint for constraint in Version.__table__.foreign_key_constraints
        if constraint.name == "con_mtv_source_fk")
    assert source.elements[0].target_fullname == (
        "construction_starter_measurement_template_versions.id")
    section = next(
        constraint for constraint in Question.__table__.foreign_key_constraints
        if constraint.name == "con_mtq_section_fk")
    assert tuple(column.name for column in section.columns) == (
        "tenant_id", "template_version_id", "template_section_id")
    assert tuple(element.target_fullname for element in section.elements) == (
        "construction_measurement_template_sections.tenant_id",
        "construction_measurement_template_sections.template_version_id",
        "construction_measurement_template_sections.id")
    phase = next(
        constraint for constraint in Question.__table__.foreign_key_constraints
        if constraint.name == "con_mtq_section_phase_fk")
    assert phase.deferrable is True and phase.initially == "DEFERRED"


def test_json_nulls_and_explicit_business_fields_match_storage():
    for model, fields in (
        (Version, ("labels",)),
        (Question, (
            "input_schema", "validation_schema", "options", "show_if", "default_value")),
    ):
        for name in fields:
            column = model.__table__.c[name]
            assert isinstance(column.type, JSONB) and column.type.none_as_null
    for model, fields in (
        (Template, ("code", "active")),
        (Version, ("version", "status", "definition_schema_version", "source_language", "labels")),
        (Section, ("code", "work_phase_code", "sort_order", "active")),
        (Question, ("question_type", "required", "default_source", "photo_policy", "active")),
    ):
        for name in fields:
            column = model.__table__.c[name]
            assert column.default is None and column.server_default is None
    assert Version.__table__.c.source_starter_version_id.nullable
    assert isinstance(Version.__table__.c.published_at.server_onupdate, FetchedValue)


@pytest.fixture
def local_storage():
    engine = create_engine("sqlite://")
    @event.listens_for(engine, "connect")
    def configure(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.create_function("now", 0, lambda: "2026-11-06 10:00:00")
        connection.create_function("gen_random_uuid", 0, lambda: uuid4().hex)

    metadata = MetaData()
    tenants = Table("tenants", metadata, Column("id", Uuid, primary_key=True))
    users = Table("users", metadata, Column("id", Uuid, primary_key=True))
    sources = Table(
        "construction_starter_measurement_template_versions", metadata,
        Column("id", Uuid, primary_key=True))
    tables = {}
    for model in MODELS:
        table = model.__table__.to_metadata(metadata)
        # Emulate only local storage, FK and ORM locking.
        # PostgreSQL CHECKs, RLS and installed triggers are not emulated.
        for constraint in tuple(table.constraints):
            if isinstance(constraint, CheckConstraint):
                table.constraints.remove(constraint)
        for column in table.c:
            if isinstance(column.type, JSONB):
                column.type = JSON(none_as_null=True)
        tables[model] = table

    metadata.create_all(engine)
    c = NS(
        engine=engine, tables=tables, tenant=uuid4(), foreign=uuid4(),
        user=uuid4(), source=uuid4(), template=uuid4(), other_template=uuid4(),
        version=uuid4(), other_version=uuid4(), section=uuid4())
    with engine.begin() as connection:
        connection.execute(insert(tenants), [{"id": c.tenant}, {"id": c.foreign}])
        connection.execute(insert(users), {"id": c.user})
        connection.execute(insert(sources), {"id": c.source})
        for tenant, template, version, code in (
            (c.tenant, c.template, c.version, "BASE"),
            (c.foreign, c.other_template, c.other_version, "OTHER"),
        ):
            connection.execute(insert(tables[Template]).values(
                id=template, tenant_id=tenant, code=code,
                active=True, created_by=c.user))
            connection.execute(insert(tables[Version]).values(
                id=version, tenant_id=tenant, template_id=template,
                version=1, status="DRAFT", source_starter_version_id=c.source,
                definition_schema_version=1, source_language="en",
                title_key="title", description_key=None,
                labels={"en": {"title": "Template"}}, content_hash=None,
                created_by=c.user, published_at=None))
        connection.execute(insert(tables[Section]).values(
            id=c.section, tenant_id=c.tenant, template_version_id=c.version,
            code="PAINT", label_key="title", work_phase_code="PAINT_FINISH",
            sort_order=10, active=True))
    try:
        yield c
    finally:
        engine.dispose()


def test_orm_updates_once_and_rejects_a_stale_writer(local_storage):
    c = local_storage
    with Session(c.engine, expire_on_commit=False) as first, Session(c.engine) as second:
        a = first.get(Template, c.template)
        b = second.get(Template, c.template)
        assert a.row_version == b.row_version == 1
        a.code = "FIRST_EDIT"
        first.commit()
        assert a.row_version == 2
        b.code = "STALE_EDIT"
        with pytest.raises(StaleDataError):
            second.commit()
        second.rollback()
        stored = second.get(Template, c.template)
        assert stored.code == "FIRST_EDIT" and stored.row_version == 2


@pytest.mark.parametrize("failure", ["tenant_context", "phase_context"])
def test_question_fk_rejects_foreign_or_inconsistent_section(local_storage, failure):
    c = local_storage
    tenant = c.foreign if failure == "tenant_context" else c.tenant
    version = c.other_version if failure == "tenant_context" else c.version
    with pytest.raises(IntegrityError):
        with c.engine.begin() as connection:
            connection.execute(insert(c.tables[Question]).values(
                id=uuid4(), tenant_id=tenant, template_version_id=version,
                template_section_id=c.section, code="BAD",
                question_type="TEXT", label_key="title", required=False,
                unit_code=None, formula_type=None, input_schema={},
                validation_schema={}, options=[], show_if=None,
                default_source="NONE", default_value=None,
                work_type_source=None, work_type_id=None,
                work_type_version_policy=None, pinned_work_type_version_id=None,
                work_phase_code="DEMOLITION", photo_policy="NONE",
                help_text_key=None, help_media_ref=None,
                sort_order=10, active=True))
