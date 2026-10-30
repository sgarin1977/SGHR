import asyncio
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from pydantic import SecretStr
import pytest

from api.auth import get_api_auth_service
from api.platform_bot_context import PlatformBotApiContext
from api.routes import platform_bot_entry as bootstrap
from api.routes import construction_navigation as navigation
from services.construction.construction_api_client import (
    ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import (
    BotApiSessionCache, ConstructionBotApiEntry,
)
from services.platform_bot_api_client import (
    PlatformBotApiClient, PlatformBotApiTokens,
)

from test_platform_bot_authentication import case
from test_construction_api_client import (
    api_case, read_http, list_case, detail_case, read_case, terminal_case,
)


def supplied(value):
    return lambda: value


def context(c):
    return PlatformBotApiContext(
        verifier=c.verifier, proof=c.verifier.proof
    )


@pytest.mark.asyncio
async def test_concurrent_clicks_share_one_session_and_refresh_after_expiry(case):
    c = case
    clock = [100.0]
    tokens = PlatformBotApiTokens(
        PlatformApiClientSession(c.user_id, c.tenant, SecretStr("access-one")),
        SecretStr("refresh-one"), 60,
    )
    refreshed = replace(
        tokens,
        session=replace(tokens.session, access_token=SecretStr("access-two")),
        refresh_token=SecretStr("refresh-two"),
    )

    async def authenticate(**kwargs):
        await asyncio.sleep(0)
        return tokens

    api = NS(
        authenticate=AsyncMock(side_effect=authenticate),
        refresh=AsyncMock(return_value=refreshed),
    )
    cache = BotApiSessionCache(clock=lambda: clock[0])
    options = dict(api=api, context=context(c), tenant_id=c.tenant)

    results = await asyncio.gather(*(cache.get(**options) for _ in range(3)))
    assert all(value is tokens.session for value in results)
    api.authenticate.assert_awaited_once_with(tenant_id=c.tenant)
    api.refresh.assert_not_awaited()

    clock[0] = 160.0
    assert await cache.get(**options) is refreshed.session
    api.refresh.assert_awaited_once_with(tokens=tokens)
    assert api.authenticate.await_count == 1


@pytest.mark.asyncio
async def test_failed_refresh_is_not_retried_or_replaced_by_automatic_login(case):
    c = case
    clock = [100.0]
    tokens = PlatformBotApiTokens(
        PlatformApiClientSession(c.user_id, c.tenant, SecretStr("access")),
        SecretStr("refresh"), 60,
    )
    api = NS(
        authenticate=AsyncMock(return_value=tokens),
        refresh=AsyncMock(side_effect=ConstructionApiClientError(
            status_code=401, code="invalid_refresh_token"
        )),
    )
    cache = BotApiSessionCache(clock=lambda: clock[0])
    options = dict(api=api, context=context(c), tenant_id=c.tenant)
    await cache.get(**options)
    clock[0] = 160.0

    with pytest.raises(ConstructionApiClientError):
        await cache.get(**options)

    assert api.authenticate.await_count == 1
    assert api.refresh.await_count == 1
    assert not cache._entries


@pytest.fixture
def wired(api_case, case):
    c, a = api_case, case
    a.user_id = c.user
    a.tenant = c.tenant
    a.user.id = c.user
    a.user.tenant_id = c.tenant
    a.account.user_id = c.user
    a.users.get_by_id = AsyncMock(return_value=a.user)

    # Platform /me needs the full actor projection.
    # Construction authority remains in the separate c.context.
    c.actor.active_role = a.user.active_role
    c.actor.roles = ("client",)
    c.actor.language_code = a.user.language_code
    c.actor.timezone = a.user.timezone
    a.tokens.access_token_codec = c.codec
    a.rates = NS(
        ensure_platform_user_allowed=AsyncMock(return_value=1)
    )

    organizations = NS(list_organizations=AsyncMock(return_value=[
        {"id": c.tenant, "name": "Organization"}
    ]))
    organization = NS(require_current=AsyncMock(return_value=NS(
        tenant_id=c.tenant, name="Organization", default_currency="EUR",
    )))
    for dependency, value in (
        (bootstrap.get_platform_bot_users, a.users),
        (bootstrap.get_platform_bot_rates, a.rates),
        (bootstrap.get_platform_bot_organizations, organizations),
        (bootstrap.get_platform_bot_authentication, a.service),
        (navigation.get_navigation_access, c.access),
        (navigation.get_navigation_organization, organization),
        (get_api_auth_service, a.tokens),
    ):
        c.app.dependency_overrides[dependency] = supplied(value)

    return NS(c=c, a=a, organization=organization, context=context(a))


@pytest.mark.asyncio
async def test_bot_reads_registered_navigation_and_crm_apis_with_one_session(wired):
    p, c, a = wired, wired.c, wired.a
    async with PlatformBotApiClient(
        application=c.app, context=p.context
    ) as api:
        entry = ConstructionBotApiEntry(
            api=api, context=p.context, sessions=BotApiSessionCache()
        )
        page = await entry.leads(
            telegram_user_id=123, tenant_id=c.tenant
        )
        detail = await entry.lead(
            telegram_user_id=123, tenant_id=c.tenant, lead_id=c.lead.id
        )

    assert page["organization"] == "Organization"
    assert page["items"][0]["id"] == detail["item"]["id"] == str(c.lead.id)
    assert detail["item"]["next_action"]["id"] == str(c.row.id)
    assert a.sessions.create_session.await_count == 1
    assert a.session.commit.await_count == 1
    assert c.list_repo.list_active_leads.await_count == 1


@pytest.mark.asyncio
async def test_refresh_uses_existing_public_platform_operation(wired):
    p, c, a = wired, wired.c, wired.a
    async with PlatformBotApiClient(
        application=c.app, context=p.context
    ) as api:
        first = await api.authenticate(tenant_id=c.tenant)
        a.sessions.get_active_session_for_update.return_value = a.stored[0]
        second = await api.refresh(tokens=first)
        me = await api.construction.me(session=second.session)

    assert me.data["id"] == str(c.user)
    assert second.session.tenant_id == c.tenant
    assert second.refresh_token != first.refresh_token
    assert a.sessions.create_session.await_count == 1
    a.sessions.rotate_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_navigation_rechecks_membership_before_organization_read(wired):
    p, c = wired, wired.c
    c.access.has_active_platform_membership.return_value = False

    async with PlatformBotApiClient(
        application=c.app, context=p.context
    ) as api:
        tokens = await api.authenticate(tenant_id=c.tenant)
        with pytest.raises(ConstructionApiClientError) as caught:
            await api.construction.navigation(session=tokens.session)

    assert caught.value.status_code == 403
    p.organization.require_current.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_callback_forwards_actual_dispatch_context(monkeypatch):
    import handlers.start as start
    import handlers.construction.construction_suite as handler

    callback, state, bot, update = object(), object(), object(), object()
    opened = AsyncMock()
    monkeypatch.setattr(handler, "open_construction", opened)

    await start.open_construction_suite(
        callback, state, bot=bot, event_update=update
    )
    opened.assert_awaited_once_with(
        callback, state, bot=bot, event_update=update
    )
