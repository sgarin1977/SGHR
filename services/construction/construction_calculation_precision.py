"""CON-019 precision application; not a monetary line calculation engine.

Quantity rounding belongs after the canonical formula/unit conversion.
Money input validation never rounds a price or a final monetary component.
Snapshots contain calculation provenance, not authorization.
"""
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from decimal import (
    Context, Decimal, DecimalException, DivisionByZero, InvalidOperation,
    MAX_EMAX, MIN_EMIN, Overflow, ROUND_HALF_UP,
)
from uuid import UUID

from services.construction.construction_calculation_settings import (
    ConstructionCalculationSettingsService as SettingsService,
    ConstructionCalculationSettingsState as SettingsState,
    ConstructionCalculationSettingsValidationError,
    ConstructionCalculationSettingsNotFoundError,
    ConstructionCalculationSettingsOperationError,
    positive_version,
    validated_fields,
)


class ConstructionCalculationPrecisionError(ConstructionCalculationSettingsValidationError):
    pass


def _decimal(value):
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ConstructionCalculationPrecisionError("A finite exact Decimal is required.")
    return value


@dataclass(frozen=True)
class ConstructionCalculationPrecision:
    tenant_id: UUID
    settings_row_version: int
    money_input_precision: int
    quantity_precision: int

    def __post_init__(self):
        if not isinstance(self.tenant_id, UUID):
            raise ConstructionCalculationPrecisionError("A canonical tenant UUID is required.")
        try:
            positive_version(self.settings_row_version)
            validated_fields({
                "money_input_precision": self.money_input_precision,
                "quantity_precision": self.quantity_precision,
            })
        except ConstructionCalculationSettingsValidationError:
            raise ConstructionCalculationPrecisionError(
                "Stored precision or source version is invalid.") from None

    def round_calculated_quantity(self, value):
        value = _decimal(value)
        parts = value.as_tuple()
        exponent = -self.quantity_precision

        # Already exact at this precision. Do not pad a large positive exponent.
        if parts.exponent >= exponent:
            return value

        quantum = Decimal((0, (1,), exponent))
        # Include a possible carry, e.g. 999.995 -> 1000.00.
        precision = max(
            1, len(parts.digits) + parts.exponent + self.quantity_precision + 1)
        try:
            context = Context(
                prec=precision, rounding=ROUND_HALF_UP,
                Emin=MIN_EMIN, Emax=MAX_EMAX, capitals=1, clamp=0,
                flags=[], traps=[InvalidOperation, DivisionByZero, Overflow])
            result = value.quantize(quantum, context=context)
            if not result.is_finite():
                raise ValueError()
            return result
        except (DecimalException, ValueError, OverflowError):
            raise ConstructionCalculationPrecisionError(
                "Calculated quantity exceeds Decimal arithmetic bounds.") from None

    def require_money_input(self, value):
        value = _decimal(value)
        if value.is_zero():
            return value
        parts = value.as_tuple()
        exponent = parts.exponent
        # Extra trailing zeros carry no additional fractional information.
        for digit in reversed(parts.digits):
            if digit != 0:
                break
            exponent += 1
        if exponent < -self.money_input_precision:
            raise ConstructionCalculationPrecisionError(
                "Money input exceeds the configured fractional precision.")
        # Preserve the exact input; no rounding, scaling or currency conversion.
        return value

    def as_snapshot(self):
        return {
            "schema_version": 1,
            "source": "construction.calculation_settings",
            "tenant_id": str(self.tenant_id),
            "settings_row_version": self.settings_row_version,
            "money_input_precision": self.money_input_precision,
            "quantity_precision": self.quantity_precision,
            "quantity_rounding": "ROUND_HALF_UP",
        }


def precision_from_calculation_settings(*, settings, tenant_id):
    if not isinstance(tenant_id, UUID) or not isinstance(settings, SettingsState):
        raise ConstructionCalculationPrecisionError(
            "Canonical calculation settings and tenant are required.")
    try:
        # Reuse the canonical configuration validator, including source metadata.
        checked = SettingsService._state(asdict(settings), tenant_id=tenant_id)
    except (ConstructionCalculationSettingsOperationError, ValueError, TypeError):
        raise ConstructionCalculationPrecisionError(
            "Canonical calculation settings context is invalid.") from None
    return ConstructionCalculationPrecision(
        tenant_id=checked.tenant_id,
        settings_row_version=checked.row_version,
        money_input_precision=checked.money_input_precision,
        quantity_precision=checked.quantity_precision)


def restore_calculation_precision(*, snapshot, tenant_id):
    fields = {
        "schema_version", "source", "tenant_id", "settings_row_version",
        "money_input_precision", "quantity_precision", "quantity_rounding",
    }
    if (
        not isinstance(tenant_id, UUID)
        or not isinstance(snapshot, Mapping) or set(snapshot) != fields
        or type(snapshot["schema_version"]) is not int
        or snapshot["schema_version"] != 1
        or snapshot["source"] != "construction.calculation_settings"
        or snapshot["quantity_rounding"] != "ROUND_HALF_UP"
        or type(snapshot["tenant_id"]) is not str
    ):
        raise ConstructionCalculationPrecisionError("Precision snapshot is invalid.")
    try:
        stored_tenant = UUID(snapshot["tenant_id"])
    except (ValueError, TypeError, AttributeError):
        raise ConstructionCalculationPrecisionError(
            "Precision snapshot tenant is invalid.") from None
    if stored_tenant != tenant_id or str(stored_tenant) != snapshot["tenant_id"]:
        raise ConstructionCalculationPrecisionError("Precision snapshot tenant differs.")
    return ConstructionCalculationPrecision(
        tenant_id=stored_tenant,
        settings_row_version=snapshot["settings_row_version"],
        money_input_precision=snapshot["money_input_precision"],
        quantity_precision=snapshot["quantity_precision"])


async def read_calculation_precision(*, service, actor):
    """Use existing settings authorization; retain its transaction ownership."""
    if not isinstance(service, SettingsService):
        raise ConstructionCalculationPrecisionError(
            "The canonical calculation settings service is required.")
    settings = await service.get_settings(actor=actor)
    if settings is None:
        raise ConstructionCalculationSettingsNotFoundError(
            "Calculation settings must be explicitly configured.")
    return precision_from_calculation_settings(
        settings=settings, tenant_id=actor.tenant_id)
