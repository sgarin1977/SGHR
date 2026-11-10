"""Local-only CON-018 SQL transactions; no runtime DB, native RLS or concurrency claim.

Only local parent projection and Decimal/UTC adapters differ from production.
"""
import asyncio
from dataclasses import asdict
from decimal import Decimal, localcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, String, Table, UniqueConstraint, Uuid, insert, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from database.models import ConstructionProjectPricingAdjustment
from database.repositories.construction.construction_project_pricing_adjustments import ConstructionProjectPricingAdjustmentRepository
from services.construction.construction_foundation import ConstructionProjectTerminalError, ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction.construction_project_pricing_adjustments import (
    ConstructionProjectPricingAdjustmentService as Service,
    ConstructionProjectPricingAdjustmentValidationError as ValidationError,
    ConstructionProjectPricingAdjustmentNotFoundError as NotFoundError,
    ConstructionProjectPricingAdjustmentOperationError as OperationError,
    ConstructionProjectPricingAdjustmentCursor as Cursor, MAX_VERSION)
from test_construction_02_tax_profiles import case, LocalUTC
from test_construction_02_tax_rules import LocalDecimal


@pytest.fixture
def adjustment_case(case):
    c = case
    c.sync.rollback()
    metadata = c.table.metadata
    c.projects = Table(
        "construction_projects", metadata,
        Column("id", Uuid, primary_key=True),
        Column("tenant_id", Uuid, ForeignKey("tenants.id"), nullable=False),
        Column("status", String, nullable=False),
        Column("deleted_at", LocalUTC(), nullable=True),
        Column("row_version", BigInteger, nullable=False),
        UniqueConstraint("tenant_id", "id", name="uq_construction_projects_tenant_id_id"))
    c.projects.create(c.sync.get_bind())
    c.adjustments = ConstructionProjectPricingAdjustment.__table__.to_metadata(metadata)
    c.adjustments.c.coefficient.type = LocalDecimal()
    for constraint in list(c.adjustments.constraints):
        if constraint.name == "ck_construction_project_pricing_adjustments_coefficient":
            c.adjustments.constraints.remove(constraint)
    c.adjustments.append_constraint(CheckConstraint(
        "CAST(coefficient AS NUMERIC) > 0",
        name="ck_construction_project_pricing_adjustments_coefficient"))
    for name in ("created_at", "updated_at"):
        c.adjustments.c[name].type = LocalUTC()
    c.adjustments.create(c.sync.get_bind())
    c.project, c.other_project, c.foreign_project = uuid4(), uuid4(), uuid4()
    c.sync.execute(insert(c.projects), [
        dict(id=project, tenant_id=tenant, status="draft", deleted_at=None, row_version=1)
        for project, tenant in ((c.project, c.tenant), (c.other_project, c.tenant), (c.foreign_project, c.foreign))
    ])
    c.sync.commit()
    c.adjustment_repo = ConstructionProjectPricingAdjustmentRepository(c.session)
    c.adjustment_repo.table, c.adjustment_repo.projects = c.adjustments, c.projects
    c.order = []
    original_lock = c.serialization.lock_member.side_effect
    async def member(**values):
        c.order.append("member")
        await original_lock(**values)
    c.serialization.lock_member.side_effect = member
    real_project = c.adjustment_repo.get_project
    async def project(**values):
        c.order.append("project_lock" if values["for_update"] else "project_read")
        return await real_project(**values)
    c.adjustment_repo.get_project = AsyncMock(side_effect=project)
    real_adjustment = c.adjustment_repo.get_adjustment
    async def adjustment(**values):
        c.order.append("adjustment_lock" if values.get("for_update") else "adjustment_read")
        return await real_adjustment(**values)
    c.adjustment_repo.get_adjustment = AsyncMock(side_effect=adjustment)
    c.adjustment_service = Service(
        session=c.session, repository=c.adjustment_repo, access_repository=c.access,
        event_repository=c.events, serialization=c.serialization, now_provider=lambda: c.now)
    c.sync.begin()
    yield c


def begin(c):
    if not c.sync.in_transaction():
        c.sync.begin()


def snapshot(c):
    return tuple(tuple(dict(row) for row in c.sync.execute(
        select(table).order_by(table.c.id)).mappings()) for table in (c.adjustments, c.audits, c.projects))


