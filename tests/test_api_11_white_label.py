from sqlalchemy import DateTime


def test_white_label_models_match_database_contract():
    from database.models import (
        Module,
        Suite,
        SuiteModule,
        TenantDomain,
        TenantLanguage,
        TenantModule,
        TenantSuite,
        TenantWhiteLabelSetting,
    )

    expected_columns = {
        TenantDomain: {
            "id",
            "tenant_id",
            "domain",
            "is_primary",
            "status",
            "verified_at",
            "created_at",
            "updated_at",
        },
        TenantWhiteLabelSetting: {
            "id",
            "tenant_id",
            "logo_url",
            "favicon_url",
            "primary_color",
            "secondary_color",
            "accent_color",
            "theme_config",
            "created_at",
            "updated_at",
        },
        TenantLanguage: {
            "id",
            "tenant_id",
            "language_code",
            "is_active",
            "created_at",
            "updated_at",
        },
        Suite: {
            "id",
            "code",
            "name",
            "status",
            "created_at",
            "updated_at",
        },
        Module: {
            "id",
            "code",
            "name",
            "status",
            "created_at",
            "updated_at",
        },
        SuiteModule: {
            "id",
            "suite_id",
            "module_id",
            "is_required",
            "created_at",
        },
        TenantSuite: {
            "id",
            "tenant_id",
            "suite_id",
            "status",
            "activated_at",
            "expires_at",
            "created_at",
            "updated_at",
        },
        TenantModule: {
            "id",
            "tenant_id",
            "module_id",
            "is_enabled",
            "source",
            "created_at",
            "updated_at",
        },
    }

    for model, expected in expected_columns.items():
        assert set(
            model.__table__.columns.keys()
        ) == expected

    assert TenantDomain.__tablename__ == (
        "tenant_domains"
    )
    assert TenantWhiteLabelSetting.__tablename__ == (
        "tenant_white_label_settings"
    )
    assert TenantLanguage.__tablename__ == (
        "tenant_languages"
    )
    assert Suite.__tablename__ == "suites"
    assert Module.__tablename__ == "modules"
    assert SuiteModule.__tablename__ == (
        "suite_modules"
    )
    assert TenantSuite.__tablename__ == (
        "tenant_suites"
    )
    assert TenantModule.__tablename__ == (
        "tenant_modules"
    )

    for model in expected_columns:
        for column in model.__table__.columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone is True, (
                    f"{model.__tablename__}."
                    f"{column.name} must be "
                    "timezone-aware"
                )

    assert (
        TenantWhiteLabelSetting
        .__table__
        .columns["theme_config"]
        .type.__class__.__name__
        == "JSONB"
    )

    business_columns = {
        "suite_codes",
        "module_codes",
        "enabled_languages",
        "enabled_modules",
    }

    assert business_columns.isdisjoint(
        TenantWhiteLabelSetting
        .__table__
        .columns.keys()
    )


import pytest


@pytest.mark.asyncio
async def test_white_label_repository_resolves_active_tenant_by_domain():
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.white_label import (
        WhiteLabelRepository,
    )

    expected = SimpleNamespace(
        id=uuid4(),
        status="active",
    )

    class FakeResult:
        def scalar_one_or_none(self):
            return expected

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = WhiteLabelRepository(session)

    result = await (
        repository
        .get_active_tenant_for_domain(
            domain="brand.example.com",
        )
    )

    assert result is expected
    assert len(session.statements) == 1

    statement = session.statements[0]
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )

    normalized = " ".join(compiled.split())

    assert (
        "tenant_domains.domain = "
        "'brand.example.com'"
        in normalized
    )
    assert (
        "tenant_domains.status = 'active'"
        in normalized
    )
    assert (
        "tenant_domains.verified_at IS NOT NULL"
        in normalized
    )
    assert (
        "tenants.status = 'active'"
        in normalized
    )


