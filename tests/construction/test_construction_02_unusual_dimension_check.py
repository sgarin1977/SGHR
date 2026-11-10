"""Exact advisory ranges and local message rendering; no external operations."""
from decimal import Decimal, Inexact, Rounded, localcontext
from uuid import uuid4

import pytest

from services.construction.construction_checks import (
    ConstructionCheckContext as Context,
    ConstructionCheckCode as Code,
    ConstructionCheckSeverity as Severity,
    ConstructionCheckContractError as ContractError,
    ConstructionCheckBlockedError,
    collect_construction_checks,
)
from services.construction.construction_check_dimensions import check_unusual_dimension
from services.construction.construction_check_evaluators import check_required_tax
from test_construction_02_check_rendering import (
    case, rendered, business_rows,
)


@pytest.fixture
def context():
    return Context(
        tenant_id=uuid4(), user_id=uuid4(),
        action="construction.measurement.item.update", project_id=uuid4())


def evaluate(context, **changes):
    values = dict(
        context=context,
        value=Decimal("5"),
        expected_minimum=Decimal("1"),
        expected_maximum=Decimal("10"),
        field_path="areas.0.width",
    )
    values.update(changes)
    return check_unusual_dimension(**values)


@pytest.mark.parametrize("value,minimum,maximum,warn", [
    ("1", "1", "10", False),
    ("10", "1", "10", False),
    ("5", "1", "10", False),
    ("0.999999999999999999999999999999999", "1", "10", True),
    ("10.000000000000000000000000000000001", "1", "10", True),
    ("5", "5", "5", False),
    ("4.999999999999999999999999999999999", "5", "5", True),
    ("0", "0", "0", False),
    ("-2", "-2", "2", False),
    ("-2.00000000000000000000000000000001", "-2", "2", True),
])
def test_inclusive_advisory_boundaries_use_exact_decimal(
    context, value, minimum, maximum, warn,
):
    measured, lower, upper = map(Decimal, (value, minimum, maximum))
    original = measured.as_tuple(), lower.as_tuple(), upper.as_tuple()
    with localcontext() as arithmetic:
        arithmetic.prec = 1
        arithmetic.traps[Inexact] = True
        arithmetic.traps[Rounded] = True
        findings = evaluate(
            context, value=measured,
            expected_minimum=lower, expected_maximum=upper)
    assert bool(findings) is warn
    assert (measured.as_tuple(), lower.as_tuple(), upper.as_tuple()) == original
    if warn:
        finding, = findings
        assert finding.code is Code.UNUSUAL_DIMENSION
        assert finding.severity is Severity.WARNING
        assert finding.parameters == {"value": measured}


@pytest.mark.parametrize("value,minimum,maximum,warn", [
    ("1", "2", None, True),
    ("2", "2", None, False),
    ("999999", "2", None, False),
    ("11", None, "10", True),
    ("10", None, "10", False),
    ("-999999", None, "10", False),
    ("999999999999999999999", None, None, False),
])
def test_explicit_one_sided_or_absent_advisory_bounds(
    context, value, minimum, maximum, warn,
):
    findings = evaluate(
        context, value=Decimal(value),
        expected_minimum=None if minimum is None else Decimal(minimum),
        expected_maximum=None if maximum is None else Decimal(maximum))
    assert bool(findings) is warn


@pytest.mark.parametrize("name", ["value", "expected_minimum", "expected_maximum"])
@pytest.mark.parametrize("bad", [
    True, 1, 1.5, "1", Decimal("NaN"),
    Decimal("Infinity"), Decimal("-Infinity"),
])
def test_non_decimal_or_nonfinite_values_are_rejected(context, name, bad):
    with pytest.raises(ContractError):
        evaluate(context, **{name: bad})


def test_unknown_measurement_is_not_assumed_zero(context):
    with pytest.raises(ContractError):
        evaluate(
            context, value=None,
            expected_minimum=None, expected_maximum=None)


def test_reversed_range_is_invalid_configuration_not_an_unusual_value(context):
    with pytest.raises(ContractError):
        evaluate(
            context, value=Decimal("0"),
            expected_minimum=Decimal("10"), expected_maximum=Decimal("1"))


def test_context_and_location_are_validated_without_configured_bounds(context):
    with pytest.raises(ContractError):
        evaluate(None, expected_minimum=None, expected_maximum=None)
    with pytest.raises(ContractError):
        evaluate(
            context, expected_minimum=None, expected_maximum=None,
            field_path="../untrusted")


def test_advisory_warning_requires_attention_without_blocking_or_clamping(context):
    measured = Decimal("10.00000000000000000000000000001")
    finding, = evaluate(context, value=measured)
    report = collect_construction_checks(context=context, findings=(finding,))
    assert measured == Decimal("10.00000000000000000000000000001")
    assert report.is_blocked is False
    assert report.requires_attention is True
    assert report.raise_for_errors() is None
    assert finding.to_payload() == {
        "code": "UNUSUAL_DIMENSION",
        "severity": "WARNING",
        "message_key": "construction.checks.unusual_dimension",
        "field_path": "areas.0.width",
        "parameters": {"value": str(measured)},
    }


def test_advisory_result_does_not_remove_a_blocking_error(context):
    findings = (
        *evaluate(context, value=Decimal("100")),
        *check_required_tax(context=context, rate_percent=None),
    )
    report = collect_construction_checks(context=context, findings=findings)
    assert report.is_blocked and report.requires_attention
    assert report.errors[0].code is Code.TAX_MISSING
    assert report.warnings[0].code is Code.UNUSUAL_DIMENSION
    with pytest.raises(ConstructionCheckBlockedError):
        report.raise_for_errors()


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["uk", "fi"])
async def test_warning_uses_existing_localization_without_translating_numbers(
    rendered, language,
):
    c = rendered
    c.language = language
    value = Decimal("100.123456789012345678901234567890")
    findings = check_unusual_dimension(
        context=c.context, value=value,
        expected_minimum=Decimal("1"), expected_maximum=Decimal("10"),
        field_path="areas.0.width")
    report = collect_construction_checks(context=c.context, findings=findings)
    before = business_rows(c)
    result = await c.renderer.render_for_actor(actor=c.actor, report=report)
    payload = result.to_payload()
    row, = payload["findings"]
    assert payload["language_code"] == language
    assert payload["is_blocked"] is False
    assert payload["requires_attention"] is True
    assert row["parameters"] == {"value": str(value)}
    assert row["message"] != row["message_key"]
    assert row["message"] != row["code"]
    assert row["severity_label"] != "WARNING"
    assert business_rows(c) == before
    if language == "uk":
        c.provider.translate.assert_not_awaited()
    else:
        assert c.provider.translate.await_count == 2
        for call in c.provider.translate.await_args_list:
            text = call.kwargs["text"]
            assert str(value) not in text and "areas.0.width" not in text
