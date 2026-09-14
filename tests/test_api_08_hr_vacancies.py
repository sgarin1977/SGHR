def test_hr_employer_and_vacancy_models_match_database():
    from database.models import (
        Employer,
        Vacancy,
    )

    assert Employer.__tablename__ == "employers"
    assert Vacancy.__tablename__ == "vacancies"

    employer_columns = {
        column.name: column
        for column in Employer.__table__.columns
    }
    vacancy_columns = {
        column.name: column
        for column in Vacancy.__table__.columns
    }

    assert set(employer_columns) == {
        "id",
        "tenant_id",
        "user_id",
        "company_name",
        "representative_name",
        "company_type",
        "country_id",
        "city_id",
        "email",
        "phone",
        "status",
        "metadata",
        "created_at",
        "updated_at",
    }

    assert set(vacancy_columns) == {
        "id",
        "tenant_id",
        "employer_id",
        "profession_id",
        "country_id",
        "city_id",
        "title",
        "description",
        "salary_min",
        "salary_max",
        "currency",
        "employment_type",
        "work_format",
        "recruitment_mode",
        "status",
        "published_at",
        "expires_at",
        "metadata",
        "created_at",
        "updated_at",
    }

    employer_foreign_keys = {
        (
            column.name,
            foreign_key.target_fullname,
        )
        for column in employer_columns.values()
        for foreign_key in column.foreign_keys
    }
    vacancy_foreign_keys = {
        (
            column.name,
            foreign_key.target_fullname,
        )
        for column in vacancy_columns.values()
        for foreign_key in column.foreign_keys
    }

    assert employer_foreign_keys == {
        ("tenant_id", "tenants.id"),
        ("user_id", "users.id"),
        ("country_id", "countries.id"),
        ("city_id", "cities.id"),
    }

    assert vacancy_foreign_keys == {
        ("tenant_id", "tenants.id"),
        ("employer_id", "employers.id"),
        ("profession_id", "professions.id"),
        ("country_id", "countries.id"),
        ("city_id", "cities.id"),
    }

    assert employer_columns[
        "tenant_id"
    ].nullable is False
    assert employer_columns[
        "user_id"
    ].nullable is False
    assert employer_columns[
        "company_name"
    ].nullable is False

    assert vacancy_columns[
        "tenant_id"
    ].nullable is False
    assert vacancy_columns[
        "employer_id"
    ].nullable is False
    assert vacancy_columns[
        "title"
    ].nullable is False
    assert vacancy_columns[
        "recruitment_mode"
    ].nullable is False


from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_hr_repository_resolves_employer_by_actor_scope():
    from sqlalchemy.dialects import postgresql

    from database.repositories.hr import (
        HrRepository,
    )

    tenant_id = uuid4()
    user_id = uuid4()
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
    repository = HrRepository(session)

    result = await (
        repository.get_active_employer_for_user(
            tenant_id=tenant_id,
            user_id=user_id,
        )
    )

    assert result is expected
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).lower()

    normalized_sql = " ".join(sql.split())

    required_scope = (
        "employers.tenant_id",
        "employers.user_id",
        "employers.status = 'active'",
    )

    for value in required_scope:
        assert value in normalized_sql


