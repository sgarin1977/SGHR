"""CON-020 information about values already applied by an authorized domain.

The caller reports the actual organization default/profile used in its
calculation. None explicitly means it was not applied. These helpers do not
select defaults, apply coefficients, authorize access or mutate business data.
"""
from uuid import UUID

from services.construction.construction_checks import (
    ConstructionCheckCode as Code,
    ConstructionCheckFinding as Finding,
    ConstructionCheckContractError,
)
from services.construction.construction_check_evaluators import _context, _decimal
from services.construction.construction_pricing_profiles import (
    ConstructionPricingProfileState,
    ConstructionPricingProfileValidationError,
    validated_fields as _pricing_fields,
    positive_version as _pricing_version,
)


def check_standard_percent_applied(
    *, context, applied_percent, field_path=None,
):
    """Call only when the calculation actually used an organization default."""
    _context(context, field_path)
    if applied_percent is None:
        return ()
    percent = _decimal(applied_percent, percent=True)
    return (
        Finding(
            context, Code.STANDARD_PERCENT_APPLIED,
            field_path=field_path, parameters={"percent": percent}),
    )


def check_pricing_profile_applied(
    *, context, applied_profile, field_path=None,
):
    """Report a canonical applied profile belonging to the current tenant."""
    _context(context, field_path)
    if applied_profile is None:
        return ()
    if (
        not isinstance(applied_profile, ConstructionPricingProfileState)
        or not isinstance(applied_profile.id, UUID)
        or applied_profile.tenant_id != context.tenant_id
    ):
        raise ConstructionCheckContractError(
            "A canonical applied profile in the current tenant is required.")

    try:
        _pricing_version(applied_profile.row_version)
        _pricing_fields({
            "code": applied_profile.code,
            "work_coefficient": applied_profile.work_coefficient,
            "material_markup_percent": applied_profile.material_markup_percent,
        }, complete=True)
    except ConstructionPricingProfileValidationError:
        raise ConstructionCheckContractError(
            "Applied pricing profile metadata is invalid.") from None

    return (
        Finding(
            context, Code.PRICING_PROFILE_APPLIED,
            field_path=field_path,
            parameters={"profile_id": applied_profile.id}),
    )
