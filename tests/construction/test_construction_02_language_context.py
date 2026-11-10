"""CON-011 local SQLite projection and HTTP gates; no deployed acceptance."""
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import BigInteger, Boolean, Column, DateTime, MetaData, String, Table, Uuid, create_engine, select
from sqlalchemy.orm import Session

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import (
    get_api_request_rate_limit_service, get_tenant_module_access_service,
    get_tenant_suite_access_service,
)
from api.routes import construction_language_settings as routes
from construction_rate_limit_fixture import with_local_construction_rate_limit
from database.repositories.platform_localization import PlatformLocalizationRepository
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.construction.construction_language_context import (
    ConstructionLanguageAccessError, ConstructionLanguageConfigurationError,
    ConstructionLanguageContextService,
)
from services.module_access import ModuleDisabledError

PATH = "/api/v1/construction/settings/languages"


@pytest.fixture
def language_case():
    c = NS(user=uuid4(), tenant=uuid4(), foreign=uuid4())
    c.updated_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    metadata = MetaData()
    c.tenants = Table("tenants", metadata, Column("id", Uuid, primary_key=True),
        Column("status", String), Column("default_language", String),
        Column("updated_at", DateTime(timezone=True)),
        Column("row_version", BigInteger, nullable=False, default=1))
    c.languages = Table("languages", metadata, Column("code", String, primary_key=True),
        Column("name", String), Column("native_name", String), Column("is_active", Boolean))
    c.tenant_languages = Table("tenant_languages", metadata,
        Column("tenant_id", Uuid, primary_key=True), Column("language_code", String, primary_key=True),
        Column("is_active", Boolean))
    c.users = Table("local_users", metadata, Column("id", Uuid, primary_key=True),
        Column("status", String), Column("language_code", String))
    c.members = Table("local_memberships", metadata,
        Column("tenant_id", Uuid, primary_key=True), Column("user_id", Uuid, primary_key=True),
        Column("is_active", Boolean))
    c.engine = create_engine("sqlite://")
    metadata.create_all(c.engine)
    c.sync = Session(c.engine, expire_on_commit=False)
    c.sync.execute(c.tenants.insert(), [
        {"id": c.tenant, "status": "active", "default_language": "pt", "updated_at": c.updated_at},
        {"id": c.foreign, "status": "active", "default_language": "ja", "updated_at": c.updated_at},
    ])
    c.sync.execute(c.languages.insert(), [
        {"code": code, "name": name, "native_name": native, "is_active": active}
        for code, name, native, active in (
            ("ru", "Russian", "Русский", True), ("pt", "Portuguese", "Português", True),
            ("en", "English", "English", True), ("es", "Spanish", "Español", True),
            ("it", "Italian", "Italiano", True), ("ja", "Japanese", None, True),
            ("fr", "French", "Français", False),
        )
    ])
    c.sync.execute(c.tenant_languages.insert(), [
        {"tenant_id": c.tenant, "language_code": "pt", "is_active": True},
        {"tenant_id": c.tenant, "language_code": "ru", "is_active": True},
        {"tenant_id": c.tenant, "language_code": "it", "is_active": False},
        {"tenant_id": c.foreign, "language_code": "ja", "is_active": True},
    ])
    c.sync.execute(c.users.insert().values(id=c.user, status="active", language_code="es"))
    c.sync.execute(c.members.insert().values(
        tenant_id=c.tenant, user_id=c.user, is_active=True))
    c.sync.commit()

    class Bridge:
        @property
        def no_autoflush(self):
            return c.sync.no_autoflush
        async def execute(self, statement):
            result = c.sync.execute(statement)
            rows = result.mappings()
            def adapt(row):
                if row is None:
                    return None
                # SQLite timezone adaptation is local to this test bridge.
                return {key: value.replace(tzinfo=timezone.utc)
                        if isinstance(value, datetime) and value.tzinfo is None else value
                        for key, value in row.items()}
            return NS(mappings=lambda: NS(
                one_or_none=lambda: adapt(rows.one_or_none()),
                all=lambda: [adapt(row) for row in rows.all()]))
    c.db = Bridge()
    c.actor = ApiActorContext(c.user, c.tenant, None, ("client",),
                              "untrusted-caller-language", None, "active")

    async def fresh(*, user_id, tenant_id):
        user = c.sync.execute(select(c.users).where(
            c.users.c.id == user_id, c.users.c.status == "active",
        )).mappings().one_or_none()
        member = c.sync.scalar(select(c.members.c.is_active).where(
            c.members.c.tenant_id == tenant_id, c.members.c.user_id == user_id,
        ))
        if user is None or member is not True:
            raise ApiIdentityAccessError("Identity unavailable.")
        return replace(c.actor, user_id=user_id, tenant_id=tenant_id,
                       language_code=user["language_code"])
    c.identity = NS(session=c.db, require_actor=AsyncMock(side_effect=fresh))
    c.repository = PlatformLocalizationRepository(c.db)
    c.service = ConstructionLanguageContextService(
        session=c.db, repository=c.repository, identity=c.identity)
    c.tables = (c.tenants, c.languages, c.tenant_languages, c.users, c.members)
    try:
        yield c
    finally:
        c.sync.close()
        c.engine.dispose()


def snapshot(c):
    return [c.sync.execute(select(table)).all() for table in c.tables]


