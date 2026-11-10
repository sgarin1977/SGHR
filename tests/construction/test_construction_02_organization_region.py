"""Local SQLite region workflows; PostgreSQL JSONB behavior is checked as SQL only."""
import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import (
    Boolean, Column, JSON, MetaData, String, Table, Uuid,
    bindparam, create_engine, event, func, select, update,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from database.repositories.platform_organization_region import (
    PlatformOrganizationRegionRepository, REGION_TENANTS)
from database.repositories.platform_organization_currency import PlatformOrganizationCurrencyRepository
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.construction.construction_grants import ConstructionAccessContextResolver
from services.construction.construction_organization_language import ConstructionOrganizationLanguageAuthorizer
from services.construction.construction_organization_region import (
    ConstructionOrganizationRegionService as Service,
    OrganizationRegionValidationError as ValidationError,
    OrganizationRegionDenied as Denied,
    OrganizationRegionConfigurationError as ConfigurationError,
    OrganizationRegionConflict as Conflict,
    OrganizationRegionOperationError as OperationError,
    MAX_VERSION,
)
from test_construction_02_tax_profiles import LocalSession, LocalUTC


@pytest.fixture
def region():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def setup(dbapi, record):
        def kind(value):
            if value is None:
                return None
            parsed = json.loads(value)
            return "object" if isinstance(parsed, dict) else "array" if isinstance(parsed, list) else "other"
        dbapi.create_function("jsonb_typeof", 1, kind)

    metadata = MetaData()
    tenants = REGION_TENANTS.to_metadata(metadata)
    tenants.c.metadata.type = JSON(none_as_null=True)
    tenants.c.updated_at.type = LocalUTC()
    tenants.append_column(Column("default_language", String))
    tenants.append_column(Column("default_currency", String))
    countries = Table("countries", metadata, Column("code", String, primary_key=True),
                      Column("is_active", Boolean))
    audits = Table("local_region_audit", metadata,
                   Column("id", Uuid, primary_key=True), Column("tenant_id", Uuid),
                   Column("user_id", Uuid), Column("entity_id", Uuid),
                   Column("event_type", String), Column("trace_id", String), Column("payload", JSON))
    metadata.create_all(engine)
    sync = Session(engine)
    c = NS(sync=sync, tenants=tenants, countries=countries, audits=audits,
           tenant=uuid4(), foreign=uuid4(), user=uuid4(), membership=True,
           now=datetime(2026, 11, 5, 12, tzinfo=timezone.utc), order=[], fail=None)
    sync.execute(tenants.insert(), [
        dict(id=c.tenant, status="active", metadata={"keep": {"nested": [1, 2]}, "billing": "preserve"},
             updated_at=c.now, row_version=1, default_language="uk", default_currency="UAH"),
        dict(id=c.foreign, status="active", metadata={"foreign": True},
             updated_at=c.now, row_version=8, default_language="pt", default_currency="EUR"),
    ])
    sync.execute(countries.insert(), [
        {"code": "UA", "is_active": True}, {"code": "PT", "is_active": True},
        {"code": "ZZ", "is_active": False},
    ])
    sync.commit()
    c.session = LocalSession(sync)
    c.session.get_transaction = sync.get_transaction
    c.session.get_bind = sync.get_bind
    c.actor = ApiActorContext(c.user, c.tenant, None, ("client",), "uk", None, "active")
    c.grant = NS(role="OWNER", scope_type="tenant", scope_id=c.tenant, status="active", expires_at=None)

    async def identity(**values):
        assert values == {"user_id": c.user, "tenant_id": c.tenant}
        if not c.membership:
            raise ApiIdentityAccessError("private membership failure")
        return replace(c.actor)

    identity_service = NS(session=c.session, require_actor=AsyncMock(side_effect=identity))
    access = NS(session=c.session, list_active_grants=AsyncMock(return_value=[c.grant]))
    resolver = ConstructionAccessContextResolver(repository=access, now_provider=lambda: c.now)
    c.authorizer = ConstructionOrganizationLanguageAuthorizer(
        session=c.session, identity=identity_service, access_resolver=resolver)

    async def barrier(**values):
        assert values == {"tenant_id": c.tenant, "user_id": c.user}
        c.order.append("member")

    c.serialization = NS(session=c.session, lock_member=AsyncMock(side_effect=barrier))

    async def audit(**values):
        sync.execute(audits.insert().values(
            id=uuid4(), **{key: values[key] for key in (
                "tenant_id", "user_id", "entity_id", "event_type", "trace_id", "payload")}))
        if c.fail == "audit":
            raise RuntimeError("private audit failure")
        if c.fail == "cancel":
            raise asyncio.CancelledError()
        if c.fail == "authority":
            c.membership = False

    c.events = NS(session=c.session, create_event=AsyncMock(side_effect=audit))

    class LocalRepository(PlatformOrganizationRegionRepository):
        def _metadata_value(self, values):
            return func.json_set(
                self.table.c.metadata, "$.regional_settings",
                func.json(bindparam("local_region", value=json.dumps(values))))

        async def get_active(self, **values):
            if values.get("for_update"):
                c.order.append("tenant")
            return await super().get_active(**values)

        async def country_is_active(self, **values):
            if values.get("for_share"):
                c.order.append("country")
            return await super().country_is_active(**values)

    c.repo = LocalRepository(c.session)
    c.repo.table = tenants
    c.service = Service(
        session=c.session, repository=c.repo, authorizer=c.authorizer,
        events=c.events, serialization=c.serialization, now_provider=lambda: c.now)
    sync.begin()
    yield c
    sync.close()
    engine.dispose()


def begin(c):
    if not c.sync.in_transaction():
        c.sync.begin()


def snapshot(c):
    return tuple(tuple(dict(row) for row in c.sync.execute(
        select(table).order_by(table.c[0])).mappings())
        for table in (c.tenants, c.countries, c.audits))


async def save(c, **values):
    begin(c)
    fields = dict(actor=c.actor, country_code="UA", timezone_code="Europe/Kyiv",
                  expected_row_version=1, trace_id="region-test")
    fields.update(values)
    return await c.service.update_for_actor(**fields)


@pytest.mark.asyncio
async def test_absent_region_is_not_inferred_and_read_never_writes(region):
    c = region
    before = snapshot(c)
    state = await c.service.read_for_actor(actor=c.actor)
    assert not state.configured and state.country_code is None and state.timezone is None
    assert state.row_version == 1 and snapshot(c) == before and c.session.commits == 0
    c.serialization.lock_member.assert_not_awaited()
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_canonical_settings_policy_and_versioned_audit_preserve_other_data(region, role):
    c = region
    c.grant.role = role
    before = snapshot(c)
    result = await save(c)
    assert result.changed and result.region.configured and result.region.row_version == 2
    assert result.region.country_code == "UA" and result.region.timezone == "Europe/Kyiv"
    assert c.order == ["member", "country", "tenant"] and c.session.commits == 1
    after = snapshot(c)
    own = next(row for row in after[0] if row["id"] == c.tenant)
    foreign = next(row for row in after[0] if row["id"] == c.foreign)
    assert own["metadata"] == {
        "keep": {"nested": [1, 2]}, "billing": "preserve",
        "regional_settings": {"country_code": "UA", "timezone": "Europe/Kyiv"}}
    assert own["default_language"] == "uk" and own["default_currency"] == "UAH"
    assert foreign == next(row for row in before[0] if row["id"] == c.foreign)
    assert after[1] == before[1] and len(after[2]) == 1
    event = after[2][0]
    assert event["tenant_id"] == c.tenant and event["user_id"] == c.user
    assert event["entity_id"] == c.tenant and event["trace_id"] == "region-test"
    assert event["payload"]["previous_row_version"] == 1 and event["payload"]["row_version"] == 2
    assert "billing" not in repr(event["payload"])
    begin(c)
    assert (await PlatformOrganizationCurrencyRepository(c.session).get_active(
        tenant_id=c.tenant))["row_version"] == 2
    assert await c.service.read_for_actor(actor=c.actor) == result.region


@pytest.mark.asyncio
async def test_current_noop_requires_current_shared_version(region):
    c = region
    await save(c)
    before = snapshot(c)
    noop = await save(c, expected_row_version=2)
    assert not noop.changed and snapshot(c) == before
    with pytest.raises(Conflict):
        await save(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit", "cancel", "authority"])
async def test_real_local_metadata_update_and_audit_roll_back_together(region, failure):
    c = region
    before = snapshot(c)
    if failure == "commit":
        c.session.fail_commit = True
    else:
        c.fail = failure
    expected = asyncio.CancelledError if failure == "cancel" else Denied if failure == "authority" else OperationError
    with pytest.raises(expected):
        await save(c)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("role,scope", [
    ("OBSERVER", "project"), ("ESTIMATOR", "project"), ("PROJECT_MANAGER", "project")])
async def test_existing_permission_and_scope_policy_are_not_expanded(region, role, scope):
    c = region
    c.grant.role, c.grant.scope_type, c.grant.scope_id = role, scope, uuid4()
    before = snapshot(c)
    with pytest.raises(Denied):
        await save(c)
    assert snapshot(c) == before
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OBSERVER", "ESTIMATOR"])
async def test_invalid_tenant_role_binding_fails_closed_without_write(region, role):
    c = region
    c.grant.role, c.grant.scope_type = role, "tenant"
    c.repo.get_active = AsyncMock(wraps=c.repo.get_active)
    before = snapshot(c)
    with pytest.raises(OperationError):
        await save(c)
    assert snapshot(c) == before
    c.repo.get_active.assert_not_awaited()
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_membership_is_rechecked_after_waiting_for_barrier(region):
    c = region
    async def revoked(**values):
        c.membership = False
    c.serialization.lock_member.side_effect = revoked
    c.repo.get_active = AsyncMock(wraps=c.repo.get_active)
    with pytest.raises(Denied):
        await save(c)
    c.repo.get_active.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("values", [
    {"country_code": "ua"}, {"country_code": " UA"}, {"country_code": "ZZ"},
    {"country_code": "XX"}, {"timezone_code": "Not/A_Zone"},
    {"timezone_code": "../UTC"}, {"timezone_code": "localtime"},
    {"timezone_code": " Europe/Kyiv"}, {"timezone_code": None},
    {"expected_row_version": True}, {"expected_row_version": 0},
    {"expected_row_version": "1"}, {"expected_row_version": 2**63},
])
async def test_explicit_catalog_country_timezone_and_version_are_required(region, values):
    c = region
    before = snapshot(c)
    with pytest.raises(ValidationError):
        await save(c, **values)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [
    [], {"regional_settings": None}, {"regional_settings": {}},
    {"regional_settings": {"country_code": "UA", "timezone": "Invalid/Zone"}},
    {"regional_settings": {"country_code": "UA", "timezone": "Europe/Kyiv", "unknown": True}},
])
async def test_incompatible_existing_metadata_is_not_overwritten(region, metadata):
    c = region
    c.sync.execute(update(c.tenants).where(c.tenants.c.id == c.tenant).values(metadata=metadata))
    c.sync.commit()
    before = snapshot(c)
    begin(c)
    with pytest.raises(ConfigurationError):
        await c.service.read_for_actor(actor=c.actor)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_prepared_update_leaves_commit_to_caller(region):
    c = region
    before = snapshot(c)
    result = await save(c, manage_transaction=False)
    assert result.changed and c.session.commits == 0 and c.sync.in_transaction()
    await c.session.rollback()
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_conditional_miss_is_conflict_without_audit(region):
    c = region
    c.repo.update_region = AsyncMock(return_value=None)
    before = snapshot(c)
    with pytest.raises(Conflict):
        await save(c)
    assert snapshot(c) == before
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_max_version_allows_current_noop_but_no_mutation(region):
    c = region
    await save(c)
    c.sync.execute(update(c.tenants).where(c.tenants.c.id == c.tenant).values(row_version=MAX_VERSION))
    c.sync.commit()
    assert not (await save(c, expected_row_version=MAX_VERSION)).changed
    before = snapshot(c)
    with pytest.raises(OperationError):
        await save(c, expected_row_version=MAX_VERSION, country_code="PT", timezone_code="Europe/Lisbon")
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_postgresql_sql_updates_only_region_subtree_and_uses_country_share_lock():
    statements = []
    class Capture:
        async def execute(self, statement):
            statements.append(statement)
            return NS(mappings=lambda: NS(one_or_none=lambda: None),
                      scalars=lambda: NS(all=lambda: ["UA"]))
    repo = PlatformOrganizationRegionRepository(Capture())
    values = {"country_code": "UA", "timezone": "Europe/Kyiv"}
    await repo.update_region(tenant_id=uuid4(), expected_row_version=3,
                             region=values, as_of=datetime.now(timezone.utc))
    compiled = statements[-1].compile(dialect=postgresql.dialect())
    assert "jsonb_set" in str(compiled) and "row_version" in str(compiled)
    assert compiled.params["region_value"] == values
    assert "metadata" not in compiled.params
    assert await repo.country_is_active(country_code="UA", for_share=True)
    assert "FOR SHARE" in str(statements[-1].compile(dialect=postgresql.dialect()))
