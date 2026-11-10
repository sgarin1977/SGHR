"""Pure CON-020 evaluators: exact boundaries, absent data and action context."""
from dataclasses import replace
from decimal import Decimal, Inexact, Rounded, localcontext
from uuid import uuid4

import pytest

from services.construction.construction_checks import (
    ConstructionCheckContext as Context, ConstructionCheckCode as Code,
    ConstructionCheckSeverity as Severity, ConstructionCheckContractError as ContractError,
    ConstructionCheckBlockedError, collect_construction_checks)
from services.construction.construction_check_evaluators import (
    check_opening_area, check_required_price, check_required_tax, check_negative_margin)


@pytest.fixture
def context():
    return Context(tenant_id=uuid4(), user_id=uuid4(),
                   action="construction.estimate.review", project_id=uuid4())


@pytest.mark.parametrize("wall,openings,blocked", [
    ("10", "9.999999999999999999999999999999999999999", False),
    ("10", "10.000000000000000000000000000000000000000", False),
    ("10", "10.000000000000000000000000000000000000001", True),
    ("0", "0", False), ("0", "0.000000000000000000000000000000000000001", True),
])
def test_opening_area_uses_strict_exact_boundary_without_rounded_tolerance(context, wall, openings, blocked):
    wall, openings = Decimal(wall), Decimal(openings)
    with localcontext() as arithmetic:
        arithmetic.prec = 2
        arithmetic.traps[Inexact] = True
        arithmetic.traps[Rounded] = True
        findings = check_opening_area(context=context, wall_area=wall, opening_area=openings,
                                      field_path="areas.0.opening_area")
        report = collect_construction_checks(context=context, findings=findings)
    assert report.is_blocked is blocked
    if blocked:
        finding = findings[0]
        assert finding.code is Code.OPENINGS_EXCEED_WALL_AREA and finding.severity is Severity.ERROR
        assert finding.context == context and finding.field_path == "areas.0.opening_area"
        assert finding.parameters == {"wall_area": wall, "opening_area": openings}
        assert finding.to_payload()["parameters"]["opening_area"] == str(openings)
    else:
        assert findings == ()


@pytest.mark.parametrize("function,argument,code", [
    (check_required_price, "price", Code.PRICE_MISSING),
    (check_required_tax, "rate_percent", Code.TAX_MISSING),
])
def test_missing_required_data_is_a_blocking_error_not_an_invented_zero(context, function, argument, code):
    findings = function(context=context, field_path="lines.0.value", **{argument: None})
    report = collect_construction_checks(context=context, findings=findings)
    assert len(findings) == 1 and findings[0].code is code and findings[0].parameters == {}
    assert report.is_blocked
    with pytest.raises(ConstructionCheckBlockedError):
        report.raise_for_errors()


@pytest.mark.parametrize("function,argument,values", [
    (check_required_price, "price", ("0", "0.00000000000000000000000001", "123.456789012345678901234567890")),
    (check_required_tax, "rate_percent", ("0", "100", "99.999999999999999999999999999")),
])
def test_explicit_zero_and_valid_exact_values_are_present(context, function, argument, values):
    for value in values:
        assert function(context=context, **{argument: Decimal(value)}) == ()


@pytest.mark.parametrize("margin,warn", [
    ("-0.000000000000000000000000000000000000001", True),
    ("-100000000000000000000000000000000000000", True),
    ("-0", False), ("0", False), ("0.000000000000000000000000000000000000001", False),
])
def test_negative_margin_is_warning_without_an_implicit_approval_bypass(context, margin, warn):
    amount = Decimal(margin)
    with localcontext() as arithmetic:
        arithmetic.prec = 1
        arithmetic.traps[Inexact] = True
        arithmetic.traps[Rounded] = True
        findings = check_negative_margin(context=context, margin=amount, field_path="totals.margin")
        report = collect_construction_checks(context=context, findings=findings)
    assert not report.is_blocked and report.requires_attention is warn
    assert report.raise_for_errors() is None
    if warn:
        assert findings[0].code is Code.NEGATIVE_MARGIN
        assert findings[0].parameters["margin"] == amount
        assert findings[0].to_payload()["parameters"] == {"margin": str(amount)}
    else:
        assert findings == ()


