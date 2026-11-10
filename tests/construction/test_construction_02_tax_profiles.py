"""Local-only SQLite transactions; no runtime database, provider or credentials."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import Column, DateTime, JSON, MetaData, String, Table, Uuid, create_engine, event, insert, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.types import TypeDecorator

from database.models import ConstructionTaxProfile
from database.repositories.construction.construction_tax_profiles import ConstructionTaxProfileRepository
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from services.construction.construction_tax_profiles import (
    ConstructionTaxProfileService, ConstructionTaxProfileValidationError,
    ConstructionTaxProfileNotFoundError, ConstructionTaxProfileOperationError, MAX_VERSION,
)


class LocalUTC(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


class LocalSession:
    """Explicit async adapter over a local synchronous SQLite session."""
    def __init__(self, sync):
        self.sync, self.fail_commit = sync, False
        self.commits = self.rollbacks = 0

    @property
    def no_autoflush(self):
        return self.sync.no_autoflush

    @property
    def new(self):
        return self.sync.new

    @property
    def dirty(self):
        return self.sync.dirty

    @property
    def deleted(self):
        return self.sync.deleted

    def in_transaction(self):
        return self.sync.in_transaction()

    def get_nested_transaction(self):
        return self.sync.get_nested_transaction()

    def expire_all(self):
        self.sync.expire_all()

    async def execute(self, statement):
        return self.sync.execute(statement)

    async def commit(self):
        if self.fail_commit:
            raise RuntimeError("private commit failure")
        self.sync.commit()
        self.commits += 1

    async def rollback(self):
        self.sync.rollback()
        self.rollbacks += 1


@pytest.fixture
def case():
    engine = create_engine("sqlite://")
    @event.listens_for(engine, "connect")
    def setup(dbapi, record):
        dbapi.execute("PRAGMA foreign_keys=ON")
        dbapi.create_function("btrim", 1, lambda value: value.strip(" "))
        dbapi.create_function("char_length", 1, len)
    metadata = MetaData()
    tenants = Table("tenants", metadata, Column("id", Uuid, primary_key=True))
    users = Table("users", metadata, Column("id", Uuid, primary_key=True))
    table = ConstructionTaxProfile.__table__.to_metadata(metadata)
    for name in ("created_at", "updated_at"):
        table.c[name].type = LocalUTC()
    audits = Table("local_tax_audit", metadata,
                   Column("id", Uuid, primary_key=True), Column("payload", JSON), Column("event_type", String),
                   Column("tenant_id", Uuid), Column("user_id", Uuid), Column("entity_id", Uuid), Column("trace_id", String))
    metadata.create_all(engine)
    sync = Session(engine)
    c = SimpleNamespace(sync=sync, tenant=uuid4(), foreign=uuid4(), user=uuid4(), table=table, audits=audits,
                        now=datetime(2026, 11, 3, 10, tzinfo=timezone.utc), membership=True, barrier_taken=False,
                        fail_audit=False, after_audit=None)
    sync.execute(insert(tenants), [{"id": c.tenant}, {"id": c.foreign}])
    sync.execute(insert(users), {"id": c.user})
    sync.commit()
    c.actor = SimpleNamespace(user_id=c.user, tenant_id=c.tenant, status="active")
    c.session = LocalSession(sync)
    c.grant = SimpleNamespace(role="OWNER", scope_type="tenant", scope_id=c.tenant, status="active", expires_at=None)
    async def membership(**kwargs):
        assert kwargs == {"tenant_id": c.tenant, "user_id": c.user}
        return c.membership
    async def grants(**kwargs):
        assert kwargs["tenant_id"] == c.tenant and kwargs["user_id"] == c.user
        return [c.grant]
    c.access = SimpleNamespace(session=c.session, has_active_platform_membership=AsyncMock(side_effect=membership),
                               list_active_grants=AsyncMock(side_effect=grants))
    async def lock(**kwargs):
        assert kwargs == {"tenant_id": c.tenant, "user_id": c.user}
        c.barrier_taken = True
    c.serialization = SimpleNamespace(session=c.session, lock_member=AsyncMock(side_effect=lock))
    async def audit(**kwargs):
        assert c.barrier_taken
        c.sync.execute(insert(audits), {key: kwargs[key] for key in
                       ("payload", "event_type", "tenant_id", "user_id", "entity_id", "trace_id")} | {"id": uuid4()})
        if c.after_audit:
            c.after_audit()
        if c.fail_audit:
            raise RuntimeError("private failure after a real audit insert")
    c.events = SimpleNamespace(session=c.session, create_event=AsyncMock(side_effect=audit))
    c.repository = ConstructionTaxProfileRepository(c.session)
    c.repository.table = table  # Only this local test metadata adapts SQLite datetime handling.
    c.service = ConstructionTaxProfileService(
        session=c.session, repository=c.repository, access_repository=c.access, event_repository=c.events,
        serialization=c.serialization, now_provider=lambda: c.now)
    sync.begin()
    yield c
    sync.close()
    engine.dispose()


async def create(c, **overrides):
    if not c.sync.in_transaction():
        c.sync.begin()
    fields = dict(name="Company tax", client_price_tax_mode="TAX_EXCLUSIVE", material_cost_basis="NET")
    fields.update(overrides)
    return await c.service.create_profile(actor=c.actor, trace_id="tax-test", **fields)


async def patch(c, profile, *, version=None, **fields):
    if not c.sync.in_transaction():
        c.sync.begin()
    return await c.service.update_profile(actor=c.actor, profile_id=profile.id,
        expected_row_version=profile.row_version if version is None else version, fields=fields, trace_id="tax-test")


def rows(c, table):
    return c.sync.execute(select(table)).mappings().all()


def test_model_exact_nine_columns_eight_constraints_and_no_mode_defaults():
    table = ConstructionTaxProfile.__table__
    assert list(table.c.keys()) == ["id", "tenant_id", "name", "client_price_tax_mode", "material_cost_basis",
                                    "row_version", "created_by", "created_at", "updated_at"]
    assert len(table.constraints) == 8
    assert {fk.name for fk in table.foreign_key_constraints} == {
        "fk_construction_tax_profiles_tenant", "fk_construction_tax_profiles_creator"}
    assert table.c.row_version.type.compile(dialect=postgresql.dialect()) == "BIGINT"
    assert str(table.c.row_version.server_default.arg) == "1"
    assert ConstructionTaxProfile.__mapper__.version_id_col is table.c.row_version
    for key in ("client_price_tax_mode", "material_cost_basis"):
        assert table.c[key].nullable is False
        assert table.c[key].default is None and table.c[key].server_default is None


@pytest.mark.asyncio
async def test_create_sparse_update_audit_and_read_use_canonical_state(case):
    c = case
    result = await create(c)
    profile = result.profile
    assert result.changed and profile.row_version == 1 and profile.created_by == c.user
    result = await patch(c, profile, material_cost_basis="GROSS")
    assert result.changed and result.profile.row_version == 2
    assert result.profile.client_price_tax_mode == "TAX_EXCLUSIVE"
    assert result.profile.created_at == profile.created_at and result.profile.created_by == profile.created_by
    events = rows(c, c.audits)
    assert [r["event_type"] for r in events] == ["construction_tax_profile_created", "construction_tax_profile_updated"]
    assert events[1]["payload"]["changes"] == [{"field": "material_cost_basis", "old_value": "NET", "new_value": "GROSS"}]
    assert all(r["tenant_id"] == c.tenant and r["user_id"] == c.user
               and r["entity_id"] == profile.id and r["trace_id"] == "tax-test" for r in events)
    commits = c.session.commits
    c.barrier_taken = False
    c.serialization.lock_member.reset_mock()
    actual = await c.service.get_profile(actor=c.actor, profile_id=profile.id)
    assert actual == result.profile and c.session.commits == commits
    c.serialization.lock_member.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [{}, {"name": "Company tax"}, {"name": " Company tax "}])
async def test_current_noop_does_not_write_increment_or_audit_but_stale_noop_conflicts(case, fields):
    c = case
    profile = (await create(c)).profile
    before = rows(c, c.table)[0]
    result = await patch(c, profile, **fields)
    assert result.changed is False and rows(c, c.table)[0] == before and len(rows(c, c.audits)) == 1
    await patch(c, profile, name="Changed")
    with pytest.raises(ConstructionRowVersionConflictError):
        await patch(c, profile)
    assert len(rows(c, c.audits)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    {"name": ""}, {"name": " "}, {"name": "x"*256}, {"client_price_tax_mode": None},
    {"client_price_tax_mode": "tax_inclusive"}, {"material_cost_basis": "AUTO"},
    {"created_by": None}, {"row_version": 9}, {"tenant_id": None}, {"default": True},
])
async def test_sparse_validation_performs_no_domain_write(case, bad):
    c = case
    profile = (await create(c)).profile
    with pytest.raises(ConstructionTaxProfileValidationError):
        await patch(c, profile, **bad)
    assert len(rows(c, c.table)) == len(rows(c, c.audits)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [0, -1, True, 1.0, "1", 2**63, None])
async def test_invalid_expected_versions_are_rejected_before_lock(case, version):
    c = case
    with pytest.raises(ConstructionTaxProfileValidationError):
        await c.service.update_profile(actor=c.actor, profile_id=uuid4(), expected_row_version=version, fields={})
    c.serialization.lock_member.assert_not_awaited()
    assert rows(c, c.table) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "revoked", "expired", "project", "read_only"])
async def test_fresh_policy_denies_mutations_without_tenant_edit_authority(case, failure):
    c = case
    if failure == "membership": c.membership = False
    elif failure == "revoked": c.grant.status = "revoked"
    elif failure == "expired": c.grant.expires_at = c.now
    elif failure == "project":
        c.grant.role, c.grant.scope_type, c.grant.scope_id = "PROJECT_MANAGER", "project", uuid4()
    else:
        c.grant.role, c.grant.scope_type, c.grant.scope_id = "OBSERVER", "project", uuid4()
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await create(c)
    assert rows(c, c.table) == rows(c, c.audits) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["barrier", "row", "audit"])
async def test_access_revocation_after_each_wait_rolls_back_real_writes(case, phase):
    c = case
    profile = (await create(c)).profile
    if phase == "barrier":
        async def lock(**kwargs): c.membership = False
        c.serialization.lock_member.side_effect = lock
    elif phase == "row":
        original = c.repository.get_profile
        async def get(**kwargs):
            assert c.barrier_taken
            value = await original(**kwargs)
            c.grant.status = "revoked"
            return value
        c.repository.get_profile = get
    else:
        c.after_audit = lambda: setattr(c.grant, "expires_at", c.now)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await patch(c, profile, name="Must roll back")
    assert rows(c, c.table)[0]["name"] == profile.name and len(rows(c, c.audits)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("failure", ["audit", "commit", "cancel"])
async def test_real_domain_and_audit_flushes_roll_back_together(case, operation, failure):
    c = case
    profile = (await create(c)).profile if operation == "update" else None
    before_rows, before_audits = list(rows(c, c.table)), list(rows(c, c.audits))
    if failure == "audit": c.fail_audit = True
    elif failure == "commit": c.session.fail_commit = True
    else:
        async def cancelled(**kwargs):
            c.sync.execute(insert(c.audits).values(id=uuid4(), payload={"probe": True}))
            raise asyncio.CancelledError()
        c.events.create_event.side_effect = cancelled
    expected = asyncio.CancelledError if failure == "cancel" else ConstructionTaxProfileOperationError
    with pytest.raises(expected):
        if operation == "create": await create(c)
        else: await patch(c, profile, name="Must not persist")
    assert rows(c, c.table) == before_rows and rows(c, c.audits) == before_audits
    assert c.session.rollbacks == 1


@pytest.mark.asyncio
async def test_missing_and_foreign_profiles_share_not_found_and_cas_is_tenant_version_bound(case):
    c = case
    profile = (await create(c)).profile
    c.sync.execute(c.table.update().where(c.table.c.id == profile.id).values(tenant_id=c.foreign))
    c.sync.commit()
    for profile_id in (profile.id, uuid4()):
        c.sync.begin()
        with pytest.raises(ConstructionTaxProfileNotFoundError):
            await c.service.get_profile(actor=c.actor, profile_id=profile_id)
    assert await c.repository.update_profile(tenant_id=c.tenant, profile_id=profile.id,
        expected_row_version=profile.row_version, fields={"name": "Leak"}, as_of=c.now) is None
    assert await c.repository.update_profile(tenant_id=c.foreign, profile_id=profile.id,
        expected_row_version=2, fields={"name": "Stale"}, as_of=c.now) is None
    assert rows(c, c.table)[0]["name"] == profile.name


@pytest.mark.asyncio
async def test_maximum_version_allows_noop_but_never_overflows(case):
    c = case
    profile = (await create(c)).profile
    c.sync.execute(c.table.update().where(c.table.c.id == profile.id).values(row_version=MAX_VERSION))
    c.sync.commit()
    result = await patch(c, profile, version=MAX_VERSION)
    assert result.changed is False and result.profile.row_version == MAX_VERSION
    with pytest.raises(ConstructionTaxProfileValidationError):
        await patch(c, profile, version=MAX_VERSION, name="Overflow")
    assert rows(c, c.table)[0]["row_version"] == MAX_VERSION and len(rows(c, c.audits)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OBSERVER", "PROJECT_MANAGER"])
async def test_tenant_read_permission_does_not_expand_project_scope(case, role):
    c = case
    profile = (await create(c)).profile
    c.sync.begin()
    assert (await c.service.get_profile(actor=c.actor, profile_id=profile.id)).id == profile.id
    c.sync.rollback()
    # Valid project grants carry tax.read but do not authorize organization profiles.
    c.grant.role, c.grant.scope_type, c.grant.scope_id = role, "project", uuid4()
    c.sync.begin()
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await c.service.get_profile(actor=c.actor, profile_id=profile.id)


@pytest.mark.asyncio
async def test_conditional_update_losing_version_returns_conflict_without_audit(case):
    c = case
    profile = (await create(c)).profile
    c.repository.update_profile = AsyncMock(return_value=None)
    with pytest.raises(ConstructionRowVersionConflictError):
        await patch(c, profile, name="Must not apply")
    assert rows(c, c.table)[0]["row_version"] == 1 and len(rows(c, c.audits)) == 1