@pytest.mark.asyncio
async def test_white_label_repository_loads_tenant_scoped_settings():
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.white_label import (
        WhiteLabelRepository,
    )

    tenant_id = uuid4()
    expected = SimpleNamespace(
        tenant_id=tenant_id,
    )

    class FakeResult:
        def scalar_one_or_none(self):
            return expected

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = WhiteLabelRepository(session)

    result = await repository.get_settings(
        tenant_id=tenant_id,
    )

    assert result is expected
    assert len(session.statements) == 1

    compiled = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )
    normalized = " ".join(compiled.split())

    assert (
        "tenant_white_label_settings."
        "tenant_id = "
        f"'{tenant_id}'"
        in normalized
    )


@pytest.mark.asyncio
async def test_white_label_repository_lists_active_tenant_languages():
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.white_label import (
        WhiteLabelRepository,
    )

    tenant_id = uuid4()
    expected = [
        SimpleNamespace(code="en"),
        SimpleNamespace(code="uk"),
    ]

    class FakeScalars:
        def all(self):
            return expected

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = WhiteLabelRepository(session)

    result = await (
        repository.list_active_languages(
            tenant_id=tenant_id,
        )
    )

    assert result == expected
    assert len(session.statements) == 1

    compiled = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )
    normalized = " ".join(compiled.split())

    assert (
        "tenant_languages.tenant_id = "
        f"'{tenant_id}'"
        in normalized
    )
    assert (
        "tenant_languages.is_active IS true"
        in normalized
    )
    assert (
        "languages.is_active IS true"
        in normalized
    )
    assert (
        "ORDER BY languages.code"
        in normalized
    )


@pytest.mark.asyncio
async def test_white_label_repository_lists_active_tenant_suites():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.white_label import (
        WhiteLabelRepository,
    )

    tenant_id = uuid4()
    now = datetime(
        2026,
        9,
        2,
        12,
        0,
        tzinfo=UTC,
    )
    expected = [
        SimpleNamespace(code="hr"),
    ]

    class FakeScalars:
        def all(self):
            return expected

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = WhiteLabelRepository(session)

    result = await repository.list_active_suites(
        tenant_id=tenant_id,
        now=now,
    )

    assert result == expected
    assert len(session.statements) == 1

    compiled = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )
    normalized = " ".join(compiled.split())

    assert (
        "tenant_suites.tenant_id = "
        f"'{tenant_id}'"
        in normalized
    )
    assert (
        "tenant_suites.status = 'active'"
        in normalized
    )
    assert (
        "suites.status = 'active'"
        in normalized
    )
    assert "tenant_suites.expires_at IS NULL" in normalized
    assert "tenant_suites.expires_at >" in normalized
    assert "ORDER BY suites.code" in normalized


@pytest.mark.asyncio
async def test_white_label_repository_lists_enabled_tenant_modules():
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.white_label import (
        WhiteLabelRepository,
    )

    tenant_id = uuid4()
    expected = [
        SimpleNamespace(code="calendar"),
        SimpleNamespace(code="hr"),
    ]

    class FakeScalars:
        def all(self):
            return expected

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = WhiteLabelRepository(session)

    result = await (
        repository.list_enabled_modules(
            tenant_id=tenant_id,
        )
    )

    assert result == expected
    assert len(session.statements) == 1

    compiled = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )
    normalized = " ".join(compiled.split())

    assert (
        "tenant_modules.tenant_id = "
        f"'{tenant_id}'"
        in normalized
    )
    assert (
        "tenant_modules.is_enabled IS true"
        in normalized
    )
    assert (
        "modules.status = 'active'"
        in normalized
    )
    assert "ORDER BY modules.code" in normalized
    assert "theme_config" not in normalized