@pytest.mark.asyncio
async def test_hr_repository_lists_vacancies_by_tenant_and_employer():
    from sqlalchemy.dialects import postgresql

    from database.repositories.hr import (
        HrRepository,
    )

    tenant_id = uuid4()
    employer_id = uuid4()

    expected = [
        SimpleNamespace(
            id=uuid4(),
            status="published",
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
    repository = HrRepository(session)

    result = await (
        repository.list_employer_vacancies(
            tenant_id=tenant_id,
            employer_id=employer_id,
            limit=20,
            offset=0,
        )
    )

    assert result == expected
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).lower()

    normalized_sql = " ".join(sql.split())

    required_scope = (
        "vacancies.tenant_id",
        "vacancies.employer_id",
        "vacancies.created_at desc",
    )

    for value in required_scope:
        assert value in normalized_sql


@pytest.mark.asyncio
async def test_hr_vacancy_service_uses_actor_and_maps_safe_views():
    from datetime import UTC, datetime
    from decimal import Decimal

    from services.hr_vacancies import (
        HrVacancyListItem,
        HrVacancyService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    employer_id = uuid4()
    vacancy_id = uuid4()
    profession_id = uuid4()
    country_id = uuid4()
    city_id = uuid4()
    calls = []

    published_at = datetime(
        2026,
        8,
        20,
        10,
        0,
        tzinfo=UTC,
    )
    expires_at = datetime(
        2026,
        9,
        20,
        10,
        0,
        tzinfo=UTC,
    )
    created_at = datetime(
        2026,
        8,
        19,
        9,
        0,
        tzinfo=UTC,
    )
    updated_at = published_at

    employer = SimpleNamespace(
        id=employer_id,
        tenant_id=tenant_id,
        user_id=user_id,
        status="active",
    )
    vacancy = SimpleNamespace(
        id=vacancy_id,
        tenant_id=tenant_id,
        employer_id=employer_id,
        profession_id=profession_id,
        country_id=country_id,
        city_id=city_id,
        title="Senior Auditor",
        description="Audit vacancy",
        salary_min=Decimal("2000.00"),
        salary_max=Decimal("3000.00"),
        currency="EUR",
        employment_type="full_time",
        work_format="remote",
        recruitment_mode="direct",
        status="published",
        published_at=published_at,
        expires_at=expires_at,
        extra_metadata={
            "private": True,
        },
        created_at=created_at,
        updated_at=updated_at,
    )

    class FakeRepository:
        async def get_active_employer_for_user(
            self,
            **kwargs,
        ):
            calls.append(
                ("employer", kwargs)
            )
            return employer

        async def list_employer_vacancies(
            self,
            **kwargs,
        ):
            calls.append(
                ("vacancies", kwargs)
            )
            return [vacancy]

    service = HrVacancyService(
        FakeRepository()
    )

    result = await service.list_vacancies_for_user(
        tenant_id=tenant_id,
        user_id=user_id,
        limit=20,
        offset=0,
    )

    assert result == (
        HrVacancyListItem(
            id=vacancy_id,
            profession_id=profession_id,
            country_id=country_id,
            city_id=city_id,
            title="Senior Auditor",
            description="Audit vacancy",
            salary_min=Decimal("2000.00"),
            salary_max=Decimal("3000.00"),
            currency="EUR",
            employment_type="full_time",
            work_format="remote",
            recruitment_mode="direct",
            status="published",
            published_at=published_at,
            expires_at=expires_at,
            created_at=created_at,
            updated_at=updated_at,
        ),
    )

    assert calls == [
        (
            "employer",
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
            },
        ),
        (
            "vacancies",
            {
                "tenant_id": tenant_id,
                "employer_id": employer_id,
                "limit": 20,
                "offset": 0,
            },
        ),
    ]

    assert "tenant_id" not in vars(result[0])
    assert "employer_id" not in vars(result[0])
    assert "extra_metadata" not in vars(
        result[0]
    )


@pytest.mark.asyncio
async def test_hr_vacancies_endpoint_uses_current_actor():
    from datetime import UTC, datetime
    from decimal import Decimal

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_hr_vacancy_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.hr_vacancies import (
        HrVacancyListItem,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    vacancy_id = uuid4()
    profession_id = uuid4()
    country_id = uuid4()
    city_id = uuid4()
    request_id = "hr-vacancies-request"
    calls = []

    published_at = datetime(
        2026,
        8,
        20,
        10,
        0,
        tzinfo=UTC,
    )
    expires_at = datetime(
        2026,
        9,
        20,
        10,
        0,
        tzinfo=UTC,
    )
    created_at = datetime(
        2026,
        8,
        19,
        9,
        0,
        tzinfo=UTC,
    )
    updated_at = published_at

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="employer",
        roles=("employer",),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(
            "hr.vacancies.read",
        ),
    )

    item = HrVacancyListItem(
        id=vacancy_id,
        profession_id=profession_id,
        country_id=country_id,
        city_id=city_id,
        title="Senior Auditor",
        description="Audit vacancy",
        salary_min=Decimal("2000.00"),
        salary_max=Decimal("3000.00"),
        currency="EUR",
        employment_type="full_time",
        work_format="remote",
        recruitment_mode="direct",
        status="published",
        published_at=published_at,
        expires_at=expires_at,
        created_at=created_at,
        updated_at=updated_at,
    )

    class FakeHrVacancyService:
        async def list_vacancies_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return (item,)

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_hr_vacancy_service
    ] = lambda: FakeHrVacancyService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/hr/vacancies",
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "limit": 21,
            "offset": 0,
        }
    ]

    assert response.json() == {
        "data": [
            {
                "id": str(vacancy_id),
                "profession_id": (
                    str(profession_id)
                ),
                "country_id": str(country_id),
                "city_id": str(city_id),
                "title": "Senior Auditor",
                "description": "Audit vacancy",
                "salary_min": "2000.00",
                "salary_max": "3000.00",
                "currency": "EUR",
                "employment_type": (
                    "full_time"
                ),
                "work_format": "remote",
                "recruitment_mode": "direct",
                "status": "published",
                "published_at": (
                    "2026-08-20T10:00:00Z"
                ),
                "expires_at": (
                    "2026-09-20T10:00:00Z"
                ),
                "created_at": (
                    "2026-08-19T09:00:00Z"
                ),
                "updated_at": (
                    "2026-08-20T10:00:00Z"
                ),
            }
        ],
        "meta": {
            "next_cursor": None,
            "has_more": False,
        },
        "request_id": request_id,
    }

    assert "tenant_id" not in response.text
    assert "employer_id" not in response.text
    assert "metadata" not in response.text


