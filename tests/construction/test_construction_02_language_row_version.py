"""Strict organization counters and local rollback; no native concurrency claim."""
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.dml import Update

from test_construction_02_language_http import http_language, body, request, snapshot
from test_construction_02_organization_language import organization_language, change
from api.routes.construction_language_settings import OrganizationLanguageUpdate

MAX = 2**63 - 1


@pytest.mark.asyncio
async def test_get_patch_and_audit_use_the_same_integer_counter(http_language):
    c = http_language
    first = await request(c, "GET")
    assert first.status_code == 200, first.text
    initial = first.json()["data"]
    assert type(initial["row_version"]) is int
    assert initial["row_version"] == 1

    response = await request(c, payload={
        "default_language": "es",
        "expected_row_version": initial["row_version"],
    })
    assert response.status_code == 200, response.text
    assert response.json()["data"]["row_version"] == 2
    latest = await request(c, "GET")
    assert latest.json()["data"]["row_version"] == 2

    events = c.sync.execute(select(c.audit)).mappings().all()
    assert len(events) == 1
    assert events[0]["payload"]["previous_row_version"] == 1
    assert events[0]["payload"]["row_version"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["pt", "es"])
async def test_changed_counter_conflicts_even_with_identical_language_and_timestamp(
    http_language, language,
):
    c = http_language
    c.sync.execute(
        c.tenants.update().where(c.tenants.c.id == c.tenant).values(row_version=2)
    )
    c.sync.commit()
    before = snapshot(c)
    response = await request(c, payload=body(c, default_language=language))
    assert response.status_code == 409, response.text
    assert snapshot(c) == before
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [0, -1, True, "1", 1.0, MAX + 1, None])
async def test_http_rejects_noncanonical_expected_versions(http_language, invalid):
    c = http_language
    before = snapshot(c)
    response = await request(c, payload=body(c, expected_row_version=invalid))
    assert response.status_code == 422, response.text
    assert snapshot(c) == before
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [
    {"expected_updated_at": "2026-10-01T00:00:00+00:00"},
    {"expected_default_language": "pt"},
])
async def test_legacy_optimistic_lock_fields_are_not_accepted(http_language, legacy):
    c = http_language
    before = snapshot(c)
    response = await request(c, payload=body(c, **legacy))
    assert response.status_code == 422
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_maximum_counter_is_exact_for_get_and_current_noop(http_language):
    c = http_language
    c.sync.execute(
        c.tenants.update().where(c.tenants.c.id == c.tenant).values(row_version=MAX)
    )
    c.sync.commit()
    before = snapshot(c)

    response = await request(c, "GET")
    assert response.status_code == 200, response.text
    value = response.json()["data"]["row_version"]
    assert type(value) is int and value == MAX

    response = await request(c, payload=body(
        c, default_language="pt", expected_row_version=MAX,
    ))
    assert response.status_code == 200, response.text
    assert response.json()["data"]["row_version"] == MAX
    assert response.json()["data"]["changed"] is False
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_conditional_sql_uses_counter_and_single_increment(organization_language):
    c = organization_language
    result = await change(c)
    updates = [
        query for query in c.statements
        if isinstance(query, Update) and query.table.name == "tenants"
    ]
    assert len(updates) == 1 and result.row_version == 2
    sql = str(updates[0].compile(dialect=postgresql.dialect()))
    where = sql.split("WHERE", 1)[1].split("RETURNING", 1)[0]
    assert "tenants.row_version =" in where
    assert "tenants.updated_at =" not in where


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, 0, True, MAX + 1])
async def test_missing_or_invalid_stored_counter_has_no_fallback(http_language, invalid):
    c = http_language
    original = c.reader.repository.get_tenant_language

    async def broken(**values):
        row = await original(**values)
        return dict(row, row_version=invalid)

    c.reader.repository.get_tenant_language = AsyncMock(side_effect=broken)
    before = snapshot(c)
    response = await request(c, "GET")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "LANGUAGE_CONFIGURATION_REQUIRED"
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_unexpected_double_increment_rolls_back_the_whole_change(http_language):
    c = http_language
    original = c.writer.repository.replace_default

    async def doubled(**values):
        row = await original(**values)
        return dict(row, row_version=row["row_version"] + 1)

    c.writer.repository.replace_default = AsyncMock(side_effect=doubled)
    before = snapshot(c)
    response = await request(c)
    assert response.status_code == 500, response.text
    assert snapshot(c) == before
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_published_openapi_preserves_exact_int64_bounds(http_language):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=http_language.app),
        base_url="https://testserver",
    ) as client:
        response = await client.get("/api/v1/openapi.json")

    assert response.status_code == 200, response.text
    schemas = response.json()["components"]["schemas"]
    for title, field_name in (
        ("LanguageContextResponse", "row_version"),
        ("OrganizationLanguageUpdate", "expected_row_version"),
        ("OrganizationLanguageWriteData", "row_version"),
    ):
        schema = next(
            value for value in schemas.values()
            if value.get("title") == title
        )
        field = schema["properties"][field_name]
        assert type(field["minimum"]) is int and field["minimum"] == 1
        assert type(field["maximum"]) is int and field["maximum"] == MAX
        assert field["format"] == "int64"

    model = OrganizationLanguageUpdate.model_validate({
        "default_language": "es", "expected_row_version": MAX,
    })
    assert type(model.expected_row_version) is int
    assert model.expected_row_version == MAX

# con_011_row_version_v1
