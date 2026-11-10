"""CON-019 local-only behavior. No runtime database or provider operations."""
import asyncio
from decimal import Decimal, localcontext
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import CheckConstraint, select
from sqlalchemy.dialects import postgresql

from database.models import ConstructionCalculationSetting
from database.repositories.construction.construction_calculation_settings import (
    ConstructionCalculationSettingsRepository,
)
from services.construction.construction_calculation_settings import (
    ConstructionCalculationSettingsService,
    ConstructionCalculationSettingsValidationError as Validation,
    ConstructionCalculationSettingsConflictError as Conflict,
    ConstructionCalculationSettingsOperationError as Operation,
    validated_fields,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from test_construction_02_tax_profiles import case, LocalUTC
from test_construction_02_pricing_profiles import LocalDecimal


class NullableLocalDecimal(LocalDecimal):
    """SQL NULL is valid for optional calculation configuration."""
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return super().process_bind_param(value, dialect)


@pytest.fixture
def calculation(case):
    c = case
    c.sync.rollback()
    c.calculations = ConstructionCalculationSetting.__table__.to_metadata(c.table.metadata)
    for name in (
        "minimum_job_amount", "default_technological_waste_percent",
        "default_local_repair_percent",
    ):
        c.calculations.c[name].type = NullableLocalDecimal()
    replacements = {
        "ck_con019_minimum_amount":
            "minimum_job_amount IS NULL OR CAST(minimum_job_amount AS NUMERIC) >= 0",
        "ck_con019_minimum_currency":
            "minimum_job_currency IS NULL OR length(minimum_job_currency) = 3",
        "ck_con019_waste_percent":
            "default_technological_waste_percent IS NULL OR "
            "CAST(default_technological_waste_percent AS NUMERIC) BETWEEN 0 AND 100",
        "ck_con019_local_repair_percent":
            "default_local_repair_percent IS NULL OR "
            "CAST(default_local_repair_percent AS NUMERIC) BETWEEN 0 AND 100",
    }
    for constraint in list(c.calculations.constraints):
        if constraint.name in replacements:
            c.calculations.constraints.remove(constraint)
            c.calculations.append_constraint(CheckConstraint(
                replacements[constraint.name], name=constraint.name))
    for name in ("created_at", "updated_at"):
        c.calculations.c[name].type = LocalUTC()
    c.calculations.create(c.sync.get_bind())
    c.session.get_transaction = c.sync.get_transaction
    c.calc_repository = ConstructionCalculationSettingsRepository(c.session)
    c.calc_repository.table = c.calculations
    c.calc = ConstructionCalculationSettingsService(
        session=c.session, repository=c.calc_repository,
        access_repository=c.access, event_repository=c.events,
        serialization=c.serialization, now_provider=lambda: c.now)
    c.sync.begin()
    return c


def begin(c):
    if not c.sync.in_transaction():
        c.sync.begin()


def snapshot(c):
    return tuple(
        tuple(dict(row) for row in c.sync.execute(select(table)).mappings())
        for table in (c.calculations, c.audits)
    )


async def create(c, **fields):
    begin(c)
    return await c.calc.create_settings(
        actor=c.actor,
        fields={"money_input_precision": 4, "supplier_stale_price_days": 0} | fields,
        trace_id="calculation-test")


async def patch(c, state, **fields):
    begin(c)
    return await c.calc.update_settings(
        actor=c.actor, expected_row_version=state.row_version,
        fields=fields, trace_id="calculation-test")


def test_model_matches_applied_storage():
    table = ConstructionCalculationSetting.__table__
    assert list(table.c.keys()) == [
        "tenant_id", "money_input_precision", "quantity_precision",
        "minimum_job_amount", "minimum_job_currency", "supplier_stale_price_days",
        "default_technological_waste_percent", "default_local_repair_percent",
        "row_version", "created_by", "created_at", "updated_at"]
    assert len(table.constraints) == 12
    assert ConstructionCalculationSetting.__mapper__.version_id_col is table.c.row_version
    assert str(table.c.quantity_precision.server_default.arg) == "4"
    assert table.c.row_version.type.compile(dialect=postgresql.dialect()) == "BIGINT"
    for name in (
        "money_input_precision", "supplier_stale_price_days",
        "minimum_job_amount", "minimum_job_currency",
        "default_technological_waste_percent", "default_local_repair_percent",
    ):
        assert table.c[name].default is None and table.c[name].server_default is None
    assert table.c.minimum_job_amount.type.scale is None


@pytest.mark.asyncio
async def test_absent_read_does_not_create_settings(calculation):
    c = calculation
    assert await c.calc.get_settings(actor=c.actor) is None
    assert snapshot(c) == ((), ())
    assert c.session.commits == 0
    c.serialization.lock_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_sparse_edit_exact_decimal_and_correlated_audit(calculation):
    c = calculation
    amount = Decimal("123456789012345678901234567890.123456789")
    with localcontext() as context:
        context.prec = 2
        original = (await create(c, minimum_job_amount=amount,
                                 minimum_job_currency="EUR")).settings
        edited = await patch(c, original, quantity_precision=6,
                             default_local_repair_percent=Decimal("12.3456789"))
    assert original.quantity_precision == 4
    assert original.supplier_stale_price_days == 0
    assert original.default_technological_waste_percent is None
    assert edited.settings.minimum_job_amount == amount
    assert edited.settings.minimum_job_currency == "EUR"
    assert edited.settings.row_version == 2
    assert edited.settings.created_by == original.created_by
    assert edited.settings.created_at == original.created_at
    _, audits = snapshot(c)
    assert len(audits) == 2
    event = next(row for row in audits
                 if row["event_type"] == "construction_calculation_settings_updated")
    assert event["entity_id"] == c.tenant
    assert event["tenant_id"] == c.tenant and event["user_id"] == c.user
    assert event["trace_id"] == "calculation-test"
    assert event["payload"]["changes"] == [
        {"field": "quantity_precision", "old_value": 4, "new_value": 6},
        {"field": "default_local_repair_percent",
         "old_value": None, "new_value": "12.3456789"},
    ]
    begin(c)
    assert await c.calc.get_settings(actor=c.actor) == edited.settings


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [{}, {"money_input_precision": 4}])
async def test_current_noop_and_stale_noop(calculation, fields):
    c = calculation
    original = (await create(c)).settings
    before = snapshot(c)
    unchanged = await patch(c, original, **fields)
    assert unchanged.changed is False and snapshot(c) == before
    await patch(c, original, quantity_precision=5)
    with pytest.raises(ConstructionRowVersionConflictError):
        await patch(c, original, **fields)
    assert len(snapshot(c)[1]) == 2


