from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.dialects import postgresql

from api.app import create_app
from api.platform_bot_context import (
    PlatformBotApiContext,
    bind_platform_bot_api_context,
)
from api.routes import platform_bot_entry as routes
from database.repositories.platform_organizations import (
    PlatformOrganizationsRepository,
)
from services.api_rate_limits import (
    ApiRequestRateLimitExceededError,
)

from test_platform_bot_authentication import case


def _supply_dependency(value):
    def dependency():
        return value
    return dependency


@pytest.fixture
def entry(case):
    c = case
    c.app = create_app()
    c.users.get_by_id = AsyncMock(return_value=c.user)
    c.rates = SimpleNamespace(
        ensure_platform_user_allowed=AsyncMock()
    )
    c.organizations = SimpleNamespace(
        list_organizations=AsyncMock(
            return_value=[
                {
                    "id": c.tenant,
                    "name": "Organization",
                }
            ]
        )
    )

    for dependency, value in (
        (routes.get_platform_bot_users, c.users),
        (
            routes.get_platform_bot_organizations,
            c.organizations,
        ),
        (routes.get_platform_bot_rates, c.rates),
        (
            routes.get_platform_bot_authentication,
            c.service,
        ),
    ):
        c.app.dependency_overrides[dependency] = _supply_dependency(value)

    c.context = PlatformBotApiContext(
        verifier=c.verifier,
        proof=c.verifier.proof,
    )
    c.bound_app = bind_platform_bot_api_context(
        c.app, context=c.context
    )
    return c


async def request(c, method, path, *, trusted=True, **kwargs):
    app = c.bound_app if trusted else c.app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=app,
            raise_app_exceptions=False,
        ),
        base_url="http://127.0.0.1",
    ) as client:
        return await client.request(
            method, path, **kwargs
        )


@pytest.mark.asyncio
async def test_internal_organization_read_uses_canonical_identity(entry):
    c = entry
    response = await request(
        c, "GET",
        "/api/v1/platform/bot/organizations",
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"] == [
        {
            "id": str(c.tenant),
            "name": "Organization",
        }
    ]
    assert body["meta"]["pagination"]["next_cursor"] is None
    c.organizations.list_organizations.assert_awaited_once_with(
        user_id=c.user_id, after=None, limit=10
    )
    c.rates.ensure_platform_user_allowed.assert_awaited_once_with(
        user_id=c.user_id
    )
    assert response.headers["Cache-Control"] == "private, no-store"
    assert not c.stored


@pytest.mark.asyncio
async def test_selected_organization_session_uses_existing_platform_tokens(entry):
    c = entry
    response = await request(
        c, "POST",
        "/api/v1/platform/bot/session",
        json={"tenant_id": str(c.tenant)},
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    claims = c.codec.decode_access_token(
        body["access_token"]
    )
    assert (
        claims.user_id,
        claims.tenant_id,
    ) == (c.user_id, c.tenant)
    assert body["token_type"] == "bearer"
    assert len(c.stored) == 2
    assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize("path,method", [
    ("/api/v1/platform/bot/organizations", "GET"),
    ("/api/v1/platform/bot/session", "POST"),
])
async def test_ordinary_http_cannot_supply_the_internal_proof(entry, path, method):
    c = entry
    kwargs = {
        "headers": {
            "X-Telegram-User-ID": "123",
            "X-Platform-Bot-Proof": "trusted",
        }
    }
    if method == "POST":
        kwargs["json"] = {
            "tenant_id": str(c.tenant)
        }
    response = await request(
        c, method, path,
        trusted=False, **kwargs
    )
    assert response.status_code == 401, response.text
    c.users.get_by_platform_account.assert_not_called()
    assert not c.stored


@pytest.mark.asyncio
async def test_bot_transport_proof_does_not_replace_business_bearer_auth(entry):
    c = entry
    response = await request(
        c, "GET",
        "/api/v1/construction/crm/leads",
    )
    assert response.status_code == 401, response.text
    assert not c.stored


@pytest.mark.asyncio
async def test_foreign_tenant_selector_does_not_create_authority(entry):
    c = entry
    response = await request(
        c, "POST",
        "/api/v1/platform/bot/session",
        json={"tenant_id": str(uuid4())},
    )
    assert response.status_code == 403, response.text
    assert not c.stored
    c.sessions.create_session.assert_not_called()


@pytest.mark.asyncio
async def test_caller_cannot_set_user_roles_or_permissions(entry):
    c = entry
    response = await request(
        c, "POST",
        "/api/v1/platform/bot/session",
        json={
            "tenant_id": str(c.tenant),
            "user_id": str(c.user_id),
            "roles": ["OWNER"],
        },
    )
    assert response.status_code == 422, response.text
    c.sessions.create_session.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "missing", "unlinked", "blocked",
])
async def test_unavailable_identity_has_no_organization_output(entry, failure):
    c = entry
    if failure == "missing":
        c.users.get_by_platform_account.return_value = None
    elif failure == "unlinked":
        c.account.status = "unlinked"
    else:
        c.user.status = "blocked"

    response = await request(
        c, "GET",
        "/api/v1/platform/bot/organizations",
    )
    assert response.status_code == 403, response.text
    assert "data" not in response.json()
    c.organizations.list_organizations.assert_not_called()


@pytest.mark.asyncio
async def test_rate_limit_is_enforced_before_membership_listing(entry):
    c = entry
    c.rates.ensure_platform_user_allowed.side_effect = (
        ApiRequestRateLimitExceededError()
    )
    response = await request(
        c, "GET",
        "/api/v1/platform/bot/organizations",
    )
    assert response.status_code == 429, response.text
    c.organizations.list_organizations.assert_not_called()


@pytest.mark.asyncio
async def test_page_cursor_is_last_visible_organization(entry):
    c = entry
    c.organizations.list_organizations.return_value = [
        {"id": UUID(int=10), "name": "A"},
        {"id": UUID(int=20), "name": "B"},
    ]
    response = await request(
        c, "GET",
        "/api/v1/platform/bot/organizations",
        params={"limit": 1},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["data"]) == 1
    assert body["meta"]["pagination"]["next_cursor"] == str(
        UUID(int=10)
    )


@pytest.mark.asyncio
async def test_query_cannot_select_another_user(entry):
    c = entry
    response = await request(
        c, "GET",
        "/api/v1/platform/bot/organizations",
        params={"user_id": str(uuid4())},
    )
    assert response.status_code == 422, response.text
    c.organizations.list_organizations.assert_not_called()


@pytest.mark.asyncio
async def test_platform_repository_uses_only_active_canonical_membership():
    result = SimpleNamespace(
        mappings=lambda: SimpleNamespace(all=lambda: [])
    )
    session = SimpleNamespace(
        execute=AsyncMock(return_value=result)
    )
    user_id = uuid4()
    await PlatformOrganizationsRepository(
        session
    ).list_organizations(user_id=user_id)

    sql = str(
        session.execute.await_args.args[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "users.status =" in sql
    assert "tenants.status =" in sql
    assert "user_roles.status =" in sql
    assert "user_roles.tenant_id = tenants.id" in sql
    assert "users.tenant_id = tenants.id" in sql
    assert str(user_id) in sql


def test_internal_bootstrap_is_not_a_new_public_auth_contract(entry):
    paths = entry.app.openapi()["paths"]
    assert "/api/v1/platform/bot/session" not in paths
    assert "/api/v1/platform/bot/organizations" not in paths
    assert "/api/v1/auth/telegram" in paths