async def create(c, *, project=None, manage_transaction=True, **changes):
    begin(c)
    fields = dict(name="Urgency", coefficient=Decimal("1.10"), enabled=True)
    fields.update(changes)
    return await c.adjustment_service.create_adjustment(
        actor=c.actor, project_id=c.project if project is None else project,
        trace_id="adjustment-test", manage_transaction=manage_transaction, **fields)


async def patch(c, state, *, project=None, version=None, manage_transaction=True, **fields):
    begin(c)
    return await c.adjustment_service.update_adjustment(
        actor=c.actor, project_id=state.project_id if project is None else project,
        adjustment_id=state.id, expected_row_version=state.row_version if version is None else version,
        fields=fields, trace_id="adjustment-test", manage_transaction=manage_transaction)


async def get(c, state):
    begin(c)
    return await c.adjustment_service.get_adjustment(
        actor=c.actor, project_id=state.project_id, adjustment_id=state.id)


async def page(c, *, project=None, **values):
    begin(c)
    return await c.adjustment_service.list_adjustments(
        actor=c.actor, project_id=c.project if project is None else project, **values)


def test_model_matches_ten_columns_eight_constraints_and_explicit_fields():
    table = ConstructionProjectPricingAdjustment.__table__
    assert list(table.c.keys()) == [
        "id", "tenant_id", "project_id", "name", "coefficient", "enabled",
        "row_version", "created_by", "created_at", "updated_at"]
    assert len(table.constraints) == 8 and len(table.indexes) == 1
    project_fk = next(fk for fk in table.foreign_key_constraints
                      if fk.name == "fk_construction_project_pricing_adjustments_project")
    assert list(project_fk.column_keys) == ["tenant_id", "project_id"]
    assert [part.target_fullname for part in project_fk.elements] == ["construction_projects.tenant_id", "construction_projects.id"]
    assert ConstructionProjectPricingAdjustment.__mapper__.version_id_col is table.c.row_version
    assert table.c.row_version.type.compile(dialect=postgresql.dialect()) == "BIGINT"
    assert table.c.coefficient.type.compile(dialect=postgresql.dialect()) == "NUMERIC"
    assert table.c.coefficient.type.scale is None and table.c.coefficient.type.precision is None
    for name in ("name", "coefficient", "enabled"):
        assert not table.c[name].nullable
        assert table.c[name].default is None and table.c[name].server_default is None


@pytest.mark.asyncio
async def test_create_edit_toggle_and_read_preserve_parent_and_correlate_audit(adjustment_case):
    c = adjustment_case
    parents = snapshot(c)[2]
    original = (await create(c)).adjustment
    assert c.order.index("member") < c.order.index("project_lock")
    c.order.clear()
    edited = await patch(c, original, coefficient=Decimal("1.25"), enabled=False)
    assert c.order.index("member") < c.order.index("project_lock") < c.order.index("adjustment_lock")
    assert edited.changed and edited.adjustment.row_version == 2
    assert edited.adjustment.created_at == original.created_at and edited.adjustment.created_by == c.user
    commits = c.session.commits
    c.serialization.lock_member.reset_mock()
    assert await get(c, edited.adjustment) == edited.adjustment
    result = await page(c)
    assert result.items == (edited.adjustment,) and result.next_cursor is None
    assert result.items[0].enabled is False and c.session.commits == commits
    c.serialization.lock_member.assert_not_awaited()
    records, audits, actual_parents = snapshot(c)
    assert actual_parents == parents and len(records) == len(audits) - 1 == 1
    changed = next(event for event in audits if event["event_type"] == "construction_project_pricing_adjustment_updated")
    assert changed["payload"]["changes"] == [
        {"field": "coefficient", "old_value": "1.10", "new_value": "1.25"},
        {"field": "enabled", "old_value": True, "new_value": False}]
    assert all(event["tenant_id"] == c.tenant and event["user_id"] == c.user and event["entity_id"] == original.id
               and event["payload"]["project_id"] == str(c.project) and event["trace_id"] == "adjustment-test" for event in audits)


