from construction_rate_limit_fixture import with_local_construction_rate_limit
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest


@pytest.mark.asyncio
async def test_identifier_get_allows_active_member_without_manage_permission():
    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_api_request_rate_limit_service
    from api.routes.company_profiles import get_company_identifier_service

    tenant_id = uuid4()
    user_id = uuid4()
    identifier_id = uuid4()
    profile_id = uuid4()
    now = datetime.now(UTC)
    actor = SimpleNamespace(
        user_id=user_id,
        tenant_id=tenant_id,
        status="active",
        permissions=(),
    )
    calls = []

    class FakeService:
        async def list_identifiers(self, *, actor):
            calls.append(actor)
            return [
                SimpleNamespace(
                    id=identifier_id,
                    tenant_company_profile_id=profile_id,
                    country_code="PT",
                    identifier_type="vat",
                    identifier_value="PT 123",
                    is_primary=True,
                    created_at=now,
                    updated_at=now,
                )
            ]

    class FakeRateLimit:
        async def ensure_authenticated_user_allowed(self, **kwargs):
            return None

    application = with_local_construction_rate_limit(create_app())
    path_url = "/api/v1/organization/company-profile/identifiers"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        unauthenticated = await client.get(path_url)

        application.dependency_overrides[
            get_current_actor
        ] = lambda: actor
        application.dependency_overrides[
            get_api_request_rate_limit_service
        ] = lambda: FakeRateLimit()
        application.dependency_overrides[
            get_company_identifier_service
        ] = lambda: FakeService()
        authenticated = await client.get(path_url)

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200, authenticated.text
    assert calls == [actor]
    assert authenticated.json()["data"] == [
        {
            "id": str(identifier_id),
            "country_code": "PT",
            "identifier_type": "vat",
            "identifier_value": "PT 123",
            "is_primary": True,
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "updated_at": now.isoformat().replace("+00:00", "Z"),
        }
    ]
    assert "identifier_value_normalized" not in authenticated.text


@pytest.mark.asyncio
async def test_identifier_post_uses_profile_manage_context():
    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_api_request_rate_limit_service
    from api.routes.company_profiles import (
        get_company_identifier_service,
        get_company_profile_write_context,
    )

    tenant_id = uuid4()
    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=tenant_id,
        status="active",
        permissions=("platform.organization.profile.manage",),
    )
    access_context = object()
    identifier_id = uuid4()
    now = datetime.now(UTC)
    calls = []

    class FakeService:
        async def create_identifier(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                id=identifier_id,
                country_code="PT",
                identifier_type="vat",
                identifier_value="PT 123",
                is_primary=True,
                created_at=now,
                updated_at=now,
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
        get_company_identifier_service
    ] = lambda: FakeService()
    application.dependency_overrides[
        get_company_profile_write_context
    ] = lambda: access_context

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/organization/company-profile/identifiers",
            headers={"X-Request-ID": "identifier-create"},
            json={
                "country_code": " pt ",
                "identifier_type": " VAT ",
                "identifier_value": "PT 123",
                "is_primary": True,
            },
        )

    assert response.status_code == 201, response.text
    assert len(calls) == 1
    assert calls[0]["actor"] is actor
    assert calls[0]["construction_access_context"] is access_context
    assert calls[0]["country_code"] == " pt "
    assert calls[0]["identifier_type"] == " VAT "
    assert calls[0]["identifier_value"] == "PT 123"
    assert calls[0]["is_primary"] is True
    assert calls[0]["trace_id"]
    assert response.json()["data"]["id"] == str(identifier_id)
    assert "identifier_value_normalized" not in response.text


@pytest.mark.asyncio
async def test_identifier_post_denies_without_profile_manage(monkeypatch):
    import importlib

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_api_request_rate_limit_service
    from services.platform_company_profile_access import (
        CompanyProfilePermissionDeniedError,
    )

    route_module = importlib.import_module("api.routes.company_profiles")
    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        status="active",
        permissions=(),
    )

    async def deny_write_context(*, actor):
        raise CompanyProfilePermissionDeniedError(
            "Private permission details."
        )

    class FakeService:
        async def create_identifier(self, **kwargs):
            pytest.fail("Unauthorized request reached identifier service")

    class FakeRateLimit:
        async def ensure_authenticated_user_allowed(self, **kwargs):
            return None

    monkeypatch.setattr(
        route_module,
        "load_company_profile_write_context",
        deny_write_context,
    )
    application = with_local_construction_rate_limit(create_app())
    application.dependency_overrides[get_current_actor] = lambda: actor
    application.dependency_overrides[
        get_api_request_rate_limit_service
    ] = lambda: FakeRateLimit()
    application.dependency_overrides[
        route_module.get_company_identifier_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/organization/company-profile/identifiers",
            json={
                "country_code": "PT",
                "identifier_type": "vat",
                "identifier_value": "PT 123",
            },
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert "Private permission details." not in response.text


@pytest.mark.asyncio
async def test_identifier_post_returns_409_for_unique_conflict():
    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_api_request_rate_limit_service
    from api.routes.company_profiles import (
        get_company_identifier_service,
        get_company_profile_write_context,
    )
    from services.platform_company_identifiers import (
        CompanyIdentifierConflictError,
    )

    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        status="active",
        permissions=("platform.organization.profile.manage",),
    )

    class FakeService:
        async def create_identifier(self, **kwargs):
            raise CompanyIdentifierConflictError(
                "private database index details"
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
        get_company_identifier_service
    ] = lambda: FakeService()
    application.dependency_overrides[
        get_company_profile_write_context
    ] = lambda: object()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/organization/company-profile/identifiers",
            json={
                "country_code": "PT",
                "identifier_type": "vat",
                "identifier_value": "PT 123",
                "is_primary": True,
            },
        )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "company_identifier_conflict"
    assert "private database index details" not in response.text


