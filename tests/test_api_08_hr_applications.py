import pytest

def test_hr_seeker_and_application_models_match_database():
    from database.models import Application, Seeker

    assert Seeker.__tablename__ == "seekers"
    assert Application.__tablename__ == "applications"

    seeker_columns = {
        column.name: column
        for column in Seeker.__table__.columns
    }
    application_columns = {
        column.name: column
        for column in Application.__table__.columns
    }

    assert set(seeker_columns) == {
        "id",
        "tenant_id",
        "user_id",
        "profession_id",
        "country_id",
        "city_id",
        "display_name",
        "summary",
        "salary_expectation_min",
        "salary_expectation_max",
        "currency",
        "status",
        "metadata",
        "created_at",
        "updated_at",
    }

    assert set(application_columns) == {
        "id",
        "tenant_id",
        "vacancy_id",
        "seeker_id",
        "message",
        "status",
        "metadata",
        "created_at",
        "updated_at",
    }

    seeker_foreign_keys = {
        (column.name, foreign_key.target_fullname)
        for column in seeker_columns.values()
        for foreign_key in column.foreign_keys
    }
    application_foreign_keys = {
        (column.name, foreign_key.target_fullname)
        for column in application_columns.values()
        for foreign_key in column.foreign_keys
    }

    assert seeker_foreign_keys == {
        ("tenant_id", "tenants.id"),
        ("user_id", "users.id"),
        ("profession_id", "professions.id"),
        ("country_id", "countries.id"),
        ("city_id", "cities.id"),
    }

    assert application_foreign_keys == {
        ("tenant_id", "tenants.id"),
        ("vacancy_id", "vacancies.id"),
        ("seeker_id", "seekers.id"),
    }

    unique_columns = {
        tuple(constraint.columns.keys())
        for constraint in Application.__table__.constraints
        if constraint.__class__.__name__
        == "UniqueConstraint"
    }

    assert ("vacancy_id", "seeker_id") in unique_columns


@pytest.mark.asyncio
async def test_hr_repository_resolves_seeker_by_actor_scope():
    from types import SimpleNamespace
    from uuid import uuid4

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
        repository.get_active_seeker_for_user(
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
        "seekers.tenant_id",
        "seekers.user_id",
        "seekers.status = 'active'",
    )

    for value in required_scope:
        assert value in normalized_sql


