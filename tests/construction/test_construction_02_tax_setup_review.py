"""Local tax review facts; no onboarding completion policy or native lock claim."""
import asyncio
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects import postgresql

from database.repositories.construction.construction_tax_profiles import ConstructionTaxProfileRepository
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError)
from services.construction.construction_tax_profiles import (
    ConstructionTaxProfileNotFoundError, ConstructionTaxProfileValidationError)
from services.construction.construction_tax_rules import (
    ConstructionTaxRuleState, ConstructionTaxRulePage, ConstructionTaxRuleCursor)
from services.construction.construction_tax_setup_review import (
    ConstructionTaxSetupReviewService as Service,
    ConstructionTaxSetupReviewOperationError as OperationError,
    CATEGORY_ORDER,
)
from test_construction_02_tax_profiles import case
from test_construction_02_tax_rules import rule_case
from test_construction_02_onboarding_progress import onboarding


@pytest.fixture
def review(onboarding, rule_case):
    assert onboarding is rule_case
    c = onboarding
    c.profile_query = c.repository.get_profile
    c.repository.get_profile = AsyncMock(wraps=c.profile_query)
    c.rule_query = c.rule_repo.list_rules
    c.rule_repo.list_rules = AsyncMock(wraps=c.rule_query)
    c.reader = Service(session=c.session, profiles=c.service, rules=c.rule_service)
    yield c


def begin(c):
    if not c.sync.in_transaction():
        c.sync.begin()


def snapshot(c):
    return tuple(tuple(dict(row) for row in c.sync.execute(
        select(table).order_by(table.c[0])).mappings())
        for table in (c.table, c.rules, c.settings, c.audits))


async def read(c, profile_id=None):
    begin(c)
    return await c.reader.review_profile(actor=c.actor, profile_id=c.profile if profile_id is None else profile_id)


def put_rules(c, rows):
    c.sync.execute(insert(c.rules), [
        dict(id=UUID(int=number), tax_profile_id=profile_id, tenant_id=tenant_id,
             category=category, rate_percent=rate, row_version=1,
             created_by=c.user, created_at=c.now, updated_at=c.now)
        for number, profile_id, tenant_id, category, rate in rows
    ])
    c.sync.commit()


@pytest.mark.asyncio
async def test_empty_rules_are_reported_as_facts_without_defaults_or_progress_changes(review):
    c = review
    before, commits = snapshot(c), c.session.commits
    result = await read(c)
    assert result.profile.id == c.profile and result.profile.tenant_id == c.tenant
    assert result.profile.client_price_tax_mode == "TAX_EXCLUSIVE"
    assert result.profile.material_cost_basis == "NET"
    assert tuple(item.category for item in result.categories) == CATEGORY_ORDER
    assert all(item.rule_count == 0 and item.single_rule is None for item in result.categories)
    assert result.empty_categories == CATEGORY_ORDER and result.multiple_rule_categories == ()
    assert snapshot(c) == before and c.session.commits == commits and c.sync.in_transaction()
    assert c.repository.get_profile.await_args_list[0].kwargs == {
        "tenant_id": c.tenant, "profile_id": c.profile, "for_share": True}
    assert c.repository.get_profile.await_args_list[-1].kwargs == {
        "tenant_id": c.tenant, "profile_id": c.profile}
    c.events.create_event.assert_not_awaited()
    c.serialization.lock_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_all_pages_are_counted_and_explicit_zero_is_distinct_from_missing_rate(review):
    c = review
    exact = Decimal("7.125000000000000000000000000001")
    put_rules(c, [
        (1, c.profile, c.tenant, "WORK", exact),
        (2, c.profile, c.tenant, "MATERIAL", Decimal("0.0000")),
        (3, c.profile, c.tenant, "EQUIPMENT", Decimal("30")),
        (4, c.profile, c.tenant, "OTHER", Decimal("100")),
        (2001, c.other_profile, c.tenant, "SERVICE", Decimal("21")),
        (3001, c.foreign_profile, c.foreign, "EQUIPMENT", Decimal("30")),
    ])
    before = snapshot(c)
    result = await read(c)
    categories = {item.category: item for item in result.categories}
    assert categories["WORK"].rule_count == 1
    assert categories["WORK"].single_rule.rate_percent == exact
    assert categories["MATERIAL"].rule_count == 1
    assert str(categories["MATERIAL"].single_rule.rate_percent) == "0.0000"
    assert categories["SERVICE"].rule_count == 0
    assert categories["SERVICE"].single_rule is None
    assert result.multiple_rule_categories == ()
    assert result.empty_categories == ("SERVICE",)
    c.rule_repo.list_rules.assert_awaited_once_with(
        tenant_id=c.tenant, profile_id=c.profile, limit=101, after_id=None)
    assert snapshot(c) == before and c.session.commits == 0


