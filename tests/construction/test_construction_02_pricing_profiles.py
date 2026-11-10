"""Local-only CON-017 transactions. No runtime database or provider access.

A test-only text Decimal adapter retains exact values in SQLite.
PostgreSQL numeric, native RLS and parallel commit acceptance are separate.
"""
import asyncio
from datetime import timedelta
from decimal import Decimal, localcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import CheckConstraint, String, insert, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.types import TypeDecorator

from database.models import ConstructionPricingProfile
from database.repositories.construction.construction_pricing_profiles import ConstructionPricingProfileRepository
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction.construction_pricing_profiles import (
    ConstructionPricingProfileService, ConstructionPricingProfileValidationError,
    ConstructionPricingProfileNotFoundError, ConstructionPricingProfileConflictError,
    ConstructionPricingProfileOperationError, MAX_VERSION)
from test_construction_02_tax_profiles import case, LocalUTC


class LocalDecimal(TypeDecorator):
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if not isinstance(value, Decimal):
            raise ValueError("Tests require exact Decimal.")
        return str(value)

    def process_result_value(self, value, dialect):
        return Decimal(value) if value is not None else None


@pytest.fixture
def pricing(case):
    c = case
    c.sync.rollback()
    c.prices = ConstructionPricingProfile.__table__.to_metadata(c.table.metadata)
    for name in ("work_coefficient", "material_markup_percent"):
        c.prices.c[name].type = LocalDecimal()
    for constraint in list(c.prices.constraints):
        if constraint.name in {
            "ck_construction_pricing_profiles_work_coefficient",
            "ck_construction_pricing_profiles_material_markup_percent",
        }:
            c.prices.constraints.remove(constraint)
    c.prices.append_constraint(CheckConstraint(
        "CAST(work_coefficient AS NUMERIC) > 0",
        name="ck_construction_pricing_profiles_work_coefficient"))
    c.prices.append_constraint(CheckConstraint(
        "CAST(material_markup_percent AS NUMERIC) BETWEEN 0 AND 100",
        name="ck_construction_pricing_profiles_material_markup_percent"))
    for name in ("created_at", "updated_at"):
        c.prices.c[name].type = LocalUTC()
    c.prices.create(c.sync.get_bind())
    c.price_repository = ConstructionPricingProfileRepository(c.session)
    c.price_repository.table = c.prices
    c.price_service = ConstructionPricingProfileService(
        session=c.session, repository=c.price_repository, access_repository=c.access,
        event_repository=c.events, serialization=c.serialization, now_provider=lambda: c.now)
    c.sync.begin()
    yield c


def begin(c):
    if not c.sync.in_transaction():
        c.sync.begin()


def snapshot(c):
    return tuple(tuple(dict(row) for row in c.sync.execute(
        select(table).order_by(table.c.id)).mappings()) for table in (c.prices, c.audits))


async def create(c, *, manage_transaction=True, **values):
    begin(c)
    fields = dict(code="STANDARD", work_coefficient=Decimal("1.00"),
                  material_markup_percent=Decimal("10.00"))
    fields.update(values)
    return await c.price_service.create_profile(
        actor=c.actor, trace_id="pricing-test", manage_transaction=manage_transaction, **fields)


async def patch(c, state, *, version=None, manage_transaction=True, **fields):
    begin(c)
    return await c.price_service.update_profile(
        actor=c.actor, profile_id=state.id, expected_row_version=state.row_version if version is None else version,
        fields=fields, trace_id="pricing-test", manage_transaction=manage_transaction)


