"""CON-020 advisory dimension checks, following rev.12 section 10.3.

The authorized domain supplies advisory bounds and values in one canonical
unit. This helper does not infer thresholds, convert units, change measured
values or replace hard geometry/technology applicability validation.
"""
from services.construction.construction_checks import (
    ConstructionCheckCode as Code,
    ConstructionCheckFinding as Finding,
    ConstructionCheckContractError,
)
from services.construction.construction_check_evaluators import _context, _decimal


def check_unusual_dimension(
    *, context, value, expected_minimum, expected_maximum, field_path=None,
):
    """Warn strictly outside explicitly supplied inclusive advisory bounds.

    Each None explicitly means that side has no advisory bound. Both None
    produce no finding, not proof that geometry or technology is valid.
    """
    _context(context, field_path)
    measured = _decimal(value)
    minimum = None if expected_minimum is None else _decimal(expected_minimum)
    maximum = None if expected_maximum is None else _decimal(expected_maximum)

    if minimum is not None and maximum is not None and minimum > maximum:
        raise ConstructionCheckContractError(
            "Advisory dimension minimum must not exceed maximum.")

    if (
        minimum is not None and measured < minimum
        or maximum is not None and measured > maximum
    ):
        return (
            Finding(
                context, Code.UNUSUAL_DIMENSION,
                field_path=field_path, parameters={"value": measured}),
        )
    return ()