def test_tenant_model_supports_white_label_config():
    from sqlalchemy import DateTime

    from database.models import Tenant

    columns = {
        column.name: column
        for column in Tenant.__table__.columns
    }

    assert set(columns) == {
        "id",
        "name",
        "slug",
        "default_language",
        "default_currency",
        "status",
        "metadata",
        "created_at",
        "updated_at",
    }

    assert columns["name"].nullable is False
    assert columns["slug"].nullable is False
    assert (
        columns["default_language"].nullable
        is False
    )
    assert (
        columns["default_currency"].nullable
        is False
    )
    assert columns["status"].nullable is False
    assert columns["metadata"].nullable is False

    assert isinstance(
        columns["created_at"].type,
        DateTime,
    )
    assert (
        columns["created_at"].type.timezone
        is True
    )
    assert (
        columns["updated_at"].type.timezone
        is True
    )


@pytest.mark.asyncio
async def test_white_label_service_resolves_tenant_from_normalized_hostname():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.white_label import (
        WhiteLabelService,
    )

    expected = SimpleNamespace(
        id=uuid4(),
        status="active",
    )
    calls = []

    class FakeRepository:
        async def get_active_tenant_for_domain(
            self,
            *,
            domain,
        ):
            calls.append(domain)
            return expected

    service = WhiteLabelService(
        repository=FakeRepository(),
    )

    result = await service.resolve_tenant(
        hostname="  Brand.Example.COM.  ",
    )

    assert result is expected
    assert calls == [
        "brand.example.com",
    ]


@pytest.mark.asyncio
async def test_white_label_service_builds_safe_frontend_config():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.white_label import (
        WhiteLabelService,
    )

    tenant_id = uuid4()
    now = datetime(
        2026,
        9,
        2,
        14,
        0,
        tzinfo=UTC,
    )
    calls = []

    tenant = SimpleNamespace(
        id=tenant_id,
        slug="example-tenant",
        name="Example Tenant",
        default_language="uk",
        default_currency="EUR",
        extra_metadata={
            "private": True,
        },
    )
    settings = SimpleNamespace(
        logo_url="https://cdn.example/logo.png",
        favicon_url=(
            "https://cdn.example/favicon.ico"
        ),
        primary_color="#112233",
        secondary_color="#445566",
        accent_color="#778899",
        theme_config={
            "radius": "small",
        },
    )
    languages = [
        SimpleNamespace(
            code="en",
            name="English",
            native_name="English",
        ),
        SimpleNamespace(
            code="uk",
            name="Ukrainian",
            native_name="Українська",
        ),
    ]
    suites = [
        SimpleNamespace(code="hr"),
    ]
    modules = [
        SimpleNamespace(code="calendar"),
        SimpleNamespace(code="hr"),
    ]

    class FakeRepository:
        async def get_active_tenant_for_domain(
            self,
            *,
            domain,
        ):
            calls.append(
                ("tenant", domain)
            )
            return tenant

        async def get_settings(
            self,
            *,
            tenant_id,
        ):
            calls.append(
                ("settings", tenant_id)
            )
            return settings

        async def list_active_languages(
            self,
            *,
            tenant_id,
        ):
            calls.append(
                ("languages", tenant_id)
            )
            return languages

        async def list_active_suites(
            self,
            *,
            tenant_id,
            now,
        ):
            calls.append(
                ("suites", tenant_id, now)
            )
            return suites

        async def list_enabled_modules(
            self,
            *,
            tenant_id,
        ):
            calls.append(
                ("modules", tenant_id)
            )
            return modules

        async def list_published_legal_documents(
            self,
            *,
            tenant_id,
        ):
            return []

        async def list_active_pricing(
            self,
            *,
            tenant_id,
        ):
            return []

    service = WhiteLabelService(
        repository=FakeRepository(),
    )

    config = await service.get_config(
        hostname="Brand.Example.com",
        now=now,
    )

    assert config.tenant_slug == (
        "example-tenant"
    )
    assert config.tenant_name == (
        "Example Tenant"
    )
    assert config.default_language == "uk"
    assert config.default_currency == "EUR"

    assert config.branding.logo_url == (
        "https://cdn.example/logo.png"
    )
    assert config.branding.primary_color == (
        "#112233"
    )
    assert config.branding.theme_config == {
        "radius": "small",
    }

    assert tuple(
        language.code
        for language in config.languages
    ) == (
        "en",
        "uk",
    )
    assert config.suites == ("hr",)
    assert config.modules == (
        "calendar",
        "hr",
    )

    assert not hasattr(config, "tenant_id")
    assert not hasattr(config, "metadata")
    assert not hasattr(
        config.branding,
        "enabled_modules",
    )

    assert calls == [
        (
            "tenant",
            "brand.example.com",
        ),
        (
            "settings",
            tenant_id,
        ),
        (
            "languages",
            tenant_id,
        ),
        (
            "suites",
            tenant_id,
            now,
        ),
        (
            "modules",
            tenant_id,
        ),
    ]


