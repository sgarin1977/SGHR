"""Tax selection over canonical services and explicit local SQLite fixtures."""
import asyncio
from dataclasses import replace
from decimal import Decimal, localcontext, Inexact, Rounded
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import update

from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_tax_profiles import (
    ConstructionTaxProfileNotFoundError,
)
from services.construction.construction_tax_rules import (
    ConstructionTaxRuleValidationError,
)
from services.construction.construction_tax_resolution import (
    ConstructionTaxResolutionService,
    ConstructionTaxResolutionError,
    ConstructionTaxMissingError,
)
from test_construction_02_tax_setup_review import (
    case, onboarding, rule_case, review, begin, snapshot, put_rules,
)


@pytest.fixture
def selection(review):
    c = review
    c.selection = ConstructionTaxResolutionService(
        session=c.session, review_service=c.reader)
    return c


async def resolve(c, *, category="WORK", profile_id=None):
    begin(c)
    return await c.selection.resolve_category(
        actor=c.actor, category=category,
        profile_id=c.profile if profile_id is None else profile_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["WORK", "MATERIAL", "SERVICE", "EQUIPMENT", "OTHER"])
async def test_exact_category_uses_explicit_profile_without_writes(selection, category):
    c = selection
    rate = Decimal("7.125000000000000000000000000001")
    put_rules(c, [(1, c.profile, c.tenant, category, rate)])
    before, commits = snapshot(c), c.session.commits
    result = await resolve(c, category=category)
    assert result.tenant_id == c.tenant and result.tax_profile_id == c.profile
    assert result.category == category and result.rate_percent == rate
    assert result.client_price_tax_mode == "TAX_EXCLUSIVE"
    assert result.material_cost_basis == "NET"
    assert result.tax_profile_row_version == result.tax_rule_row_version == 1
    assert snapshot(c) == before and c.session.commits == commits
    assert c.sync.in_transaction()
    assert c.repository.get_profile.await_args_list[0].kwargs["for_share"] is True
    c.events.create_event.assert_not_awaited()
    c.serialization.lock_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_category_does_not_fall_back_to_other_or_another_profile(selection):
    c = selection
    put_rules(c, [
        (1, c.profile, c.tenant, "OTHER", Decimal("0")),
        (2, c.other_profile, c.tenant, "WORK", Decimal("20")),
    ])
    before = snapshot(c)
    with pytest.raises(ConstructionTaxMissingError) as caught:
        await resolve(c)
    assert caught.value.code == "TAX_MISSING" and caught.value.category == "WORK"
    assert not c.sync.in_transaction()
    assert snapshot(c) == before
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_zero_is_valid_and_is_not_missing(selection):
    c = selection
    put_rules(c, [(1, c.profile, c.tenant, "WORK", Decimal("0.0000"))])
    result = await resolve(c)
    assert result.rate_percent == Decimal("0")
    assert Decimal(result.as_snapshot()["rate_percent"]) == Decimal("0")


@pytest.mark.asyncio
async def test_snapshot_keeps_source_versions_after_configuration_changes(selection):
    c = selection
    put_rules(c, [(1, c.profile, c.tenant, "WORK", Decimal("7.125"))])
    original = await resolve(c)
    saved = json.loads(json.dumps(original.as_snapshot()))
    c.sync.rollback()

    c.sync.execute(update(c.rules).where(
        c.rules.c.tax_profile_id == c.profile,
        c.rules.c.category == "WORK",
    ).values(rate_percent=Decimal("20"), row_version=2))
    c.sync.execute(update(c.table).where(c.table.c.id == c.profile).values(
        client_price_tax_mode="TAX_INCLUSIVE", material_cost_basis="GROSS", row_version=2))
    c.sync.commit()

    current = await resolve(c)
    assert current.rate_percent == Decimal("20")
    assert current.client_price_tax_mode == "TAX_INCLUSIVE"
    assert current.material_cost_basis == "GROSS"
    assert current.tax_profile_row_version == current.tax_rule_row_version == 2
    assert original.as_snapshot() == saved
    assert Decimal(saved["rate_percent"]) == Decimal("7.125")
    assert saved["client_price_tax_mode"] == "TAX_EXCLUSIVE"
    assert saved["tax_rule_row_version"] == saved["tax_profile_row_version"] == 1


@pytest.mark.asyncio
async def test_snapshot_does_not_use_ambient_decimal_rounding(selection):
    c = selection
    exact = Decimal("7.125000000000000000000000000001")
    put_rules(c, [(1, c.profile, c.tenant, "WORK", exact)])
    result = await resolve(c)
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        flags = context.flags.copy()
        saved = result.as_snapshot()
        assert Decimal(saved["rate_percent"]) == exact
        assert context.flags == flags


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "observer", "estimator"])
async def test_existing_authority_is_not_expanded(selection, failure):
    c = selection
    before = snapshot(c)
    if failure == "membership":
        c.membership = False
    else:
        c.grant.role = "OBSERVER" if failure == "observer" else "ESTIMATOR"
        c.grant.scope_type = "project"
        c.grant.scope_id = uuid4()
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await resolve(c)
    assert snapshot(c) == before
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign", [False, True])
async def test_missing_and_foreign_profiles_remain_safe_not_found(selection, foreign):
    c = selection
    before = snapshot(c)
    with pytest.raises(ConstructionTaxProfileNotFoundError):
        await resolve(c, profile_id=c.foreign_profile if foreign else uuid4())
    assert snapshot(c) == before
    c.rule_repo.list_rules.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["duplicate", "order", "foreign"])
async def test_ambiguous_or_malformed_review_cannot_select_a_rate(selection, change):
    c = selection
    original = c.reader.review_profile
    async def malformed(**values):
        result = await original(**values)
        if change == "duplicate":
            return replace(result, categories=(
                replace(result.categories[0], rule_count=2, single_rule=None),
                *result.categories[1:]))
        if change == "order":
            return replace(result, categories=tuple(reversed(result.categories)))
        return replace(result, profile=replace(result.profile, tenant_id=c.foreign))
    c.reader.review_profile = malformed
    before = snapshot(c)
    with pytest.raises(ConstructionTaxResolutionError):
        await resolve(c)
    assert not c.sync.in_transaction()
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("category", [None, "work", "UNKNOWN", "MATERIALS"])
async def test_unknown_category_is_not_guessed_or_normalized(selection, category):
    c = selection
    c.reader.review_profile = AsyncMock(wraps=c.reader.review_profile)
    with pytest.raises(ConstructionTaxRuleValidationError):
        await resolve(c, category=category)
    c.reader.review_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_releases_caller_transaction_without_writes(selection):
    c = selection
    c.reader.review_profile = AsyncMock(side_effect=asyncio.CancelledError())
    before = snapshot(c)
    with pytest.raises(asyncio.CancelledError):
        await resolve(c)
    assert not c.sync.in_transaction()
    assert snapshot(c) == before
    c.events.create_event.assert_not_awaited()