def test_model_matches_storage_without_commercial_defaults():
    table = ConstructionPricingProfile.__table__
    assert list(table.c.keys()) == [
        "id", "tenant_id", "code", "work_coefficient", "material_markup_percent",
        "row_version", "created_by", "created_at", "updated_at"]
    assert len(table.constraints) == 9
    assert {constraint.name for constraint in table.foreign_key_constraints} == {
        "fk_construction_pricing_profiles_tenant", "fk_construction_pricing_profiles_creator"}
    assert ConstructionPricingProfile.__mapper__.version_id_col is table.c.row_version
    assert table.c.row_version.type.compile(dialect=postgresql.dialect()) == "BIGINT"
    for name in ("code", "work_coefficient", "material_markup_percent"):
        assert table.c[name].default is None and table.c[name].server_default is None
    for name in ("work_coefficient", "material_markup_percent"):
        numeric = table.c[name].type
        assert numeric.compile(dialect=postgresql.dialect()) == "NUMERIC"
        assert numeric.precision is None and numeric.scale is None and numeric.asdecimal is True


@pytest.mark.asyncio
async def test_create_sparse_edit_read_and_exact_audit(pricing):
    c = pricing
    original = (await create(c)).profile
    edited = await patch(c, original, work_coefficient=Decimal("1.125"))
    assert edited.changed and edited.profile.row_version == 2
    assert edited.profile.material_markup_percent == original.material_markup_percent
    assert edited.profile.code == original.code
    assert edited.profile.created_by == original.created_by
    assert edited.profile.created_at == original.created_at
    commits = c.session.commits
    c.serialization.lock_member.reset_mock()
    begin(c)
    assert await c.price_service.get_profile(actor=c.actor, profile_id=original.id) == edited.profile
    c.serialization.lock_member.assert_not_awaited()
    assert c.session.commits == commits
    _, events = snapshot(c)
    assert len(events) == 2
    created = next(event for event in events if event["event_type"] == "construction_pricing_profile_created")
    changed = next(event for event in events if event["event_type"] == "construction_pricing_profile_updated")
    assert changed["payload"]["changes"] == [
        {"field": "work_coefficient", "old_value": "1.00", "new_value": "1.125"}]
    assert created["payload"]["code"] == "STANDARD"
    assert all(event["tenant_id"] == c.tenant and event["user_id"] == c.user
               and event["entity_id"] == original.id and event["trace_id"] == "pricing-test"
               for event in events)


@pytest.mark.asyncio
async def test_empty_list_does_not_invent_templates_and_three_levels_are_ordered(pricing):
    c = pricing
    assert await c.price_service.list_profiles(actor=c.actor) == ()
    assert snapshot(c) == ((), ())
    for code in ("PREMIUM", "ECONOMY", "STANDARD"):
        await create(c, code=code)
    c.serialization.lock_member.reset_mock()
    commits = c.session.commits
    begin(c)
    states = await c.price_service.list_profiles(actor=c.actor)
    assert [state.code for state in states] == ["ECONOMY", "STANDARD", "PREMIUM"]
    assert c.session.commits == commits
    c.serialization.lock_member.assert_not_awaited()
    before = snapshot(c)
    with pytest.raises(ConstructionPricingProfileConflictError):
        await create(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("markup", [Decimal("0"), Decimal("100")])
async def test_exact_decimal_values_do_not_depend_on_context_precision(pricing, markup):
    c = pricing
    coefficient = Decimal("1.234567890123456789012345678901234567890123456789")
    with localcontext() as context:
        context.prec = 2
        original = (await create(c, work_coefficient=coefficient, material_markup_percent=markup)).profile
        edited = await patch(c, original, work_coefficient=Decimal("0.000000000000000000000000000001"))
    assert original.work_coefficient == coefficient
    assert edited.profile.work_coefficient == Decimal("1e-30")
    assert edited.profile.material_markup_percent == markup


@pytest.mark.asyncio
@pytest.mark.parametrize("name,bad", [
    ("work_coefficient", Decimal("0")), ("work_coefficient", Decimal("-1")),
    ("work_coefficient", Decimal("NaN")), ("work_coefficient", Decimal("sNaN")),
    ("work_coefficient", Decimal("Infinity")), ("work_coefficient", Decimal("-Infinity")),
    ("work_coefficient", 1), ("work_coefficient", 1.0), ("work_coefficient", "1.0"),
    ("work_coefficient", True), ("work_coefficient", None),
    ("material_markup_percent", Decimal("-0.01")), ("material_markup_percent", Decimal("100.01")),
    ("material_markup_percent", Decimal("NaN")), ("material_markup_percent", Decimal("sNaN")),
    ("material_markup_percent", Decimal("Infinity")), ("material_markup_percent", Decimal("-Infinity")),
    ("material_markup_percent", 10), ("material_markup_percent", 10.0),
    ("material_markup_percent", "10"), ("material_markup_percent", False),
    ("material_markup_percent", None),
])
async def test_invalid_commercial_values_do_not_write_or_take_locks(pricing, name, bad):
    c = pricing
    with pytest.raises(ConstructionPricingProfileValidationError):
        await create(c, **{name: bad})
    c.serialization.lock_member.assert_not_awaited()
    assert snapshot(c) == ((), ())


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["economy", "CUSTOM", "", None, 1])
async def test_unknown_levels_are_rejected(pricing, code):
    with pytest.raises(ConstructionPricingProfileValidationError):
        await create(pricing, code=code)
    assert snapshot(pricing) == ((), ())


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [{}, {"work_coefficient": Decimal("1")},
                                   {"material_markup_percent": Decimal("10.000")}])