@pytest.mark.asyncio
async def test_white_label_repository_lists_published_legal_documents():
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.white_label import (
        WhiteLabelRepository,
    )

    tenant_id = uuid4()
    expected = [
        SimpleNamespace(
            doc_type="privacy",
            language="en",
        ),
    ]

    class FakeScalars:
        def all(self):
            return expected

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = WhiteLabelRepository(session)

    result = await (
        repository
        .list_published_legal_documents(
            tenant_id=tenant_id,
        )
    )

    assert result == expected
    assert len(session.statements) == 1

    compiled = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )
    normalized = " ".join(compiled.split())

    assert (
        "legal_documents.tenant_id = "
        f"'{tenant_id}'"
        in normalized
    )
    assert (
        "legal_documents.status = "
        "'published'"
        in normalized
    )
    assert (
        "ORDER BY "
        "legal_documents.doc_type, "
        "legal_documents.language"
        in normalized
    )


@pytest.mark.asyncio
async def test_white_label_repository_lists_active_tenant_pricing():
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.white_label import (
        WhiteLabelRepository,
    )

    tenant_id = uuid4()
    expected = [
        SimpleNamespace(
            code="specialist_premium",
        ),
    ]

    class FakeScalars:
        def all(self):
            return expected

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = WhiteLabelRepository(session)

    result = await (
        repository.list_active_pricing(
            tenant_id=tenant_id,
        )
    )

    assert result == expected
    assert len(session.statements) == 1

    compiled = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )
    normalized = " ".join(compiled.split())

    assert (
        "paid_features.tenant_id = "
        f"'{tenant_id}'"
        in normalized
    )
    assert (
        "paid_features.status = 'active'"
        in normalized
    )
    assert (
        "ORDER BY paid_features.code"
        in normalized
    )


