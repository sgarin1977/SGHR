"""Local-only tax rule transactions.

SQLite stores Decimal as text in cloned metadata to retain exact rates.
Native numeric/RLS/concurrency behavior is not claimed by these tests.
"""
import asyncio
from decimal import Decimal, localcontext
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import CheckConstraint, String, insert, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.types import TypeDecorator

from database.models import ConstructionTaxRule
from database.repositories.construction.construction_tax_rules import (
    ConstructionTaxRuleRepository,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.construction.construction_tax_rules import (
    ConstructionTaxRuleService, ConstructionTaxRuleValidationError,
    ConstructionTaxRuleNotFoundError, ConstructionTaxRuleOperationError,
    MAX_VERSION,
)
from test_construction_02_tax_profiles import case, LocalUTC


class LocalDecimal(TypeDecorator):
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if not isinstance(value, Decimal):
            raise ValueError("Local tests require Decimal.")
        return str(value)

    def process_result_value(self, value, dialect):
        return Decimal(value) if value is not None else None


@pytest.fixture
def rule_case(case):
    c = case
    c.sync.rollback()
    table = ConstructionTaxRule.__table__.to_metadata(c.table.metadata)
    table.c.rate_percent.type = LocalDecimal()
    for constraint in list(table.constraints):
        if constraint.name == "ck_construction_tax_rules_rate_percent":
            table.constraints.remove(constraint)
    table.append_constraint(CheckConstraint(
        "CAST(rate_percent AS NUMERIC) BETWEEN 0 AND 100",
        name="ck_construction_tax_rules_rate_percent",
    ))
    for name in ("created_at", "updated_at"):
        table.c[name].type = LocalUTC()
    table.create(c.sync.get_bind())
    c.rules = table
    c.profile, c.other_profile, c.foreign_profile = uuid4(), uuid4(), uuid4()
    c.sync.execute(insert(c.table), [
        dict(
            id=profile, tenant_id=tenant, name="Configured profile",
            client_price_tax_mode="TAX_EXCLUSIVE", material_cost_basis="NET",
            row_version=1, created_by=c.user,
            created_at=c.now, updated_at=c.now,
        )
        for profile, tenant in (
            (c.profile, c.tenant),
            (c.other_profile, c.tenant),
            (c.foreign_profile, c.foreign),
        )
    ])
    c.sync.commit()
    c.rule_repo = ConstructionTaxRuleRepository(c.session)
    c.rule_repo.table, c.rule_repo.profiles = table, c.table
    c.rule_service = ConstructionTaxRuleService(
        session=c.session, repository=c.rule_repo,
        access_repository=c.access, event_repository=c.events,
        serialization=c.serialization, now_provider=lambda: c.now,
    )
    c.sync.begin()
    yield c


def begin(c):
    if not c.sync.in_transaction():
        c.sync.begin()


def snapshot(c):
    return tuple(
        tuple(
            dict(row)
            for row in c.sync.execute(
                select(table).order_by(table.c.id)
            ).mappings()
        )
        for table in (c.rules, c.audits)
    )


async def create(c, *, manage_transaction=True, **fields):
    begin(c)
    values = dict(
        profile_id=c.profile, category="WORK", rate_percent=Decimal("20.00"),
    )
    values.update(fields)
    return await c.rule_service.create_rule(
        actor=c.actor, trace_id="tax-rule-test",
        manage_transaction=manage_transaction, **values,
    )


async def patch(c, rule, *, version=None, manage_transaction=True, **fields):
    begin(c)
    return await c.rule_service.update_rule(
        actor=c.actor, profile_id=rule.tax_profile_id, rule_id=rule.id,
        expected_row_version=rule.row_version if version is None else version,
        fields=fields, trace_id="tax-rule-test",
        manage_transaction=manage_transaction,
    )


def test_model_preserves_exact_numeric_and_tenant_bound_profile():
    table = ConstructionTaxRule.__table__
    assert list(table.c.keys()) == [
        "id", "tenant_id", "tax_profile_id", "category", "rate_percent",
        "row_version", "created_by", "created_at", "updated_at",
    ]
    assert len(table.constraints) == 9
    profile_fk = next(
        fk for fk in table.foreign_key_constraints
        if fk.name == "fk_construction_tax_rules_profile"
    )
    assert list(profile_fk.column_keys) == ["tenant_id", "tax_profile_id"]
    assert [element.target_fullname for element in profile_fk.elements] == [
        "construction_tax_profiles.tenant_id", "construction_tax_profiles.id",
    ]
    assert table.c.rate_percent.type.compile(dialect=postgresql.dialect()) == "NUMERIC"
    assert table.c.rate_percent.type.scale is None
    assert table.c.rate_percent.type.asdecimal is True
    assert ConstructionTaxRule.__mapper__.version_id_col is table.c.row_version
    for name in ("category", "rate_percent", "tax_profile_id"):
        assert table.c[name].default is None and table.c[name].server_default is None


@pytest.mark.asyncio
async def test_create_sparse_update_and_audit_are_correlated(rule_case):
    c = rule_case
    original = (await create(c)).rule
    result = await patch(c, original, rate_percent=Decimal("7.125"))
    assert result.changed and result.rule.row_version == 2
    assert result.rule.category == "WORK"
    assert result.rule.tax_profile_id == c.profile
    assert result.rule.created_by == c.user
    assert result.rule.created_at == original.created_at
    commits = c.session.commits
    begin(c)
    assert await c.rule_service.get_rule(
        actor=c.actor, profile_id=c.profile, rule_id=original.id,
    ) == result.rule
    assert c.session.commits == commits
    _, audits = snapshot(c)
    created = next(
        event for event in audits
        if event["event_type"] == "construction_tax_rule_created"
    )
    edited = next(
        event for event in audits
        if event["event_type"] == "construction_tax_rule_updated"
    )
    assert len(audits) == 2
    assert edited["payload"]["changes"] == [
        {"field": "rate_percent", "old_value": "20.00", "new_value": "7.125"},
    ]
    assert created["payload"]["tax_profile_id"] == str(c.profile)
    assert edited["payload"]["tax_profile_id"] == str(c.profile)
    assert all(
        event["tenant_id"] == c.tenant
        and event["user_id"] == c.user
        and event["entity_id"] == original.id
        and event["trace_id"] == "tax-rule-test"
        for event in audits
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("category", [
    "WORK", "MATERIAL", "SERVICE", "EQUIPMENT", "OTHER",
])
@pytest.mark.parametrize("rate", [Decimal("0"), Decimal("100")])
async def test_categories_and_inclusive_rate_boundaries(rule_case, category, rate):
    result = await create(rule_case, category=category, rate_percent=rate)
    assert result.rule.category == category and result.rule.rate_percent == rate


@pytest.mark.asyncio
async def test_rates_remain_exact_independently_of_decimal_context(rule_case):
    c = rule_case
    rate = Decimal("99.123456789012345678901234567890123456789")
    with localcontext() as context:
        context.prec = 2
        original = (await create(c, rate_percent=rate)).rule
        result = await patch(
            c, original,
            rate_percent=Decimal("0.000000000000000000000000000001"),
        )
    rules, audits = snapshot(c)
    assert original.rate_percent == rate
    assert result.rule.rate_percent == rules[0]["rate_percent"] == Decimal("1E-30")
    changes = next(
        event for event in audits
        if event["event_type"] == "construction_tax_rule_updated"
    )["payload"]["changes"]
    assert changes[0]["old_value"] == str(rate)
    assert changes[0]["new_value"] == "1E-30"


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"category": None}, {"category": "works"},
    {"category": []}, {"category": "AUTO"},
    {"rate_percent": None}, {"rate_percent": 20},
    {"rate_percent": 20.0}, {"rate_percent": True},
    {"rate_percent": "20"}, {"rate_percent": Decimal("NaN")},
    {"rate_percent": Decimal("sNaN")}, {"rate_percent": Decimal("Infinity")},
    {"rate_percent": Decimal("-Infinity")},
    {"rate_percent": Decimal("-0.000000000001")},
    {"rate_percent": Decimal("100.0000000000000000000000000001")},
])
async def test_invalid_input_does_not_lock_or_write(rule_case, fields):
    c = rule_case
    with pytest.raises(ConstructionTaxRuleValidationError):
        await create(c, **fields)
    c.serialization.lock_member.assert_not_awaited()
    assert snapshot(c) == ((), ())


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {}, {"rate_percent": Decimal("20.000")}, {"category": "WORK"},
])
async def test_current_noop_is_unchanged_but_stale_noop_conflicts(rule_case, fields):
    c = rule_case
    original = (await create(c)).rule
    before = snapshot(c)
    result = await patch(c, original, **fields)
    assert result.changed is False
    assert result.rule == original and snapshot(c) == before
    await patch(c, original, category="OTHER")
    with pytest.raises(ConstructionRowVersionConflictError):
        await patch(c, original)
    assert len(snapshot(c)[1]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, True, 1.0, "1", 0, -1, 2**63])
