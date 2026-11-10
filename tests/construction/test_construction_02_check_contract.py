"""Local contract tests only; no database, provider, environment or public route."""
import json
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext
from uuid import uuid4

import pytest

from services.construction.construction_checks import (
    CHECK_RULES, ConstructionCheckSeverity as Severity, ConstructionCheckCode as Code,
    ConstructionCheckContext as Context, ConstructionCheckFinding as Finding,
    ConstructionCheckReport as Report, ConstructionCheckContractError as ContractError,
    ConstructionCheckBlockedError as BlockedError, collect_construction_checks)


@pytest.fixture
def context():
    return Context(tenant_id=uuid4(), user_id=uuid4(),
                   action="construction.project_pricing_adjustment.update", project_id=uuid4())


@pytest.mark.parametrize("code,expected", [
    (Code.OPENINGS_EXCEED_WALL_AREA, Severity.ERROR),
    (Code.PRICE_MISSING, Severity.ERROR),
    (Code.TAX_MISSING, Severity.ERROR),
    (Code.SUPPLIER_PRICE_STALE, Severity.WARNING),
    (Code.NEGATIVE_MARGIN, Severity.WARNING),
    (Code.UNUSUAL_DIMENSION, Severity.WARNING),
    (Code.STANDARD_PERCENT_APPLIED, Severity.INFO),
    (Code.PRICING_PROFILE_APPLIED, Severity.INFO),
    (Code.MINIMUM_ORDER_NOT_REACHED, Severity.WARNING),
])
def test_normative_levels_control_blocking_and_attention(context, code, expected):
    finding = Finding(context, code)
    report = collect_construction_checks(context=context, findings=[finding])
    assert finding.severity is expected
    assert report.is_blocked is (expected is Severity.ERROR)
    assert report.requires_attention is (expected is Severity.WARNING)
    if expected is Severity.ERROR:
        with pytest.raises(BlockedError) as caught:
            report.raise_for_errors()
        assert caught.value.report is report
    else:
        assert report.raise_for_errors() is None


def test_mixed_results_preserve_each_error_warning_and_information(context):
    info = Finding(context, Code.PRICING_PROFILE_APPLIED, parameters={"profile_id": uuid4()})
    warning = Finding(context, Code.NEGATIVE_MARGIN, parameters={"margin": Decimal("-0.01")})
    first = Finding(context, Code.PRICE_MISSING, field_path="lines.0.unit_price")
    second = Finding(context, Code.TAX_MISSING, field_path="lines.1.tax")
    report = collect_construction_checks(context=context, findings=[info, warning, first, second])
    assert report.findings == (info, warning, first, second)
    assert report.errors == (first, second) and report.warnings == (warning,) and report.information == (info,)
    assert report.is_blocked and report.requires_attention
    with pytest.raises(BlockedError):
        report.raise_for_errors()
    assert [row["code"] for row in report.to_payload()["findings"]] == [item.code.value for item in report.findings]


def test_evaluated_empty_report_has_no_reported_errors(context):
    report = collect_construction_checks(context=context, findings=[])
    assert report.findings == () and report.errors == () and report.warnings == () and report.information == ()
    assert not report.is_blocked and not report.requires_attention
    assert report.raise_for_errors() is None


@pytest.mark.parametrize("values", [None, {}, "", "ERROR", 1, True])
def test_missing_or_malformed_results_are_not_treated_as_empty(context, values):
    with pytest.raises(ContractError):
        collect_construction_checks(context=context, findings=values)


@pytest.mark.parametrize("part", ["tenant_id", "user_id", "project_id", "action"])
def test_results_cannot_be_combined_between_contexts(context, part):
    other = replace(context, **{part: "construction.other.update" if part == "action" else uuid4()})
    finding = Finding(other, Code.PRICE_MISSING)
    with pytest.raises(ContractError):
        collect_construction_checks(context=context, findings=[finding])


@pytest.mark.parametrize("values", [
    {"tenant_id": "not-a-uuid"}, {"user_id": None}, {"project_id": "not-a-uuid"},
    {"action": "publish"}, {"action": "construction.invalid action"}, {"action": "construction.x\n"},
])
def test_context_requires_canonical_identity_and_action(context, values):
    with pytest.raises(ContractError):
        replace(context, **values)


@pytest.mark.parametrize("code", ["PRICE_MISSING", "UNKNOWN", None, {}, True])
def test_unknown_or_untyped_rules_fail_closed(context, code):
    with pytest.raises(ContractError):
        Finding(context, code)