@pytest.mark.asyncio
async def test_hr_repository_lists_applications_by_tenant_and_seeker():
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.hr import (
        HrRepository,
    )

    tenant_id = uuid4()
    seeker_id = uuid4()
    expected = [
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(id=uuid4()),
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
        repository.list_seeker_applications(
            tenant_id=tenant_id,
            seeker_id=seeker_id,
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
        "applications.tenant_id",
        "applications.seeker_id",
        "applications.created_at desc",
        "limit 20",
        "offset 0",
    )

    for value in required_scope:
        assert value in normalized_sql


@pytest.mark.asyncio
async def test_hr_application_service_uses_actor_and_maps_safe_views():
    from dataclasses import asdict
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.hr_applications import (
        HrApplicationService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    seeker_id = uuid4()
    application_id = uuid4()
    vacancy_id = uuid4()
    created_at = datetime(
        2026,
        8,
        31,
        10,
        0,
        tzinfo=UTC,
    )
    updated_at = datetime(
        2026,
        8,
        31,
        11,
        0,
        tzinfo=UTC,
    )
    calls = []

    class FakeRepository:
        async def get_active_seeker_for_user(
            self,
            *,
            tenant_id,
            user_id,
        ):
            calls.append(
                (
                    "resolve_seeker",
                    tenant_id,
                    user_id,
                )
            )
            return SimpleNamespace(
                id=seeker_id,
            )

        async def list_seeker_applications(
            self,
            *,
            tenant_id,
            seeker_id,
            limit,
            offset,
        ):
            calls.append(
                (
                    "list_applications",
                    tenant_id,
                    seeker_id,
                    limit,
                    offset,
                )
            )
            return [
                SimpleNamespace(
                    id=application_id,
                    tenant_id=tenant_id,
                    vacancy_id=vacancy_id,
                    seeker_id=seeker_id,
                    message="Application message",
                    status="new",
                    extra_metadata={
                        "private": True,
                    },
                    created_at=created_at,
                    updated_at=updated_at,
                )
            ]

    service = HrApplicationService(
        FakeRepository()
    )

    result = await service.list_applications_for_user(
        tenant_id=tenant_id,
        user_id=user_id,
        limit=20,
        offset=0,
    )

    assert calls == [
        (
            "resolve_seeker",
            tenant_id,
            user_id,
        ),
        (
            "list_applications",
            tenant_id,
            seeker_id,
            20,
            0,
        ),
    ]
    assert len(result) == 1

    assert asdict(result[0]) == {
        "id": application_id,
        "vacancy_id": vacancy_id,
        "message": "Application message",
        "status": "new",
        "created_at": created_at,
        "updated_at": updated_at,
    }


@pytest.mark.asyncio
async def test_hr_applications_endpoint_uses_current_actor():
    from datetime import UTC, datetime
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_hr_application_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.hr_applications import (
        HrApplicationListItem,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    application_id = uuid4()
    vacancy_id = uuid4()
    request_id = "hr-applications-request"
    calls = []

    created_at = datetime(
        2026,
        8,
        31,
        10,
        0,
        tzinfo=UTC,
    )
    updated_at = datetime(
        2026,
        8,
        31,
        11,
        0,
        tzinfo=UTC,
    )

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="seeker",
        roles=("seeker",),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(
            "hr.applications.read",
        ),
    )

    item = HrApplicationListItem(
        id=application_id,
        vacancy_id=vacancy_id,
        message="Application message",
        status="new",
        created_at=created_at,
        updated_at=updated_at,
    )

    class FakeHrApplicationService:
        async def list_applications_for_user(
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
        get_hr_application_service
    ] = lambda: FakeHrApplicationService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/hr/applications",
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

    body = response.json()

    assert body["request_id"] == request_id
    assert body["meta"] == {
        "next_cursor": None,
        "has_more": False,
    }
    assert body["data"] == [
        {
            "id": str(application_id),
            "vacancy_id": str(vacancy_id),
            "message": "Application message",
            "status": "new",
            "created_at": (
                "2026-08-31T10:00:00Z"
            ),
            "updated_at": (
                "2026-08-31T11:00:00Z"
            ),
        }
    ]

    assert "tenant_id" not in body["data"][0]
    assert "seeker_id" not in body["data"][0]
    assert "metadata" not in body["data"][0]


@pytest.mark.asyncio
async def test_hr_applications_require_authentication():
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
            "/api/v1/hr/applications"
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == (
        "authentication_required"
    )


@pytest.mark.asyncio
async def test_hr_applications_require_read_permission():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_hr_application_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="seeker",
        roles=("seeker",),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(),
    )

    class ForbiddenService:
        async def list_applications_for_user(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Service must not be called "
                "without permission."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_hr_application_service
    ] = lambda: ForbiddenService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/hr/applications"
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == (
        "permission_required"
    )


@pytest.mark.asyncio
async def test_hr_applications_hide_missing_seeker_details():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_hr_application_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.hr_applications import (
        HrApplicationAccessError,
    )

    request_id = "missing-seeker-request"

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="seeker",
        roles=("seeker",),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(
            "hr.applications.read",
        ),
    )

    class MissingSeekerService:
        async def list_applications_for_user(
            self,
            **kwargs,
        ):
            raise HrApplicationAccessError(
                "Sensitive database details."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_hr_application_service
    ] = lambda: MissingSeekerService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/hr/applications",
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "seeker_not_found",
            "message": (
                "Active seeker was not found."
            ),
            "request_id": request_id,
        }
    }

    assert (
        "Sensitive database details"
        not in response.text
    )


def test_hr_applications_openapi_contract():
    from api.app import create_app

    schema = create_app().openapi()

    operation = schema["paths"][
        "/api/v1/hr/applications"
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
            "HrApplicationListResponse"
        )
    }

    components = schema[
        "components"
    ]["schemas"]

    properties = set(
        components[
            "HrApplicationItem"
        ]["properties"]
    )

    assert properties == {
        "id",
        "vacancy_id",
        "message",
        "status",
        "created_at",
        "updated_at",
    }

    assert not (
        {
            "tenant_id",
            "seeker_id",
            "metadata",
            "email",
            "phone",
        }
        & properties
    )

