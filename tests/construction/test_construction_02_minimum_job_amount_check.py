"""Pure currency-bound minimum warning; no FX, database or monetary mutation."""
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
from services.construction.construction_check_evaluators import (
    check_minimum_job_amount,
    check_required_price,
    check_required_tax,
)


@pytest.fixture
def context():
    return Context(
        tenant_id=uuid4(), user_id=uuid4(),
        action="construction.estimate.review", project_id=uuid4())


def evaluate(context, **changes):
    values = dict(
        context=context,
        document_amount=Decimal("50"),
        document_currency="EUR",
        minimum_job_amount=Decimal("100"),
        minimum_job_currency="EUR",
        field_path="totals.amount",
    )
    values.update(changes)
    return check_minimum_job_amount(**values)


@pytest.mark.parametrize("amount,minimum,warn", [
    ("99", "100", True),
    ("100", "100", False),
    ("101", "100", False),
    ("0", "0", False),
    ("0", "0.00000000000000000000000000001", True),
    ("100", "100.00000000000000000000000000001", True),
    ("100.00000000000000000000000000001", "100", False),
    ("123456789012345678901234567890", "123456789012345678901234567890", False),
])
def test_exact_boundary_without_rounding_or_surcharge(context, amount, minimum, warn):
    total, threshold = Decimal(amount), Decimal(minimum)
    original = total.as_tuple(), threshold.as_tuple()
    with localcontext() as arithmetic:
        arithmetic.prec = 1
        arithmetic.traps[Inexact] = True
        arithmetic.traps[Rounded] = True
        findings = evaluate(
            context, document_amount=total, minimum_job_amount=threshold)
    assert bool(findings) is warn
    assert (total.as_tuple(), threshold.as_tuple()) == original
    if warn:
        finding, = findings
        assert finding.code is Code.MINIMUM_ORDER_NOT_REACHED
        assert finding.severity is Severity.WARNING
        assert finding.parameters == {}


@pytest.mark.parametrize("document_currency,minimum_currency", [
    ("EUR", "USD"), ("USD", "EUR"), ("UAH", "EUR"), ("JPY", "GBP"),
])
def test_different_currencies_do_not_apply_or_convert_minimum(
    context, document_currency, minimum_currency,
):
    assert evaluate(
        context,
        document_amount=Decimal("0"),
        minimum_job_amount=Decimal("1E+100"),
        document_currency=document_currency,
        minimum_job_currency=minimum_currency,
    ) == ()


@pytest.mark.parametrize("currency", ["EUR", "USD", "UAH", "JPY"])
def test_same_currency_uses_shared_catalog_without_eur_only_policy(context, currency):
    assert evaluate(
        context, document_currency=currency, minimum_job_currency=currency)


def test_explicit_cleared_pair_does_not_invent_zero_or_another_minimum(context):
    assert evaluate(
        context, minimum_job_amount=None, minimum_job_currency=None) == ()


@pytest.mark.parametrize("amount,currency", [
    (None, "EUR"), (Decimal("100"), None), (Decimal("0"), None),
])
def test_incomplete_minimum_pair_is_an_error(context, amount, currency):
    with pytest.raises(ContractError):
        evaluate(
            context, minimum_job_amount=amount, minimum_job_currency=currency)


@pytest.mark.parametrize("name", ["document_amount", "minimum_job_amount"])
@pytest.mark.parametrize("bad", [
    True, 0, 1.5, "100", Decimal("NaN"), Decimal("Infinity"),
    Decimal("-Infinity"), Decimal("-0.00000001"),
])
def test_amounts_require_finite_nonnegative_decimal_even_for_other_currency(
    context, name, bad,
):
    with pytest.raises(ContractError):
        evaluate(context, document_currency="USD", **{name: bad})


@pytest.mark.parametrize("name", ["document_currency", "minimum_job_currency"])
@pytest.mark.parametrize("bad", [True, 1, "", "eur", " EUR", "EURO", "ZZZ"])
def test_invalid_currency_codes_are_not_normalized_or_silently_ignored(
    context, name, bad,
):
    with pytest.raises(ContractError):
        evaluate(context, **{name: bad})


def test_document_amount_and_currency_remain_required_when_minimum_is_cleared(context):
    for changes in (
        {"document_amount": None},
        {"document_currency": None},
    ):
        with pytest.raises(ContractError):
            evaluate(
                context, minimum_job_amount=None, minimum_job_currency=None,
                **changes)


def test_context_and_location_are_checked_even_when_rule_does_not_apply(context):
    with pytest.raises(ContractError):
        evaluate(None, minimum_job_amount=None, minimum_job_currency=None)
    with pytest.raises(ContractError):
        evaluate(
            context, document_currency="USD", field_path="../minimum")


def test_warning_requires_attention_without_blocking_or_changing_total(context):
    amount = Decimal("75.25")
    findings = evaluate(context, document_amount=amount)
    report = collect_construction_checks(context=context, findings=findings)
    assert amount == Decimal("75.25")
    assert report.is_blocked is False
    assert report.requires_attention is True
    assert report.raise_for_errors() is None
    finding, = report.warnings
    assert finding.context == context
    assert finding.to_payload() == {
        "code": "MINIMUM_ORDER_NOT_REACHED",
        "severity": "WARNING",
        "message_key": "construction.checks.minimum_order_not_reached",
        "field_path": "totals.amount",
        "parameters": {},
    }


def test_minimum_warning_does_not_downgrade_price_or_tax_errors(context):
    findings = (
        *evaluate(context),
        *check_required_price(context=context, price=None),
        *check_required_tax(context=context, rate_percent=None),
    )
    report = collect_construction_checks(context=context, findings=findings)
    assert report.is_blocked and report.requires_attention
    assert [item.code for item in report.errors] == [
        Code.PRICE_MISSING, Code.TAX_MISSING]
    assert [item.code for item in report.warnings] == [
        Code.MINIMUM_ORDER_NOT_REACHED]
    with pytest.raises(ConstructionCheckBlockedError):
        report.raise_for_errors()