async def test_invalid_version_rejected_before_membership_barrier(rule_case, version):
    c = rule_case
    with pytest.raises(ConstructionTaxRuleValidationError):
        await c.rule_service.update_rule(
            actor=c.actor, profile_id=c.profile, rule_id=uuid4(),
            expected_row_version=version, fields={},
        )
    c.serialization.lock_member.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", [
    "id", "tenant_id", "tax_profile_id",
    "created_by", "row_version", "created_at",
])
async def test_sparse_patch_cannot_rebind_immutable_context(rule_case, field):
    c = rule_case
    original = (await create(c)).rule
    before = snapshot(c)
    with pytest.raises(ConstructionTaxRuleValidationError):
        await patch(c, original, **{field: uuid4()})
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "membership", "revoked", "expired", "project", "observer",
])
async def test_fresh_tenant_edit_authority_is_required(rule_case, failure):
    c = rule_case
    if failure == "membership":
        c.membership = False
    elif failure == "revoked":
        c.grant.status = "revoked"
    elif failure == "expired":
        c.grant.expires_at = c.now
    else:
        c.grant.role = (
            "PROJECT_MANAGER" if failure == "project" else "OBSERVER"
        )
        c.grant.scope_type, c.grant.scope_id = "project", uuid4()
    with pytest.raises((
        ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
    )):
        await create(c)
    assert snapshot(c) == ((), ())


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_existing_tenant_roles_read_without_write_barrier(rule_case, role):
    c = rule_case
    original = (await create(c)).rule
    c.grant.role = role
    c.serialization.lock_member.reset_mock()
    begin(c)
    assert await c.rule_service.get_rule(
        actor=c.actor, profile_id=c.profile, rule_id=original.id,
    ) == original
    c.serialization.lock_member.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["barrier", "profile", "rule", "audit"])
