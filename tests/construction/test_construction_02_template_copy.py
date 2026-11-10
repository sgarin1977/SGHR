"""Exact-source copying with local SQLite transactions; no external operations."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import CheckConstraint, JSON, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from database.models import (
    ConstructionMeasurementTemplate, ConstructionMeasurementTemplateVersion,
    ConstructionMeasurementTemplateSection, ConstructionMeasurementTemplateQuestion,
)
from database.repositories.construction.construction_template_copy import (
    ConstructionTemplateCopyRepository,
)
from services.construction.construction_onboarding import (
    ConstructionOnboardingOperationError as Operation,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_starter_template_definitions import (
    validate_starter_definition,
)
from services.construction.construction_starter_templates import (
    ConstructionStarterTemplateNotFoundError as NotFound,
)
from services.construction import construction_template_copy as module
from services.construction.construction_template_copy import (
    ConstructionTemplateCopyService as Service,
    ConstructionTemplateCopyConflictError as Conflict,
    ConstructionTemplateCopyBindingUnavailable as BindingUnavailable,
)
from test_construction_02_starter_template_catalog import (
    case, onboarding, catalog_case, put, definition,
)
from test_construction_02_tax_profiles import LocalUTC


@pytest.fixture
def copy_case(catalog_case):
    c = catalog_case
    c.sync.rollback()
    models = (
        ConstructionMeasurementTemplate, ConstructionMeasurementTemplateVersion,
        ConstructionMeasurementTemplateSection, ConstructionMeasurementTemplateQuestion,
    )
    tables = []
    for model in models:
        table = model.__table__.to_metadata(c.table.metadata)
        for constraint in tuple(table.constraints):
            if isinstance(constraint, CheckConstraint):
                table.constraints.remove(constraint)
        for column in table.c:
            if isinstance(column.type, module.datetime.__class__):
                raise AssertionError("Unexpected column type.")
            if column.name in ("created_at", "updated_at", "published_at"):
                column.type = LocalUTC()
            elif column.type.__class__.__name__ == "JSONB":
                column.type = JSON(none_as_null=True)
        table.create(c.sync.get_bind())
        tables.append(table)

    class LocalRepository(ConstructionTemplateCopyRepository):
        def _identity_insert(self):
            return sqlite_insert(self.templates)

    c.copy_repo = LocalRepository(c.session)
    (
        c.copy_repo.templates, c.copy_repo.versions,
        c.copy_repo.sections, c.copy_repo.questions,
    ) = tables
    c.copy_tables = tuple(tables)
    c.copier = Service(
        session=c.session, repository=c.copy_repo, starter_service=c.catalog)
    c.sync.begin()
    yield c


def snapshot(c):
    return tuple(
        tuple(dict(row) for row in c.sync.execute(
            select(table).order_by(table.c[0])).mappings())
        for table in (
            *c.copy_tables, c.catalog_templates, c.catalog_versions,
            c.settings, c.audits))


async def copy(c, **options):
    if not c.sync.in_transaction():
        c.sync.begin()
    return await c.copier.copy_template(**(
        dict(actor=c.actor, source_version_id=UUID(int=1001),
             trace_id="template-copy-test") | options))


def add_source(c, value):
    data = validate_starter_definition(value, source_language="en")
    version_id = uuid4()
    c.sync.execute(insert(c.catalog_versions).values(
        id=version_id, template_id=UUID(int=101), version=99,
        definition_schema_version=1, source_language="en",
        definition=data.payload(), content_hash=data.content_hash,
        published_at=c.now, created_at=c.now))
    c.sync.commit()
    return version_id


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_copy_preserves_exact_content_with_new_ids_and_one_audit(copy_case, role):
    c = copy_case
    c.grant.role = role
    put(c)
    result = await copy(c)
    assert result.tenant_id == c.tenant and result.created_by == c.user
    assert result.source_starter_version_id == UUID(int=1001)
    assert result.template_id not in {UUID(int=101), UUID(int=1001)}
    assert result.template_version_id != result.template_id
    assert result.section_count == 2 and result.question_count == 3
    assert result.version == 1 and result.version_row_version == 2
    assert result.template_row_version == 1 and c.session.commits == 1
    stored = await c.copy_repo.get_copy(
        tenant_id=c.tenant, template_id=result.template_id,
        version_id=result.template_version_id)
    assert stored["version"]["status"] == "PUBLISHED"
    assert stored["version"]["content_hash"] == result.content_hash
    sections = {row["id"] for row in stored["sections"]}
    assert len(sections) == 2
    assert all(row["template_section_id"] in sections for row in stored["questions"])
    event = c.events.create_event.await_args.kwargs
    assert event["event_type"] == "construction_measurement_template_copied"
    assert event["entity_id"] == result.template_id
    assert event["tenant_id"] == c.tenant and event["user_id"] == c.user
    assert event["trace_id"] == "template-copy-test"
    assert event["payload"]["source_starter_version_id"] == str(UUID(int=1001))
    assert set(event["payload"]["section_ids"]) == {str(value) for value in sections}
    assert "labels" not in event["payload"]
    c.events.create_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_caller_can_rollback_copy_and_audit_together(copy_case):
    c = copy_case
    put(c)
    before = snapshot(c)
    result = await copy(c, manage_transaction=False)
    assert result.section_count == 2 and c.session.commits == 0
    await c.session.rollback()
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "scope", "expiry", "actor"])
async def test_fresh_settings_authority_is_required(copy_case, failure):
    c = copy_case
    put(c)
    if failure == "membership":
        c.membership = False
    elif failure == "scope":
        c.grant.role, c.grant.scope_type = "OBSERVER", "project"
    elif failure == "expiry":
        c.grant.expires_at = c.now
    else:
        c.actor.status = "blocked"
    before = snapshot(c)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await copy(c)
    assert snapshot(c) == before
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("source_id", [1003, 3001, 9999])
async def test_future_inactive_and_missing_sources_cannot_be_copied(copy_case, source_id):
    c = copy_case
    put(c)
    before = snapshot(c)
    with pytest.raises(NotFound):
        await copy(c, source_version_id=UUID(int=source_id))
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_existing_code_is_not_an_implicit_reuse_or_overwrite(copy_case):
    c = copy_case
    put(c)
    await copy(c)
    before = snapshot(c)
    with pytest.raises(Conflict):
        await copy(c)
    assert snapshot(c) == before and c.session.commits == 1
    c.events.create_event.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,expected", [
    ("content", Operation),
    ("readback", Operation),
    ("audit", Operation),
    ("commit", Operation),
    ("authority_after_audit", ConstructionPermissionDeniedError),
    ("source_after_audit", NotFound),
    ("cancel", asyncio.CancelledError),
])
async def test_failures_rollback_all_copy_rows_and_audit(copy_case, failure, expected):
    c = copy_case
    put(c)
    before = snapshot(c)
    if failure in ("content", "cancel"):
        original = c.copy_repo.insert_content
        async def failed(**arguments):
            await original(**arguments)
            if failure == "cancel":
                raise asyncio.CancelledError()
            raise RuntimeError("private content failure")
        c.copy_repo.insert_content = failed
    elif failure == "readback":
        original = c.copy_repo.get_copy
        async def corrupted(**arguments):
            data = await original(**arguments)
            data["questions"][0]["active"] = False
            return data
        c.copy_repo.get_copy = corrupted
    elif failure == "audit":
        c.fail_audit = True
    elif failure == "commit":
        c.session.fail_commit = True
    elif failure == "authority_after_audit":
        c.after_audit = lambda: setattr(c, "membership", False)
    else:
        def disable_source():
            c.sync.execute(update(c.catalog_templates).where(
                c.catalog_templates.c.id == UUID(int=101)).values(active=False))
        c.after_audit = disable_source
    with pytest.raises(expected):
        await copy(c)
    assert snapshot(c) == before and c.session.commits == 0


@pytest.mark.asyncio
async def test_new_source_version_does_not_change_an_existing_copy(copy_case):
    c = copy_case
    put(c)
    result = await copy(c)
    arguments = dict(
        tenant_id=c.tenant, template_id=result.template_id,
        version_id=result.template_version_id)
    before = await c.copy_repo.get_copy(**arguments)
    value = definition()
    value["labels"]["en"]["title"] = "New platform version"
    add_source(c, value)
    after = await c.copy_repo.get_copy(**arguments)
    assert after == before
    assert after["version"]["source_starter_version_id"] == UUID(int=1001)


@pytest.mark.asyncio
async def test_content_is_written_in_bounded_batches_without_truncation(copy_case):
    c = copy_case
    put(c)
    value = definition()
    value["sections"][0]["questions"].extend([
        dict(
            code="EXTRA_" + str(number), question_type="TEXT",
            label_key="area", required=False, active=True, sort_order=30 + number)
        for number in range(130)
    ])
    source_id = add_source(c, value)
    statements = []
    original = c.session.execute
    async def record(statement):
        statements.append(statement)
        return await original(statement)
    c.session.execute = record
    result = await copy(c, source_version_id=source_id)
    assert result.question_count == 133
    writes = [
        statement for statement in statements
        if getattr(statement, "is_insert", False)
        and statement.table.name == c.copy_repo.questions.name
    ]
    assert len(writes) == 2
    assert len(c.sync.execute(select(c.copy_repo.questions)).all()) == 133


@pytest.mark.asyncio
async def test_work_bindings_require_canonical_validation_and_keep_identity(copy_case):
    c = copy_case
    put(c)
    value = definition()
    work_id = uuid4()
    value["sections"][0]["questions"][1].update(
        work_type_source="MASTER", work_type_id=str(work_id),
        work_type_version_policy="CURRENT_PUBLISHED")
    source_id = add_source(c, value)
    before = snapshot(c)
    with pytest.raises(BindingUnavailable):
        await copy(c, source_version_id=source_id)
    assert snapshot(c) == before
    validator = NS(
        session=c.session, validate_template_bindings=AsyncMock(return_value=True))
    c.copier = Service(
        session=c.session, repository=c.copy_repo,
        starter_service=c.catalog, binding_validator=validator)
    result = await copy(c, source_version_id=source_id)
    row = c.sync.execute(select(c.copy_repo.questions).where(
        c.copy_repo.questions.c.template_version_id == result.template_version_id,
        c.copy_repo.questions.c.code == "AREA",
    )).mappings().one()
    assert row["work_type_id"] == work_id and row["work_type_source"] == "MASTER"
    validator.validate_template_bindings.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_generated_identity_is_rejected_before_writes(copy_case, monkeypatch):
    c = copy_case
    put(c)
    before = snapshot(c)
    monkeypatch.setattr(module, "uuid4", lambda: c.user)
    with pytest.raises(Operation):
        await copy(c)
    assert snapshot(c) == before
    c.events.create_event.assert_not_awaited()