async def test_current_noop_keeps_version_audit_and_stale_noop_conflicts(pricing, fields):
    c = pricing
    original = (await create(c)).profile
    before = snapshot(c)
    result = await patch(c, original, **fields)
    assert result.changed is False and result.profile.row_version == 1
    assert snapshot(c) == before
    await patch(c, original, material_markup_percent=Decimal("11"))
    with pytest.raises(ConstructionRowVersionConflictError):
        await patch(c, original, **fields)
    assert len(snapshot(c)[1]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [{"code": "PREMIUM"}, {"tenant_id": None},
                                   {"created_by": None}, {"row_version": 99}, {"default": True}])
async def test_identity_and_level_cannot_be_changed_by_patch(pricing, fields):
    c = pricing
    original = (await create(c)).profile
    before = snapshot(c)
    c.serialization.lock_member.reset_mock()
    begin(c)
    with pytest.raises(ConstructionPricingProfileValidationError):
        await c.price_service.update_profile(
            actor=c.actor, profile_id=original.id, expected_row_version=1, fields=fields)
    c.serialization.lock_member.assert_not_awaited()
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [0, -1, True, 1.0, "1", 2**63, None])
async def test_invalid_expected_versions_are_rejected_before_storage(pricing, version):
    c = pricing
    with pytest.raises(ConstructionPricingProfileValidationError):
        await c.price_service.update_profile(
            actor=c.actor, profile_id=uuid4(), expected_row_version=version, fields={})
    c.serialization.lock_member.assert_not_awaited()
    assert snapshot(c) == ((), ())


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["read", "create", "update", "list"])
@pytest.mark.parametrize("denial", ["membership", "read_only_project", "manager_project"])
async def test_membership_and_existing_pricing_scope_are_required(pricing, operation, denial):
    c = pricing
    original = (await create(c)).profile
    before = snapshot(c)
    if denial == "membership":
        c.membership = False
    else:
        c.grant.role = "OBSERVER" if denial == "read_only_project" else "PROJECT_MANAGER"
        c.grant.scope_type, c.grant.scope_id = "project", uuid4()
    begin(c)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        if operation == "read":
            await c.price_service.get_profile(actor=c.actor, profile_id=original.id)
        elif operation == "list":
            await c.price_service.list_profiles(actor=c.actor)
        elif operation == "create":
            await create(c, code="ECONOMY")
        else:
            await patch(c, original, material_markup_percent=Decimal("11"))
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_foreign_tenant_is_hidden_and_has_independent_level(pricing):
    c = pricing
    foreign_id = uuid4()
    c.sync.execute(insert(c.prices).values(
        id=foreign_id, tenant_id=c.foreign, created_by=c.user, code="STANDARD",
        work_coefficient=Decimal("2"), material_markup_percent=Decimal("0"),
        row_version=1, created_at=c.now, updated_at=c.now))
    c.sync.commit()
    own = (await create(c)).profile
    begin(c)
    assert [state.id for state in await c.price_service.list_profiles(actor=c.actor)] == [own.id]
    for profile_id in (foreign_id, uuid4()):
        begin(c)
        with pytest.raises(ConstructionPricingProfileNotFoundError):
            await c.price_service.get_profile(actor=c.actor, profile_id=profile_id)
        begin(c)
        with pytest.raises(ConstructionPricingProfileNotFoundError):
            await c.price_service.update_profile(
                actor=c.actor, profile_id=profile_id, expected_row_version=1,
                fields={"work_coefficient": Decimal("3")})
    assert len(snapshot(c)[1]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["list", "update"])
