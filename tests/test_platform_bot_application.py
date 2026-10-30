from dataclasses import replace
from importlib import import_module

import httpx
from pydantic import SecretStr
import pytest

from api.auth import get_current_actor
from api.dependencies import get_api_request_rate_limit_service
from api.platform_bot_application import create_platform_bot_application
from api.routes.platform_bot_entry import get_platform_bot_rates
from services.construction.construction_api_client import (
    ConstructionApiClient,
    ConstructionApiClientError,
)
from services.module_access import ModuleDisabledError

from test_construction_api_client import (
    api_case, read_http, list_case, detail_case, read_case, terminal_case,
)
from test_construction_bot_postgres_rate_limit import rate_case


def composed(c, rate_case, monkeypatch):
    module = import_module("api.app")
    monkeypatch.setattr(module, "create_app", lambda: c.app)
    before = {
        key: value
        for key, value in c.app.dependency_overrides.items()
        if key not in {
            get_api_request_rate_limit_service,
            get_platform_bot_rates,
        }
    }
    application = create_platform_bot_application(
        session_factory=rate_case.factory
    )
    for key, value in before.items():
        assert application.dependency_overrides[key] is value
    assert get_current_actor not in application.dependency_overrides
    return application


async def list_leads(application, session):
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.ASGITransport(
            app=application, raise_app_exceptions=False
        ),
    ) as http:
        return await ConstructionApiClient(http=http).leads(
            session=session
        )


def test_factory_does_not_require_redis_or_open_database(monkeypatch):
    monkeypatch.delenv("API_ENVIRONMENT", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    def unopened_factory():
        raise AssertionError("Composition must not open the database.")

    application = create_platform_bot_application(
        session_factory=unopened_factory
    )
    assert set(application.dependency_overrides) == {
        get_api_request_rate_limit_service,
        get_platform_bot_rates,
    }
    first = application.dependency_overrides[
        get_api_request_rate_limit_service
    ]()
    second = application.dependency_overrides[
        get_platform_bot_rates
    ]()
    assert first is second


@pytest.mark.asyncio
async def test_real_api_read_counts_one_postgres_admission(
    api_case, rate_case, monkeypatch
):
    c = api_case
    application = composed(c, rate_case, monkeypatch)
    result = await list_leads(application, c.client_session)

    assert result.data[0]["id"] == str(c.lead.id)
    assert len(rate_case.committed) == 1
    assert rate_case.count == 1
    c.rate.ensure_authenticated_user_allowed.assert_not_awaited()
    c.suite.require_enabled.assert_awaited_once()
    c.module.require_enabled.assert_awaited_once()


@pytest.mark.asyncio
async def test_limit_returns_429_before_business_read(
    api_case, rate_case, monkeypatch
):
    c = api_case
    rate_case.count = 120
    application = composed(c, rate_case, monkeypatch)

    with pytest.raises(ConstructionApiClientError) as caught:
        await list_leads(application, c.client_session)

    assert caught.value.status_code == 429
    assert caught.value.code == "RATE_LIMIT_EXCEEDED"
    assert rate_case.committed == []
    c.list_repo.list_active_leads.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_bearer_is_not_replaced_by_backend_composition(
    api_case, rate_case, monkeypatch
):
    c = api_case
    application = composed(c, rate_case, monkeypatch)
    bad = replace(
        c.client_session, access_token=SecretStr("invalid-bearer")
    )

    with pytest.raises(ConstructionApiClientError) as caught:
        await list_leads(application, bad)

    assert caught.value.status_code == 401
    assert rate_case.queries == []
    c.identity.require_actor.assert_not_awaited()
    c.list_repo.list_active_leads.assert_not_awaited()


@pytest.mark.asyncio
async def test_module_denial_preserves_gate_and_committed_admission(
    api_case, rate_case, monkeypatch
):
    c = api_case
    c.module.require_enabled.side_effect = ModuleDisabledError()
    application = composed(c, rate_case, monkeypatch)

    with pytest.raises(ConstructionApiClientError) as caught:
        await list_leads(application, c.client_session)

    assert caught.value.status_code == 403
    assert len(rate_case.committed) == 1
    c.list_repo.list_active_leads.assert_not_awaited()


@pytest.mark.asyncio
async def test_postgres_failure_has_no_unlimited_fallback(
    api_case, rate_case, monkeypatch
):
    c = api_case
    rate_case.insert_failure = True
    application = composed(c, rate_case, monkeypatch)

    with pytest.raises(ConstructionApiClientError) as caught:
        await list_leads(application, c.client_session)

    assert caught.value.status_code >= 500
    assert rate_case.committed == []
    c.list_repo.list_active_leads.assert_not_awaited()
