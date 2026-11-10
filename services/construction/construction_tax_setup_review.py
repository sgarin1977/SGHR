"""Facts about one explicitly selected tax profile; no onboarding completion policy."""
from dataclasses import asdict, dataclass
from uuid import UUID

from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_tax_profiles import (
    ConstructionTaxProfileService, ConstructionTaxProfileState,
    ConstructionTaxProfileValidationError, ConstructionTaxProfileNotFoundError,
)
from services.construction.construction_tax_rules import (
    CATEGORIES, ConstructionTaxRuleService, ConstructionTaxRuleState,
    ConstructionTaxRulePage, ConstructionTaxRuleCursor, ConstructionTaxRuleNotFoundError,
)

CATEGORY_ORDER = ("WORK", "MATERIAL", "SERVICE", "EQUIPMENT", "OTHER")


class ConstructionTaxSetupReviewOperationError(Exception):
    pass


@dataclass(frozen=True)
class ConstructionTaxCategoryReview:
    category: str
    rule_count: int
    single_rule: ConstructionTaxRuleState | None


@dataclass(frozen=True)
class ConstructionTaxSetupReview:
    profile: ConstructionTaxProfileState
    categories: tuple[ConstructionTaxCategoryReview, ...]

    @property
    def empty_categories(self):
        return tuple(item.category for item in self.categories if item.rule_count == 0)

    @property
    def multiple_rule_categories(self):
        return tuple(item.category for item in self.categories if item.rule_count > 1)


class ConstructionTaxSetupReviewService:
    def __init__(self, *, session, profiles, rules):
        if (session is None or not hasattr(session, "no_autoflush")
                or any(not callable(getattr(session, name, None)) for name in (
                    "get_transaction", "in_transaction", "get_nested_transaction", "rollback"))
                or getattr(profiles, "session", None) is not session
                or getattr(rules, "session", None) is not session
                or not callable(getattr(profiles, "get_profile", None))
                or not callable(getattr(rules, "list_rules", None))):
            raise ValueError("Tax review requires shared-session canonical readers.")
        self.session, self.profiles, self.rules = session, profiles, rules

    def _same_transaction(self, transaction):
        if (self.session.get_transaction() is not transaction or not self.session.in_transaction()
                or self.session.get_nested_transaction() is not None):
            raise ConstructionTaxSetupReviewOperationError("Tax review transaction changed.")

    @staticmethod
    def _profile(value, actor, profile_id):
        if not isinstance(value, ConstructionTaxProfileState):
            raise ConstructionTaxSetupReviewOperationError("Tax review profile is invalid.")
        return ConstructionTaxProfileService._state(
            asdict(value), tenant_id=actor.tenant_id, profile_id=profile_id)

    async def review_profile(self, *, actor, profile_id):
        ConstructionTaxProfileService._actor(actor)
        if not isinstance(profile_id, UUID):
            raise ConstructionTaxProfileValidationError("A profile UUID is required.")
        transaction = self.session.get_transaction()
        if (transaction is None or not self.session.in_transaction()
                or self.session.get_nested_transaction() is not None
                or any(bool(getattr(self.session, name, ())) for name in ("new", "dirty", "deleted"))):
            raise ConstructionTaxSetupReviewOperationError("A clean active outer transaction is required.")
        try:
            with self.session.no_autoflush:
                if set(CATEGORY_ORDER) != CATEGORIES:
                    raise ConstructionTaxSetupReviewOperationError("Tax category catalog differs.")
                # Tax profile and tax-rule writers lock this parent FOR UPDATE.
                # FOR SHARE keeps the selected profile/rules stable until the
                # caller finishes this transaction. No writer barrier is taken.
                profile = self._profile(await self.profiles.get_profile(
                    actor=actor, profile_id=profile_id, lock_for_review=True), actor, profile_id)
                self._same_transaction(transaction)
                counts = {category: 0 for category in CATEGORY_ORDER}
                singles = {category: None for category in CATEGORY_ORDER}
                cursor, previous = None, None
                while True:
                    page = await self.rules.list_rules(
                        actor=actor, profile_id=profile_id, limit=100, after=cursor)
                    self._same_transaction(transaction)
                    if (not isinstance(page, ConstructionTaxRulePage)
                            or not isinstance(page.items, tuple) or len(page.items) > 100):
                        raise ConstructionTaxSetupReviewOperationError("Tax review page is invalid.")
                    for value in page.items:
                        if not isinstance(value, ConstructionTaxRuleState) or not isinstance(value.id, UUID):
                            raise ConstructionTaxSetupReviewOperationError("Tax review rule is invalid.")
                        rule = ConstructionTaxRuleService._state(
                            asdict(value), tenant_id=actor.tenant_id,
                            profile_id=profile_id, rule_id=value.id)
                        if previous is not None and rule.id <= previous:
                            raise ConstructionTaxSetupReviewOperationError("Tax review order is invalid.")
                        previous = rule.id
                        counts[rule.category] += 1
                        singles[rule.category] = rule if counts[rule.category] == 1 else None
                    following = page.next_cursor
                    if following is None:
                        break
                    if (not isinstance(following, ConstructionTaxRuleCursor) or len(page.items) != 100
                            or (following.tenant_id, following.user_id, following.tax_profile_id)
                            != (actor.tenant_id, actor.user_id, profile_id)
                            or following.after_id != previous):
                        raise ConstructionTaxSetupReviewOperationError("Tax review continuation is invalid.")
                    cursor = ConstructionTaxRuleCursor(
                        following.tenant_id, following.user_id,
                        following.tax_profile_id, following.after_id)
                refreshed = self._profile(
                    await self.profiles.get_profile(actor=actor, profile_id=profile_id), actor, profile_id)
                self._same_transaction(transaction)
                if refreshed != profile:
                    raise ConstructionTaxSetupReviewOperationError("Tax review profile changed.")
                return ConstructionTaxSetupReview(profile, tuple(
                    ConstructionTaxCategoryReview(category, counts[category], singles[category])
                    for category in CATEGORY_ORDER))
        except BaseException as error:
            try:
                if self.session.in_transaction():
                    await self.session.rollback()
            except Exception:
                if not isinstance(error, Exception):
                    raise error
                raise ConstructionTaxSetupReviewOperationError("Tax review rollback failed.") from None
            if isinstance(error, (
                ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ConstructionTaxProfileNotFoundError, ConstructionTaxRuleNotFoundError,
                ConstructionTaxSetupReviewOperationError,
            )) or not isinstance(error, Exception):
                raise
            raise ConstructionTaxSetupReviewOperationError("Tax setup review failed.") from None


def build_construction_tax_setup_review_service(*, session, now_provider=None):
    from services.construction.construction_tax_profiles import build_construction_tax_profile_service
    from services.construction.construction_tax_rules import build_construction_tax_rule_service
    return ConstructionTaxSetupReviewService(
        session=session,
        profiles=build_construction_tax_profile_service(session=session, now_provider=now_provider),
        rules=build_construction_tax_rule_service(session=session, now_provider=now_provider))

# con016_canonical_categories_unique_v1