@pytest.mark.asyncio
async def test_identifier_replace_uses_platform_write_context():
    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_api_request_rate_limit_service
    from api.routes.company_profiles import (
        get_company_identifier_service,
        get_company_profile_write_context,
    )

    old_id = uuid4()
    new_id = uuid4()
    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        status="active",
        permissions=("platform.organization.profile.manage",),
    )
    access_context = object()
    calls = []
    now = datetime.now(UTC)

    class FakeService:
        async def replace_identifier(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                id=new_id,
                country_code="PT",
                identifier_type="vat",
                identifier_value="PT 456",
                is_primary=True,
                created_at=now,
                updated_at=now,
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
        get_company_identifier_service
    ] = lambda: FakeService()
    application.dependency_overrides[
        get_company_profile_write_context
    ] = lambda: access_context

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/v1/organization/company-profile/"
            f"identifiers/{old_id}/replace",
            json={
                "country_code": "PT",
                "identifier_type": "vat",
                "identifier_value": "PT 456",
                "is_primary": True,
            },
        )

    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert calls[0]["actor"] is actor
    assert calls[0]["construction_access_context"] is access_context
    assert calls[0]["identifier_id"] == old_id
    assert calls[0]["identifier_value"] == "PT 456"
    assert calls[0]["is_primary"] is True
    assert calls[0]["trace_id"]
    assert response.json()["data"]["id"] == str(new_id)


@pytest.mark.asyncio
async def test_identifier_replace_denies_without_profile_manage(monkeypatch):
    import importlib

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_api_request_rate_limit_service
    from services.platform_company_profile_access import (
        CompanyProfilePermissionDeniedError,
    )

    route_module = importlib.import_module("api.routes.company_profiles")
    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        status="active",
        permissions=(),
    )

    async def deny_write_context(*, actor):
        raise CompanyProfilePermissionDeniedError(
            "private permission details"
        )

    class FakeService:
        async def replace_identifier(self, **kwargs):
            pytest.fail("Unauthorized request reached identifier service")

    class FakeRateLimit:
        async def ensure_authenticated_user_allowed(self, **kwargs):
            return None

    monkeypatch.setattr(
        route_module,
        "load_company_profile_write_context",
        deny_write_context,
    )
    application = with_local_construction_rate_limit(create_app())
    application.dependency_overrides[get_current_actor] = lambda: actor
    application.dependency_overrides[
        get_api_request_rate_limit_service
    ] = lambda: FakeRateLimit()
    application.dependency_overrides[
        route_module.get_company_identifier_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/v1/organization/company-profile/"
            f"identifiers/{uuid4()}/replace",
            json={
                "country_code": "PT",
                "identifier_type": "vat",
                "identifier_value": "PT 456",
                "is_primary": True,
            },
        )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"
    assert "private permission details" not in response.text


@pytest.mark.asyncio
async def test_identifier_replace_returns_409_for_unique_conflict():
    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_api_request_rate_limit_service
    from api.routes.company_profiles import (
        get_company_identifier_service,
        get_company_profile_write_context,
    )
    from services.platform_company_identifiers import (
        CompanyIdentifierConflictError,
    )

    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        status="active",
        permissions=("platform.organization.profile.manage",),
    )

    class FakeService:
        async def replace_identifier(self, **kwargs):
            raise CompanyIdentifierConflictError(
                "private database index details"
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
        get_company_identifier_service
    ] = lambda: FakeService()
    application.dependency_overrides[
        get_company_profile_write_context
    ] = lambda: object()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/v1/organization/company-profile/"
            f"identifiers/{uuid4()}/replace",
            json={
                "country_code": "PT",
                "identifier_type": "vat",
                "identifier_value": "PT 456",
                "is_primary": True,
            },
        )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "company_identifier_conflict"
    assert "private database index details" not in response.text