@pytest.mark.asyncio
@pytest.mark.parametrize("coefficient", [Decimal("1e-30"), Decimal("1E+10000"), Decimal("1.23456789012345678901234567890123456789")])
async def test_name_normalization_and_exact_coefficients_ignore_decimal_context(adjustment_case, coefficient):
    with localcontext() as context:
        context.prec = 2
        state = (await create(adjustment_case, name="  Logistics  ", coefficient=coefficient, enabled=False)).adjustment
    assert state.name == "Logistics" and state.coefficient == coefficient and state.enabled is False


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"name": ""}, {"name": "  "}, {"name": "x" * 256}, {"name": None}, {"name": "a\x00b"},
    {"coefficient": Decimal("0")}, {"coefficient": Decimal("-1")}, {"coefficient": Decimal("NaN")},
    {"coefficient": Decimal("sNaN")}, {"coefficient": Decimal("Infinity")}, {"coefficient": Decimal("-Infinity")},
    {"coefficient": 1}, {"coefficient": 1.1}, {"coefficient": True}, {"coefficient": "1.1"},
    {"coefficient": None}, {"enabled": None}, {"enabled": 1}, {"enabled": "true"},
])
async def test_invalid_fields_do_not_write_or_take_locks(adjustment_case, fields):
    c = adjustment_case
    before = snapshot(c)
    with pytest.raises(ValidationError):
        await create(c, **fields)
    c.serialization.lock_member.assert_not_awaited()
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [{}, {"name": " Urgency "}, {"coefficient": Decimal("1.100")}, {"enabled": True}])
async def test_current_noop_has_no_update_version_or_audit_and_stale_noop_conflicts(adjustment_case, fields):
    c = adjustment_case
    original = (await create(c)).adjustment
    before = snapshot(c)
    noop = await patch(c, original, **fields)
    assert not noop.changed and noop.adjustment.row_version == 1 and snapshot(c) == before
    await patch(c, original, enabled=False)
    with pytest.raises(ConstructionRowVersionConflictError):
        await patch(c, original, **fields)
    assert len(snapshot(c)[1]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [{"project_id": None}, {"tenant_id": None}, {"created_by": None}, {"row_version": 9}, {"automatic": True}])
async def test_patch_cannot_change_identity_context(adjustment_case, fields):
    c = adjustment_case
    original = (await create(c)).adjustment
    before = snapshot(c)
    with pytest.raises(ValidationError):
        await patch(c, original, **fields)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [0, -1, True, 1.0, "1", 2**63, None])
async def test_invalid_versions_are_rejected_before_locks(adjustment_case, version):
    c = adjustment_case
    with pytest.raises(ValidationError):
        await c.adjustment_service.update_adjustment(
            actor=c.actor, project_id=c.project, adjustment_id=uuid4(), expected_row_version=version, fields={})
    c.serialization.lock_member.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role,scope,write,allowed", [
    ("OWNER", "tenant", False, True), ("OWNER", "tenant", True, True),
    ("ADMIN", "tenant", False, True), ("ADMIN", "tenant", True, True),
    ("ESTIMATOR", "project", False, True), ("ESTIMATOR", "project", True, False),
    ("PROJECT_MANAGER", "project", False, True), ("PROJECT_MANAGER", "project", True, False),
    ("OBSERVER", "project", False, True), ("OBSERVER", "project", True, False),
    ("EXECUTOR", "project", False, False),
])
async def test_existing_role_permissions_and_exact_project_scope(adjustment_case, role, scope, write, allowed):
    c = adjustment_case
    original = (await create(c)).adjustment
    c.grant.role, c.grant.scope_type = role, scope
    c.grant.scope_id = c.tenant if scope == "tenant" else c.project
    async def operation():
        if write:
            return await patch(c, original, enabled=False)
        return await get(c, original)
    if allowed:
        await operation()
    else:
        with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
            await operation()
    if scope == "project":
        with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
            await page(c, project=c.other_project)


@pytest.mark.asyncio
async def test_permission_from_one_project_is_not_combined_with_another_role_scope(adjustment_case):
    c = adjustment_case
    c.grant.role, c.grant.scope_type, c.grant.scope_id = "OBSERVER", "project", c.project
    executor = SimpleNamespace(role="EXECUTOR", scope_type="project", scope_id=c.other_project, status="active", expires_at=None)
    c.access.list_active_grants = AsyncMock(return_value=[c.grant, executor])
    assert (await page(c)).items == ()
    c.adjustment_repo.get_project.reset_mock()
    with pytest.raises(ConstructionScopeDeniedError):
        await page(c, project=c.other_project)
    c.adjustment_repo.get_project.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["draft", "planning", "active", "completed", "cancelled"])