@pytest.mark.asyncio
async def test_minimum_pair_can_be_cleared_but_not_left_incomplete(calculation):
    c = calculation
    original = (await create(c, minimum_job_amount=Decimal("0"),
                             minimum_job_currency="EUR")).settings
    before = snapshot(c)
    with pytest.raises(Validation):
        await patch(c, original, minimum_job_currency=None)
    assert snapshot(c) == before
    cleared = await patch(c, original, minimum_job_amount=None, minimum_job_currency=None)
    assert cleared.settings.minimum_job_amount is None
    assert cleared.settings.minimum_job_currency is None


@pytest.mark.asyncio
async def test_duplicate_creation_is_not_an_implicit_update(calculation):
    c = calculation
    await create(c)
    before = snapshot(c)
    with pytest.raises(Conflict):
        await create(c, money_input_precision=2)
    assert snapshot(c) == before


@pytest.mark.parametrize("bad", [
    {}, {"money_input_precision": 2},
    {"money_input_precision": True, "supplier_stale_price_days": 1},
    {"money_input_precision": 7, "supplier_stale_price_days": 1},
    {"money_input_precision": -1, "supplier_stale_price_days": 1},
    {"money_input_precision": 2, "supplier_stale_price_days": None},
    {"money_input_precision": 2, "supplier_stale_price_days": 3651},
])
def test_creation_requires_explicit_valid_non_boolean_integers(bad):
    with pytest.raises(Validation):
        validated_fields(bad, complete=True)