@pytest.mark.asyncio
async def test_identifier_delete_uses_platform_write_context():
    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_api_request_rate_limit_service
    from api.routes.company_profiles import (
        get_company_identifier_service,
        get_company_profile_write_context,
    )

    identifier_id = uuid4()
    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        status="active",
        permissions=("platform.organization.profile.manage",),
    )
    access_context = object()
    calls = []

    class FakeService:
        async def delete_identifier(self, **kwargs):
            calls.append(kwargs)

    class FakeRateLimit:
        async def ensure_authenticated_user_allowed(self, **kwargs):
            return None

    application = with_local_construction_rate_limit(create_app())
    application.dependency_overrides[get_current_actor] = lambda: actor
    application.dependency_overrides[
        get_api_request_rate_limit_service
    ] = lambda: FakeRateLimit()
    application.dependency_overrides[
        get_company_identifier_service
    ] = lambda: FakeService()
    application.dependency_overrides[
        get_company_profile_write_context
    ] = lambda: access_context

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.delete(
            f"/api/v1/organization/company-profile/"
            f"identifiers/{identifier_id}"
        )

    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert calls[0]["actor"] is actor
    assert calls[0]["construction_access_context"] is access_context
    assert calls[0]["identifier_id"] == identifier_id
    assert calls[0]["trace_id"]
    assert response.json()["data"] == {
        "id": str(identifier_id),
        "deleted": True,
    }


@pytest.mark.asyncio
async def test_identifier_delete_denies_without_profile_manage(monkeypatch):
    import importlib

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_api_request_rate_limit_service
    from services.platform_company_profile_access import (
        CompanyProfilePermissionDeniedError,
    )

    route_module = importlib.import_module("api.routes.company_profiles")
    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        status="active",
        permissions=(),
    )

    async def deny_write_context(*, actor):
        raise CompanyProfilePermissionDeniedError(
            "private permission details"
        )

    class FakeService:
        async def delete_identifier(self, **kwargs):
            pytest.fail("Unauthorized request reached identifier service")

    class FakeRateLimit:
        async def ensure_authenticated_user_allowed(self, **kwargs):
            return None

    monkeypatch.setattr(
        route_module,
        "load_company_profile_write_context",
        deny_write_context,
    )
    application = with_local_construction_rate_limit(create_app())
    application.dependency_overrides[get_current_actor] = lambda: actor
    application.dependency_overrides[
        get_api_request_rate_limit_service
    ] = lambda: FakeRateLimit()
    application.dependency_overrides[
        route_module.get_company_identifier_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.delete(
            f"/api/v1/organization/company-profile/"
            f"identifiers/{uuid4()}"
        )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"
    assert "private permission details" not in response.text


@pytest.mark.parametrize("operation", ("replace", "delete"))
@pytest.mark.asyncio
async def test_identifier_change_hides_missing_or_foreign_record(operation):
    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_api_request_rate_limit_service
    from api.routes.company_profiles import (
        get_company_identifier_service,
        get_company_profile_write_context,
    )
    from services.platform_company_identifiers import (
        CompanyIdentifierNotFoundError,
    )

    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=uuid4(),
        status="active",
        permissions=("platform.organization.profile.manage",),
    )

    class FakeService:
        async def replace_identifier(self, **kwargs):
            raise CompanyIdentifierNotFoundError(
                "private tenant details"
            )

        async def delete_identifier(self, **kwargs):
            raise CompanyIdentifierNotFoundError(
                "private tenant details"
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
        get_company_identifier_service
    ] = lambda: FakeService()
    application.dependency_overrides[
        get_company_profile_write_context
    ] = lambda: object()

    url = (
        f"/api/v1/organization/company-profile/"
        f"identifiers/{uuid4()}"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        if operation == "replace":
            response = await client.post(
                url + "/replace",
                json={
                    "country_code": "PT",
                    "identifier_type": "vat",
                    "identifier_value": "PT 456",
                    "is_primary": True,
                },
            )
        else:
            response = await client.delete(url)

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "company_identifier_not_found"
    assert "private tenant details" not in response.text


@pytest.mark.parametrize("operation", ("create", "replace", "delete"))
@pytest.mark.asyncio
async def test_identifier_http_requires_existing_company_profile(
    operation,
):
    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_api_request_rate_limit_service
    from api.routes.company_profiles import (
        get_company_identifier_service,
        get_company_profile_write_context,
    )
    from services.platform_company_identifiers import (
        CompanyIdentifierProfileNotFoundError,
    )

    async def missing_profile(**kwargs):
        raise CompanyIdentifierProfileNotFoundError(
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
        get_company_identifier_service
    ] = lambda: SimpleNamespace(
        create_identifier=missing_profile,
        replace_identifier=missing_profile,
        delete_identifier=missing_profile,
    )

    base = "/api/v1/organization/company-profile/identifiers"
    identifier_id = uuid4()
    payload = {
        "country_code": "PT",
        "identifier_type": "vat",
        "identifier_value": "PT 123",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=app, raise_app_exceptions=False
        ),
        base_url="http://testserver",
    ) as client:
        if operation == "create":
            response = await client.post(base, json=payload)
        elif operation == "replace":
            response = await client.post(
                f"{base}/{identifier_id}/replace", json=payload
            )
        else:
            response = await client.delete(f"{base}/{identifier_id}")

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == (
        "company_profile_not_found"
    )
    assert "private profile details" not in response.text
