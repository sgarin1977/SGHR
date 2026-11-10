"""Pure supplier-price warning boundaries; no DB, network or host clock."""
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
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
    check_supplier_price_stale,
    check_required_price,
)

FETCHED = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
MICROSECOND = timedelta(microseconds=1)


@pytest.fixture
def context():
    return Context(
        tenant_id=uuid4(), user_id=uuid4(),
        action="construction.estimate.review", project_id=uuid4())


def evaluate(context, *, days=1, fetched=FETCHED, current=None, field_path=None):
    return check_supplier_price_stale(
        context=context,
        fetched_at=fetched,
        supplier_stale_price_days=days,
        as_of=FETCHED if current is None else current,
        field_path=field_path)


@pytest.mark.parametrize("days", [0, 1, 30, 3650])
@pytest.mark.parametrize("offset,stale", [
    (-MICROSECOND, False), (timedelta(0), False), (MICROSECOND, True),
])
def test_exact_timestamp_boundary(context, days, offset, stale):
    current = FETCHED + timedelta(days=days) + offset
    findings = evaluate(context, days=days, current=current)
    assert bool(findings) is stale
    if stale:
        finding, = findings
        assert finding.code is Code.SUPPLIER_PRICE_STALE
        assert finding.severity is Severity.WARNING
        assert finding.context == context
        assert finding.parameters == {}


def test_zero_has_no_hidden_day_or_clock_tolerance(context):
    assert evaluate(context, days=0, current=FETCHED) == ()
    assert evaluate(
        context, days=0, current=FETCHED + MICROSECOND)


def test_midnight_does_not_count_as_a_full_day(context):
    fetched = datetime(2026, 11, 10, 23, 59, tzinfo=timezone.utc)
    current = datetime(2026, 11, 11, 0, 1, tzinfo=timezone.utc)
    assert evaluate(context, fetched=fetched, current=current, days=1) == ()


def test_calendar_duration_includes_weekend(context):
    fetched = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
    current = datetime(2026, 11, 7, 12, tzinfo=timezone.utc) + MICROSECOND
    assert evaluate(context, fetched=fetched, current=current, days=1)


@pytest.mark.parametrize("offset_hours", [-5, 0, 2, 3, 9])
def test_equivalent_instants_have_same_result_in_every_offset(context, offset_hours):
    zone = timezone(timedelta(hours=offset_hours))
    fetched = FETCHED.astimezone(zone)
    current = (FETCHED + timedelta(days=1) + MICROSECOND).astimezone(zone)
    findings = evaluate(context, fetched=fetched, current=current)
    assert len(findings) == 1


def test_offset_change_does_not_shorten_elapsed_day(context):
    fetched = datetime(
        2026, 3, 28, 12, tzinfo=timezone(timedelta(hours=2)))
    before = datetime(
        2026, 3, 29, 12, 30, tzinfo=timezone(timedelta(hours=3)))
    boundary = datetime(
        2026, 3, 29, 13, tzinfo=timezone(timedelta(hours=3)))
    assert evaluate(context, fetched=fetched, current=before) == ()
    assert evaluate(context, fetched=fetched, current=boundary) == ()
    assert evaluate(
        context, fetched=fetched, current=boundary + MICROSECOND)


@pytest.mark.parametrize("bad", [
    None, True, False, -1, 3651, 1.0, "1", Decimal("1"),
])
def test_explicit_integer_setting_is_required_without_default(context, bad):
    with pytest.raises(ContractError):
        evaluate(context, days=bad)


class NoOffset(tzinfo):
    def utcoffset(self, value):
        return None


@pytest.mark.parametrize("name", ["fetched_at", "as_of"])
@pytest.mark.parametrize("bad", [
    None, True, 1, "2026-11-10T12:00:00Z",
    date(2026, 11, 10), datetime(2026, 11, 10, 12),
    datetime(2026, 11, 10, 12, tzinfo=NoOffset()),
])
def test_timestamps_must_be_aware_datetime_objects(context, name, bad):
    values = dict(
        context=context, fetched_at=FETCHED,
        supplier_stale_price_days=1, as_of=FETCHED)
    values[name] = bad
    with pytest.raises(ContractError):
        check_supplier_price_stale(**values)


def test_context_and_field_path_are_checked_even_without_warning(context):
    with pytest.raises(ContractError):
        evaluate(None)
    with pytest.raises(ContractError):
        evaluate(context, field_path="../prices")


def test_warning_requires_attention_without_blocking_action(context):
    findings = evaluate(
        context, days=0, current=FETCHED + MICROSECOND,
        field_path="materials.0.price")
    report = collect_construction_checks(context=context, findings=findings)
    assert report.is_blocked is False
    assert report.requires_attention is True
    assert report.raise_for_errors() is None
    finding, = report.warnings
    assert finding.field_path == "materials.0.price"
    assert finding.to_payload() == {
        "code": "SUPPLIER_PRICE_STALE",
        "severity": "WARNING",
        "message_key": "construction.checks.supplier_price_stale",
        "field_path": "materials.0.price",
        "parameters": {},
    }


def test_supplier_warning_does_not_downgrade_existing_price_error(context):
    findings = (
        *evaluate(context, days=0, current=FETCHED + MICROSECOND),
        *check_required_price(context=context, price=None),
    )
    report = collect_construction_checks(context=context, findings=findings)
    assert report.is_blocked and report.requires_attention
    assert report.errors[0].code is Code.PRICE_MISSING
    assert report.warnings[0].code is Code.SUPPLIER_PRICE_STALE
    with pytest.raises(ConstructionCheckBlockedError):
        report.raise_for_errors()


def test_large_supported_datetimes_do_not_overflow_deadline_arithmetic(context):
    maximum = datetime.max.replace(tzinfo=timezone.utc)
    minimum = datetime.min.replace(tzinfo=timezone.utc)
    assert evaluate(
        context, fetched=maximum, current=maximum, days=3650) == ()
    assert evaluate(
        context, fetched=minimum, current=maximum, days=3650)
