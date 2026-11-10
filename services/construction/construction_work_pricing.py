"""Exact work unit-price formula from rev.12 section 14.2.

The calling domain owns authorization, fresh canonical reads and selection of
the price-book base price. This pure function grants no access and performs no
IO. No implicit profile, geography multiplier, tax, discount or currency rounding.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal
from uuid import UUID

from services.construction.construction_pricing_profiles import (
    ConstructionPricingProfileState,
    ConstructionPricingProfileService,
    ConstructionPricingProfileOperationError,
)
from services.construction.construction_project_pricing_adjustments import (
    ConstructionProjectPricingAdjustmentState,
    ConstructionProjectPricingAdjustmentService,
    ConstructionProjectPricingAdjustmentOperationError,
)
from services.construction.construction_unit_conversion import (
    _multiply_exact, ConstructionUnitArithmeticError,
)


class ConstructionWorkPricingValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ConstructionWorkPricingResult:
    tenant_id: UUID
    project_id: UUID
    base_sell_price: Decimal
    profile: ConstructionPricingProfileState
    adjustments: tuple[ConstructionProjectPricingAdjustmentState, ...]
    work_multiplier: Decimal
    effective_unit_price: Decimal

    @property
    def applied_adjustment_ids(self):
        return tuple(item.id for item in self.adjustments if item.enabled)

    def as_snapshot(self):
        return {
            "schema_version": 1,
            "tenant_id": str(self.tenant_id),
            "project_id": str(self.project_id),
            "base_sell_price": format(self.base_sell_price, "E"),
            "pricing_profile": {
                "id": str(self.profile.id),
                "row_version": self.profile.row_version,
                "code": self.profile.code,
                "work_coefficient": format(self.profile.work_coefficient, "E"),
            },
            "adjustments": [
                {
                    "id": str(item.id),
                    "row_version": item.row_version,
                    "name": item.name,
                    "coefficient": format(item.coefficient, "E"),
                    "enabled": item.enabled,
                }
                for item in self.adjustments
            ],
            "applied_adjustment_ids": [
                str(identifier) for identifier in self.applied_adjustment_ids],
            "work_multiplier": format(self.work_multiplier, "E"),
            "effective_unit_price": format(self.effective_unit_price, "E"),
        }


def apply_work_pricing(
    *, tenant_id, project_id, base_sell_price, profile, adjustments,
):
    if not isinstance(tenant_id, UUID) or not isinstance(project_id, UUID):
        raise ConstructionWorkPricingValidationError(
            "Canonical tenant and project UUIDs are required.")
    if (
        not isinstance(base_sell_price, Decimal)
        or not base_sell_price.is_finite() or base_sell_price < 0
    ):
        raise ConstructionWorkPricingValidationError(
            "An explicit finite non-negative Decimal base price is required.")
    if (
        not isinstance(profile, ConstructionPricingProfileState)
        or not isinstance(adjustments, tuple)
        or any(
            not isinstance(item, ConstructionProjectPricingAdjustmentState)
            for item in adjustments)
    ):
        raise ConstructionWorkPricingValidationError(
            "An explicit canonical pricing profile and adjustment tuple are required.")

    try:
        checked_profile = ConstructionPricingProfileService._state(
            asdict(profile), tenant_id=tenant_id, profile_id=profile.id)
        checked_adjustments, seen = [], set()
        for item in adjustments:
            checked = ConstructionProjectPricingAdjustmentService._state(
                asdict(item), tenant_id=tenant_id,
                project_id=project_id, adjustment_id=item.id)
            if checked.id in seen:
                raise ConstructionWorkPricingValidationError(
                    "A project adjustment cannot be supplied twice.")
            seen.add(checked.id)
            checked_adjustments.append(checked)
    except (
        ConstructionPricingProfileOperationError,
        ConstructionProjectPricingAdjustmentOperationError,
        TypeError, ValueError,
    ):
        raise ConstructionWorkPricingValidationError(
            "Pricing source context is invalid or duplicated.") from None

    ordered = tuple(sorted(checked_adjustments, key=lambda item: item.id))
    multiplier = checked_profile.work_coefficient
    try:
        for item in ordered:
            if item.enabled:
                multiplier = _multiply_exact(multiplier, item.coefficient)
        effective = _multiply_exact(base_sell_price, multiplier)
    except ConstructionUnitArithmeticError:
        raise ConstructionWorkPricingValidationError(
            "Work pricing exceeds exact Decimal arithmetic bounds.") from None

    return ConstructionWorkPricingResult(
        tenant_id=tenant_id, project_id=project_id,
        base_sell_price=base_sell_price,
        profile=checked_profile, adjustments=ordered,
        work_multiplier=multiplier, effective_unit_price=effective)