@pytest.mark.asyncio
async def test_white_label_config_exposes_safe_legal_and_pricing_views():
    from decimal import Decimal
    from types import SimpleNamespace
    from uuid import uuid4

    from services.white_label import (
        WhiteLabelService,
    )

    tenant_id = uuid4()

    class FakeRepository:
        async def get_active_tenant_for_domain(
            self,
            *,
            domain,
        ):
            return SimpleNamespace(
                id=tenant_id,
                slug="tenant",
                name="Tenant",
                default_language="en",
                default_currency="EUR",
            )

        async def get_settings(
            self,
            *,
            tenant_id,
        ):
            return None

        async def list_active_languages(
            self,
            *,
            tenant_id,
        ):
            return []

        async def list_active_suites(
            self,
            *,
            tenant_id,
            now,
        ):
            return []

        async def list_enabled_modules(
            self,
            *,
            tenant_id,
        ):
            return []

        async def list_published_legal_documents(
            self,
            *,
            tenant_id,
        ):
            return [
                SimpleNamespace(
                    doc_type="privacy",
                    version="1.0",
                    language="en",
                    title="Privacy Policy",
                    content_url=(
                        "https://example.com/privacy"
                    ),
                    tenant_id=uuid4(),
                    content_text="internal payload",
                ),
            ]

        async def list_active_pricing(
            self,
            *,
            tenant_id,
        ):
            return [
                SimpleNamespace(
                    code="premium",
                    name="Premium",
                    description="Premium access",
                    price=Decimal("9.00"),
                    currency="EUR",
                    tenant_id=uuid4(),
                    extra_metadata={
                        "private": True,
                    },
                ),
            ]

    service = WhiteLabelService(
        repository=FakeRepository(),
    )

    config = await service.get_config(
        hostname="tenant.example.com",
    )

    assert len(config.legal_documents) == 1
    legal = config.legal_documents[0]

    assert legal.doc_type == "privacy"
    assert legal.version == "1.0"
    assert legal.language == "en"
    assert legal.title == "Privacy Policy"
    assert legal.content_url == (
        "https://example.com/privacy"
    )
    assert not hasattr(legal, "tenant_id")
    assert not hasattr(legal, "content_text")

    assert len(config.pricing) == 1
    price = config.pricing[0]

    assert price.code == "premium"
    assert price.name == "Premium"
    assert price.description == (
        "Premium access"
    )
    assert price.price == Decimal("9.00")
    assert price.currency == "EUR"
    assert not hasattr(price, "tenant_id")
    assert not hasattr(price, "metadata")


@pytest.mark.asyncio
async def test_white_label_config_endpoint_uses_request_hostname():
    from decimal import Decimal

    import httpx

    from api.app import create_app
    from api.dependencies import (
        get_white_label_service,
    )
    from services.white_label import (
        WhiteLabelBranding,
        WhiteLabelConfig,
        WhiteLabelLanguage,
        WhiteLabelLegalDocument,
        WhiteLabelPricingItem,
    )

    request_id = "white-label-config"
    calls = []

    config = WhiteLabelConfig(
        tenant_slug="example-tenant",
        tenant_name="Example Tenant",
        default_language="en",
        default_currency="EUR",
        branding=WhiteLabelBranding(
            logo_url=(
                "https://cdn.example/logo.png"
            ),
            favicon_url=None,
            primary_color="#112233",
            secondary_color=None,
            accent_color="#778899",
            theme_config={
                "radius": "small",
            },
        ),
        languages=(
            WhiteLabelLanguage(
                code="en",
                name="English",
                native_name="English",
            ),
        ),
        suites=("hr",),
        modules=(
            "calendar",
            "hr",
        ),
        legal_documents=(
            WhiteLabelLegalDocument(
                doc_type="privacy",
                version="1.0",
                language="en",
                title="Privacy Policy",
                content_url=(
                    "https://example.com/privacy"
                ),
            ),
        ),
        pricing=(
            WhiteLabelPricingItem(
                code="premium",
                name="Premium",
                description="Premium access",
                price=Decimal("9.00"),
                currency="EUR",
            ),
        ),
    )

    class FakeWhiteLabelService:
        async def get_config(
            self,
            *,
            hostname,
        ):
            calls.append(hostname)
            return config

    application = create_app()
    application.dependency_overrides[
        get_white_label_service
    ] = lambda: FakeWhiteLabelService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/white-label/config",
            headers={
                "Host": (
                    "Brand.Example.com:8443"
                ),
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 200
    assert calls == [
        "brand.example.com",
    ]

    assert response.json() == {
        "data": {
            "tenant_slug": "example-tenant",
            "tenant_name": "Example Tenant",
            "default_language": "en",
            "default_currency": "EUR",
            "branding": {
                "logo_url": (
                    "https://cdn.example/logo.png"
                ),
                "favicon_url": None,
                "primary_color": "#112233",
                "secondary_color": None,
                "accent_color": "#778899",
                "theme_config": {
                    "radius": "small",
                },
            },
            "languages": [
                {
                    "code": "en",
                    "name": "English",
                    "native_name": "English",
                },
            ],
            "suites": ["hr"],
            "modules": [
                "calendar",
                "hr",
            ],
            "legal_documents": [
                {
                    "doc_type": "privacy",
                    "version": "1.0",
                    "language": "en",
                    "title": "Privacy Policy",
                    "content_url": (
                        "https://example.com/privacy"
                    ),
                },
            ],
            "pricing": [
                {
                    "code": "premium",
                    "name": "Premium",
                    "description": (
                        "Premium access"
                    ),
                    "price": "9.00",
                    "currency": "EUR",
                },
            ],
        },
        "meta": {},
        "request_id": request_id,
    }

    assert "tenant_id" not in (
        response.json()["data"]
    )


@pytest.mark.asyncio
async def test_white_label_unknown_hostname_is_sanitized():
    import httpx

    from api.app import create_app
    from api.dependencies import (
        get_white_label_service,
    )
    from services.white_label import (
        WhiteLabelTenantNotFoundError,
    )

    request_id = (
        "white-label-unknown-host"
    )

    class FakeWhiteLabelService:
        async def get_config(
            self,
            *,
            hostname,
        ):
            raise WhiteLabelTenantNotFoundError(
                "Private tenant lookup details."
            )

    application = create_app()
    application.dependency_overrides[
        get_white_label_service
    ] = lambda: FakeWhiteLabelService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/white-label/config",
            headers={
                "Host": "unknown.example.com",
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "white_label_not_found",
            "message": (
                "White Label configuration "
                "is not available."
            ),
            "request_id": request_id,
        },
    }

    assert (
        "Private tenant lookup details."
        not in response.text
    )


