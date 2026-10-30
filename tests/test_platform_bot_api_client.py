from dataclasses import replace
from uuid import uuid4

from pydantic import SecretStr
import pytest

from api.auth import get_access_token_codec, get_api_identity_service
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.construction.construction_api_client import ConstructionApiClientError
from services.platform_bot_api_client import PlatformBotApiClient

from test_platform_bot_api_entry import entry, case


def client(c):
    return PlatformBotApiClient(application=c.app, context=c.context)


def enable_real_bearer(c):
    c.app.dependency_overrides[get_access_token_codec] = lambda: c.codec
    c.app.dependency_overrides[get_api_identity_service] = lambda: c.identity


@pytest.mark.asyncio
async def test_organizations_use_registered_api_and_exact_canonical_user(entry):
    c = entry
    async with client(c) as api:
        result = await api.organizations()
    assert result.data == [{"id": str(c.tenant), "name": "Organization"}]
    assert result.meta["pagination"]["next_cursor"] is None
    c.organizations.list_organizations.assert_awaited_once_with(
        user_id=c.user_id, after=None, limit=10
    )
    assert not c.stored


@pytest.mark.asyncio
async def test_bootstrap_tokens_work_with_real_shared_bearer_dependency(entry):
    c = entry
    enable_real_bearer(c)
    async with client(c) as api:
        tokens = await api.authenticate(tenant_id=c.tenant)
        result = await api.construction.me(session=tokens.session)

    assert result.data["id"] == str(c.user_id)
    assert result.data["tenant_id"] == str(c.tenant)
    claims = c.codec.decode_access_token(
        tokens.session.access_token.get_secret_value()
    )
    assert (claims.user_id, claims.tenant_id) == (c.user_id, c.tenant)
    assert tokens.session.access_token.get_secret_value() not in repr(tokens)
    assert tokens.refresh_token.get_secret_value() not in repr(tokens)
    assert len(c.stored) == 2
    c.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_foreign_organization_is_denied_without_creating_session(entry):
    c = entry
    async with client(c) as api:
        with pytest.raises(ConstructionApiClientError) as caught:
            await api.authenticate(tenant_id=uuid4())
    assert caught.value.status_code == 403
    assert not c.stored
    c.sessions.create_session.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_bearer_cannot_be_replaced_by_trusted_bot_context(entry):
    c = entry
    enable_real_bearer(c)
    async with client(c) as api:
        tokens = await api.authenticate(tenant_id=c.tenant)
        bad = replace(tokens.session, access_token=SecretStr("invalid-token"))
        with pytest.raises(ConstructionApiClientError) as caught:
            await api.construction.me(session=bad)
    assert caught.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["blocked", "membership"])
async def test_api_rechecks_identity_after_session_issuance(entry, change):
    c = entry
    enable_real_bearer(c)
    async with client(c) as api:
        tokens = await api.authenticate(tenant_id=c.tenant)
        if change == "blocked":
            c.user.status = "blocked"
        else:
            c.user.tenant_id = uuid4()
            c.users.has_active_tenant_membership.return_value = False
        with pytest.raises(ConstructionApiClientError) as caught:
            await api.construction.me(session=tokens.session)
    assert caught.value.status_code == 401


@pytest.mark.asyncio
async def test_rate_limit_stays_an_api_error_without_direct_fallback(entry):
    c = entry
    c.rates.ensure_platform_user_allowed.side_effect = (
        ApiRequestRateLimitExceededError()
    )
    async with client(c) as api:
        with pytest.raises(ConstructionApiClientError) as caught:
            await api.organizations()
    assert caught.value.status_code == 429
    assert caught.value.code == "RATE_LIMIT_EXCEEDED"
    c.organizations.list_organizations.assert_not_called()
    assert not c.stored