async def test_existing_project_lifecycle_allows_reads_and_blocks_terminal_mutations(adjustment_case, status):
    c = adjustment_case
    original = (await create(c)).adjustment
    c.sync.execute(update(c.projects).where(c.projects.c.id == c.project).values(status=status))
    c.sync.commit()
    assert await get(c, original) == original
    assert (await page(c)).items == (original,)
    if status in {"completed", "cancelled"}:
        before = snapshot(c)
        with pytest.raises(ConstructionProjectTerminalError):
            await create(c)
        with pytest.raises(ConstructionProjectTerminalError):
            await patch(c, original)
        assert snapshot(c) == before
    else:
        assert (await patch(c, original, enabled=False)).changed


@pytest.mark.asyncio
@pytest.mark.parametrize("context", ["foreign", "missing", "archived"])
async def test_unavailable_project_is_hidden_for_all_operations(adjustment_case, context):
    c = adjustment_case
    original = (await create(c)).adjustment
    project = c.foreign_project if context == "foreign" else uuid4() if context == "missing" else c.project
    if context == "archived":
        c.sync.execute(update(c.projects).where(c.projects.c.id == c.project).values(deleted_at=c.now))
        c.sync.commit()
    before = snapshot(c)
    with pytest.raises(NotFoundError):
        await create(c, project=project)
    with pytest.raises(NotFoundError):
        await patch(c, original, project=project, enabled=False)
    with pytest.raises(NotFoundError):
        begin(c)
        await c.adjustment_service.get_adjustment(actor=c.actor, project_id=project, adjustment_id=original.id)
    with pytest.raises(NotFoundError):
        await page(c, project=project)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_adjustment_cannot_be_read_or_updated_under_another_project(adjustment_case):
    c = adjustment_case
    original = (await create(c, project=c.other_project)).adjustment
    before = snapshot(c)
    with pytest.raises(NotFoundError):
        await patch(c, original, project=c.project, enabled=False)
    with pytest.raises(NotFoundError):
        begin(c)
        await c.adjustment_service.get_adjustment(actor=c.actor, project_id=c.project, adjustment_id=original.id)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_local_composite_fk_rejects_existing_project_of_another_tenant(adjustment_case):
    c = adjustment_case
    before = snapshot(c)
    with pytest.raises(IntegrityError):
        await c.adjustment_repo.create_adjustment(
            tenant_id=c.tenant, project_id=c.foreign_project, adjustment_id=uuid4(), created_by=c.user,
            fields=dict(name="Explicit", coefficient=Decimal("1"), enabled=True), as_of=c.now)
    await c.session.rollback()
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["member", "project", "adjustment"])
async def test_authority_is_refreshed_after_waits(adjustment_case, stage):
    c = adjustment_case
    original = (await create(c)).adjustment
    before = snapshot(c)
    if stage == "member":
        real = c.serialization.lock_member.side_effect
        async def member(**values):
            await real(**values)
            c.membership = False
        c.serialization.lock_member.side_effect = member
    else:
        name = "get_project" if stage == "project" else "get_adjustment"
        real = getattr(c.adjustment_repo, name)
        async def changed(**values):
            row = await real(**values)
            c.grant.expires_at = c.now
            return row
        setattr(c.adjustment_repo, name, changed)
    with pytest.raises(ConstructionPermissionDeniedError):
        await patch(c, original, enabled=False)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("collection", [False, True])
async def test_archive_between_parent_and_adjustment_queries_hides_result(adjustment_case, collection):
    c = adjustment_case
    original = (await create(c)).adjustment
    before = snapshot(c)
    name = "list_adjustments" if collection else "get_adjustment"
    real = getattr(c.adjustment_repo, name)
    async def archived(**values):
        row = await real(**values)
        c.sync.execute(update(c.projects).where(c.projects.c.id == c.project).values(deleted_at=c.now))
        return row
    setattr(c.adjustment_repo, name, archived)
    with pytest.raises(NotFoundError):
        if collection:
            await page(c)
        else:
            await get(c, original)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("failure", ["audit", "commit", "cancel", "authority"])
