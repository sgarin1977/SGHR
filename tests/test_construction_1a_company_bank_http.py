from construction_rate_limit_fixture import with_local_construction_rate_limit
import pytest



def test_company_bank_account_platform_routes_match_confirmed_operations():
    from api.routes.company_profiles import router

    routes = {
        (route.path, method)
        for route in router.routes
        if getattr(route, "path", None)
        for method in getattr(route, "methods", ())
    }
    base = "/organization/company-profile/bank-accounts"
    expected = {
        (base, "GET"),
        (base, "POST"),
        (base + "/{account_id}", "PATCH"),
        (base + "/{account_id}/deactivate", "POST"),
    }

    assert expected <= routes, sorted(expected - routes)


@pytest.mark.asyncio
async def test_company_bank_account_http_dispatches_confirmed_operations():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_api_request_rate_limit_service
    from api.routes.company_profiles import (
        get_company_bank_account_service,
        get_company_profile_write_context,
    )

    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        status="active",
        permissions=("platform.organization.profile.manage",),
    )
    access_context = object()
    account_id = uuid4()
    now = datetime.now(UTC)
    calls = []

    def account(**overrides):
        fields = {
            "id": account_id,
            "label": "Main",
            "account_holder_name": "Example Company",
            "bank_name": "Example Bank",
            "country_code": "PT",
            "iban": None,
            "bic_swift": None,
            "account_number": "123456",
            "currency": "EUR",
            "is_primary": True,
            "is_active": True,
            "show_in_documents": False,
            "created_at": now,
            "updated_at": now,
            "row_version": 1,
        }
        fields.update(overrides)
        return SimpleNamespace(**fields)

    class FakeService:
        async def list_accounts(self, *, actor):
            calls.append(("list", actor))
            return [account()]

        async def create_account(self, **kwargs):
            calls.append(("create", kwargs))
            return account()

        async def update_account(self, **kwargs):
            calls.append(("update", kwargs))
            return account(label="Updated", row_version=2)

        async def deactivate_account(self, **kwargs):
            calls.append(("deactivate", kwargs))
            return account(is_active=False, row_version=3)

    class FakeRateLimit:
        async def ensure_authenticated_user_allowed(self, **kwargs):
            return None

    application = with_local_construction_rate_limit(create_app())
    application.dependency_overrides[get_current_actor] = lambda: actor
    application.dependency_overrides[
        get_api_request_rate_limit_service
    ] = lambda: FakeRateLimit()
    application.dependency_overrides[
        get_company_bank_account_service
    ] = lambda: FakeService()
    application.dependency_overrides[
        get_company_profile_write_context
    ] = lambda: access_context

    base = "/api/v1/organization/company-profile/bank-accounts"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        listed = await client.get(base)
        created = await client.post(
            base,
            json={
                "account_holder_name": "Example Company",
                "currency": "EUR",
                "account_number": "123456",
                "is_primary": True,
            },
        )
        updated = await client.patch(
            f"{base}/{account_id}",
            json={"expected_row_version": 1, "label": "Updated"},
        )
        deactivated = await client.post(
            f"{base}/{account_id}/deactivate",
            json={"expected_row_version": 2},
        )

    assert [r.status_code for r in (
        listed, created, updated, deactivated
    )] == [200, 201, 200, 200]
    assert [name for name, _ in calls] == [
        "list", "create", "update", "deactivate"
    ]
    assert calls[0][1] is actor
    for _, kwargs in calls[1:]:
        assert kwargs["actor"] is actor
        assert kwargs["construction_access_context"] is access_context
        assert kwargs["trace_id"]
    assert calls[2][1]["account_id"] == account_id
    assert calls[2][1]["expected_row_version"] == 1
    assert calls[2][1]["changes"] == {"label": "Updated"}
    assert calls[3][1]["expected_row_version"] == 2
    assert listed.json()["data"][0]["id"] == str(account_id)
    assert deactivated.json()["data"]["is_active"] is False


