from construction_rate_limit_fixture import with_local_construction_rate_limit
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_api_request_rate_limit_service
from api.routes.company_profiles import get_company_profile_service


@pytest.mark.asyncio
async def test_con_c001_profile_get_allows_member_without_write_permission():
    calls = []

    class FakeProfileService:
        async def get_profile(self, *, actor):
            calls.append(actor.tenant_id)
            return None

    class FakeRateLimit:
        async def ensure_authenticated_user_allowed(self, **kwargs):
            return None

    application = with_local_construction_rate_limit(create_app())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application, raise_app_exceptions=False
        ),
        base_url="http://test",
    ) as client:
        unauthenticated = await client.get(
            "/api/v1/organization/company-profile"
        )

        actor = SimpleNamespace(
            user_id=uuid4(),
            tenant_id=uuid4(),
            status="active",
            permissions=(),
        )
        application.dependency_overrides[
            get_current_actor
        ] = lambda: actor
        application.dependency_overrides[
            get_api_request_rate_limit_service
        ] = lambda: FakeRateLimit()
        application.dependency_overrides[
            get_company_profile_service
        ] = lambda: FakeProfileService()
        authenticated = await client.get(
            "/api/v1/organization/company-profile"
        )

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200
    assert authenticated.json()["data"] is None
    assert calls == [actor.tenant_id]
