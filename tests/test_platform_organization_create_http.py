"""Local registered API and bot transport tests for CON-001."""
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from aiogram import Bot
from aiogram.types import Update
from sqlalchemy import text

from api.app import create_app
from api.errors import ApiHttpError
from api.platform_bot_context import PlatformBotApiContext, bind_platform_bot_api_context
from api.platform_principal import get_authenticated_platform_principal
from api.routes import platform_bot_entry
from api.routes import platform_organization_create as routes
from services.api_platform_principal import ApiPlatformPrincipal
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.platform_bot_api_client import PlatformBotApiClient
from services.platform_bot_auth_proof import PlatformBotUpdateVerifier
from test_platform_organization_bootstrap import case, counts
from test_platform_organization_creation_policy import release_case
from test_platform_construction_onboarding import onboarding_case, assignment_counts


@pytest.fixture
async def creation_http(onboarding_case):
    c = onboarding_case
    c.sync.execute(text("UPDATE users SET tenant_id=NULL"))
    c.sync.commit()
    c.app = create_app()
    c.rates = NS(ensure_platform_user_allowed=AsyncMock())
    c.users = NS(
        get_by_platform_account=AsyncMock(return_value=NS(
            user_id=c.user, status="active",
        )),
        get_by_id=AsyncMock(return_value=NS(
            id=c.user, tenant_id=None, status="active", language_code="uk",
        )),
    )
    c.app.dependency_overrides[get_authenticated_platform_principal] = (
        lambda: ApiPlatformPrincipal(c.user)
    )
    c.app.dependency_overrides[routes.get_organization_creation_rates] = lambda: c.rates
    c.app.dependency_overrides[routes.get_organization_creation_workflow] = lambda: c.workflow
    c.app.dependency_overrides[routes.get_organization_creation_repository] = lambda: c.repo
    c.app.dependency_overrides[platform_bot_entry.get_platform_bot_users] = lambda: c.users

    c.bot = Bot(token="123456:local-organization-create-test")
    now = datetime.now(timezone.utc)
    update = Update.model_validate({
        "update_id": 12,
        "message": {
            "message_id": 1, "date": int(now.timestamp()),
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 123, "is_bot": False, "first_name": "Person"},
            "text": "Create organization",
        },
    }, context={"bot": c.bot})
    verifier = PlatformBotUpdateVerifier(bot=c.bot, update=update)
    c.context = PlatformBotApiContext(verifier=verifier, proof=verifier.proof)
    c.bound = bind_platform_bot_api_context(c.app, context=c.context)
    yield c
    await c.bot.session.close()


async def post(c, *, bot=False, trusted=True, body=None, key="organization-http-key"):
    if not c.sync.in_transaction():
        c.sync.begin()
    payload = {"name": "  Organization  ", "language": "uk", "currency": "EUR"}
    if bot:
        payload["expected_creator_user_id"] = str(c.user)
    if body is not None:
        payload.update(body)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=c.bound if bot and trusted else c.app,
            raise_app_exceptions=False,
        ),
        base_url="http://127.0.0.1",
    ) as client:
        return await client.post(
            "/api/v1/platform/bot/organizations" if bot
            else "/api/v1/platform/organizations",
            json=payload,
            headers={"Idempotency-Key": key} if key is not None else {},
        )


@pytest.mark.asyncio
async def test_public_creation_without_existing_membership_and_retry(creation_http):
    c = creation_http
    first = await post(c)
    assert first.status_code == 201, first.text
    assert first.json()["data"]["creator_user_id"] == str(c.user)
    assert counts(c) == (1, 1, 1, 1, 1, 0)
    assert assignment_counts(c) == (1, 1, 1)
    replay = await post(c)
    assert replay.status_code == 201, replay.text
    assert replay.json()["data"] == first.json()["data"]
    assert counts(c) == (1, 1, 1, 1, 1, 0)
    assert first.headers["Cache-Control"] == "private, no-store"


