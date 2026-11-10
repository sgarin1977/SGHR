"""CON-020 explicit value checks, without domain storage or inferred settings.

Call only the checks applicable to the current server action, after fresh
authorization and domain reads. Required commercial data is not a global
prerequisite for starting a measurement. Area inputs must already use the
same canonical area unit; margin must be supplied by the monetary calculation.
"""
from decimal import Decimal

from services.construction.construction_checks import (
    ConstructionCheckContext as Context, ConstructionCheckCode as Code,
    ConstructionCheckFinding as Finding, ConstructionCheckContractError,
    _machine_name)


def _context(context, field_path):
    if not isinstance(context, Context):
        raise ConstructionCheckContractError("Typed check context is required.")
    if field_path is not None:
        _machine_name(field_path)


def _decimal(value, *, nonnegative=False, percent=False):
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ConstructionCheckContractError("A finite exact Decimal is required.")
    if (nonnegative and value < 0) or (percent and not Decimal(0) <= value <= Decimal(100)):
        raise ConstructionCheckContractError("Check input is outside its domain.")
    return value


def check_opening_area(*, context, wall_area, opening_area, field_path=None):
    """Only a strictly larger opening total is an error; no tolerance is guessed."""
    _context(context, field_path)
    wall = _decimal(wall_area, nonnegative=True)
    openings = _decimal(opening_area, nonnegative=True)
    if openings > wall:
        return (Finding(context, Code.OPENINGS_EXCEED_WALL_AREA, field_path=field_path,
                        parameters={"opening_area": openings, "wall_area": wall}),)
    return ()


def check_required_price(*, context, price, field_path=None):
    """Missing required price is distinct from an explicitly supplied zero."""
    _context(context, field_path)
    if price is None:
        return (Finding(context, Code.PRICE_MISSING, field_path=field_path),)
    _decimal(price, nonnegative=True)
    return ()


def check_required_tax(*, context, rate_percent, field_path=None):
    """Unknown required tax is an error; an explicit zero rate remains valid."""
    _context(context, field_path)
    if rate_percent is None:
        return (Finding(context, Code.TAX_MISSING, field_path=field_path),)
    _decimal(rate_percent, percent=True)
    return ()


def check_negative_margin(*, context, margin, field_path=None):
    """Report the normative warning; this does not authorize margin approval."""
    _context(context, field_path)
    value = _decimal(margin)
    if value < 0:
        return (Finding(context, Code.NEGATIVE_MARGIN, field_path=field_path, parameters={"margin": value}),)
    return ()


# CON_020_SUPPLIER_PRICE_STALE_V1
from datetime import datetime, timedelta, timezone


def _supplier_price_utc(value):
    try:
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError()
        return value.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise ConstructionCheckContractError(
            "An aware supplier price timestamp is required.") from None


def check_supplier_price_stale(
    *, context, fetched_at, supplier_stale_price_days, as_of, field_path=None,
):
    """Pure timestamp check; caller supplies fresh settings and server time.

    Calendar duration is compared as exact UTC instants, not local dates or
    business days. Zero means stale strictly after fetched_at. This evaluator
    only assesses staleness, not the price's visibility or other validity.
    """
    _context(context, field_path)
    if (
        type(supplier_stale_price_days) is not int
        or not 0 <= supplier_stale_price_days <= 3650
    ):
        raise ConstructionCheckContractError(
            "An explicit integer supplier price lifetime in 0..3650 is required.")

    fetched = _supplier_price_utc(fetched_at)
    current = _supplier_price_utc(as_of)
    # Equivalent to current > fetched + duration, without overflowing a
    # representable datetime when fetched_at is near the datetime upper bound.
    if current - fetched > timedelta(days=supplier_stale_price_days):
        return (
            Finding(
                context, Code.SUPPLIER_PRICE_STALE,
                field_path=field_path),
        )
    return ()


# CON_020_MINIMUM_JOB_AMOUNT_V1
from services.platform_currency_catalog import (
    PlatformCurrencyCodeError, require_platform_currency,
)


def _minimum_job_currency(code):
    try:
        return require_platform_currency(code).code
    except PlatformCurrencyCodeError:
        raise ConstructionCheckContractError(
            "An exact known Platform currency code is required.") from None


def check_minimum_job_amount(
    *, context, document_amount, document_currency,
    minimum_job_amount, minimum_job_currency, field_path=None,
):
    """Warn below an explicit minimum in the same currency.

    The authorized Estimate/Offer domain supplies its canonical amount and
    freshly read tenant settings. No FX, rounding, surcharge or mutation.
    A cleared minimum is represented by an explicit (None, None) pair.
    """
    _context(context, field_path)
    amount = _decimal(document_amount, nonnegative=True)
    currency = _minimum_job_currency(document_currency)

    if (minimum_job_amount is None) != (minimum_job_currency is None):
        raise ConstructionCheckContractError(
            "Minimum amount and currency must be configured or cleared together.")
    if minimum_job_amount is None:
        return ()

    minimum = _decimal(minimum_job_amount, nonnegative=True)
    minimum_currency = _minimum_job_currency(minimum_job_currency)
    if currency != minimum_currency:
        return ()

    if amount < minimum:
        return (
            Finding(
                context, Code.MINIMUM_ORDER_NOT_REACHED,
                field_path=field_path),
        )
    return ()
