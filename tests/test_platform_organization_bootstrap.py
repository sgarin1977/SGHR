"""Bootstrap semantics and real local rollback; not native PG acceptance."""
import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from database.models import (
    ApiIdempotencyRecord, ConstructionAccessGrant, Tenant, UserRoleMapping,
)
from database.repositories.api_idempotency import ApiIdempotencyRepository
from database.repositories.construction import ConstructionAccessRepository
from database.repositories.platform_organization_bootstrap import (
    OPERATION, PlatformOrganizationBootstrapRepository,
)
from services.api_idempotency import (
    ApiIdempotencyKeyReusedError, ApiIdempotencyResponseCodec,
    ApiIdempotencyService,
)
from services.api_platform_principal import ApiPlatformPrincipal
from services.platform_organization_bootstrap import (
    OrganizationBootstrapDenied, OrganizationBootstrapError,
    OrganizationBootstrapValidationError, PlatformOrganizationBootstrap,
)


class LocalSession:
    def __init__(self, sync):
        self.sync, self.fail_commit = sync, False
        self.commits, self.rollbacks = 0, 0

    def in_transaction(self):
        return self.sync.in_transaction()

    def get_transaction(self):
        return self.sync.get_transaction()

    def get_nested_transaction(self):
        return self.sync.get_nested_transaction()

    def get_bind(self):
        return self.sync.get_bind()

    def add(self, row):
        self.sync.add(row)

    async def execute(self, statement, parameters=None):
        return self.sync.execute(statement, parameters or {})

    async def flush(self):
        self.sync.flush()

    async def commit(self):
        self.sync.flush()
        if self.fail_commit:
            raise RuntimeError("local commit failure")
        self.sync.commit()
        self.commits += 1

    async def rollback(self):
        self.sync.rollback()
        self.rollbacks += 1


class LocalRepository(PlatformOrganizationBootstrapRepository):
    async def require_runtime(self):
        # Explicit test adapter. Runtime repository still rejects SQLite.
        assert self.session.in_transaction()
        assert self.session.get_nested_transaction() is None


@pytest.fixture
def case():
    engine = create_engine("sqlite://")
    sync = Session(engine)
    ddl = [
        """CREATE TABLE users (
            id CHAR(32) PRIMARY KEY, status TEXT NOT NULL, tenant_id CHAR(32)
        )""",
        """CREATE TABLE languages (
            code VARCHAR(10) PRIMARY KEY, is_active BOOLEAN NOT NULL
        )""",
        """CREATE TABLE tenants (
            id CHAR(32) PRIMARY KEY, name TEXT NOT NULL, slug TEXT UNIQUE NOT NULL,
            default_language VARCHAR(10) NOT NULL, default_currency VARCHAR(3) NOT NULL,
            status TEXT NOT NULL, metadata JSON NOT NULL,
            created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
        )""",
        """CREATE TABLE user_roles (
            id CHAR(32) PRIMARY KEY, user_id CHAR(32) NOT NULL,
            tenant_id CHAR(32), role TEXT NOT NULL, status TEXT NOT NULL,
            granted_by CHAR(32), granted_at DATETIME
        )""",
        """CREATE TABLE construction_access_grants (
            id CHAR(32) PRIMARY KEY, tenant_id CHAR(32) NOT NULL,
            user_id CHAR(32) NOT NULL, role TEXT NOT NULL,
            scope_type TEXT NOT NULL, scope_id CHAR(32) NOT NULL,
            status TEXT NOT NULL, expires_at DATETIME,
            granted_by_user_id CHAR(32) NOT NULL, granted_at DATETIME NOT NULL,
            revoked_by_user_id CHAR(32), revoked_at DATETIME
        )""",
        """CREATE TABLE api_idempotency_records (
            id CHAR(32) PRIMARY KEY, tenant_id CHAR(32) NOT NULL,
            principal_type TEXT NOT NULL, principal_id CHAR(32) NOT NULL,
            operation TEXT NOT NULL, key_hash TEXT NOT NULL,
            request_hash TEXT NOT NULL, status TEXT NOT NULL,
            response_status SMALLINT, response_ciphertext BLOB,
            created_at DATETIME NOT NULL, completed_at DATETIME,
            expires_at DATETIME NOT NULL,
            UNIQUE(tenant_id, principal_type, principal_id, operation, key_hash)
        )""",
        "CREATE TABLE probe_audit (payload TEXT NOT NULL)",
        "CREATE TABLE probe_product (tenant_id CHAR(32) PRIMARY KEY)",
    ]
    for statement in ddl:
        sync.execute(text(statement))
    user, home = uuid4(), uuid4()
    sync.execute(text(
        "INSERT INTO users (id, status, tenant_id) VALUES (:id, 'active', :home)"
    ), {"id": user.hex, "home": home.hex})
    sync.execute(text("INSERT INTO languages VALUES ('uk', 1)"))
    sync.commit()

    session = LocalSession(sync)
    c = NS(
        sync=sync, session=session, user=user, home=home,
        principal=ApiPlatformPrincipal(user),
        now=datetime(2026, 10, 23, 10, tzinfo=timezone.utc),
    )
    c.repo = LocalRepository(session)
    c.grants = ConstructionAccessRepository(session)
    real_records = ApiIdempotencyRepository(session)
    # Shared service + real ORM storage. Only PG INSERT ON CONFLICT is replaced
    # by its existing fallback because this is a local SQLite transaction test.
    c.records = NS(
        session=session,
        get_active_record_for_update=real_records.get_active_record_for_update,
        create_record=real_records.create_record,
        complete_record=real_records.complete_record,
    )
    c.idempotency = ApiIdempotencyService(
        repository=c.records,
        response_codec=ApiIdempotencyResponseCodec(
            encryption_key=Fernet.generate_key().decode("ascii"),
        ),
    )

    async def provision(**kwargs):
        await session.execute(text(
            "INSERT INTO probe_product VALUES (:tenant)"
        ), {"tenant": kwargs["tenant_id"].hex})

    async def enabled(*, tenant_id, **kwargs):
        row = await session.execute(text(
            "SELECT tenant_id FROM probe_product WHERE tenant_id=:tenant"
        ), {"tenant": tenant_id.hex})
        return row.scalar_one_or_none() is not None

    async def audit(**kwargs):
        await session.execute(text(
            "INSERT INTO probe_audit VALUES (:payload)"
        ), {"payload": json.dumps(kwargs, default=str)})
        return NS(id=uuid4())

    c.policy = NS(
        session=session, require_creation=AsyncMock(return_value=True),
        provision_construction=AsyncMock(side_effect=provision),
    )
    c.modules = NS(
        session=session,
        is_suite_enabled=AsyncMock(side_effect=enabled),
        is_module_enabled=AsyncMock(side_effect=enabled),
    )
    c.events = NS(session=session, create_event=AsyncMock(side_effect=audit))
    c.workflow = PlatformOrganizationBootstrap(
        session=session, repository=c.repo, grants=c.grants,
        events=c.events, modules=c.modules, idempotency=c.idempotency,
        product_policy=c.policy, now_provider=lambda: c.now,
    )
    yield c
    sync.close()
    engine.dispose()