@pytest.mark.asyncio
async def test_changed_body_same_key_conflicts(creation_http):
    c = creation_http
    assert (await post(c)).status_code == 201
    response = await post(c, body={"name": "Different"})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert counts(c) == (1, 1, 1, 1, 1, 0)


@pytest.mark.asyncio
async def test_public_authentication_is_required(creation_http):
    c = creation_http
    async def denied():
        raise ApiHttpError(
            status_code=401, code="invalid_token", message="Unavailable.",
        )
    c.app.dependency_overrides[get_authenticated_platform_principal] = denied
    response = await post(c)
    assert response.status_code == 401, response.text
    assert counts(c) == (0, 0, 0, 0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [
    {"tenant_id": str(uuid4())},
    {"creator_user_id": str(uuid4())},
    {"owner_user_id": str(uuid4())},
    {"role": "super_admin"},
])
async def test_caller_cannot_assign_tenant_or_creator_or_role(creation_http, extra):
    response = await post(creation_http, body=extra)
    assert response.status_code == 422, response.text
    assert counts(creation_http) == (0, 0, 0, 0, 0, 0)


@pytest.mark.asyncio
async def test_create_key_is_required(creation_http):
    response = await post(creation_http, key=None)
    assert response.status_code == 422, response.text
    assert counts(creation_http) == (0, 0, 0, 0, 0, 0)


@pytest.mark.asyncio
async def test_platform_rate_limit_prevents_creation(creation_http):
    c = creation_http
    c.rates.ensure_platform_user_allowed.side_effect = ApiRequestRateLimitExceededError()
    response = await post(c)
    assert response.status_code == 429, response.text
    assert counts(c) == (0, 0, 0, 0, 0, 0)


@pytest.mark.asyncio
async def test_bot_creation_uses_verified_dispatch_without_tenant_session(creation_http):
    c = creation_http
    response = await post(c, bot=True)
    assert response.status_code == 201, response.text
    assert response.json()["data"]["creator_user_id"] == str(c.user)
    c.users.get_by_platform_account.assert_awaited_once_with("telegram", "123")
    assert counts(c) == (1, 1, 1, 1, 1, 0)


@pytest.mark.asyncio
async def test_ordinary_http_cannot_supply_internal_bot_proof(creation_http):
    response = await post(creation_http, bot=True, trusted=False)
    assert response.status_code == 401, response.text
    assert counts(creation_http) == (0, 0, 0, 0, 0, 0)


@pytest.mark.asyncio
async def test_changed_bot_creator_is_denied_before_bootstrap(creation_http):
    response = await post(
        creation_http, bot=True,
        body={"expected_creator_user_id": str(uuid4())},
    )
    assert response.status_code == 403, response.text
    assert counts(creation_http) == (0, 0, 0, 0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit"])
async def test_http_failure_rolls_back_real_bootstrap_rows(creation_http, failure):
    c = creation_http
    if failure == "audit":
        c.events.create_event.side_effect = RuntimeError("private audit failure")
    else:
        c.session.fail_commit = True
    response = await post(c)
    assert response.status_code == 503, response.text
    assert "private audit failure" not in response.text
    assert counts(c) == (0, 0, 0, 0, 0, 0)
    assert assignment_counts(c) == (0, 0, 0)


@pytest.mark.asyncio
async def test_bot_client_context_and_creation_use_registered_shared_api(creation_http):
    c = creation_http
    async with PlatformBotApiClient(application=c.app, context=c.context) as client:
        context = await client.organization_creation_context()
        assert context.data["actor_user_id"] == str(c.user)
        assert context.data["languages"] == ["uk"]
        result = await client.create_organization(
            payload={"name": "Organization", "language": "uk", "currency": "EUR"},
            expected_user_id=c.user, idempotency_key="client-bootstrap-key",
        )
    UUID(result.data["id"])
    assert result.data["creator_user_id"] == str(c.user)
    assert counts(c) == (1, 1, 1, 1, 1, 0)