@pytest.mark.asyncio
async def test_white_label_config_ignores_client_tenant_id():
    from types import SimpleNamespace
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.dependencies import (
        get_white_label_service,
    )

    supplied_tenant_id = uuid4()
    calls = []

    config = SimpleNamespace(
        tenant_slug="server-tenant",
        tenant_name="Server Tenant",
        default_language="en",
        default_currency="EUR",
        branding=SimpleNamespace(
            logo_url=None,
            favicon_url=None,
            primary_color=None,
            secondary_color=None,
            accent_color=None,
            theme_config={},
        ),
        languages=(),
        suites=(),
        modules=(),
        legal_documents=(),
        pricing=(),
    )

    class FakeWhiteLabelService:
        async def get_config(
            self,
            *,
            hostname,
        ):
            calls.append(hostname)
            return config

    application = create_app()
    application.dependency_overrides[
        get_white_label_service
    ] = lambda: FakeWhiteLabelService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/white-label/config",
            params={
                "tenant_id": str(
                    supplied_tenant_id
                ),
            },
            headers={
                "Host": "server.example.com",
            },
        )

    assert response.status_code == 200
    assert calls == [
        "server.example.com",
    ]

    data = response.json()["data"]

    assert data["tenant_slug"] == (
        "server-tenant"
    )
    assert "tenant_id" not in data
    assert (
        str(supplied_tenant_id)
        not in response.text
    )


def test_white_label_openapi_has_typed_safe_config():
    from api.app import create_app

    schema = create_app().openapi()

    response_schema = (
        schema["paths"]
        ["/api/v1/white-label/config"]
        ["get"]["responses"]["200"]
        ["content"]["application/json"]
        ["schema"]
    )

    assert response_schema == {
        "$ref": (
            "#/components/schemas/"
            "WhiteLabelConfigResponse"
        ),
    }

    components = schema[
        "components"
    ]["schemas"]

    assert set(
        components[
            "WhiteLabelConfigResponse"
        ]["properties"]
    ) == {
        "data",
        "meta",
        "request_id",
    }

    assert set(
        components[
            "WhiteLabelConfigData"
        ]["properties"]
    ) == {
        "tenant_slug",
        "tenant_name",
        "default_language",
        "default_currency",
        "branding",
        "languages",
        "suites",
        "modules",
        "legal_documents",
        "pricing",
    }

    assert "tenant_id" not in (
        components[
            "WhiteLabelConfigData"
        ]["properties"]
    )


