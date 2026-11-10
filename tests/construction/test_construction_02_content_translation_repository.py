"""Local SQL cache behavior; PostgreSQL RLS/concurrency are not claimed."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import (
    BigInteger, CheckConstraint, Column, DateTime, MetaData, String,
    Table, Uuid, create_engine, event, select, update,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable
from sqlalchemy.types import TypeDecorator

from database.models import ConstructionContentTranslation as Model
from database.repositories.construction.construction_content_translations import (
    ConstructionContentTranslationRepository as Repository,
    KEY_FIELDS, UNIQUE_COLUMNS,
)
from services.construction.construction_content_translation import (
    ContentTranslationSource as Source,
    ContentTranslationRecord as Record,
    ContentTranslationStatus as Status,
    ContentFieldKind as Kind,
)

NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)


class LocalUTC(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


@pytest.fixture
def cache():
    c = NS(tenant=uuid4(), foreign=uuid4(), reviewer=uuid4())
    metadata = MetaData()
    tenants = Table("tenants", metadata, Column("id", Uuid, primary_key=True))
    users = Table("users", metadata, Column("id", Uuid, primary_key=True))
    languages = Table(
        "languages", metadata, Column("code", String(10), primary_key=True))
    c.table = Model.__table__.to_metadata(metadata)
    # Retain portable checks, all uniqueness and FKs. PostgreSQL regex/text
    # checks are inspected in the PostgreSQL DDL test, not emulated as RLS.
    for constraint in list(c.table.constraints):
        if isinstance(constraint, CheckConstraint):
            expression = str(constraint.sqltext)
            if "~" in expression or "octet_length" in expression:
                c.table.constraints.remove(constraint)
    for name in ("translated_at", "created_at", "updated_at"):
        c.table.c[name].type = LocalUTC()

    c.engine = create_engine("sqlite://")

    @event.listens_for(c.engine, "connect")
    def foreign_keys(connection, connection_record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(c.engine)
    c.sync = Session(c.engine, expire_on_commit=False)
    c.sync.execute(tenants.insert(), [{"id": c.tenant}, {"id": c.foreign}])
    c.sync.execute(users.insert().values(id=c.reviewer))
    c.sync.execute(languages.insert(), [{"code": code} for code in ("uk", "en", "de")])
    c.sync.commit()
    c.statements = []

    class Bridge:
        async def execute(self, statement):
            c.statements.append(statement)
            return c.sync.execute(statement)

    c.session = Bridge()
    c.session.commit = AsyncMock(side_effect=c.sync.commit)
    c.session.rollback = AsyncMock(side_effect=c.sync.rollback)

    class LocalRepository(Repository):
        def _insert(self):
            return sqlite_insert(self.table)

    c.repo = LocalRepository(c.session)
    c.repo.table = c.table
    c.source = Source(
        entity_type="construction.project", entity_id=uuid4(),
        field_name="description", source_language="uk",
        source_version=3, original_text="Оригінальний опис",
        field_kind=Kind.TEXT)
    c.record = Record(
        key=c.source.key_for("en"), translated_text="Translated description",
        translation_status=Status.AUTO_TRANSLATED, translated_at=NOW)
    try:
        yield c
    finally:
        c.sync.close()
        c.engine.dispose()


async def add(c, *, tenant=None, record=None, instant=NOW):
    return await c.repo.insert_automatic(
        tenant_id=c.tenant if tenant is None else tenant,
        record=c.record if record is None else record, as_of=instant)


async def read(c, *, tenant=None, key=None):
    return await c.repo.get_exact(
        tenant_id=c.tenant if tenant is None else tenant,
        key=c.record.key if key is None else key)


def rows(c):
    return tuple(dict(row) for row in c.sync.execute(
        select(c.table).order_by(c.table.c.id)).mappings())


def test_orm_matches_applied_storage_contract():
    table = Model.__table__
    assert len(table.columns) == 16
    assert len(table.constraints) == 16
    assert table.primary_key.name == "construction_content_translations_pkey"
    assert isinstance(table.c.source_version.type, BigInteger)
    assert isinstance(table.c.row_version.type, BigInteger)
    assert table.c.reviewed_by.nullable is True
    for name in ("translated_at", "created_at", "updated_at"):
        assert table.c[name].type.timezone is True
    for name in (
        "entity_type", "entity_id", "field_name", "source_language",
        "target_language", "source_version", "source_text_hash",
        "translated_text", "translation_status", "translated_at",
    ):
        assert table.c[name].server_default is None
    unique = next(
        item for item in table.constraints
        if item.name == "uq_con_content_translation_source")
    assert tuple(column.name for column in unique.columns) == UNIQUE_COLUMNS
    assert Model.__mapper__.version_id_col is table.c.row_version
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "source_version BIGINT NOT NULL" in ddl
    assert "AUTO_TRANSLATED" in ddl and "STALE" in ddl
    assert "octet_length(translated_text)" in ddl


@pytest.mark.asyncio
async def test_insert_returns_exact_record_and_does_not_own_commit(cache):
    c = cache
    saved = await add(c)
    assert saved["tenant_id"] == c.tenant
    assert saved["translation_status"] == "AUTO_TRANSLATED"
    assert saved["reviewed_by"] is None and saved["row_version"] == 1
    assert saved["created_at"] == saved["updated_at"] == NOW
    assert saved["translated_at"] == NOW
    assert all(saved[name] == getattr(c.record.key, name) for name in KEY_FIELDS)
    assert await read(c) == saved
    c.session.commit.assert_not_awaited()
    c.session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_identical_source_keys_remain_tenant_isolated(cache):
    c = cache
    first = await add(c)
    assert await read(c, tenant=c.foreign) is None
    second_record = replace(c.record, translated_text="Other tenant text")
    second = await add(c, tenant=c.foreign, record=second_record)
    assert second["id"] != first["id"]
    assert (await read(c))["translated_text"] == "Translated description"
    assert (await read(c, tenant=c.foreign))["translated_text"] == "Other tenant text"
    assert len(rows(c)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("difference", [
    "entity_type", "entity_id", "field_name", "source_language",
    "target_language", "source_version", "source_text_hash",
])
async def test_read_requires_the_entire_exact_key(cache, difference):
    c = cache
    await add(c)
    changes = {
        "entity_type": "construction.area",
        "entity_id": uuid4(),
        "field_name": "name",
        "source_language": "de",
        "target_language": "de",
        "source_version": 4,
        "source_text_hash": "a" * 64,
    }
    assert await read(c, key=replace(
        c.record.key, **{difference: changes[difference]})) is None


@pytest.mark.asyncio
async def test_duplicate_insert_does_not_replace_first_automatic_translation(cache):
    c = cache
    first = await add(c)
    before = rows(c)
    later = replace(c.record, translated_text="Second provider result")
    assert await add(c, record=later) is None
    assert rows(c) == before and await read(c) == first


@pytest.mark.asyncio
async def test_automatic_insert_never_overwrites_verified_translation(cache):
    c = cache
    saved = await add(c)
    c.sync.execute(update(c.table).where(c.table.c.id == saved["id"]).values(
        translated_text="Reviewed translation", translation_status="VERIFIED",
        reviewed_by=c.reviewer, row_version=2))
    c.sync.commit()
    before = rows(c)
    assert await add(c) is None
    assert rows(c) == before
    assert (await read(c))["reviewed_by"] == c.reviewer


@pytest.mark.asyncio
async def test_stale_replacement_requires_current_version_and_preserves_identity(cache):
    c = cache
    first = await add(c)
    c.sync.execute(update(c.table).where(c.table.c.id == first["id"]).values(
        translation_status="STALE", row_version=2))
    c.sync.commit()
    before = rows(c)
    fresh = replace(c.record, translated_text="Fresh automatic translation")
    assert await c.repo.replace_stale_automatic(
        tenant_id=c.tenant, record=fresh,
        expected_row_version=1, as_of=NOW + timedelta(minutes=1)) is None
    assert rows(c) == before
    result = await c.repo.replace_stale_automatic(
        tenant_id=c.tenant, record=fresh,
        expected_row_version=2, as_of=NOW + timedelta(minutes=1))
    assert result["id"] == first["id"]
    assert result["created_at"] == first["created_at"]
    assert result["updated_at"] == NOW + timedelta(minutes=1)
    assert result["row_version"] == 3
    assert result["translation_status"] == "AUTO_TRANSLATED"
    assert result["reviewed_by"] is None
    assert all(result[name] == first[name] for name in UNIQUE_COLUMNS)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["AUTO_TRANSLATED", "VERIFIED"])
async def test_stale_replacement_cannot_overwrite_live_record(cache, status):
    c = cache
    first = await add(c)
    c.sync.execute(update(c.table).where(c.table.c.id == first["id"]).values(
        translation_status=status,
        reviewed_by=c.reviewer if status == "VERIFIED" else None))
    c.sync.commit()
    before = rows(c)
    assert await c.repo.replace_stale_automatic(
        tenant_id=c.tenant, record=c.record,
        expected_row_version=1, as_of=NOW) is None
    assert rows(c) == before


@pytest.mark.asyncio
async def test_foreign_tenant_cannot_replace_stale_record(cache):
    c = cache
    first = await add(c)
    c.sync.execute(update(c.table).where(c.table.c.id == first["id"]).values(
        translation_status="STALE"))
    c.sync.commit()
    before = rows(c)
    assert await c.repo.replace_stale_automatic(
        tenant_id=c.foreign, record=c.record,
        expected_row_version=1, as_of=NOW) is None
    assert rows(c) == before


@pytest.mark.asyncio
async def test_old_source_version_is_retained_when_new_version_is_cached(cache):
    c = cache
    old = await add(c)
    new_key = replace(c.record.key, source_version=4)
    newer = replace(c.record, key=new_key, translated_text="New source version")
    saved = await add(c, record=newer)
    assert saved["id"] != old["id"]
    assert await read(c) == old
    assert (await read(c, key=new_key))["source_version"] == 4
    assert len(rows(c)) == 2


@pytest.mark.asyncio
async def test_caller_rollback_removes_uncommitted_cache_insert(cache):
    c = cache
    assert await add(c) is not None
    assert len(rows(c)) == 1
    c.session.commit.assert_not_awaited()
    c.session.rollback.assert_not_awaited()
    await c.session.rollback()
    assert rows(c) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "tenant", "key", "verified", "reviewer", "same_language", "naive_time",
])
async def test_invalid_write_inputs_fail_before_sql(cache, failure):
    c = cache
    tenant, record, instant = c.tenant, c.record, NOW
    if failure == "tenant":
        tenant = str(c.tenant)
    elif failure == "key":
        with pytest.raises(ValueError):
            await c.repo.get_exact(tenant_id=tenant, key="caller-key")
        assert c.statements == []
        return
    elif failure == "verified":
        record = replace(record, translation_status=Status.VERIFIED)
    elif failure == "reviewer":
        record = replace(record, reviewed_by=c.reviewer)
    elif failure == "same_language":
        record = replace(record, key=replace(record.key, target_language="uk"))
    else:
        instant = datetime(2026, 11, 9)
    with pytest.raises(ValueError):
        await c.repo.insert_automatic(
            tenant_id=tenant, record=record, as_of=instant)
    assert c.statements == []


@pytest.mark.asyncio
@pytest.mark.parametrize("expected", [True, 0, "1", 2**63 - 1])
async def test_invalid_expected_version_does_not_execute_update(cache, expected):
    c = cache
    with pytest.raises(ValueError):
        await c.repo.replace_stale_automatic(
            tenant_id=c.tenant, record=c.record,
            expected_row_version=expected, as_of=NOW)
    assert c.statements == []
