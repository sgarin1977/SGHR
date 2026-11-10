"""Pure canonical-input pricing formula; no runtime services or database."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, Inexact, Rounded, ROUND_DOWN, localcontext
import json
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import pytest

from services.construction.construction_pricing_profiles import (
    ConstructionPricingProfileState as Profile,
)
from services.construction.construction_project_pricing_adjustments import (
    ConstructionProjectPricingAdjustmentState as Adjustment,
)
from services.construction.construction_work_pricing import (
    apply_work_pricing, ConstructionWorkPricingValidationError as Invalid,
)

D = Decimal


@pytest.fixture
def pricing_input():
    tenant, project, creator = uuid4(), uuid4(), uuid4()
    now = datetime(2026, 11, 10, 10, tzinfo=timezone.utc)
    profile = Profile(
        id=uuid4(), tenant_id=tenant, code="STANDARD",
        work_coefficient=D("1.20"), material_markup_percent=D("99"),
        row_version=3, created_by=creator, created_at=now, updated_at=now)
    def adjustment(number, coefficient, enabled=True):
        return Adjustment(
            id=UUID(int=number), tenant_id=tenant, project_id=project,
            name="Explicit adjustment " + str(number),
            coefficient=D(coefficient), enabled=enabled,
            row_version=2, created_by=creator, created_at=now, updated_at=now)
    return NS(
        tenant=tenant, project=project, profile=profile,
        first=adjustment(1, "1.10"),
        second=adjustment(2, "0.95"),
        disabled=adjustment(3, "9.00", False))


def calculate(c, **overrides):
    return apply_work_pricing(**({
        "tenant_id": c.tenant,
        "project_id": c.project,
        "base_sell_price": D("100.12345"),
        "profile": c.profile,
        "adjustments": (c.first, c.second, c.disabled),
    } | overrides))


def test_formula_applies_only_enabled_explicit_adjustments(pricing_input):
    c = pricing_input
    result = calculate(c)
    assert result.work_multiplier == D("1.254")
    assert result.effective_unit_price == D("125.55480630")
    assert result.applied_adjustment_ids == (c.first.id, c.second.id)
    assert result.base_sell_price == D("100.12345")
    assert result.profile.material_markup_percent == D("99")
    assert c.disabled in result.adjustments
    assert result.as_snapshot()["adjustments"][2]["enabled"] is False


def test_no_adjustments_uses_only_the_explicit_profile_coefficient(pricing_input):
    c = pricing_input
    result = calculate(c, adjustments=())
    assert result.work_multiplier == D("1.20")
    assert result.effective_unit_price == D("120.148140")
    assert result.applied_adjustment_ids == ()


@pytest.mark.parametrize("code", ["ECONOMY", "STANDARD", "PREMIUM"])
def test_price_level_name_does_not_invent_a_coefficient(pricing_input, code):
    c = pricing_input
    result = calculate(c, profile=replace(c.profile, code=code))
    assert result.work_multiplier == D("1.254")
    assert result.profile.code == code


def test_input_order_does_not_change_calculation_or_snapshot(pricing_input):
    c = pricing_input
    first = calculate(c)
    reordered = calculate(c, adjustments=(c.disabled, c.second, c.first))
    assert reordered == first
    assert reordered.as_snapshot() == first.as_snapshot()


def test_ambient_context_cannot_round_coefficients_or_price(pricing_input):
    c = pricing_input
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        flags = context.flags.copy()
        result = calculate(c)
        assert result.work_multiplier == D("1.254")
        assert result.effective_unit_price == D("125.55480630")
        assert context.flags == flags
        assert context.prec == 2 and context.rounding == ROUND_DOWN


def test_coefficient_precision_is_not_quantity_precision(pricing_input):
    c = pricing_input
    coefficient = D("1.123456789012345678901234567890")
    result = calculate(
        c, base_sell_price=D("1"),
        profile=replace(c.profile, work_coefficient=coefficient), adjustments=())
    assert result.work_multiplier == coefficient
    assert result.effective_unit_price == coefficient


def test_explicit_zero_price_is_not_a_missing_price(pricing_input):
    assert calculate(pricing_input, base_sell_price=D("0")).effective_unit_price == D("0")


@pytest.mark.parametrize("value", [
    None, True, 10, 10.5, "10.5", D("-1"),
    D("NaN"), D("sNaN"), D("Infinity"),
])
def test_missing_inexact_or_invalid_base_price_is_rejected(pricing_input, value):
    with pytest.raises(Invalid):
        calculate(pricing_input, base_sell_price=value)


@pytest.mark.parametrize("change", [
    {"tenant_id": uuid4()},
    {"work_coefficient": D("0")},
    {"work_coefficient": D("NaN")},
    {"material_markup_percent": D("101")},
    {"row_version": True},
])
def test_invalid_profile_is_not_replaced_with_a_default(pricing_input, change):
    c = pricing_input
    with pytest.raises(Invalid):
        calculate(c, profile=replace(c.profile, **change))


@pytest.mark.parametrize("change", [
    {"tenant_id": uuid4()},
    {"project_id": uuid4()},
    {"coefficient": D("0")},
    {"coefficient": D("Infinity")},
    {"enabled": 1},
    {"row_version": 0},
])
def test_invalid_adjustment_context_is_rejected(pricing_input, change):
    c = pricing_input
    with pytest.raises(Invalid):
        calculate(c, adjustments=(replace(c.first, **change),))


def test_duplicate_adjustment_cannot_be_applied_twice(pricing_input):
    c = pricing_input
    with pytest.raises(Invalid):
        calculate(c, adjustments=(c.first, c.first))


@pytest.mark.parametrize("value", [None, [], ({},)])
def test_canonical_adjustment_tuple_is_required(pricing_input, value):
    with pytest.raises(Invalid):
        calculate(pricing_input, adjustments=value)


def test_explicit_profile_is_required(pricing_input):
    with pytest.raises(Invalid):
        calculate(pricing_input, profile=None)


def test_source_changes_do_not_rewrite_previous_result(pricing_input):
    c = pricing_input
    original = calculate(c)
    saved = json.loads(json.dumps(original.as_snapshot()))
    changed = calculate(
        c,
        profile=replace(c.profile, work_coefficient=D("2"), row_version=4),
        adjustments=(replace(c.first, enabled=False, row_version=3),))
    assert changed.effective_unit_price == D("200.24690")
    assert changed.applied_adjustment_ids == ()
    assert original.as_snapshot() == saved
    assert saved["pricing_profile"]["row_version"] == 3
    assert Decimal(saved["effective_unit_price"]) == D("125.55480630")


def test_snapshot_is_detached_from_frozen_calculation(pricing_input):
    result = calculate(pricing_input)
    snapshot = result.as_snapshot()
    snapshot["pricing_profile"]["work_coefficient"] = "999"
    snapshot["adjustments"].clear()
    assert result.work_multiplier == D("1.254")
    assert len(result.adjustments) == 3
