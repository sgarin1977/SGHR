"""Local SQL rollback and PostgreSQL lock syntax; no native acceptance."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, JSON, MetaData, String, Table,
    Uuid, create_engine, select,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from database.models import TenantLanguage
from database.repositories.platform_organization_language import (
    PlatformOrganizationLanguageRepository,
)
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS,
    ConstructionAccessContext,
    ConstructionGrantContext,
)
from services.construction.construction_organization_language import (
    ConstructionOrganizationLanguageAuthorizer,
    ConstructionOrganizationLanguageService,
    OrganizationLanguageConflict,
    OrganizationLanguageDenied,
    OrganizationLanguageOperationError,
    OrganizationLanguageValidationError,
)

UTC = timezone.utc


@pytest.fixture
def organization_language():
    c = NS(
        tenant=uuid4(), foreign=uuid4(), user=uuid4(), role="OWNER",
        scope="tenant", deny_at=None, calls=0, missing_permission=False,
        fail=None, commits=0, rollbacks=0, statements=[],
    )
    c.base = datetime(2026, 10, 1, tzinfo=UTC)
    metadata = MetaData()
    c.tenants = Table(
        "tenants", metadata,
        Column("id", Uuid, primary_key=True),
        Column("status", String),
        Column("default_language", String),
        Column("default_currency", String),
        Column("updated_at", DateTime(timezone=True)),
        Column("row_version", BigInteger, nullable=False, default=1),
    )
    c.languages = Table(
        "languages", metadata,
        Column("code", String, primary_key=True),
        Column("is_active", Boolean),
    )
    c.users = Table(
        "local_users", metadata,
        Column("id", Uuid, primary_key=True),
        Column("language_code", String),
    )
    c.audit = Table(
        "local_language_audit", metadata,
        Column("id", Uuid, primary_key=True),
        Column("tenant_id", Uuid),
        Column("user_id", Uuid),
        Column("event_type", String),
        Column("trace_id", String),
        Column("payload", JSON),
    )
    c.tenant_languages = TenantLanguage.__table__.to_metadata(metadata)
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    c.sync = Session(engine, expire_on_commit=False)

    c.sync.execute(c.tenants.insert(), [
        {
            "id": c.tenant, "status": "active", "default_language": "pt",
            "default_currency": "EUR", "updated_at": c.base,
        },
        {
            "id": c.foreign, "status": "active", "default_language": "ru",
            "default_currency": "UAH", "updated_at": c.base,
        },
    ])
    c.sync.execute(c.languages.insert(), [
        {"code": code, "is_active": active}
        for code, active in (
            ("pt", True), ("ru", True), ("es", True),
            ("it", True), ("ja", True), ("fr", False),
        )
    ])
    c.sync.execute(
        c.users.insert().values(id=c.user, language_code="it")
    )
    c.sync.execute(c.tenant_languages.insert(), [
        {
            "id": uuid4(), "tenant_id": c.tenant, "language_code": "pt",
            "is_active": True, "created_at": c.base, "updated_at": c.base,
        },
        {
            "id": uuid4(), "tenant_id": c.tenant, "language_code": "it",
            "is_active": False, "created_at": c.base, "updated_at": c.base,
        },
    ])
    c.sync.commit()

    class Result:
        def __init__(self, result):
            self.result = result

        def scalar_one_or_none(self):
            return self.result.scalar_one_or_none()

        def mappings(self):
            def one():
                row = self.result.mappings().one_or_none()
                if row is None:
                    return None
                # Adapt SQLite timezone loss only in this local test bridge.
                return {
                    key: (
                        value.replace(tzinfo=UTC)
                        if isinstance(value, datetime) and value.tzinfo is None
                        else value
                    )
                    for key, value in row.items()
                }
            return NS(one_or_none=one)

    class Bridge:
        @property
        def new(self):
            return c.sync.new

        @property
        def dirty(self):
            return c.sync.dirty

        @property
        def deleted(self):
            return c.sync.deleted

        def expire_all(self):
            c.sync.expire_all()

        async def execute(self, statement):
            c.statements.append(statement)
            return Result(c.sync.execute(statement))

        async def commit(self):
            c.sync.flush()
            if c.fail == "commit":
                raise RuntimeError("private commit failure")
            c.sync.commit()
            c.commits += 1

        async def rollback(self):
            c.sync.rollback()
            c.rollbacks += 1

    c.db = Bridge()
    c.actor = ApiActorContext(
        c.user, c.tenant, None, ("client",), "it", None, "active",
    )

    async def identity(*, user_id, tenant_id):
        c.calls += 1
        assert user_id == c.user and tenant_id == c.tenant
        if c.deny_at == c.calls:
            raise ApiIdentityAccessError("private revoked identity")
        return replace(c.actor)

    async def resolve(*, actor):
        permissions = set(CONSTRUCTION_ROLE_PERMISSIONS[c.role])
        if c.missing_permission:
            permissions.discard("construction.settings.manage")
        return ConstructionAccessContext(
            c.user,
            c.tenant,
            (c.role,),
            tuple(permissions),
            (ConstructionGrantContext(c.role, c.scope, c.tenant),),
        )

    c.identity = NS(
        session=c.db,
        require_actor=AsyncMock(side_effect=identity),
    )
    c.resolver = NS(
        repository=NS(session=c.db),
        resolve=AsyncMock(side_effect=resolve),
    )
    c.authorizer = ConstructionOrganizationLanguageAuthorizer(
        session=c.db,
        identity=c.identity,
        access_resolver=c.resolver,
    )
    c.repo = PlatformOrganizationLanguageRepository(c.db)

    async def audit(**values):
        assert values["entity_type"] == "tenant"
        assert values["entity_id"] == c.tenant
        assert values["platform"] == "api"
        c.sync.execute(c.audit.insert().values(
            id=uuid4(),
            tenant_id=values["tenant_id"],
            user_id=values["user_id"],
            event_type=values["event_type"],
            trace_id=values["trace_id"],
            payload=values["payload"],
        ))
        if c.fail == "audit":
            raise RuntimeError("private audit failure")
        if c.fail == "cancel":
            raise asyncio.CancelledError()

    c.events = NS(
        session=c.db,
        create_event=AsyncMock(side_effect=audit),
    )
    c.service = ConstructionOrganizationLanguageService(
        session=c.db,
        repository=c.repo,
        authorizer=c.authorizer,
        events=c.events,
        now_provider=lambda: c.base + timedelta(hours=1),
    )
    try:
        yield c
    finally:
        c.sync.close()
        engine.dispose()


def snapshot(c):
    return [
        c.sync.execute(select(table)).all()
        for table in (
            c.users, c.tenants, c.languages, c.tenant_languages, c.audit,
        )
    ]


async def change(c, code="es", **values):
    args = dict(
        actor=c.actor,
        language_code=code,
        expected_row_version=1,
        trace_id="organization-language-test",
    )
    args.update(values)
    return await c.service.set_default_for_actor(**args)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
@pytest.mark.parametrize("code", ["es", "it", "ja"])
async def test_authorized_language_change_is_canonical_and_atomic(
    organization_language, role, code,
):
    c = organization_language
    c.role = role
    before_user = c.sync.execute(select(c.users)).all()
    before_foreign = c.sync.execute(
        select(c.tenants).where(c.tenants.c.id == c.foreign)
    ).all()

    result = await change(c, code)
    assert result.changed and result.default_language == code
    assert result.updated_at > c.base and result.tenant_id == c.tenant
    assert result.row_version == 2

    own = c.sync.execute(
        select(c.tenants).where(c.tenants.c.id == c.tenant)
    ).mappings().one()
    assert own["default_language"] == code
    assert own["default_currency"] == "EUR"
    assert c.sync.execute(select(c.users)).all() == before_user
    assert c.sync.execute(
        select(c.tenants).where(c.tenants.c.id == c.foreign)
    ).all() == before_foreign

    enabled = c.sync.scalar(
        select(c.tenant_languages.c.is_active).where(
            c.tenant_languages.c.tenant_id == c.tenant,
            c.tenant_languages.c.language_code == code,
        )
    )
    assert enabled is True
    events = c.sync.execute(select(c.audit)).mappings().all()
    assert len(events) == 1
    assert events[0]["trace_id"] == "organization-language-test"
    assert events[0]["payload"]["old_value"] == "pt"
    assert events[0]["payload"]["new_value"] == code
    assert events[0]["event_type"] == "construction_organization_language_updated"
    assert c.commits == 1 and c.rollbacks == 0 and c.calls == 3

    sql = [
        str(query.compile(dialect=postgresql.dialect()))
        for query in c.statements
    ]
    assert any("FOR SHARE" in query for query in sql)
    assert any(
        "FOR UPDATE" in query and "tenants" in query
        for query in sql
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["fr", "xx", "", " es "])
async def test_invalid_or_inactive_language_does_not_write(
    organization_language, code,
):
    c = organization_language
    before = snapshot(c)
    with pytest.raises(OrganizationLanguageValidationError):
        await change(c, code)
    assert snapshot(c) == before and c.commits == 0
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["observer", "project_scope", "missing_permission", "global_claims"],
)
async def test_role_name_or_global_claims_do_not_replace_scoped_permission(
    organization_language, case,
):
    c = organization_language
    if case in ("observer", "global_claims"):
        c.role = "OBSERVER"
    elif case == "project_scope":
        c.scope = "project"
    else:
        c.missing_permission = True

    if case == "global_claims":
        c.actor = replace(
            c.actor,
            roles=("super_admin",),
            permissions=(
                "construction.settings.manage",
                "platform.organization.profile.manage",
            ),
        )

    before = snapshot(c)
    with pytest.raises(OrganizationLanguageDenied):
        await change(c)
    assert snapshot(c) == before
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["audit", "commit", "cancel", "late_identity"],
)
async def test_real_sql_writes_rollback_on_failure_or_cancellation(
    organization_language, failure,
):
    c = organization_language
    before = snapshot(c)
    c.fail = failure
    if failure == "late_identity":
        c.deny_at = 3

    expected = (
        asyncio.CancelledError if failure == "cancel"
        else OrganizationLanguageDenied if failure == "late_identity"
        else OrganizationLanguageOperationError
    )
    with pytest.raises(expected) as error:
        await change(c)

    assert "private" not in str(error.value)
    assert snapshot(c) == before
    assert c.commits == 0 and c.rollbacks == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["pt", "es"])
async def test_stale_version_rejects_noop_and_real_change(
    organization_language, code,
):
    c = organization_language
    c.sync.execute(c.tenants.update().where(c.tenants.c.id == c.tenant).values(row_version=2))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(OrganizationLanguageConflict):
        await change(c, code, expected_row_version=1)
    assert snapshot(c) == before
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_noop_does_not_change_timestamp_or_audit(
    organization_language,
):
    c = organization_language
    before = snapshot(c)
    result = await change(c, "pt")
    assert not result.changed and result.updated_at == c.base
    assert snapshot(c) == before
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_version_progresses_when_clock_moves_backwards(
    organization_language,
):
    c = organization_language
    c.service.now_provider = lambda: c.base - timedelta(days=1)
    result = await change(c)
    assert result.row_version == 2 and result.changed is True


@pytest.mark.asyncio
async def test_conflicting_conditional_update_rolls_back_language_enablement(
    organization_language,
):
    c = organization_language
    before = snapshot(c)
    c.repo.replace_default = AsyncMock(return_value=None)
    with pytest.raises(OrganizationLanguageConflict):
        await change(c)
    assert snapshot(c) == before
    c.events.create_event.assert_not_awaited()


def test_authorization_and_audit_cannot_use_different_session(
    organization_language,
):
    c = organization_language
    with pytest.raises(ValueError):
        ConstructionOrganizationLanguageService(
            session=c.db,
            repository=c.repo,
            authorizer=c.authorizer,
            events=NS(session=object()),
        )

# con_011_row_version_v1