async def run(c, **changes):
    if not c.sync.in_transaction():
        c.sync.begin()
    values = dict(
        principal=c.principal, name="  Organization  ", language="uk",
        currency="EUR", idempotency_key="bootstrap-key", trace_id="bootstrap-test",
    )
    values.update(changes)
    return await c.workflow.create(**values)


def counts(c):
    return tuple(c.sync.execute(text(
        "SELECT count(*) FROM " + table
    )).scalar_one() for table in (
        "tenants", "user_roles", "construction_access_grants",
        "api_idempotency_records", "probe_audit", "probe_product",
    ))


@pytest.mark.asyncio
async def test_atomic_bootstrap_uses_only_local_membership_and_first_owner(case):
    c = case
    result = await run(c)
    assert counts(c) == (1, 1, 1, 1, 1, 1)
    tenant = c.sync.get(Tenant, UUID(result["id"]))
    membership = c.sync.get(UserRoleMapping, UUID(result["membership_id"]))
    grant = c.sync.get(ConstructionAccessGrant, UUID(result["owner_grant_id"]))
    assert tenant.name == "Organization"
    assert membership.user_id == c.user and membership.tenant_id == tenant.id
    assert membership.role == "client" and membership.status == "active"
    assert grant.user_id == c.user and grant.tenant_id == tenant.id
    assert grant.role == "OWNER" and grant.scope_type == "tenant"
    assert grant.scope_id == tenant.id and grant.expires_at is None
    assert c.sync.execute(text("SELECT tenant_id FROM users")).scalar_one() == c.home.hex
    record = c.sync.execute(select(ApiIdempotencyRecord)).scalar_one()
    assert record.operation == OPERATION and record.principal_id == c.user
    assert c.idempotency.response_codec.decrypt(record.response_ciphertext) == result
    audit = c.sync.execute(text("SELECT payload FROM probe_audit")).scalar_one()
    assert "bootstrap-key" not in audit
    assert "super_admin" not in audit
    assert c.session.commits == 1 and c.session.rollbacks == 0