async def test_authority_is_refreshed_after_storage_or_row_lock_wait(pricing, operation):
    c = pricing
    original = (await create(c)).profile
    before = snapshot(c)
    name = "list_profiles" if operation == "list" else "get_profile"
    real = getattr(c.price_repository, name)
    async def revoked(**kwargs):
        result = await real(**kwargs)
        c.membership = False
        return result
    setattr(c.price_repository, name, revoked)
    begin(c)
    with pytest.raises(ConstructionPermissionDeniedError):
        if operation == "list":
            await c.price_service.list_profiles(actor=c.actor)
        else:
            await patch(c, original, work_coefficient=Decimal("2"))
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("failure", ["audit", "commit", "cancel", "expired"])
async def test_real_local_changes_and_audit_rollback_together(pricing, operation, failure):
    c = pricing
    original = (await create(c)).profile if operation == "update" else None
    before = snapshot(c)
    if failure == "audit":
        c.fail_audit = True
    elif failure == "commit":
        c.session.fail_commit = True
    elif failure == "expired":
        c.after_audit = lambda: setattr(c.grant, "expires_at", c.now - timedelta(seconds=1))
    else:
        def cancel():
            raise asyncio.CancelledError()
        c.after_audit = cancel
    expected = (asyncio.CancelledError if failure == "cancel" else
                ConstructionPermissionDeniedError if failure == "expired" else
                ConstructionPricingProfileOperationError)
    with pytest.raises(expected):
        if operation == "create":
            await create(c)
        else:
            await patch(c, original, work_coefficient=Decimal("2"))
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_preparation_mode_leaves_profile_and_audit_to_outer_commit(pricing):
    c = pricing
    result = await create(c, manage_transaction=False)
    assert result.changed and c.session.commits == 0
    assert len(snapshot(c)[0]) == len(snapshot(c)[1]) == 1
    await c.session.rollback()
    assert snapshot(c) == ((), ())


@pytest.mark.asyncio
async def test_maximum_version_allows_current_noop_but_no_increment(pricing):
    c = pricing
    original = (await create(c)).profile
    c.sync.execute(update(c.prices).where(c.prices.c.id == original.id).values(row_version=MAX_VERSION))
    c.sync.commit()
    result = await patch(c, original, version=MAX_VERSION)
    assert result.changed is False and result.profile.row_version == MAX_VERSION
    before = snapshot(c)
    with pytest.raises(ConstructionPricingProfileValidationError):
        await patch(c, result.profile, work_coefficient=Decimal("2"))
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("constraint", ["uq_construction_pricing_profiles_tenant_code", "some_other_constraint"])
async def test_only_known_postgresql_unique_conflict_is_translated(pricing, constraint):
    c = pricing
    class DriverError(Exception):
        sqlstate = "23505"
        constraint_name = constraint
    error = IntegrityError("private SQL", {"private": "value"}, DriverError("private driver failure"))
    c.price_repository.create_profile = AsyncMock(side_effect=error)
    expected = (ConstructionPricingProfileConflictError if constraint ==
                "uq_construction_pricing_profiles_tenant_code" else ConstructionPricingProfileOperationError)
    with pytest.raises(expected) as raised:
        await create(c)
    assert "private" not in str(raised.value)
    assert snapshot(c) == ((), ())