@pytest.mark.parametrize(
    "operation,expected_status,expected_code",
    (
        ("create", 403, "forbidden"),
        ("update", 403, "forbidden"),
        ("deactivate", 403, "forbidden"),
        ("update", 409, "ROW_VERSION_CONFLICT"),
        ("deactivate", 409, "ROW_VERSION_CONFLICT"),
    ),
)
@pytest.mark.asyncio
async def test_company_bank_account_http_enforces_write_guards(
    monkeypatch, operation, expected_status, expected_code,
):
    import importlib
    from types import SimpleNamespace
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_api_request_rate_limit_service
    from services.platform_company_bank_accounts import (
        CompanyBankAccountRowVersionConflictError,
    )
    from services.platform_company_profile_access import (
        CompanyProfilePermissionDeniedError,
    )

    route_module = importlib.import_module("api.routes.company_profiles")
    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        status="active",
        permissions=(
            ("platform.organization.profile.manage",)
            if expected_status == 409 else ()
        ),
    )

    async def deny_write_context(*, actor):
        raise CompanyProfilePermissionDeniedError(
            "private permission details"
        )

    async def fake_mutation(**kwargs):
        if expected_status == 403:
            pytest.fail("Unauthorized request reached bank service")
        raise CompanyBankAccountRowVersionConflictError(
            "private row-version details"
        )

    class FakeRateLimit:
        async def ensure_authenticated_user_allowed(self, **kwargs):
            return None

    application = with_local_construction_rate_limit(create_app())
    application.dependency_overrides[get_current_actor] = lambda: actor
    application.dependency_overrides[
        get_api_request_rate_limit_service
    ] = lambda: FakeRateLimit()
    application.dependency_overrides[
        route_module.get_company_bank_account_service
    ] = lambda: SimpleNamespace(
        create_account=fake_mutation,
        update_account=fake_mutation,
        deactivate_account=fake_mutation,
    )
    if expected_status == 403:
        monkeypatch.setattr(
            route_module,
            "load_company_profile_write_context",
            deny_write_context,
        )
    else:
        application.dependency_overrides[
            route_module.get_company_profile_write_context
        ] = lambda: object()

    base = "/api/v1/organization/company-profile/bank-accounts"
    account_id = uuid4()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        if operation == "create":
            response = await client.post(
                base,
                json={
                    "account_holder_name": "Example Company",
                    "currency": "EUR",
                    "account_number": "123456",
                },
            )
        elif operation == "update":
            response = await client.patch(
                f"{base}/{account_id}",
                json={"expected_row_version": 1, "label": "Updated"},
            )
        else:
            response = await client.post(
                f"{base}/{account_id}/deactivate",
                json={"expected_row_version": 1},
            )

    assert response.status_code == expected_status, response.text
    assert response.json()["error"]["code"] == expected_code
    assert "private permission details" not in response.text
    assert "private row-version details" not in response.text


@pytest.mark.parametrize("operation", ("create", "update"))
@pytest.mark.asyncio
async def test_company_bank_account_http_maps_primary_conflict(operation):
    from types import SimpleNamespace
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_api_request_rate_limit_service
    from api.routes.company_profiles import (
        get_company_bank_account_service,
        get_company_profile_write_context,
    )
    from services.platform_company_bank_accounts import (
        CompanyBankAccountConflictError,
    )

    async def raise_conflict(**kwargs):
        raise CompanyBankAccountConflictError("private bank details")

    class FakeRateLimit:
        async def ensure_authenticated_user_allowed(self, **kwargs):
            return None

    app = with_local_construction_rate_limit(create_app())
    app.dependency_overrides[get_current_actor] = lambda: SimpleNamespace(
        user_id=uuid4(), tenant_id=uuid4(), status="active"
    )
    app.dependency_overrides[
        get_api_request_rate_limit_service
    ] = lambda: FakeRateLimit()
    app.dependency_overrides[
        get_company_profile_write_context
    ] = lambda: object()
    app.dependency_overrides[
        get_company_bank_account_service
    ] = lambda: SimpleNamespace(
        create_account=raise_conflict,
        update_account=raise_conflict,
    )

    base = "/api/v1/organization/company-profile/bank-accounts"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=app, raise_app_exceptions=False
        ),
        base_url="http://testserver",
    ) as client:
        if operation == "create":
            response = await client.post(
                base,
                json={
                    "account_holder_name": "Example Company",
                    "currency": "EUR",
                    "account_number": "123456",
                    "is_primary": True,
                },
            )
        else:
            response = await client.patch(
                f"{base}/{uuid4()}",
                json={
                    "expected_row_version": 1,
                    "is_primary": True,
                },
            )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == (
        "company_bank_account_conflict"
    )
    assert "private bank details" not in response.text