@pytest.mark.asyncio
async def test_retry_returns_original_receipt_without_restoring_later_revoked_access(case):
    c = case
    first = await run(c)
    c.sync.execute(text("UPDATE user_roles SET status='revoked'"))
    c.sync.execute(text("UPDATE construction_access_grants SET status='revoked'"))
    c.sync.execute(text("DELETE FROM probe_product"))
    c.sync.commit()
    assert await run(c) == first
    assert counts(c) == (1, 1, 1, 1, 1, 0)
    assert c.sync.execute(text("SELECT status FROM user_roles")).scalar_one() == "revoked"
    assert c.sync.execute(text(
        "SELECT status FROM construction_access_grants"
    )).scalar_one() == "revoked"
    c.policy.provision_construction.assert_awaited_once()
    c.events.create_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_same_key_changed_request_uses_shared_conflict_without_second_tenant(case):
    c = case
    await run(c)
    before = counts(c)
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await run(c, name="Different")
    assert counts(c) == before
    assert c.session.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", [False, None, "allowed", 1])
async def test_product_policy_must_explicitly_allow_creation(case, decision):
    c = case
    c.policy.require_creation.return_value = decision
    with pytest.raises(OrganizationBootstrapDenied):
        await run(c)
    assert counts(c) == (0, 0, 0, 0, 0, 0)
    c.policy.provision_construction.assert_not_awaited()


@pytest.mark.asyncio
async def test_authority_is_rechecked_before_membership_and_owner_writes(case):
    c = case
    c.policy.require_creation.side_effect = [True, False]
    with pytest.raises(OrganizationBootstrapDenied):
        await run(c)
    assert counts(c) == (0, 0, 0, 0, 0, 0)
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_inactive_creator_cannot_bootstrap(case):
    c = case
    c.sync.execute(text("UPDATE users SET status='blocked'"))
    c.sync.commit()
    with pytest.raises(OrganizationBootstrapDenied):
        await run(c)
    c.policy.require_creation.assert_not_awaited()
    assert counts(c) == (0, 0, 0, 0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "membership", "provision", "module", "owner", "audit", "complete", "commit",
])
async def test_real_flushed_bootstrap_rows_roll_back_as_one_transaction(case, failure):
    c = case
    if failure == "module":
        c.modules.is_module_enabled.side_effect = None
        c.modules.is_module_enabled.return_value = False
    elif failure == "commit":
        c.session.fail_commit = True
    else:
        if failure == "membership":
            target, name = c.repo, "ensure_creator_membership"
        elif failure == "provision":
            target, name = c.policy, "provision_construction"
        elif failure == "owner":
            target, name = c.grants, "create_grant"
        elif failure == "audit":
            target, name = c.events, "create_event"
        else:
            target, name = c.idempotency, "complete"
        original = getattr(target, name)
        async def fail_after_real_write(**kwargs):
            await original(**kwargs)
            raise RuntimeError("local injected failure")
        setattr(target, name, fail_after_real_write)
    with pytest.raises(OrganizationBootstrapError):
        await run(c)
    assert counts(c) == (0, 0, 0, 0, 0, 0)
    assert c.session.commits == 0 and c.session.rollbacks == 1


@pytest.mark.asyncio
async def test_cancellation_after_real_owner_flush_rolls_back(case):
    c = case
    original = c.grants.create_grant
    async def cancel(**kwargs):
        await original(**kwargs)
        raise asyncio.CancelledError()
    c.grants.create_grant = cancel
    with pytest.raises(asyncio.CancelledError):
        await run(c)
    assert counts(c) == (0, 0, 0, 0, 0, 0)
    assert c.session.rollbacks == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"idempotency_key": ""},
    {"idempotency_key": None},
    {"name": " "},
    {"currency": "eur"},
    {"language": "not enabled"},
])
async def test_invalid_input_never_creates_partial_organization(case, changes):
    c = case
    with pytest.raises(OrganizationBootstrapValidationError):
        await run(c, **changes)
    assert counts(c) == (0, 0, 0, 0, 0, 0)


@pytest.mark.asyncio
async def test_unconfigured_locale_rolls_back_before_tenant_creation(case):
    c = case
    with pytest.raises(OrganizationBootstrapValidationError):
        await run(c, language="zz")
    assert counts(c) == (0, 0, 0, 0, 0, 0)


@pytest.mark.asyncio
async def test_runtime_repository_does_not_fall_back_to_unlocked_sqlite(case):
    c = case
    c.workflow.repository = PlatformOrganizationBootstrapRepository(c.session)
    with pytest.raises(OrganizationBootstrapError):
        await run(c)
    c.policy.require_creation.assert_not_awaited()
    assert counts(c) == (0, 0, 0, 0, 0, 0)


@pytest.mark.parametrize("mode", ["missing_policy", "foreign_session"])
def test_explicit_shared_session_product_policy_is_mandatory(case, mode):
    c = case
    policy = None if mode == "missing_policy" else NS(
        session=object(), require_creation=AsyncMock(),
        provision_construction=AsyncMock(),
    )
    with pytest.raises(ValueError):
        PlatformOrganizationBootstrap(
            session=c.session, repository=c.repo, grants=c.grants,
            events=c.events, modules=c.modules, idempotency=c.idempotency,
            product_policy=policy,
        )
