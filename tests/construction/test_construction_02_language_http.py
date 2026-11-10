"""Real local SQLite GET/PATCH workflow; no native/deployed acceptance."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, JSON, MetaData, String,
    Table, Uuid, create_engine, select,
)
from sqlalchemy.orm import Session

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import (
    get_api_request_rate_limit_service,
    get_tenant_module_access_service,
    get_tenant_suite_access_service,
)
from api.routes import construction_language_settings as routes
from construction_rate_limit_fixture import with_local_construction_rate_limit
from database.models import TenantLanguage
from database.repositories.platform_localization import PlatformLocalizationRepository
from database.repositories.platform_organization_language import (
    PlatformOrganizationLanguageRepository,
)
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.construction.construction_language_context import (
    ConstructionLanguageContextService,
)
from services.construction.construction_organization_language import (
    ConstructionOrganizationLanguageAuthorizer,
    ConstructionOrganizationLanguageService,
)
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS,
    ConstructionAccessContext,
    ConstructionGrantContext,
)
from services.module_access import ModuleDisabledError

UTC = timezone.utc
PATH = "/api/v1/construction/settings/languages"


@pytest.fixture
def http_language():
    c = NS(
        tenant=uuid4(), foreign=uuid4(), user=uuid4(), role="OWNER",
        member=True, scope="tenant", fail=None, commits=0, calls=0, deny_at=None,
    )
    c.base = datetime(2026, 10, 1, tzinfo=UTC)
    metadata = MetaData()
    c.tenants = Table(
        "tenants", metadata,
        Column("id", Uuid, primary_key=True),
        Column("status", String),
        Column("default_language", String),
        Column("updated_at", DateTime(timezone=True)),
        Column("row_version", BigInteger, nullable=False, default=1),
        Column("default_currency", String),
    )
    c.languages = Table(
        "languages", metadata,
        Column("code", String, primary_key=True),
        Column("name", String),
        Column("native_name", String),
        Column("is_active", Boolean),
    )
    c.users = Table(
        "local_users", metadata,
        Column("id", Uuid, primary_key=True),
        Column("status", String),
        Column("language_code", String),
    )
    c.audit = Table(
        "local_audit", metadata,
        Column("id", Uuid, primary_key=True),
        Column("tenant_id", Uuid),
        Column("user_id", Uuid),
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
            "updated_at": c.base, "default_currency": "EUR",
        },
        {
            "id": c.foreign, "status": "active", "default_language": "ru",
            "updated_at": c.base, "default_currency": "UAH",
        },
    ])
    c.sync.execute(c.languages.insert(), [
        {
            "code": code, "name": name,
            "native_name": None, "is_active": active,
        }
        for code, name, active in (
            ("pt", "Portuguese", True), ("ru", "Russian", True),
            ("it", "Italian", True), ("es", "Spanish", True),
            ("fr", "French", False),
        )
    ])
    c.sync.execute(
        c.users.insert().values(
            id=c.user, status="active", language_code="it",
        )
    )
    c.sync.execute(
        c.tenant_languages.insert().values(
            id=uuid4(), tenant_id=c.tenant, language_code="pt",
            is_active=True, created_at=c.base, updated_at=c.base,
        )
    )
    c.sync.commit()

    def adapt(row):
        if row is None:
            return None
        return {
            key: (
                value.replace(tzinfo=UTC)
                if isinstance(value, datetime) and value.tzinfo is None
                else value
            )
            for key, value in row.items()
        }

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value.scalar_one_or_none()

        def mappings(self):
            rows = self.value.mappings()
            return NS(
                one_or_none=lambda: adapt(rows.one_or_none()),
                all=lambda: [adapt(row) for row in rows.all()],
            )

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

        @property
        def no_autoflush(self):
            return c.sync.no_autoflush

        def expire_all(self):
            c.sync.expire_all()

        async def execute(self, statement):
            return Result(c.sync.execute(statement))

        async def commit(self):
            c.sync.flush()
            if c.fail == "commit":
                raise RuntimeError("private commit detail")
            c.sync.commit()
            c.commits += 1

        async def rollback(self):
            c.sync.rollback()

    c.db = Bridge()
    c.actor = ApiActorContext(
        c.user, c.tenant, None, ("client",), "it", None, "active",
    )

    async def fresh(*, user_id, tenant_id):
        c.calls += 1
        row = c.sync.execute(
            select(c.users).where(c.users.c.id == user_id)
        ).mappings().one_or_none()
        if (
            row is None or row["status"] != "active"
            or not c.member or tenant_id != c.tenant
            or c.calls == c.deny_at
        ):
            raise ApiIdentityAccessError("Identity unavailable.")
        return replace(c.actor, language_code=row["language_code"])

    async def resolve(*, actor):
        return ConstructionAccessContext(
            c.user, c.tenant, (c.role,),
            tuple(CONSTRUCTION_ROLE_PERMISSIONS[c.role]),
            (ConstructionGrantContext(c.role, c.scope, c.tenant),),
        )

    c.identity = NS(
        session=c.db,
        require_actor=AsyncMock(side_effect=fresh),
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

    async def audit(**values):
        c.sync.execute(
            c.audit.insert().values(
                id=uuid4(), tenant_id=values["tenant_id"],
                user_id=values["user_id"], trace_id=values["trace_id"],
                payload=values["payload"],
            )
        )
        if c.fail == "audit":
            raise RuntimeError("private audit detail")

    c.events = NS(
        session=c.db,
        create_event=AsyncMock(side_effect=audit),
    )
    c.writer = ConstructionOrganizationLanguageService(
        session=c.db,
        repository=PlatformOrganizationLanguageRepository(c.db),
        authorizer=c.authorizer,
        events=c.events,
        now_provider=lambda: c.base + timedelta(hours=1),
    )
    c.reader = ConstructionLanguageContextService(
        session=c.db,
        repository=PlatformLocalizationRepository(c.db),
        identity=c.identity,
    )

    c.app = with_local_construction_rate_limit(create_app())
    c.suite = NS(require_enabled=AsyncMock())
    c.core = NS(require_enabled=AsyncMock())
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[routes.get_language_context_service] = lambda: c.reader
    c.app.dependency_overrides[routes.get_organization_language_service] = lambda: c.writer
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.core
    try:
        yield c
    finally:
        c.sync.close()
        engine.dispose()


def snapshot(c):
    return [
        c.sync.execute(select(table)).all()
        for table in (
            c.tenants, c.languages, c.users, c.tenant_languages, c.audit,
        )
    ]


def body(c, **updates):
    value = {
        "default_language": "es",
        "expected_row_version": 1,
    }
    value.update(updates)
    return value


async def request(c, method="PATCH", payload=None, suffix=""):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=c.app, raise_app_exceptions=False,
        ),
        base_url="https://testserver",
    ) as client:
        if method == "GET":
            return await client.get(PATH + suffix)
        return await client.patch(
            PATH + suffix,
            json=body(c) if payload is None else payload,
        )


@pytest.mark.asyncio
async def test_get_then_patch_then_get_preserves_profile_and_currency(
    http_language,
):
    c = http_language
    first = await request(c, "GET")
    assert first.status_code == 200, first.text
    original = first.json()["data"]
    assert original["interface_language"] == "it"
    assert datetime.fromisoformat(
        original["organization_updated_at"].replace("Z", "+00:00")
    ) == c.base

    response = await request(c, payload={
        "default_language": "es",
        "expected_row_version": original["row_version"],
    })
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["changed"] is True
    assert data["organization_default_language"] == "es"
    assert data["tenant_id"] == str(c.tenant)
    assert response.headers["Cache-Control"] == "private, no-store"

    events = c.sync.execute(select(c.audit)).mappings().all()
    assert len(events) == 1
    assert events[0]["trace_id"] == response.json()["request_id"]
    assert events[0]["payload"]["old_value"] == "pt"

    after = await request(c, "GET")
    assert after.status_code == 200, after.text
    current = after.json()["data"]
    assert current["interface_language"] == "it"
    assert current["organization_default_language"] == "es"
    assert current["organization_updated_at"] == data["organization_updated_at"]
    assert next(
        item for item in current["languages"] if item["code"] == "es"
    )["enabled_for_organization"]

    assert c.sync.scalar(
        select(c.tenants.c.default_currency).where(
            c.tenants.c.id == c.tenant
        )
    ) == "EUR"
    assert c.sync.scalar(
        select(c.tenants.c.default_language).where(
            c.tenants.c.id == c.foreign
        )
    ) == "ru"


@pytest.mark.asyncio
async def test_current_noop_and_stale_retry_have_distinct_results(http_language):
    c = http_language
    original = snapshot(c)
    response = await request(c, payload=body(c, default_language="pt"))
    assert response.status_code == 200
    assert response.json()["data"]["changed"] is False
    assert snapshot(c) == original

    assert (await request(c)).status_code == 200
    committed = snapshot(c)
    response = await request(c)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ORGANIZATION_SETTINGS_CONFLICT"
    assert snapshot(c) == committed


@pytest.mark.asyncio
@pytest.mark.parametrize("updates", [
    {"default_language": "xx"},
    {"default_language": "fr"},
    {"default_language": " es "},
    {"default_language": None},
    {"expected_row_version": None},
    {"expected_row_version": True},
    {"expected_row_version": "1"},
    {"tenant_id": "caller-tenant"},
    {"user_id": "caller-user"},
])
async def test_invalid_body_never_writes(http_language, updates):
    c = http_language
    before = snapshot(c)
    response = await request(c, payload=body(c, **updates))
    assert response.status_code == 422, response.text
    assert snapshot(c) == before and c.commits == 0


@pytest.mark.asyncio
async def test_query_context_is_not_accepted(http_language):
    c = http_language
    before = snapshot(c)
    response = await request(c, suffix="?tenant_id=" + str(c.foreign))
    assert response.status_code == 422
    assert snapshot(c) == before
    c.identity.require_actor.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["observer", "scope", "membership", "inactive_user"],
)
async def test_fresh_authority_is_required(http_language, case):
    c = http_language
    if case == "observer":
        c.role = "OBSERVER"
    elif case == "scope":
        c.scope = "project"
    elif case == "membership":
        c.member = False
    else:
        c.sync.execute(c.users.update().values(status="blocked"))
        c.sync.commit()

    before = snapshot(c)
    response = await request(c)
    assert response.status_code == 403, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_missing_authentication_and_disabled_module_do_not_write(
    http_language,
):
    c = http_language
    before = snapshot(c)
    c.app.dependency_overrides.pop(get_current_actor)
    assert (await request(c)).status_code == 401

    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.core.require_enabled.side_effect = ModuleDisabledError()
    assert (await request(c)).status_code == 403
    assert snapshot(c) == before
    c.identity.require_actor.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit", "late_authority"])
async def test_http_failure_rolls_back_real_local_changes(
    http_language, failure,
):
    c = http_language
    c.fail = failure
    if failure == "late_authority":
        c.deny_at = 3

    before = snapshot(c)
    response = await request(c)
    assert response.status_code == (
        403 if failure == "late_authority" else 500
    ), response.text
    assert "private" not in response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_shared_rate_limit_precedes_writes(http_language):
    c = http_language
    rate = c.app.dependency_overrides[
        get_api_request_rate_limit_service
    ]()
    for _ in range(120):
        await rate.ensure_authenticated_user_allowed(
            tenant_id=c.tenant, user_id=c.user,
        )

    before = snapshot(c)
    response = await request(c)
    assert response.status_code == 429
    assert snapshot(c) == before
    c.identity.require_actor.assert_not_awaited()


def test_openapi_exposes_versioned_patch_without_key_or_language_enum(
    http_language,
):
    document = http_language.app.openapi()
    operation = document["paths"][PATH]["patch"]
    model = document["components"]["schemas"]["OrganizationLanguageUpdate"]
    assert set(model["required"]) == {
        "default_language", "expected_row_version",
    }
    assert model["additionalProperties"] is False
    assert "enum" not in model["properties"]["default_language"]
    assert model["properties"]["expected_row_version"]["minimum"] == 1
    assert not any(
        item["name"].lower() == "idempotency-key"
        for item in operation.get("parameters", [])
    )
    assert {"401", "403", "409", "422", "429", "500"} <= set(
        operation["responses"]
    )

# con_011_row_version_v1