@pytest.mark.parametrize("operation", ("update", "deactivate"))
@pytest.mark.asyncio
async def test_company_bank_account_http_hides_missing_or_foreign_record(
    operation,
):
    from types import SimpleNamespace
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_api_request_rate_limit_service
    from api.routes.company_profiles import (
        get_company_bank_account_service,
        get_company_profile_write_context,
    )
    from services.platform_company_bank_accounts import (
        CompanyBankAccountNotFoundError,
    )

    async def not_found(**kwargs):
        raise CompanyBankAccountNotFoundError(
            "private tenant and account details"
        )

    class FakeRateLimit:
        async def ensure_authenticated_user_allowed(self, **kwargs):
            return None

    app = with_local_construction_rate_limit(create_app())
    app.dependency_overrides[get_current_actor] = lambda: SimpleNamespace(
        user_id=uuid4(), tenant_id=uuid4(), status="active"
    )
    app.dependency_overrides[
        get_api_request_rate_limit_service
    ] = lambda: FakeRateLimit()
    app.dependency_overrides[
        get_company_profile_write_context
    ] = lambda: object()
    app.dependency_overrides[
        get_company_bank_account_service
    ] = lambda: SimpleNamespace(
        update_account=not_found,
        deactivate_account=not_found,
    )

    base = "/api/v1/organization/company-profile/bank-accounts"
    account_id = uuid4()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=app, raise_app_exceptions=False
        ),
        base_url="http://testserver",
    ) as client:
        if operation == "update":
            response = await client.patch(
                f"{base}/{account_id}",
                json={"expected_row_version": 1, "label": "Updated"},
            )
        else:
            response = await client.post(
                f"{base}/{account_id}/deactivate",
                json={"expected_row_version": 1},
            )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == (
        "company_bank_account_not_found"
    )
    assert "private tenant and account details" not in response.text


@pytest.mark.parametrize("operation", ("create", "update", "deactivate"))
@pytest.mark.asyncio
async def test_company_bank_account_http_requires_existing_profile(
    operation,
):
    from types import SimpleNamespace
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_api_request_rate_limit_service
    from api.routes.company_profiles import (
        get_company_bank_account_service,
        get_company_profile_write_context,
    )
    from services.platform_company_bank_accounts import (
        CompanyBankAccountProfileNotFoundError,
    )

    async def missing_profile(**kwargs):
        raise CompanyBankAccountProfileNotFoundError(
            "private profile details"
        )

    class FakeRateLimit:
        async def ensure_authenticated_user_allowed(self, **kwargs):
            return None

    app = with_local_construction_rate_limit(create_app())
    app.dependency_overrides[get_current_actor] = lambda: SimpleNamespace(
        user_id=uuid4(), tenant_id=uuid4(), status="active"
    )
    app.dependency_overrides[
        get_api_request_rate_limit_service
    ] = lambda: FakeRateLimit()
    app.dependency_overrides[
        get_company_profile_write_context
    ] = lambda: object()
    app.dependency_overrides[
        get_company_bank_account_service
    ] = lambda: SimpleNamespace(
        create_account=missing_profile,
        update_account=missing_profile,
        deactivate_account=missing_profile,
    )

    base = "/api/v1/organization/company-profile/bank-accounts"
    account_id = uuid4()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=app, raise_app_exceptions=False
        ),
        base_url="http://testserver",
    ) as client:
        if operation == "create":
            response = await client.post(
                base,
                json={
                    "account_holder_name": "Example Company",
                    "currency": "EUR",
                    "account_number": "123456",
                },
            )
        elif operation == "update":
            response = await client.patch(
                f"{base}/{account_id}",
                json={"expected_row_version": 1, "label": "Updated"},
            )
        else:
            response = await client.post(
                f"{base}/{account_id}/deactivate",
                json={"expected_row_version": 1},
            )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == (
        "company_profile_not_found"
    )
    assert "private profile details" not in response.text
