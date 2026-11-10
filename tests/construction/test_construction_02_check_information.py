"""Information facts and local message rendering; no external operations."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
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
from services.construction.construction_check_information import (
    check_standard_percent_applied,
    check_pricing_profile_applied,
)
from services.construction.construction_check_evaluators import (
    check_negative_margin, check_required_tax,
)
from services.construction.construction_pricing_profiles import (
    ConstructionPricingProfileState,
)
from test_construction_02_check_rendering import (
    case, rendered, business_rows, cache_rows,
)

NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


@pytest.fixture
def context():
    return Context(
        tenant_id=uuid4(), user_id=uuid4(),
        action="construction.estimate.review", project_id=uuid4())


def profile_for(context, code="STANDARD"):
    return ConstructionPricingProfileState(
        id=uuid4(), tenant_id=context.tenant_id, code=code,
        work_coefficient=Decimal("1.1234567890123456789"),
        material_markup_percent=Decimal("10.125"),
        row_version=3, created_by=context.user_id,
        created_at=NOW, updated_at=NOW)


@pytest.mark.parametrize("value", [
    "0", "100", "0.000000000000000000000000001",
    "33.333333333333333333333333333",
])
def test_applied_percent_is_exact_information_including_explicit_zero(context, value):
    percent = Decimal(value)
    before = percent.as_tuple()
    with localcontext() as arithmetic:
        arithmetic.prec = 1
        arithmetic.traps[Inexact] = True
        arithmetic.traps[Rounded] = True
        findings = check_standard_percent_applied(
            context=context, applied_percent=percent,
            field_path="lines.0.local_repair_percent")
        payload = findings[0].to_payload()
    assert percent.as_tuple() == before
    assert findings[0].code is Code.STANDARD_PERCENT_APPLIED
    assert findings[0].severity is Severity.INFO
    assert payload["parameters"] == {"percent": str(percent)}
    assert payload["field_path"] == "lines.0.local_repair_percent"


@pytest.mark.parametrize("bad", [
    True, 0, 1.5, "10", Decimal("NaN"), Decimal("Infinity"),
    Decimal("-Infinity"), Decimal("-0.01"), Decimal("100.00000001"),
])
def test_percent_requires_finite_decimal_inside_approved_bounds(context, bad):
    with pytest.raises(ContractError):
        check_standard_percent_applied(context=context, applied_percent=bad)


def test_unapplied_values_do_not_invent_information_or_defaults(context):
    assert check_standard_percent_applied(
        context=context, applied_percent=None) == ()
    assert check_pricing_profile_applied(
        context=context, applied_profile=None) == ()


@pytest.mark.parametrize("code", ["ECONOMY", "STANDARD", "PREMIUM"])
def test_applied_profile_reports_exact_id_without_changing_prices(context, code):
    profile = profile_for(context, code)
    before = asdict(profile)
    findings = check_pricing_profile_applied(
        context=context, applied_profile=profile, field_path="lines.0.profile")
    finding, = findings
    assert finding.code is Code.PRICING_PROFILE_APPLIED
    assert finding.severity is Severity.INFO
    assert finding.context == context
    assert finding.parameters == {"profile_id": profile.id}
    assert finding.to_payload()["parameters"] == {"profile_id": str(profile.id)}
    assert asdict(profile) == before


@pytest.mark.parametrize("change", [
    {"tenant_id": uuid4()},
    {"id": "untrusted-id"},
    {"row_version": 0},
    {"row_version": True},
    {"code": "UNKNOWN"},
    {"work_coefficient": Decimal("0")},
    {"work_coefficient": 1.5},
    {"material_markup_percent": Decimal("100.00001")},
])
def test_foreign_or_malformed_profile_is_not_reported_as_applied(context, change):
    profile = replace(profile_for(context), **change)
    with pytest.raises(ContractError):
        check_pricing_profile_applied(context=context, applied_profile=profile)


def test_bare_profile_id_is_not_a_canonical_tenant_profile(context):
    with pytest.raises(ContractError):
        check_pricing_profile_applied(context=context, applied_profile=uuid4())


def test_context_and_location_are_validated_even_when_nothing_was_applied(context):
    for function, argument in (
        (check_standard_percent_applied, "applied_percent"),
        (check_pricing_profile_applied, "applied_profile"),
    ):
        with pytest.raises(ContractError):
            function(context=None, **{argument: None})
        with pytest.raises(ContractError):
            function(context=context, field_path="../untrusted", **{argument: None})


def test_information_alone_does_not_block_or_require_warning_attention(context):
    findings = (
        *check_standard_percent_applied(
            context=context, applied_percent=Decimal("0")),
        *check_pricing_profile_applied(
            context=context, applied_profile=profile_for(context)),
    )
    report = collect_construction_checks(context=context, findings=findings)
    assert len(report.information) == 2
    assert report.errors == () and report.warnings == ()
    assert report.is_blocked is False
    assert report.requires_attention is False
    assert report.raise_for_errors() is None


def test_information_does_not_downgrade_errors_or_warnings(context):
    findings = (
        *check_standard_percent_applied(
            context=context, applied_percent=Decimal("5")),
        *check_pricing_profile_applied(
            context=context, applied_profile=profile_for(context)),
        *check_negative_margin(context=context, margin=Decimal("-1")),
        *check_required_tax(context=context, rate_percent=None),
    )
    report = collect_construction_checks(context=context, findings=findings)
    assert len(report.information) == 2
    assert [item.code for item in report.warnings] == [Code.NEGATIVE_MARGIN]
    assert [item.code for item in report.errors] == [Code.TAX_MISSING]
    assert report.is_blocked and report.requires_attention
    with pytest.raises(ConstructionCheckBlockedError):
        report.raise_for_errors()


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["uk", "fi"])
async def test_actual_information_helpers_use_existing_localized_message_pipeline(
    rendered, language,
):
    c = rendered
    c.language = language
    percent = Decimal("0.123456789012345678901234567890")
    profile = profile_for(c.context)
    report = collect_construction_checks(
        context=c.context,
        findings=(
            *check_standard_percent_applied(
                context=c.context, applied_percent=percent),
            *check_pricing_profile_applied(
                context=c.context, applied_profile=profile),
        ))
    before = business_rows(c)
    result = await c.renderer.render_for_actor(actor=c.actor, report=report)
    payload = result.to_payload()
    first, second = payload["findings"]
    assert payload["language_code"] == language
    assert payload["is_blocked"] is False
    assert payload["requires_attention"] is False
    assert first["parameters"] == {"percent": str(percent)}
    assert second["parameters"] == {"profile_id": str(profile.id)}
    assert all(
        item["severity"] == "INFO"
        and item["message"] != item["message_key"]
        and item["message"] != item["code"]
        and item["severity_label"] != "INFO"
        for item in payload["findings"])
    assert business_rows(c) == before
    if language == "uk":
        c.provider.translate.assert_not_awaited()
        assert cache_rows(c) == ()
    else:
        assert c.provider.translate.await_count == 3
        for call in c.provider.translate.await_args_list:
            text = call.kwargs["text"]
            assert str(percent) not in text and str(profile.id) not in text
