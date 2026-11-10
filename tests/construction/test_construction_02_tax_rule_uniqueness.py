"""Canonical tax categories and unique current rules; local-only checks."""
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError

from database.models import ConstructionTaxRule
from services.construction.construction_tax_rules import (
    CATEGORIES, ConstructionTaxRuleConflictError,
    ConstructionTaxRuleOperationError, ConstructionTaxRuleValidationError,
    validated_fields,
)
from test_construction_02_tax_profiles import case
from test_construction_02_tax_rules import (
    rule_case, create, patch, snapshot as domain_snapshot,
)
from test_construction_02_tax_rule_workflow import (
    rule_workflow_case, snapshot as full_snapshot,
)
from test_construction_02_tax_rule_write_http import write_http, call, created


def test_canonical_categories_and_model_uniqueness():
    assert CATEGORIES == {"WORK", "MATERIAL", "SERVICE", "EQUIPMENT", "OTHER"}
    constraint = next(
        item for item in ConstructionTaxRule.__table__.constraints
        if item.name == "uq_construction_tax_rules_profile_category")
    assert list(constraint.columns.keys()) == [
        "tenant_id", "tax_profile_id", "category"]
    assert len(ConstructionTaxRule.__table__.constraints) == 9


@pytest.mark.parametrize("legacy", ["WORKS", "MATERIALS", "SERVICES"])
def test_legacy_category_codes_are_not_new_domain_input(legacy):
    with pytest.raises(ConstructionTaxRuleValidationError):
        validated_fields({"category": legacy})


@pytest.mark.asyncio
@pytest.mark.parametrize("rate", [Decimal("0"), Decimal("20")])
async def test_duplicate_category_is_conflict_even_for_equal_or_zero_rate(rule_case, rate):
    c = rule_case
    await create(c)
    before = domain_snapshot(c)
    with pytest.raises(ConstructionTaxRuleConflictError):
        await create(c, rate_percent=rate)
    assert domain_snapshot(c) == before


@pytest.mark.asyncio
async def test_category_change_cannot_replace_another_rule(rule_case):
    c = rule_case
    original = (await create(c)).rule
    material = (await create(c, category="MATERIAL")).rule
    before = domain_snapshot(c)
    with pytest.raises(ConstructionTaxRuleConflictError):
        await patch(c, material, category=original.category)
    assert domain_snapshot(c) == before


@pytest.mark.asyncio
async def test_same_category_in_another_profile_is_allowed(rule_case):
    c = rule_case
    first = (await create(c)).rule
    second = (await create(c, profile_id=c.other_profile)).rule
    assert first.category == second.category == "WORK"
    assert first.tax_profile_id != second.tax_profile_id
    assert len(domain_snapshot(c)[0]) == 2


@pytest.mark.asyncio
async def test_database_uniqueness_also_protects_direct_insert(rule_case):
    c = rule_case
    original = (await create(c)).rule
    before = domain_snapshot(c)
    with pytest.raises(IntegrityError):
        c.sync.execute(insert(c.rules).values(
            id=uuid4(), tenant_id=c.tenant, tax_profile_id=c.profile,
            category=original.category, rate_percent=Decimal("99"),
            row_version=1, created_by=c.user,
            created_at=c.now, updated_at=c.now))
    c.sync.rollback()
    assert domain_snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("constraint_name,expected", [
    ("uq_construction_tax_rules_profile_category", ConstructionTaxRuleConflictError),
    ("construction_tax_rules_pkey", ConstructionTaxRuleOperationError),
])
async def test_only_named_postgresql_category_collision_becomes_conflict(
    rule_case, constraint_name, expected,
):
    c = rule_case
    class NativeUniqueError(Exception):
        sqlstate = "23505"
    native = NativeUniqueError("private database detail")
    native.constraint_name = constraint_name
    async def collision(**kwargs):
        raise IntegrityError("private statement", {}, native)
    c.rule_repo.create_rule = collision
    with pytest.raises(expected):
        await create(c)
    assert domain_snapshot(c) == ((), ())


@pytest.mark.asyncio
async def test_http_category_conflicts_roll_back_receipt_and_audit(write_http):
    c = write_http
    first = await created(c)
    second = await created(
        c, key="material-create",
        body={"category": "MATERIAL", "rate_percent": "10"})
    before = full_snapshot(c)

    duplicate = await call(
        c, key="duplicate-category",
        body={"category": "WORK", "rate_percent": "0"})
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["error"]["code"] == "TAX_RULE_CATEGORY_EXISTS"
    assert full_snapshot(c) == before

    changed = await call(
        c, "PATCH", rule=second["id"], key="duplicate-patch",
        body={"expected_row_version": 1, "category": first["category"]})
    assert changed.status_code == 409, changed.text
    assert changed.json()["error"]["code"] == "TAX_RULE_CATEGORY_EXISTS"
    assert full_snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", ["WORKS", "MATERIALS", "SERVICES"])
async def test_http_rejects_legacy_input_without_creating_receipt(write_http, legacy):
    c = write_http
    before = full_snapshot(c)
    response = await call(
        c, body={"category": legacy, "rate_percent": "20"})
    assert response.status_code == 422, response.text
    assert full_snapshot(c) == before

# con016_canonical_categories_unique_v1