@pytest.mark.asyncio
async def test_hr_vacancies_require_read_permission():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_hr_vacancy_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    calls = []

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="employer",
        roles=("employer",),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(),
    )

    class ForbiddenHrVacancyService:
        async def list_vacancies_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            raise AssertionError(
                "HR service must not run "
                "without permission."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_hr_vacancy_service
    ] = lambda: ForbiddenHrVacancyService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/hr/vacancies"
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == (
        "permission_required"
    )
    assert calls == []


@pytest.mark.asyncio
async def test_hr_vacancies_require_authentication():
    import httpx

    from api.app import create_app

    application = create_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/hr/vacancies"
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == (
        "authentication_required"
    )


@pytest.mark.asyncio
async def test_hr_vacancies_hide_employer_details():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_hr_vacancy_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.hr_vacancies import (
        HrVacancyAccessError,
    )

    request_id = "hr-employer-missing"
    private_detail = (
        "foreign tenant employer identifier"
    )

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="employer",
        roles=("employer",),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(
            "hr.vacancies.read",
        ),
    )

    class MissingEmployerService:
        async def list_vacancies_for_user(
            self,
            **kwargs,
        ):
            raise HrVacancyAccessError(
                private_detail
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_hr_vacancy_service
    ] = lambda: MissingEmployerService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/hr/vacancies",
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "employer_not_found",
            "message": (
                "Active employer "
                "was not found."
            ),
            "request_id": request_id,
        },
    }
    assert private_detail not in response.text


def test_hr_vacancies_openapi_contract():
    from api.app import create_app

    schema = create_app().openapi()

    operation = schema["paths"][
        "/api/v1/hr/vacancies"
    ]["get"]

    assert operation["security"] == [
        {
            "BearerAuth": [],
        }
    ]

    parameters = {
        parameter["name"]: parameter
        for parameter in operation[
            "parameters"
        ]
    }
    assert {
        "limit",
        "cursor",
    } <= set(parameters)

    response_schema = (
        operation["responses"]["200"]
        ["content"]["application/json"]
        ["schema"]
    )
    assert response_schema == {
        "$ref": (
            "#/components/schemas/"
            "HrVacancyListResponse"
        )
    }

    components = schema[
        "components"
    ]["schemas"]

    properties = set(
        components[
            "HrVacancyItem"
        ]["properties"]
    )

    assert properties == {
        "id",
        "profession_id",
        "country_id",
        "city_id",
        "title",
        "description",
        "salary_min",
        "salary_max",
        "currency",
        "employment_type",
        "work_format",
        "recruitment_mode",
        "status",
        "published_at",
        "expires_at",
        "created_at",
        "updated_at",
    }

    assert not (
        {
            "tenant_id",
            "employer_id",
            "metadata",
            "email",
            "phone",
        }
        & properties
    )