def test_error_cannot_be_downgraded_or_acknowledged_away(context):
    with pytest.raises(TypeError):
        Finding(context, Code.PRICE_MISSING, severity=Severity.WARNING)
    report = collect_construction_checks(context=context, findings=[Finding(context, Code.TAX_MISSING)])
    with pytest.raises(TypeError):
        Report(context, report.findings, is_blocked=False)
    with pytest.raises(FrozenInstanceError):
        report.findings = ()
    with pytest.raises(TypeError):
        CHECK_RULES[Code.TAX_MISSING] = CHECK_RULES[Code.STANDARD_PERCENT_APPLIED]
    assert report.is_blocked


def test_exact_decimal_parameters_survive_small_decimal_context_and_json(context):
    value = Decimal("123.123456789012345678901234567890123456789")
    with localcontext() as precision:
        precision.prec = 2
        finding = Finding(context, Code.UNUSUAL_DIMENSION, field_path="areas.0.width", parameters={"value": value})
        payload = finding.to_payload()
    assert finding.parameters["value"] == value
    assert payload["parameters"] == {"value": str(value)}
    assert json.loads(json.dumps(payload)) == payload
    assert payload["message_key"] == "construction.checks.unusual_dimension"
    assert "message" not in payload


@pytest.mark.parametrize("value", [1.1, 1, True, "1.1", None, Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_inexact_or_nonfinite_parameters_are_rejected(context, value):
    with pytest.raises(ContractError):
        Finding(context, Code.UNUSUAL_DIMENSION, parameters={"value": value})


@pytest.mark.parametrize("code,parameters", [
    (Code.PRICE_MISSING, {"token": "secret"}),
    (Code.SUPPLIER_PRICE_STALE, {"days": 30}),
    (Code.PRICING_PROFILE_APPLIED, {"profile_id": "not-a-uuid"}),
    (Code.OPENINGS_EXCEED_WALL_AREA, {"wall_area": Decimal("-1")}),
    (Code.NEGATIVE_MARGIN, {"margin": Decimal("0")}),
    (Code.STANDARD_PERCENT_APPLIED, {"percent": Decimal("-0.01")}),
    (Code.STANDARD_PERCENT_APPLIED, {"percent": Decimal("100.01")}),
])
def test_rules_accept_only_known_domain_parameters(context, code, parameters):
    with pytest.raises(ContractError):
        Finding(context, code, parameters=parameters)


@pytest.mark.parametrize("percent", [Decimal("0"), Decimal("100")])
def test_explicit_percent_boundaries_are_valid(context, percent):
    finding = Finding(context, Code.STANDARD_PERCENT_APPLIED, parameters={"percent": percent})
    assert finding.parameters["percent"] == percent and finding.severity is Severity.INFO


def test_input_and_payload_mutations_cannot_change_assessment(context):
    original = {"percent": Decimal("12.500")}
    finding = Finding(context, Code.STANDARD_PERCENT_APPLIED, parameters=original)
    items = [finding, Finding(context, Code.PRICE_MISSING)]
    report = collect_construction_checks(context=context, findings=items)
    original["percent"] = Decimal("99")
    items.clear()
    with pytest.raises(TypeError):
        finding.parameters["percent"] = Decimal("99")
    payload = report.to_payload()
    payload["findings"].clear()
    payload["is_blocked"] = False
    assert finding.parameters["percent"] == Decimal("12.500") and len(report.findings) == 2
    assert report.is_blocked


@pytest.mark.parametrize("path", ["", "line name", "lines[0]", "../secret", "x" * 256])
def test_location_is_a_machine_path_not_executable_or_free_text(context, path):
    with pytest.raises(ContractError):
        Finding(context, Code.PRICE_MISSING, field_path=path)


def test_malformed_collection_and_nonblocking_exception_are_rejected(context):
    with pytest.raises(ContractError):
        collect_construction_checks(context=context, findings=[None])
    with pytest.raises(ContractError):
        Report(context=context, findings=[])
    with pytest.raises(ContractError):
        BlockedError(collect_construction_checks(context=context, findings=[]))


def test_payload_has_only_assessment_data_and_registered_localization_keys(context):
    finding = Finding(context, Code.PRICING_PROFILE_APPLIED, parameters={"profile_id": uuid4()})
    payload = collect_construction_checks(context=context, findings=[finding]).to_payload()
    assert set(payload) == {"is_blocked", "requires_attention", "findings"}
    assert set(payload["findings"][0]) == {"code", "severity", "message_key", "field_path", "parameters"}
    keys = [rule.message_key for rule in CHECK_RULES.values()]
    assert len(keys) == len(set(keys)) == len(Code)
    assert all(key.startswith("construction.checks.") for key in keys)
    assert str(context.user_id) not in json.dumps(payload)