async def test_authority_rechecked_after_waits_and_audit(rule_case, phase):
    c = rule_case
    original = (await create(c)).rule
    before = snapshot(c)
    if phase == "barrier":
        async def barrier(**kwargs):
            c.membership = False
        c.serialization.lock_member.side_effect = barrier
    elif phase in {"profile", "rule"}:
        name = "get_profile_id" if phase == "profile" else "get_rule"
        method = getattr(c.rule_repo, name)
        async def read(**kwargs):
            result = await method(**kwargs)
            c.membership = False
            return result
        setattr(c.rule_repo, name, read)
    else:
        c.after_audit = lambda: setattr(c, "membership", False)
    with pytest.raises((
        ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
    )):
        await patch(c, original, category="OTHER")
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("failure", ["audit", "commit", "cancel"])
async def test_real_writes_and_audit_roll_back_together(rule_case, operation, failure):
    c = rule_case
    original = (await create(c)).rule if operation == "update" else None
    before = snapshot(c)
    if failure == "audit":
        c.fail_audit = True
    elif failure == "commit":
        c.session.fail_commit = True
    else:
        async def cancelled(**kwargs):
            c.sync.execute(
                insert(c.audits).values(id=uuid4(), payload={"probe": True})
            )
            raise asyncio.CancelledError()
        c.events.create_event.side_effect = cancelled
    expected = (
        asyncio.CancelledError
        if failure == "cancel" else ConstructionTaxRuleOperationError
    )
    with pytest.raises(expected):
        if original is None:
            await create(c)
        else:
            await patch(c, original, category="OTHER")
    assert snapshot(c) == before and c.session.rollbacks == 1


