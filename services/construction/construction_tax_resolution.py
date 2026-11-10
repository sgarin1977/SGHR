"""Explicit tax selection through existing authorized canonical readers.

Successful reads retain the caller's transaction and profile SHARE lock.
Failure/cancellation rolls back. No default profile/rate, monetary arithmetic,
document publication, audit write or permission expansion is introduced.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal
from uuid import UUID

from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_tax_profiles import (
    ConstructionTaxProfileService,
    ConstructionTaxProfileNotFoundError,
    ConstructionTaxProfileValidationError,
)
from services.construction.construction_tax_rules import (
    ConstructionTaxRuleService, ConstructionTaxRuleState,
    ConstructionTaxRuleNotFoundError, ConstructionTaxRuleValidationError,
    validated_fields,
)
from services.construction.construction_tax_setup_review import (
    CATEGORY_ORDER,
    ConstructionTaxSetupReviewService,
    ConstructionTaxSetupReview,
    ConstructionTaxCategoryReview,
    ConstructionTaxSetupReviewOperationError,
)


class ConstructionTaxMissingError(ConstructionTaxRuleValidationError):
    code = "TAX_MISSING"

    def __init__(self, category):
        self.category = category
        super().__init__("No tax rule is configured for the selected category.")


class ConstructionTaxResolutionError(ConstructionTaxSetupReviewOperationError):
    pass


@dataclass(frozen=True)
class SelectedConstructionTax:
    tenant_id: UUID
    tax_profile_id: UUID
    tax_profile_row_version: int
    client_price_tax_mode: str
    material_cost_basis: str
    tax_rule_id: UUID
    tax_rule_row_version: int
    category: str
    rate_percent: Decimal

    def as_snapshot(self):
        # Exact scientific Decimal text, independent of ambient rounding.
        # This snapshot records provenance; it does not grant authorization.
        return {
            "schema_version": 1,
            "tenant_id": str(self.tenant_id),
            "tax_profile_id": str(self.tax_profile_id),
            "tax_profile_row_version": self.tax_profile_row_version,
            "client_price_tax_mode": self.client_price_tax_mode,
            "material_cost_basis": self.material_cost_basis,
            "tax_rule_id": str(self.tax_rule_id),
            "tax_rule_row_version": self.tax_rule_row_version,
            "category": self.category,
            "rate_percent": format(self.rate_percent, "E"),
        }


class ConstructionTaxResolutionService:
    def __init__(self, *, session, review_service):
        if (
            not isinstance(review_service, ConstructionTaxSetupReviewService)
            or review_service.session is not session
        ):
            raise ValueError("The shared-session canonical tax review is required.")
        self.session = session
        self.review = review_service

    def _same(self, transaction):
        self.review._same_transaction(transaction)
        if any(bool(getattr(self.session, key, ())) for key in ("new", "dirty", "deleted")):
            raise ConstructionTaxResolutionError("Tax selection session is not clean.")

    @staticmethod
    def _select(review, *, actor, profile_id, category):
        if not isinstance(review, ConstructionTaxSetupReview):
            raise ConstructionTaxResolutionError("Canonical tax review is invalid.")
        try:
            profile = ConstructionTaxSetupReviewService._profile(
                review.profile, actor, profile_id)
            if (
                not isinstance(review.categories, tuple)
                or tuple(item.category for item in review.categories) != CATEGORY_ORDER
            ):
                raise ValueError()
            selected, seen = None, set()
            for item in review.categories:
                if (
                    not isinstance(item, ConstructionTaxCategoryReview)
                    or type(item.rule_count) is not int
                    or item.rule_count not in (0, 1)
                ):
                    raise ValueError()
                if item.rule_count == 0:
                    if item.single_rule is not None:
                        raise ValueError()
                    continue
                value = item.single_rule
                if not isinstance(value, ConstructionTaxRuleState):
                    raise ValueError()
                rule = ConstructionTaxRuleService._state(
                    asdict(value), tenant_id=actor.tenant_id,
                    profile_id=profile_id, rule_id=value.id)
                if rule.category != item.category or rule.id in seen:
                    raise ValueError()
                seen.add(rule.id)
                if rule.category == category:
                    selected = rule
        except Exception:
            raise ConstructionTaxResolutionError(
                "Tax rules are ambiguous or canonical source context differs.") from None

        if selected is None:
            raise ConstructionTaxMissingError(category)
        return SelectedConstructionTax(
            tenant_id=profile.tenant_id,
            tax_profile_id=profile.id,
            tax_profile_row_version=profile.row_version,
            client_price_tax_mode=profile.client_price_tax_mode,
            material_cost_basis=profile.material_cost_basis,
            tax_rule_id=selected.id,
            tax_rule_row_version=selected.row_version,
            category=selected.category,
            rate_percent=selected.rate_percent)

    async def resolve_category(self, *, actor, profile_id, category):
        ConstructionTaxProfileService._actor(actor)
        if not isinstance(profile_id, UUID):
            raise ConstructionTaxProfileValidationError("An explicit profile UUID is required.")
        validated_fields({"category": category})
        transaction = self.session.get_transaction()
        if transaction is None:
            raise ConstructionTaxResolutionError("An active caller transaction is required.")
        try:
            self._same(transaction)
            reviewed = await self.review.review_profile(actor=actor, profile_id=profile_id)
            self._same(transaction)
            result = self._select(
                reviewed, actor=actor, profile_id=profile_id, category=category)
            self._same(transaction)
            return result
        except BaseException as error:
            try:
                if self.session.in_transaction():
                    await self.session.rollback()
            except Exception:
                if not isinstance(error, Exception):
                    raise error
                raise ConstructionTaxResolutionError("Tax selection rollback failed.") from None
            if not isinstance(error, Exception) or isinstance(error, (
                ConstructionTaxResolutionError, ConstructionTaxMissingError,
                ConstructionTaxSetupReviewOperationError,
                ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionTaxProfileNotFoundError, ConstructionTaxRuleNotFoundError,
                ConstructionTaxProfileValidationError, ConstructionTaxRuleValidationError,
            )):
                raise
            raise ConstructionTaxResolutionError("Tax selection failed.") from None


def build_construction_tax_resolution_service(*, session, now_provider=None):
    from services.construction.construction_tax_setup_review import (
        build_construction_tax_setup_review_service,
    )
    return ConstructionTaxResolutionService(
        session=session,
        review_service=build_construction_tax_setup_review_service(
            session=session, now_provider=now_provider))