@pytest.mark.asyncio
async def test_tenant_module_access_is_fail_closed():
    from uuid import uuid4

    from services.module_access import (
        ModuleDisabledError,
        TenantModuleAccessService,
    )

    tenant_id = uuid4()
    calls = []

    class FakeRepository:
        async def is_module_enabled(
            self,
            *,
            tenant_id,
            module_code,
        ):
            calls.append(
                (
                    tenant_id,
                    module_code,
                )
            )
            return False

    service = TenantModuleAccessService(
        repository=FakeRepository(),
    )

    with pytest.raises(
        ModuleDisabledError,
        match="Module is disabled",
    ):
        await service.require_enabled(
            tenant_id=tenant_id,
            module_code="hr",
        )

    assert calls == [
        (
            tenant_id,
            "hr",
        ),
    ]


@pytest.mark.asyncio
async def test_white_label_repository_checks_effective_tenant_module():
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.white_label import (
        WhiteLabelRepository,
    )

    tenant_id = uuid4()
    now = datetime(
        2026,
        9,
        3,
        10,
        0,
        tzinfo=UTC,
    )

    class FakeResult:
        def scalar_one_or_none(self):
            return uuid4()

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = WhiteLabelRepository(session)

    enabled = await repository.is_module_enabled(
        tenant_id=tenant_id,
        module_code="calendar",
        now=now,
    )

    assert enabled is True
    assert len(session.statements) == 1

    compiled = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )
    normalized = " ".join(compiled.split())

    assert (
        "tenant_modules.tenant_id = "
        f"'{tenant_id}'"
        in normalized
    )
    assert (
        "modules.code = 'calendar'"
        in normalized
    )
    assert (
        "tenant_modules.is_enabled IS true"
        in normalized
    )
    assert (
        "modules.status = 'active'"
        in normalized
    )
    assert (
        "tenant_modules.source = 'manual'"
        in normalized
    )
    assert (
        "tenant_modules.source = 'suite'"
        in normalized
    )
    assert "suite_modules" in normalized
    assert "tenant_suites" in normalized
    assert (
        "tenant_suites.status = 'active'"
        in normalized
    )
    assert (
        "tenant_suites.expires_at IS NULL"
        in normalized
    )
    assert "tenant_suites.expires_at >" in normalized


@pytest.mark.asyncio
async def test_enabled_module_list_requires_effective_suite_source():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.white_label import (
        WhiteLabelRepository,
    )

    class FakeScalars:
        def all(self):
            return []

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = WhiteLabelRepository(session)

    await repository.list_enabled_modules(
        tenant_id=uuid4(),
    )

    assert len(session.statements) == 1

    compiled = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )
    normalized = " ".join(compiled.split())

    assert (
        "tenant_modules.source = 'manual'"
        in normalized
    )
    assert (
        "tenant_modules.source = 'suite'"
        in normalized
    )
    assert "suite_modules" in normalized
    assert "tenant_suites" in normalized
    assert (
        "tenant_suites.status = 'active'"
        in normalized
    )
    assert (
        "tenant_suites.expires_at IS NULL"
        in normalized
    )
    assert "tenant_suites.expires_at >" in normalized
    assert "suites.status = 'active'" in normalized