@pytest.mark.asyncio
async def test_foreign_missing_and_wrong_profile_paths_hide_rules(rule_case):
    c = rule_case
    original = (await create(c)).rule
    for profile in (c.foreign_profile, uuid4(), c.other_profile):
        begin(c)
        with pytest.raises(ConstructionTaxRuleNotFoundError):
            await c.rule_service.get_rule(
                actor=c.actor, profile_id=profile, rule_id=original.id,
            )
    for profile in (c.foreign_profile, uuid4()):
        with pytest.raises(ConstructionTaxRuleNotFoundError):
            await create(c, profile_id=profile)
    assert len(snapshot(c)[0]) == len(snapshot(c)[1]) == 1


@pytest.mark.asyncio
async def test_repository_cas_binds_tenant_profile_and_version(rule_case):
    c = rule_case
    original = (await create(c)).rule
    begin(c)
    for tenant, profile, version in (
        (c.foreign, c.profile, 1),
        (c.tenant, c.other_profile, 1),
        (c.tenant, c.profile, 2),
    ):
        assert await c.rule_repo.update_rule(
            tenant_id=tenant, profile_id=profile, rule_id=original.id,
            expected_row_version=version,
            fields={"category": "OTHER"}, as_of=c.now,
        ) is None
    assert snapshot(c)[0][0]["category"] == "WORK"


@pytest.mark.asyncio
async def test_parent_and_rule_locks_follow_membership_barrier(rule_case):
    c = rule_case
    original = (await create(c)).rule
    order = []
    lock = c.serialization.lock_member.side_effect
    profile = c.rule_repo.get_profile_id
    rule = c.rule_repo.get_rule
    async def barrier(**kwargs):
        order.append("membership")
        await lock(**kwargs)
    async def parent(**kwargs):
        assert kwargs["for_update"] is True
        order.append("profile")
        return await profile(**kwargs)
    async def child(**kwargs):
        assert kwargs["for_update"] is True
        order.append("rule")
        return await rule(**kwargs)
    c.serialization.lock_member.side_effect = barrier
    c.rule_repo.get_profile_id, c.rule_repo.get_rule = parent, child
    await patch(c, original, category="OTHER")
    assert order == ["membership", "profile", "rule"]


@pytest.mark.asyncio
async def test_composite_fk_rejects_cross_tenant_profile(rule_case):
    c = rule_case
    with pytest.raises(IntegrityError):
        c.sync.execute(insert(c.rules).values(
            id=uuid4(), tenant_id=c.tenant, tax_profile_id=c.foreign_profile,
            category="WORK", rate_percent=Decimal("20"), row_version=1,
            created_by=c.user, created_at=c.now, updated_at=c.now,
        ))
    c.sync.rollback()
    assert snapshot(c) == ((), ())


@pytest.mark.asyncio
async def test_preparation_defers_commit_and_remains_rollbackable(rule_case):
    c = rule_case
    result = await create(c, manage_transaction=False)
    assert result.changed and c.session.commits == 0
    assert len(snapshot(c)[0]) == len(snapshot(c)[1]) == 1
    c.sync.rollback()
    assert snapshot(c) == ((), ())


@pytest.mark.asyncio
async def test_max_version_noop_and_cas_loss_have_no_extra_audit(rule_case):
    c = rule_case
    original = (await create(c)).rule
    c.sync.execute(
        c.rules.update().where(c.rules.c.id == original.id)
        .values(row_version=MAX_VERSION)
    )
    c.sync.commit()
    result = await patch(c, original, version=MAX_VERSION)
    assert not result.changed and result.rule.row_version == MAX_VERSION
    with pytest.raises(ConstructionTaxRuleValidationError):
        await patch(c, original, version=MAX_VERSION, category="OTHER")
    c.sync.execute(
        c.rules.update().where(c.rules.c.id == original.id)
        .values(row_version=1)
    )
    c.sync.commit()
    c.rule_repo.update_rule = AsyncMock(return_value=None)
    with pytest.raises(ConstructionRowVersionConflictError):
        await patch(c, original, category="OTHER")
    assert len(snapshot(c)[1]) == 1

# con016_canonical_categories_unique_v1
