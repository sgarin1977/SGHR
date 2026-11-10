import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from api.errors import ApiHttpError
from api.routes.construction_project_read import public_project
from database.models import ConstructionProject
from database.repositories.construction.construction_project_currency import ConstructionProjectCurrencyRepository
from services.api_idempotency import ApiIdempotencyResponseCodec
from services.construction.construction_projects import (
    ConstructionProjectCurrencyConfigurationError, ConstructionProjectOperationError,
    ConstructionProjectService,
)
from test_construction_01_project_create_transaction import create_case, real_create, create
from test_construction_01_project_create_workflow import (
    workflow_case, real_workflow, run as run_project,
)
from test_construction_1a_lead_conversion_service import (
    conversion_case, sql_conversion, run as run_conversion, counts,
)


def configure_tenant(sync, tenant, currency="EUR"):
    # This schema belongs only to the existing in-memory SQLite fixture.
    sync.execute(text("ALTER TABLE tenants ADD COLUMN default_currency VARCHAR(3)"))
    sync.execute(text("ALTER TABLE tenants ADD COLUMN status TEXT"))
    sync.execute(text("UPDATE tenants SET default_currency=:currency,status='active' WHERE id=:id"),
                 {"currency": currency, "id": tenant.hex})
    sync.commit()


def use_real_reader(c):
    async def execute(statement):
        return c.sql_session.execute(statement)
    c.session.execute = execute
    c.session.commit.side_effect = c.sql_session.commit
    c.session.rollback.side_effect = c.sql_session.rollback
    c.service.currency_reader = ConstructionProjectCurrencyRepository(c.session)


@pytest.fixture
def currency_case(real_create):
    c = real_create
    configure_tenant(c.sql_session, c.tenant)
    use_real_reader(c)
    return c


def audit_rows(c):
    return [json.loads(row[0]) for row in c.sql_session.execute(
        text("SELECT payload FROM tx_test_audit ORDER BY rowid"))]


@pytest.mark.asyncio
async def test_new_project_captures_exact_tenant_currency_and_later_default_does_not_rewrite_it(currency_case):
    c = currency_case
    c.sql_session.execute(text("INSERT INTO tenants(id,status,default_currency) VALUES(:id,'active','GBP')"),
                          {"id": uuid4().hex})
    c.sql_session.commit()
    first = await create(c)
    first_id = first.id
    assert first.currency == "EUR"
    c.sql_session.execute(text("UPDATE tenants SET default_currency='USD' WHERE id=:id"),
                          {"id": c.tenant.hex})
    c.sql_session.commit()
    second = await create(c)
    assert second.currency == "USD"
    assert c.sql_session.get(ConstructionProject, first_id).currency == "EUR"
    payloads = audit_rows(c)
    assert [p["currency"] for p in payloads] == ["EUR", "USD"]
    assert all(p["currency_source"] == "organization_default" for p in payloads)


@pytest.mark.asyncio
@pytest.mark.parametrize("currency", [None, "eur", "ZZZ", "EU", "USD "])
async def test_invalid_organization_currency_creates_neither_project_nor_audit(currency_case, currency):
    c = currency_case
    c.sql_session.execute(text("UPDATE tenants SET default_currency=:currency WHERE id=:id"),
                          {"currency": currency, "id": c.tenant.hex})
    c.sql_session.commit()
    with pytest.raises(ConstructionProjectCurrencyConfigurationError):
        await create(c)
    assert c.sql_session.execute(text("SELECT count(*) FROM construction_projects")).scalar_one() == 0
    assert audit_rows(c) == []
    c.session.commit.assert_not_awaited()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [False, True])
async def test_unavailable_organization_has_no_currency_fallback(currency_case, missing):
    c = currency_case
    statement = "DELETE FROM tenants WHERE id=:id" if missing else "UPDATE tenants SET status='paused' WHERE id=:id"
    c.sql_session.execute(text(statement), {"id": c.tenant.hex})
    c.sql_session.commit()
    with pytest.raises(ConstructionProjectCurrencyConfigurationError):
        await create(c)
    assert audit_rows(c) == []
    assert c.sql_session.execute(text("SELECT count(*) FROM construction_projects")).scalar_one() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit"])
async def test_real_flush_currency_project_and_audit_roll_back_together(currency_case, failure):
    c = currency_case
    if failure == "audit":
        c.events.fail_after_insert = True
    else:
        c.session.commit.side_effect = RuntimeError("local failed commit")
    with pytest.raises(ConstructionProjectOperationError):
        await create(c)
    assert c.sql_session.execute(text("SELECT count(*) FROM construction_projects")).scalar_one() == 0
    assert audit_rows(c) == []
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_unknown_currency_remains_null_and_basic_read_still_works(currency_case):
    c = currency_case
    project = await c.repo.create_project(
        tenant_id=c.tenant, client_id=None, name="Legacy", responsible_user_id=None,
        comment=None, address_raw="Legacy address", created_by=c.user,
    )
    c.sql_session.commit()
    # The local SQLite driver reloads UTC timestamps without timezone metadata.
    # Restore fixture metadata without an ORM UPDATE or a production change.
    from datetime import timezone
    from sqlalchemy.orm.attributes import set_committed_value
    for field in ("created_at", "updated_at"):
        value = getattr(project, field)
        if value.tzinfo is None:
            set_committed_value(project, field, value.replace(tzinfo=timezone.utc))
    assert project.currency is None
    response = public_project(project, tenant_id=c.tenant, project_id=project.id)
    assert response["name"] == "Legacy" and "currency" not in response
    assert audit_rows(c) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("later_currency", ["USD", None])