CALLS = [
    (check_required_price, {"price": Decimal("1")}, "price"),
    (check_required_tax, {"rate_percent": Decimal("20")}, "rate_percent"),
    (check_opening_area, {"wall_area": Decimal("10"), "opening_area": Decimal("1")}, "wall_area"),
    (check_opening_area, {"wall_area": Decimal("10"), "opening_area": Decimal("1")}, "opening_area"),
    (check_negative_margin, {"margin": Decimal("1")}, "margin"),
]


@pytest.mark.parametrize("function,values,name", CALLS)
@pytest.mark.parametrize("bad", [True, 0, 1.5, "0", Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_inexact_unknown_or_nonfinite_inputs_fail_before_a_successful_report(context, function, values, name, bad):
    with pytest.raises(ContractError):
        function(context=context, **(values | {name: bad}))


@pytest.mark.parametrize("function,values,name", CALLS)
def test_none_is_not_assumed_zero_for_area_or_margin(context, function, values, name):
    if function in (check_required_price, check_required_tax):
        assert function(context=context, **(values | {name: None}))
    else:
        with pytest.raises(ContractError):
            function(context=context, **(values | {name: None}))


@pytest.mark.parametrize("function,values", [
    (check_required_price, {"price": Decimal("-0.01")}),
    (check_required_tax, {"rate_percent": Decimal("-0.01")}),
    (check_required_tax, {"rate_percent": Decimal("100.000000000000000000000001")}),
    (check_opening_area, {"wall_area": Decimal("-1"), "opening_area": Decimal("0")}),
    (check_opening_area, {"wall_area": Decimal("1"), "opening_area": Decimal("-1")}),
])
def test_numeric_domains_are_checked_without_silently_clamping(context, function, values):
    with pytest.raises(ContractError):
        function(context=context, **values)


@pytest.mark.parametrize("function,values,name", CALLS)
def test_context_and_location_are_checked_even_when_no_finding_is_expected(context, function, values, name):
    with pytest.raises(ContractError):
        function(context=None, **values)
    with pytest.raises(ContractError):
        function(context=context, field_path="../untrusted", **values)


def test_evaluations_aggregate_without_mutating_their_inputs_or_losing_errors(context):
    values = {"wall_area": Decimal("10"), "opening_area": Decimal("11")}
    original = dict(values)
    findings = (
        *check_opening_area(context=context, field_path="wall.openings", **values),
        *check_required_price(context=context, price=None, field_path="line.price"),
        *check_required_tax(context=context, rate_percent=None, field_path="line.tax"),
        *check_negative_margin(context=context, margin=Decimal("-1.25"), field_path="totals.margin"),
    )
    report = collect_construction_checks(context=context, findings=findings)
    assert values == original
    assert [item.code for item in report.errors] == [
        Code.OPENINGS_EXCEED_WALL_AREA, Code.PRICE_MISSING, Code.TAX_MISSING]
    assert [item.code for item in report.warnings] == [Code.NEGATIVE_MARGIN]
    assert report.is_blocked and report.requires_attention
    with pytest.raises(ConstructionCheckBlockedError):
        report.raise_for_errors()


def test_different_actions_cannot_share_a_success_or_error_assessment(context):
    other = replace(context, action="construction.measurement.create")
    finding = check_required_price(context=context, price=None)[0]
    with pytest.raises(ContractError):
        collect_construction_checks(context=other, findings=[finding])
    # Commercial checks are selected explicitly by the domain service, not globally.
    assert not collect_construction_checks(context=other, findings=[]).is_blocked