@pytest.mark.parametrize("bad", [
    {"quantity_precision": True}, {"quantity_precision": -1},
    {"quantity_precision": 7}, {"quantity_precision": 4.0},
    {"supplier_stale_price_days": -1}, {"supplier_stale_price_days": 1.5},
    {"minimum_job_amount": Decimal("NaN")},
    {"minimum_job_amount": Decimal("sNaN")},
    {"minimum_job_amount": Decimal("Infinity")},
    {"minimum_job_amount": Decimal("-Infinity")},
    {"minimum_job_amount": Decimal("-0.01")},
    {"minimum_job_amount": 1.0},
    {"default_technological_waste_percent": Decimal("100.01")},
    {"default_local_repair_percent": Decimal("-0.01")},
    {"default_local_repair_percent": "10"},
    {"minimum_job_currency": "eur"}, {"minimum_job_currency": "ZZZ"},
    {"tenant_id": None}, {"created_by": None}, {"row_version": 2},
])
def test_sparse_validation_rejects_invalid_values_and_immutable_fields(bad):
    with pytest.raises(Validation):
        validated_fields(bad)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_existing_tenant_management_roles(calculation, role):
    c = calculation
    c.grant.role = role
    assert (await create(c)).settings.created_by == c.user


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "project_scope"])
async def test_denied_authority_performs_no_write(calculation, failure):
    c = calculation
    if failure == "membership":
        c.membership = False
    else:
        c.grant.role = "OBSERVER"
        c.grant.scope_type = "project"
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await create(c)
    assert snapshot(c) == ((), ())


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit", "revoked_after_audit", "cancel"])
async def test_real_local_write_and_audit_roll_back_together(calculation, failure):
    c = calculation
    if failure == "audit":
        c.fail_audit = True
    elif failure == "commit":
        c.session.fail_commit = True
    elif failure == "revoked_after_audit":
        c.after_audit = lambda: setattr(c, "membership", False)
    else:
        original_audit = c.events.create_event.side_effect
        async def cancel(**kwargs):
            await original_audit(**kwargs)
            raise asyncio.CancelledError()
        c.events.create_event.side_effect = cancel
    expected = (
        asyncio.CancelledError if failure == "cancel"
        else ConstructionPermissionDeniedError if failure == "revoked_after_audit"
        else Operation
    )
    with pytest.raises(expected):
        await create(c)
    assert snapshot(c) == ((), ())
    assert c.session.rollbacks >= 1


@pytest.mark.asyncio
async def test_caller_owned_transaction_can_roll_back_settings_and_audit(calculation):
    c = calculation
    result = await c.calc.create_settings(
        actor=c.actor, fields={
            "money_input_precision": 6, "supplier_stale_price_days": 3650,
            "quantity_precision": 0,
            "default_technological_waste_percent": Decimal("100"),
            "default_local_repair_percent": Decimal("0"),
        }, manage_transaction=False)
    assert result.settings.row_version == 1
    assert c.session.commits == 0
    assert all(len(rows) == 1 for rows in snapshot(c))
    await c.session.rollback()
    assert snapshot(c) == ((), ())


@pytest.mark.asyncio
async def test_read_rechecks_authority_after_query(calculation):
    c = calculation
    await create(c)
    begin(c)
    original = c.calc_repository.get_settings
    async def changed(**kwargs):
        row = await original(**kwargs)
        c.membership = False
        return row
    c.calc_repository.get_settings = changed
    with pytest.raises(ConstructionPermissionDeniedError):
        await c.calc.get_settings(actor=c.actor)


def test_repository_keeps_tenant_and_version_in_conditional_update(calculation):
    c = calculation
    assert c.calc_repository.table is c.calculations
    assert "tenant_id" in str(
        select(ConstructionCalculationSetting.__table__).where(
            ConstructionCalculationSetting.tenant_id == c.tenant
        ).compile(dialect=postgresql.dialect())
    )


@pytest.mark.parametrize("value", [
    None, Decimal("0"),
    Decimal("123456789012345678901234567890.123456789"),
])
def test_nullable_decimal_adapter_retains_null_and_exact_decimal(value):
    adapter = NullableLocalDecimal()
    encoded = adapter.process_bind_param(value, None)
    assert adapter.process_result_value(encoded, None) == value


@pytest.mark.parametrize("value", ["10", 10, 10.0, True])
def test_nullable_decimal_adapter_still_rejects_non_decimal_values(value):
    with pytest.raises(ValueError):
        NullableLocalDecimal().process_bind_param(value, None)
