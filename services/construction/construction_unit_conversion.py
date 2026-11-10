"""CON-014 exact unit conversions; calculation data, not an authorization proof.

The consuming domain service resolves an approved, current, tenant/item-specific
coefficient and persists the returned snapshot with its business operation.
No inverse or transitive contextual coefficient is inferred here.
"""
from dataclasses import dataclass
from decimal import (
    Context, Decimal, DecimalException, Inexact, MAX_EMAX, MIN_EMIN,
    Rounded, localcontext,
)
from types import MappingProxyType

from services.construction.construction_units import (
    UnitCode, ConstructionUnitValidationError, require_construction_unit,
)


class ConstructionUnitConversionError(ValueError):
    code = "UNIT_CONVERSION_INVALID"
    message_key = "construction.unit_conversion.invalid"


class ConstructionUnitFactorRequired(ConstructionUnitConversionError):
    code = "UNIT_CONVERSION_FACTOR_REQUIRED"
    message_key = "construction.unit_conversion.factor_required"


class ConstructionUnitContextMismatch(ConstructionUnitConversionError):
    code = "UNIT_CONVERSION_CONTEXT_MISMATCH"
    message_key = "construction.unit_conversion.context_mismatch"


class ConstructionUnitArithmeticError(ConstructionUnitConversionError):
    code = "UNIT_CONVERSION_OUT_OF_RANGE"
    message_key = "construction.unit_conversion.out_of_range"


def _unit(value):
    try:
        return require_construction_unit(value).code
    except ConstructionUnitValidationError:
        raise ConstructionUnitConversionError("Canonical unit code is required.") from None


def _quantity(value):
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ConstructionUnitConversionError("A finite Decimal quantity is required.")
    return value


def _reference(value):
    if (
        not isinstance(value, str) or not value.strip()
        or value != value.strip() or len(value) > 512
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ConstructionUnitConversionError("A canonical conversion reference is required.")
    return value


@dataclass(frozen=True)
class DocumentedUnitFactor:
    source_unit: UnitCode
    target_unit: UnitCode
    factor: Decimal
    reference: str
    version: int
    context_key: str

    def __post_init__(self):
        object.__setattr__(self, "source_unit", _unit(self.source_unit))
        object.__setattr__(self, "target_unit", _unit(self.target_unit))
        if _quantity(self.factor) <= 0:
            raise ConstructionUnitConversionError("Conversion factor must be positive.")
        if type(self.version) is not int or self.version <= 0:
            raise ConstructionUnitConversionError("A positive source version is required.")
        _reference(self.reference)
        _reference(self.context_key)

    def as_snapshot(self):
        return {
            "source_unit": self.source_unit.value,
            "target_unit": self.target_unit.value,
            "factor": str(self.factor),
            "reference": self.reference,
            "version": self.version,
            "context_key": self.context_key,
        }


@dataclass(frozen=True)
class UnitConversionResult:
    source_quantity: Decimal
    source_unit: UnitCode
    target_unit: UnitCode
    quantity: Decimal
    multiplier: Decimal
    rule: str
    evidence: DocumentedUnitFactor | None = None

    def as_snapshot(self):
        return {
            "schema_version": 1, "rule_version": 1, "rule": self.rule,
            "source_quantity": str(self.source_quantity),
            "source_unit": self.source_unit.value,
            "target_unit": self.target_unit.value,
            "quantity": str(self.quantity),
            "multiplier": str(self.multiplier),
            "evidence": self.evidence.as_snapshot() if self.evidence is not None else None,
        }


# Physical family, decimal exponent relative to its reference, catalog-group base.
# Volume uses M3; the separate liquid group uses L. Both share exact volume scales.
_STANDARD = MappingProxyType({
    UnitCode.MM: ("length", -3, UnitCode.M),
    UnitCode.CM: ("length", -2, UnitCode.M),
    UnitCode.M: ("length", 0, UnitCode.M),
    UnitCode.M2: ("area", 0, UnitCode.M2),
    UnitCode.M3: ("volume", 0, UnitCode.M3),
    UnitCode.ML: ("volume", -6, UnitCode.L),
    UnitCode.L: ("volume", -3, UnitCode.L),
    UnitCode.G: ("mass", -3, UnitCode.KG),
    UnitCode.KG: ("mass", 0, UnitCode.KG),
    UnitCode.PCS: ("count", 0, UnitCode.PCS),
    UnitCode.HOUR: ("time", 0, UnitCode.HOUR),
})


def _multiply_exact(value, multiplier):
    try:
        context = Context(
            prec=max(1, len(value.as_tuple().digits) + len(multiplier.as_tuple().digits)),
            Emin=MIN_EMIN, Emax=MAX_EMAX, clamp=0,
        )
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        with localcontext(context):
            result = value * multiplier
        if not result.is_finite():
            raise ConstructionUnitArithmeticError("Unit conversion exceeds Decimal range.")
        return result
    except (DecimalException, ValueError, OverflowError):
        raise ConstructionUnitArithmeticError("Unit conversion exceeds Decimal range.") from None


def convert_unit_quantity(quantity, source_unit, target_unit, *, documented_factor=None, context_key=None):
    """Convert without rounding; entity validation/rounding belongs to its caller."""
    quantity = _quantity(quantity)
    source, target = _unit(source_unit), _unit(target_unit)
    first, second = _STANDARD.get(source), _STANDARD.get(target)
    fixed = source == target or (first is not None and second is not None and first[0] == second[0])
    if fixed:
        if documented_factor is not None or context_key is not None:
            raise ConstructionUnitConversionError("Fixed physical conversions cannot be overridden.")
        exponent = 0 if source == target else first[1] - second[1]
        multiplier = Decimal((0, (1,), exponent))
        return UnitConversionResult(
            quantity, source, target, _multiply_exact(quantity, multiplier),
            multiplier, "identity" if source == target else "standard",
        )
    if documented_factor is None:
        raise ConstructionUnitFactorRequired("A documented conversion factor is required.")
    if not isinstance(documented_factor, DocumentedUnitFactor):
        raise ConstructionUnitConversionError("A validated conversion factor snapshot is required.")
    if (
        context_key is None or documented_factor.context_key != context_key
        or documented_factor.source_unit != source or documented_factor.target_unit != target
    ):
        raise ConstructionUnitContextMismatch("Conversion source, direction or resource context differs.")
    return UnitConversionResult(
        quantity, source, target, _multiply_exact(quantity, documented_factor.factor),
        documented_factor.factor, "documented", documented_factor,
    )


def quantity_in_base_unit(quantity, source_unit):
    """Normalize a fixed physical unit; contextual units need an explicit target."""
    _quantity(quantity)
    source = _unit(source_unit)
    rule = _STANDARD.get(source)
    if rule is None:
        raise ConstructionUnitFactorRequired("The canonical target requires domain context.")
    return convert_unit_quantity(quantity, source, rule[2])