async def test_real_adjustment_and_audit_changes_rollback_together(adjustment_case, operation, failure):
    c = adjustment_case
    original = (await create(c)).adjustment if operation == "update" else None
    before = snapshot(c)
    if failure == "audit":
        c.fail_audit = True
    elif failure == "commit":
        c.session.fail_commit = True
    elif failure == "authority":
        c.after_audit = lambda: setattr(c.grant, "expires_at", c.now)
    else:
        def cancel():
            raise asyncio.CancelledError()
        c.after_audit = cancel
    expected = (asyncio.CancelledError if failure == "cancel" else
                ConstructionPermissionDeniedError if failure == "authority" else OperationError)
    with pytest.raises(expected):
        if original is None:
            await create(c)
        else:
            await patch(c, original, enabled=False)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_preparation_mode_does_not_commit_and_failure_still_rolls_back(adjustment_case):
    c = adjustment_case
    before = snapshot(c)
    await create(c, manage_transaction=False)
    assert c.session.commits == 0 and len(snapshot(c)[0]) == len(snapshot(c)[1]) == 1
    await c.session.rollback()
    assert snapshot(c) == before
    c.fail_audit = True
    with pytest.raises(OperationError):
        await create(c, manage_transaction=False)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_bounded_pagination_includes_disabled_rows_and_filters_before_limit(adjustment_case):
    c = adjustment_case
    c.sync.execute(insert(c.adjustments), [
        dict(id=UUID(int=i), tenant_id=tenant, project_id=project, name="Configured",
             coefficient=Decimal("1"), enabled=i % 2 == 0, row_version=1,
             created_by=c.user, created_at=c.now, updated_at=c.now)
        for i, tenant, project in (
            [(i, c.tenant, c.project) for i in range(1, 106)]
            + [(120, c.tenant, c.other_project), (121, c.foreign, c.foreign_project)]
        )
    ])
    c.sync.commit()
    before = snapshot(c)
    first = await page(c, limit=100)
    assert [state.id.int for state in first.items] == list(range(1, 101))
    assert first.next_cursor.after_id == UUID(int=100) and any(not state.enabled for state in first.items)
    last = await page(c, limit=100, after=first.next_cursor)
    assert [state.id.int for state in last.items] == list(range(101, 106)) and last.next_cursor is None
    assert len((await page(c)).items) == 20 and snapshot(c) == before
    c.serialization.lock_member.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, True, 101, "20"])
async def test_invalid_limit_is_rejected(adjustment_case, limit):
    with pytest.raises(ValidationError):
        await page(adjustment_case, limit=limit)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["tenant_id", "user_id", "project_id"])
async def test_cursor_context_is_bound_to_actor_tenant_and_project(adjustment_case, field):
    c = adjustment_case
    values = dict(tenant_id=c.tenant, user_id=c.user, project_id=c.project, after_id=uuid4())
    values[field] = uuid4()
    with pytest.raises(ValidationError):
        await page(c, after=Cursor(**values))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["foreign", "duplicate", "overflow"])
async def test_malformed_or_wrong_context_page_fails_without_partial_results(adjustment_case, failure):
    c = adjustment_case
    original = (await create(c)).adjustment
    one = asdict(original) | {"id": UUID(int=1)}
    two = asdict(original) | {"id": UUID(int=2)}
    rows = {"foreign": (one, two | {"project_id": c.other_project}),
            "duplicate": (one, one), "overflow": (one, two, two)}[failure]
    c.adjustment_repo.list_adjustments = AsyncMock(return_value=rows)
    with pytest.raises(OperationError):
        await page(c, limit=1)


@pytest.mark.asyncio
async def test_version_exhaustion_and_conditional_update_race_do_not_write(adjustment_case):
    c = adjustment_case
    original = (await create(c)).adjustment
    before = snapshot(c)
    real = c.adjustment_repo.update_adjustment
    c.adjustment_repo.update_adjustment = AsyncMock(return_value=None)
    with pytest.raises(ConstructionRowVersionConflictError):
        await patch(c, original, enabled=False)
    assert snapshot(c) == before
    c.adjustment_repo.update_adjustment = real
    c.sync.execute(update(c.adjustments).values(row_version=MAX_VERSION))
    c.sync.commit()
    noop = await patch(c, original, version=MAX_VERSION)
    assert not noop.changed and noop.adjustment.row_version == MAX_VERSION
    before = snapshot(c)
    with pytest.raises(ValidationError):
        await patch(c, noop.adjustment, enabled=False)
    assert snapshot(c) == before