@pytest.mark.asyncio
async def test_equal_duplicate_rates_do_not_result_in_an_arbitrary_selected_rule(review):
    c = review
    before = snapshot(c)
    with pytest.raises(IntegrityError):
        put_rules(c, [
            (1, c.profile, c.tenant, "WORK", Decimal("0.00")),
            (2, c.profile, c.tenant, "WORK", Decimal("0.00")),
        ])
    c.sync.rollback()
    assert snapshot(c) == before
    result = await read(c)
    assert result.multiple_rule_categories == ()
    assert all(item.rule_count == 0 for item in result.categories)


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign", [False, True])
async def test_missing_and_foreign_profiles_remain_safely_unavailable(review, foreign):
    c = review
    before = snapshot(c)
    with pytest.raises(ConstructionTaxProfileNotFoundError):
        await read(c, c.foreign_profile if foreign else uuid4())
    c.rule_repo.list_rules.assert_not_awaited()
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_existing_tenant_tax_read_authority_is_preserved(review, role):
    c = review
    c.grant.role = role
    assert (await read(c)).profile.id == c.profile


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "observer", "estimator"])
async def test_existing_scope_and_membership_checks_are_not_expanded(review, failure):
    c = review
    if failure == "membership":
        c.membership = False
    else:
        c.grant.role = "OBSERVER" if failure == "observer" else "ESTIMATOR"
        c.grant.scope_type, c.grant.scope_id = "project", uuid4()
    before = snapshot(c)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await read(c)
    assert snapshot(c) == before
    c.rule_repo.list_rules.assert_not_awaited()


@pytest.mark.asyncio
async def test_authority_is_rechecked_after_waiting_for_profile_share_lock(review):
    c = review
    async def revoked(**values):
        row = await c.profile_query(**values)
        if values.get("for_share"):
            c.membership = False
        return row
    c.repository.get_profile.side_effect = revoked
    before = snapshot(c)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await read(c)
    assert snapshot(c) == before
    c.rule_repo.list_rules.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "profile", "rate", "order", "cursor"])
async def test_malformed_rule_pages_cannot_produce_review_facts(review, failure):
    c = review
    rule = ConstructionTaxRuleState(
        UUID(int=1), c.tenant, c.profile, "WORK", Decimal("0"),
        1, c.user, c.now, c.now)
    if failure == "tenant":
        items, cursor = (replace(rule, tenant_id=c.foreign),), None
    elif failure == "profile":
        items, cursor = (replace(rule, tax_profile_id=c.other_profile),), None
    elif failure == "rate":
        items, cursor = (replace(rule, rate_percent=Decimal("NaN")),), None
    elif failure == "order":
        items, cursor = (rule, rule), None
    else:
        items = tuple(replace(rule, id=UUID(int=i)) for i in range(1, 101))
        cursor = ConstructionTaxRuleCursor(c.foreign, c.user, c.profile, UUID(int=100))
    c.rule_service.list_rules = AsyncMock(return_value=ConstructionTaxRulePage(items, cursor))
    before = snapshot(c)
    with pytest.raises(OperationError):
        await read(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_reader_commit_is_rejected_before_rule_query(review):
    c = review
    original = c.service.get_profile
    async def committed(**values):
        profile = await original(**values)
        await c.session.commit()
        return profile
    c.service.get_profile = committed
    before = snapshot(c)
    with pytest.raises(OperationError):
        await read(c)
    assert snapshot(c) == before
    c.rule_repo.list_rules.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_rolls_back_the_review_transaction(review):
    c = review
    c.rule_service.list_rules = AsyncMock(side_effect=asyncio.CancelledError())
    before = snapshot(c)
    with pytest.raises(asyncio.CancelledError):
        await read(c)
    assert not c.sync.in_transaction()
    assert snapshot(c) == before
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_default_profile_read_keeps_its_original_repository_call(review):
    c = review
    begin(c)
    result = await c.service.get_profile(actor=c.actor, profile_id=c.profile)
    assert result.id == c.profile
    c.repository.get_profile.assert_awaited_once_with(tenant_id=c.tenant, profile_id=c.profile)


@pytest.mark.asyncio
async def test_postgresql_share_lock_remains_tenant_scoped_and_exclusive_lock_is_preserved():
    statements = []
    class Capture:
        async def execute(self, statement):
            statements.append(statement)
            return NS(mappings=lambda: NS(one_or_none=lambda: None))
    repo = ConstructionTaxProfileRepository(Capture())
    tenant_id, profile_id = uuid4(), uuid4()
    await repo.get_profile(tenant_id=tenant_id, profile_id=profile_id, for_share=True)
    compiled = statements[-1].compile(dialect=postgresql.dialect())
    assert "FOR SHARE" in str(compiled) and tenant_id in compiled.params.values()
    assert profile_id in compiled.params.values()
    await repo.get_profile(tenant_id=tenant_id, profile_id=profile_id, for_update=True)
    assert "FOR UPDATE" in str(statements[-1].compile(dialect=postgresql.dialect()))
    with pytest.raises(ValueError):
        await repo.get_profile(tenant_id=tenant_id, profile_id=profile_id, for_update=True, for_share=True)


@pytest.mark.asyncio
async def test_invalid_profile_selector_fails_before_query(review):
    with pytest.raises(ConstructionTaxProfileValidationError):
        await read(review, "not-a-uuid")
    review.repository.get_profile.assert_not_awaited()

# con016_canonical_categories_unique_v1