@pytest.mark.asyncio
async def test_api_module_guard_uses_actor_tenant_and_fails_closed():
    from types import SimpleNamespace
    from uuid import uuid4

    from api.auth import require_module
    from api.errors import ApiHttpError
    from services.module_access import (
        ModuleDisabledError,
    )

    tenant_id = uuid4()
    actor = SimpleNamespace(
        tenant_id=tenant_id,
    )
    calls = []

    class AllowedService:
        async def require_enabled(
            self,
            *,
            tenant_id,
            module_code,
        ):
            calls.append(
                (
                    "allowed",
                    tenant_id,
                    module_code,
                )
            )

    class DeniedService:
        async def require_enabled(
            self,
            *,
            tenant_id,
            module_code,
        ):
            calls.append(
                (
                    "denied",
                    tenant_id,
                    module_code,
                )
            )
            raise ModuleDisabledError(
                "Private entitlement details."
            )

    guard = require_module("hr")

    assert (
        await guard(
            actor,
            AllowedService(),
        )
        is actor
    )

    with pytest.raises(ApiHttpError) as error:
        await guard(
            actor,
            DeniedService(),
        )

    assert error.value.status_code == 403
    assert error.value.code == (
        "module_disabled"
    )
    assert error.value.message == (
        "Required module is not available."
    )

    assert calls == [
        (
            "allowed",
            tenant_id,
            "hr",
        ),
        (
            "denied",
            tenant_id,
            "hr",
        ),
    ]


@pytest.mark.asyncio
async def test_white_label_repository_checks_active_tenant_suite():
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.white_label import (
        WhiteLabelRepository,
    )

    tenant_id = uuid4()
    now = datetime(
        2026,
        9,
        3,
        12,
        0,
        tzinfo=UTC,
    )

    class FakeResult:
        def scalar_one_or_none(self):
            return uuid4()

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = WhiteLabelRepository(session)

    enabled = await repository.is_suite_enabled(
        tenant_id=tenant_id,
        suite_code="hr",
        now=now,
    )

    assert enabled is True
    assert len(session.statements) == 1

    compiled = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )
    normalized = " ".join(compiled.split())

    assert (
        "tenant_suites.tenant_id = "
        f"'{tenant_id}'"
        in normalized
    )
    assert "suites.code = 'hr'" in normalized
    assert (
        "tenant_suites.status = 'active'"
        in normalized
    )
    assert "suites.status = 'active'" in normalized
    assert (
        "tenant_suites.expires_at IS NULL"
        in normalized
    )
    assert "tenant_suites.expires_at >" in normalized


@pytest.mark.asyncio
async def test_tenant_suite_access_is_fail_closed():
    from uuid import uuid4

    from services.module_access import (
        SuiteDisabledError,
        TenantSuiteAccessService,
    )

    tenant_id = uuid4()
    calls = []

    class FakeRepository:
        async def is_suite_enabled(
            self,
            *,
            tenant_id,
            suite_code,
        ):
            calls.append(
                (
                    tenant_id,
                    suite_code,
                )
            )
            return False

    service = TenantSuiteAccessService(
        repository=FakeRepository(),
    )

    with pytest.raises(
        SuiteDisabledError,
        match="Suite is disabled",
    ):
        await service.require_enabled(
            tenant_id=tenant_id,
            suite_code="hr",
        )

    assert calls == [
        (
            tenant_id,
            "hr",
        ),
    ]


@pytest.mark.asyncio
async def test_api_suite_guard_uses_actor_tenant_and_fails_closed():
    from types import SimpleNamespace
    from uuid import uuid4

    from api.auth import require_suite
    from api.errors import ApiHttpError
    from services.module_access import (
        SuiteDisabledError,
    )

    tenant_id = uuid4()
    actor = SimpleNamespace(
        tenant_id=tenant_id,
    )

    class DeniedService:
        async def require_enabled(
            self,
            *,
            tenant_id,
            suite_code,
        ):
            raise SuiteDisabledError(
                "Private Suite entitlement."
            )

    guard = require_suite("hr")

    with pytest.raises(ApiHttpError) as error:
        await guard(
            actor,
            DeniedService(),
        )

    assert error.value.status_code == 403
    assert error.value.code == (
        "module_disabled"
    )
    assert error.value.message == (
        "Required Suite is not available."
    )

