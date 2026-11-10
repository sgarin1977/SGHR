"""Exact precision behavior plus existing service over local SQLite only."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from decimal import Decimal, Inexact, Rounded, ROUND_DOWN, localcontext
import json
from uuid import uuid4

import pytest

from services.construction.construction_calculation_settings import (
    ConstructionCalculationSettingsState as State,
    ConstructionCalculationSettingsNotFoundError,
)
from services.construction.construction_calculation_precision import (
    ConstructionCalculationPrecision as Precision,
    ConstructionCalculationPrecisionError as Invalid,
    precision_from_calculation_settings,
    restore_calculation_precision,
    read_calculation_precision,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_unit_conversion import convert_unit_quantity
from test_construction_02_calculation_settings import (
    case, calculation, begin, create, snapshot,
)

D = Decimal


@pytest.fixture
def settings():
    now = datetime(2026, 11, 10, 10, tzinfo=timezone.utc)
    return State(
        tenant_id=uuid4(), money_input_precision=4, quantity_precision=4,
        minimum_job_amount=None, minimum_job_currency=None,
        supplier_stale_price_days=0,
        default_technological_waste_percent=None,
        default_local_repair_percent=None,
        row_version=1, created_by=uuid4(), created_at=now, updated_at=now)


@pytest.mark.parametrize("places", range(7))
def test_each_approved_quantity_precision_uses_half_up(settings, places):
    policy = precision_from_calculation_settings(
        settings=replace(settings, quantity_precision=places),
        tenant_id=settings.tenant_id)
    value = D("1.5") if places == 0 else D("1." + "0" * places + "5")
    expected = D("2") if places == 0 else D("1." + "0" * (places - 1) + "1")
    assert policy.round_calculated_quantity(value) == expected
    assert policy.round_calculated_quantity(-value) == -expected


@pytest.mark.parametrize("value,expected", [
    ("999.995", "1000.00"), ("1.234", "1.23"),
    ("1.235", "1.24"), ("-1.235", "-1.24"),
    ("0.000001", "0.00"), ("-0.000001", "-0.00"),
])
def test_quantity_boundaries(settings, value, expected):
    policy = precision_from_calculation_settings(
        settings=replace(settings, quantity_precision=2), tenant_id=settings.tenant_id)
    assert policy.round_calculated_quantity(D(value)) == D(expected)


def test_quantity_rounding_follows_exact_unit_conversion(settings):
    converted = convert_unit_quantity(D("1234.56789"), "MM", "M")
    policy = precision_from_calculation_settings(
        settings=replace(settings, quantity_precision=3), tenant_id=settings.tenant_id)
    assert converted.quantity == D("1.23456789")
    assert policy.round_calculated_quantity(converted.quantity) == D("1.235")
    assert converted.quantity == D("1.23456789")


def test_ambient_decimal_context_cannot_change_results(settings):
    policy = precision_from_calculation_settings(
        settings=replace(settings, quantity_precision=2), tenant_id=settings.tenant_id)
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        flags = context.flags.copy()
        assert policy.round_calculated_quantity(D("999.995")) == D("1000.00")
        original = D("12345678901234567890.123400")
        assert policy.require_money_input(original) is original
        assert context.flags == flags
        assert context.prec == 2 and context.rounding == ROUND_DOWN


@pytest.mark.parametrize("places", range(7))
def test_money_input_precision_does_not_round(settings, places):
    policy = precision_from_calculation_settings(
        settings=replace(settings, money_input_precision=places),
        tenant_id=settings.tenant_id)
    valid = D("12") if places == 0 else D("12." + "1" * places)
    assert policy.require_money_input(valid) is valid
    with pytest.raises(Invalid):
        policy.require_money_input(D("12." + "0" * places + "1"))


@pytest.mark.parametrize("value", ["1.2300", "100.000000", "0E-1000000"])
def test_redundant_zeros_do_not_change_numeric_precision(settings, value):
    policy = precision_from_calculation_settings(
        settings=replace(settings, money_input_precision=2), tenant_id=settings.tenant_id)
    original = D(value)
    assert policy.require_money_input(original) is original


@pytest.mark.parametrize("value", [
    True, 1, 1.25, "1.25", D("NaN"), D("sNaN"), D("Infinity"),
])
def test_non_decimal_or_nonfinite_values_are_rejected(settings, value):
    policy = precision_from_calculation_settings(
        settings=settings, tenant_id=settings.tenant_id)
    with pytest.raises(Invalid):
        policy.round_calculated_quantity(value)
    with pytest.raises(Invalid):
        policy.require_money_input(value)


@pytest.mark.parametrize("name", ["money_input_precision", "quantity_precision"])
@pytest.mark.parametrize("value", [-1, 7, None, True, D("2")])
def test_invalid_precision_is_not_replaced_with_a_default(settings, name, value):
    with pytest.raises(Invalid):
        precision_from_calculation_settings(
            settings=replace(settings, **{name: value}), tenant_id=settings.tenant_id)


def test_source_tenant_must_match(settings):
    with pytest.raises(Invalid):
        precision_from_calculation_settings(settings=settings, tenant_id=uuid4())


def test_snapshot_remains_pinned_after_current_settings_change(settings):
    original = precision_from_calculation_settings(
        settings=replace(settings, quantity_precision=2), tenant_id=settings.tenant_id)
    saved = json.loads(json.dumps(original.as_snapshot()))
    current = precision_from_calculation_settings(
        settings=replace(settings, row_version=2, quantity_precision=4),
        tenant_id=settings.tenant_id)
    restored = restore_calculation_precision(snapshot=saved, tenant_id=settings.tenant_id)
    assert restored == original and restored.settings_row_version == 1
    assert current.settings_row_version == 2
    assert restored.round_calculated_quantity(D("1.23456")) == D("1.23")
    assert current.round_calculated_quantity(D("1.23456")) == D("1.2346")
    with pytest.raises(FrozenInstanceError):
        restored.quantity_precision = 6


@pytest.mark.parametrize("change", [
    {"schema_version": True}, {"source": "caller"},
    {"quantity_rounding": "ROUND_DOWN"}, {"settings_row_version": 0},
    {"settings_row_version": True}, {"quantity_precision": 7},
    {"unexpected": "field"},
])
def test_malformed_snapshot_is_rejected(settings, change):
    policy = precision_from_calculation_settings(
        settings=settings, tenant_id=settings.tenant_id)
    with pytest.raises(Invalid):
        restore_calculation_precision(
            snapshot=policy.as_snapshot() | change, tenant_id=settings.tenant_id)


def test_snapshot_cannot_be_restored_for_another_tenant(settings):
    policy = precision_from_calculation_settings(
        settings=settings, tenant_id=settings.tenant_id)
    with pytest.raises(Invalid):
        restore_calculation_precision(snapshot=policy.as_snapshot(), tenant_id=uuid4())


@pytest.mark.asyncio
async def test_authorized_read_uses_stored_values_without_writes(calculation):
    c = calculation
    await create(c, money_input_precision=2, quantity_precision=3)
    begin(c)
    before, commits = snapshot(c), c.session.commits
    policy = await read_calculation_precision(service=c.calc, actor=c.actor)
    assert policy.tenant_id == c.tenant
    assert policy.money_input_precision == 2 and policy.quantity_precision == 3
    assert policy.round_calculated_quantity(D("1.2345")) == D("1.235")
    assert snapshot(c) == before and c.session.commits == commits


@pytest.mark.asyncio
async def test_absent_settings_are_not_implicitly_created(calculation):
    c = calculation
    before = snapshot(c)
    with pytest.raises(ConstructionCalculationSettingsNotFoundError):
        await read_calculation_precision(service=c.calc, actor=c.actor)
    assert snapshot(c) == before and c.session.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "permission"])
async def test_existing_authorization_is_preserved(calculation, failure):
    c = calculation
    await create(c)
    begin(c)
    before = snapshot(c)
    if failure == "membership":
        c.membership = False
    else:
        c.grant.role = "OBSERVER"
        c.grant.scope_type = "project"
        c.grant.scope_id = uuid4()
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await read_calculation_precision(service=c.calc, actor=c.actor)
    assert snapshot(c) == before