async def test_encrypted_create_receipt_is_unchanged_on_replay_after_currency_change(real_workflow, later_currency):
    c = real_workflow
    configure_tenant(c.sql_session, c.tenant)
    use_real_reader(c)
    reader = c.service.currency_reader
    reader.get_for_tenant = AsyncMock(wraps=reader.get_for_tenant)
    c.idempotency.response_codec = ApiIdempotencyResponseCodec(
        encryption_key=Fernet.generate_key().decode())
    c.sql_session.begin()
    first = await run_project(c)
    record = next(iter(c.records.records.values()))
    ciphertext = record.response_ciphertext
    assert "currency" not in first
    c.sql_session.execute(text("UPDATE tenants SET default_currency=:currency WHERE id=:id"),
                          {"currency": later_currency, "id": c.tenant.hex})
    c.sql_session.commit()
    c.sql_session.begin()
    assert await run_project(c) == first
    assert record.response_ciphertext == ciphertext
    assert c.sql_session.execute(text("SELECT count(*) FROM tx_test_result")).scalar_one() == 1
    stored_ciphertext = c.sql_session.execute(text("SELECT payload FROM tx_workflow_response")).scalar_one()
    assert stored_ciphertext == ciphertext
    reader.get_for_tenant.assert_awaited_once_with(c.tenant)
    assert [p["currency"] for p in audit_rows(c)] == ["EUR"]
    project_currency = c.sql_session.execute(text("SELECT currency FROM construction_projects")).scalar_one()
    assert project_currency == "EUR"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["new", "client", "existing"])
async def test_conversion_uses_common_currency_capture_and_preserves_reused_project(sql_conversion, mode):
    c = sql_conversion
    configure_tenant(c.sync, c.tenant, "USD")
    c.service.currency_reader = ConstructionProjectCurrencyRepository(c.session)
    old_project_id = c.project.id
    with c.sync.begin():
        result = await run_conversion(c, mode)
        assert result.project.currency == (None if mode == "existing" else "USD")
        payload = json.loads(c.sync.execute(text("SELECT payload FROM conversion_test_audit")).scalar_one())
        if mode == "existing":
            assert "created_project_currency" not in payload
        else:
            assert payload["created_project_currency"] == "USD"
        assert c.sync.get(ConstructionProject, old_project_id).currency is None
    c.session.commit.assert_not_awaited()
    c.session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_currency_capture_rolls_back_client_created_by_conversion(sql_conversion):
    c = sql_conversion
    configure_tenant(c.sync, c.tenant, None)
    c.service.currency_reader = ConstructionProjectCurrencyRepository(c.session)
    with pytest.raises(ConstructionProjectCurrencyConfigurationError):
        with c.sync.begin():
            await run_conversion(c, "new")
    assert counts(c) == (1, 1, 0, 0)


@pytest.mark.asyncio
async def test_currency_query_is_narrow_tenant_bound_and_has_no_platform_row_lock():
    session = SimpleNamespace(execute=AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: "EUR")))
    reader = ConstructionProjectCurrencyRepository(session)
    tenant = uuid4()
    assert await reader.get_for_tenant(tenant) == "EUR"
    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert list(statement.selected_columns.keys()) == ["default_currency"]
    assert tenant in statement.compile().params.values()
    assert "tenants.status" in sql and "FOR UPDATE" not in sql and "FOR SHARE" not in sql
    assert statement.get_execution_options()["autoflush"] is False
    with pytest.raises(ValueError):
        await reader.get_for_tenant(str(tenant))
    session.execute.assert_awaited_once()


def test_model_is_nullable_without_invented_default_and_injected_reader_must_share_session():
    column = ConstructionProject.__table__.c.currency
    assert column.nullable and column.type.length == 3
    assert column.default is None and column.server_default is None
    with pytest.raises(ValueError):
        ConstructionProjectService(
            session=object(), repository=object(),
            currency_reader=SimpleNamespace(session=object()))


@pytest.mark.asyncio
@pytest.mark.parametrize("conversion", [False, True])
async def test_routes_return_currency_configuration_conflict(conversion):
    request = SimpleNamespace(state=SimpleNamespace(request_id="currency-config"))
    error = ConstructionProjectCurrencyConfigurationError("private configuration")
    if conversion:
        from api.routes.construction_lead_conversion import convert_lead, LeadConversionRequest
        call = convert_lead(
            request=request, id=uuid4(),
            body=LeadConversionRequest(expected_row_version=1, client_type="person", project_name="Project"),
            actor=object(), access_context=object(),
            workflow=SimpleNamespace(convert_lead=AsyncMock(side_effect=error)),
            idempotency_key="test-currency-key")
    else:
        from api.routes.construction_project_create import create_project, ProjectCreateRequest
        call = create_project(
            request=request, body=ProjectCreateRequest(name="Project", address_raw="Address"),
            actor=object(), access_context=object(),
            workflow=SimpleNamespace(create_project=AsyncMock(side_effect=error)),
            idempotency_key=None)
    with pytest.raises(ApiHttpError) as caught:
        await call
    assert caught.value.status_code == 409
    assert caught.value.code == "CURRENCY_CONFIGURATION_REQUIRED"
    assert "private configuration" not in caught.value.message