@pytest.mark.asyncio
async def test_profile_language_and_organization_language_are_independent(language_case):
    c = language_case
    before = snapshot(c)
    result = await c.service.read_for_actor(actor=c.actor)
    assert result.interface_language == "es"
    assert result.organization_default_language == "pt"
    assert result.organization_updated_at == c.updated_at
    assert result.row_version == 1
    options = {option.code: option for option in result.languages}
    assert {"ru", "pt", "en", "es", "it", "ja"} == set(options)
    assert options["es"].label == "Español" and options["es"].enabled_for_organization is False
    assert options["it"].label == "Italiano" and options["it"].enabled_for_organization is False
    assert options["ja"].label == "Japanese" and options["ja"].enabled_for_organization is False
    assert options["pt"].enabled_for_organization is True
    assert c.identity.require_actor.await_count == 2
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_profile_change_does_not_change_organization_or_other_source_data(language_case):
    c = language_case
    c.sync.execute(c.users.update().values(language_code="it"))
    c.sync.commit()
    before = snapshot(c)
    result = await c.service.read_for_actor(actor=c.actor)
    assert result.interface_language == "it" and result.organization_default_language == "pt"
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["profile", "default", "disabled_default"])
async def test_invalid_configuration_never_silently_falls_back(language_case, failure):
    c = language_case
    if failure == "profile":
        c.sync.execute(c.users.update().values(language_code="unknown"))
    else:
        c.sync.execute(c.tenants.update().where(c.tenants.c.id == c.tenant).values(
            default_language="unknown" if failure == "default" else "it"))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(ConstructionLanguageConfigurationError):
        await c.service.read_for_actor(actor=c.actor)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_authority_is_rechecked_after_catalog_reads(language_case):
    c = language_case
    c.identity.require_actor.side_effect = [
        replace(c.actor, language_code="es"), ApiIdentityAccessError("Revoked"),
    ]
    with pytest.raises(ConstructionLanguageAccessError):
        await c.service.read_for_actor(actor=c.actor)
    assert c.identity.require_actor.await_count == 2


@pytest.fixture
def language_http(language_case):
    c = language_case
    c.app = with_local_construction_rate_limit(create_app())
    c.suite, c.core = NS(require_enabled=AsyncMock()), NS(require_enabled=AsyncMock())
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[routes.get_language_context_service] = lambda: c.service
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.core
    return c


async def read_http(c, suffix="", headers=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        return await client.get(PATH + suffix, headers=headers or {})


@pytest.mark.asyncio
async def test_http_uses_profile_instead_of_request_language_and_returns_no_document_default(language_http):
    c = language_http
    response = await read_http(c, headers={"Accept-Language": "ru"})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["interface_language"] == "es" and data["organization_default_language"] == "pt"
    assert set(data) == {"tenant_id", "user_id", "interface_language",
                         "organization_default_language", "organization_updated_at", "row_version", "languages"}
    assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.asyncio
async def test_http_rejects_caller_context_selectors_before_reading(language_http):
    c = language_http
    response = await read_http(c, "?tenant_id=" + str(c.foreign))
    assert response.status_code == 422
    c.identity.require_actor.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_requires_platform_authentication(language_http):
    c = language_http
    c.app.dependency_overrides.pop(get_current_actor)
    response = await read_http(c)
    assert response.status_code == 401
    c.identity.require_actor.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_denies_foreign_membership_and_disabled_module(language_http):
    c = language_http
    c.actor = replace(c.actor, tenant_id=c.foreign)
    response = await read_http(c)
    assert response.status_code == 403
    c.actor = replace(c.actor, tenant_id=c.tenant)
    c.core.require_enabled.side_effect = ModuleDisabledError()
    response = await read_http(c)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "MODULE_DISABLED"


@pytest.mark.asyncio
async def test_http_reports_missing_configuration_without_guessing_a_language(language_http):
    c = language_http
    c.sync.execute(c.users.update().values(language_code="missing"))
    c.sync.commit()
    response = await read_http(c)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LANGUAGE_CONFIGURATION_REQUIRED"


@pytest.mark.asyncio
async def test_http_sanitizes_repository_failures(language_http):
    c = language_http
    c.repository.list_language_catalog = AsyncMock(side_effect=RuntimeError("private database details"))
    response = await read_http(c)
    assert response.status_code == 500 and "private database details" not in response.text


@pytest.mark.asyncio
async def test_http_keeps_the_shared_authenticated_rate_limit(language_http):
    c = language_http
    rate = c.app.dependency_overrides[get_api_request_rate_limit_service]()
    for _ in range(120):
        await rate.ensure_authenticated_user_allowed(tenant_id=c.tenant, user_id=c.user)
    response = await read_http(c)
    assert response.status_code == 429
    c.identity.require_actor.assert_not_awaited()


def test_openapi_language_read_and_write_are_typed_without_closed_enum(language_http):
    document = language_http.app.openapi()
    assert set(document["paths"][PATH]) == {"get", "patch"}
    schema = document["components"]["schemas"]["LanguageOptionResponse"]
    assert "enum" not in schema["properties"]["code"]
    assert "409" in document["paths"][PATH]["get"]["responses"]

# con_011_organization_language_http_v1

# con_011_row_version_v1
