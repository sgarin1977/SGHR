"""Registered catalog HTTP with local SQLite services; no external operations."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Depends
from sqlalchemy import update

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import (
    get_api_request_rate_limit_service,
    get_tenant_module_access_service, get_tenant_suite_access_service,
)
from api.errors import ApiHttpError
from api.routes import construction_starter_templates as routes
from api.routes.construction import (
    get_construction_access_context, get_construction_scoped_session,
)
from services.api_rate_limits import (
    ApiRequestRateLimitService, InMemoryApiRequestRateLimitStore,
    ApiRequestRateLimitExceededError,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_02_starter_template_catalog import (
    case, onboarding, catalog_case, put, snapshot,
)

PATH = "/api/v1/construction/settings/starter-measurement-templates"


@pytest.fixture
async def catalog_http(catalog_case):
    c = catalog_case
    c.sync.rollback()
    c.app = create_app()
    c.suite = NS(require_enabled=AsyncMock())
    c.core = NS(require_enabled=AsyncMock())
    c.rates = ApiRequestRateLimitService(store=InMemoryApiRequestRateLimitStore())
    c.rates.ensure_authenticated_user_allowed = AsyncMock(
        wraps=c.rates.ensure_authenticated_user_allowed)

    async def session():
        if not c.sync.in_transaction():
            c.sync.begin()
        yield c.session

    async def context(scoped=Depends(get_construction_scoped_session)):
        assert scoped is c.session
        return await c.progress.resolver.resolve(actor=c.actor)

    async def service(scoped=Depends(get_construction_scoped_session)):
        assert scoped is c.session
        return c.catalog

    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_scoped_session] = session
    c.app.dependency_overrides[get_construction_access_context] = context
    c.app.dependency_overrides[routes.get_starter_template_service] = service
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rates
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.core
    yield c


async def call(c, suffix="", query="", method="GET"):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        return await client.request(
            method, PATH + suffix + query,
            headers={"X-Request-ID": "starter-http"})


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_empty_catalog_is_read_only_and_uses_shared_gates(catalog_http, role):
    c = catalog_http
    c.grant.role = role
    before = snapshot(c)
    response = await call(c)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "data": [], "meta": {"pagination": {"next_cursor": None}},
        "request_id": "starter-http"}
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"
    c.rates.ensure_authenticated_user_allowed.assert_awaited_once_with(
        tenant_id=c.tenant, user_id=c.user)
    c.suite.require_enabled.assert_awaited_once_with(
        tenant_id=c.tenant, suite_code="construction")
    c.core.require_enabled.assert_awaited_once_with(
        tenant_id=c.tenant, module_code="construction.core")
    assert snapshot(c) == before and c.session.commits == 0
    c.events.create_event.assert_not_awaited()
    c.serialization.lock_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_latest_pagination_and_exact_old_definition(catalog_http):
    c = catalog_http
    put(c)
    before = snapshot(c)
    first = await call(c, query="?limit=1")
    assert first.status_code == 200, first.text
    item = first.json()["data"][0]
    assert item["version_id"] == str(UUID(int=1002))
    assert item["template_id"] == str(UUID(int=101)) and item["version"] == 2
    assert item["source_language"] == "en" and item["title"] == "Starter"
    assert "definition" not in item
    cursor = first.json()["meta"]["pagination"]["next_cursor"]
    assert len(cursor) == routes.CURSOR_LENGTH
    second = await call(c, query="?limit=1&cursor=" + cursor)
    assert second.status_code == 200, second.text
    assert second.json()["data"][0]["version_id"] == str(UUID(int=2001))
    assert second.json()["meta"]["pagination"]["next_cursor"] is None
    exact = await call(c, suffix="/versions/" + str(UUID(int=1001)))
    assert exact.status_code == 200, exact.text
    data = exact.json()["data"]
    assert data["version"] == 1 and data["definition"]["title_key"] == "title"
    assert data["content_hash"] == item["content_hash"]
    assert exact.headers["Cache-Control"] == "private, no-store"
    assert snapshot(c) == before and c.session.commits == 0
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("number", [1003, 3001, 9999])
async def test_future_inactive_and_missing_versions_are_hidden(catalog_http, number):
    c = catalog_http
    put(c)
    before = snapshot(c)
    response = await call(c, suffix="/versions/" + str(UUID(int=number)))
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"
    assert response.json()["error"]["message"] == "Starter template version is unavailable."
    assert snapshot(c) == before and c.session.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [
    "?limit=0", "?limit=101", "?limit=abc", "?limit=1&limit=2",
    "?tenant_id=foreign", "?language=en", "?cursor=", "?cursor=invalid",
    "?cursor=" + "A" * 68, "?cursor=" + "A" * 69,
])
async def test_invalid_or_unsupported_queries_are_rejected(catalog_http, query):
    c = catalog_http
    before = snapshot(c)
    response = await call(c, query=query)
    assert response.status_code == 422, response.text
    assert snapshot(c) == before and c.session.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["tenant", "user"])
async def test_cursor_cannot_change_actor_context(catalog_http, field):
    c = catalog_http
    context = NS(
        tenant_id=c.foreign if field == "tenant" else c.tenant,
        user_id=uuid4() if field == "user" else c.user)
    cursor = routes.encode_cursor(routes.Cursor(
        context.tenant_id, context.user_id, UUID(int=101)), actor=context)
    response = await call(c, query="?cursor=" + cursor)
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "/versions/" + str(UUID(int=1001))])
@pytest.mark.parametrize("gate", [
    "authentication", "suite", "core", "rate", "membership", "scope",
])
async def test_shared_access_gates(catalog_http, suffix, gate):
    c = catalog_http
    before = snapshot(c)
    expected = 403
    if gate == "authentication":
        async def denied():
            raise ApiHttpError(
                status_code=401, code="authentication_required",
                message="Authentication required.")
        c.app.dependency_overrides[get_current_actor] = denied
        expected = 401
    elif gate == "suite":
        c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif gate == "core":
        c.core.require_enabled.side_effect = ModuleDisabledError()
    elif gate == "rate":
        c.rates.ensure_authenticated_user_allowed.side_effect = ApiRequestRateLimitExceededError()
        expected = 429
    elif gate == "membership":
        c.membership = False
    else:
        c.grant.role, c.grant.scope_type = "OBSERVER", "project"
    response = await call(c, suffix=suffix)
    assert response.status_code == expected, response.text
    assert snapshot(c) == before and c.session.commits == 0
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("detail", [False, True])
async def test_authority_is_rechecked_after_query(catalog_http, detail):
    c = catalog_http
    put(c)
    name = "get_version" if detail else "list_latest"
    original = getattr(c.catalog_repository, name)
    async def revoked(**arguments):
        result = await original(**arguments)
        c.membership = False
        return result
    setattr(c.catalog_repository, name, revoked)
    response = await call(
        c, suffix="/versions/" + str(UUID(int=1001)) if detail else "")
    assert response.status_code == 403, response.text
    assert c.session.commits == 0
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_corrupt_lookahead_fails_without_partial_response(catalog_http):
    c = catalog_http
    put(c)
    c.sync.execute(update(c.catalog_versions).where(
        c.catalog_versions.c.id == UUID(int=2001)
    ).values(content_hash="0" * 64))
    c.sync.commit()
    before = snapshot(c)
    response = await call(c, query="?limit=1")
    assert response.status_code == 500, response.text
    assert "data" not in response.json()
    assert snapshot(c) == before and c.session.commits == 0


@pytest.mark.asyncio
async def test_repository_errors_are_sanitized(catalog_http):
    c = catalog_http
    c.catalog_repository.list_latest = AsyncMock(
        side_effect=RuntimeError("private database detail"))
    response = await call(c)
    assert response.status_code == 500
    assert "private database detail" not in response.text
    assert response.json()["error"]["message"] == "Starter catalog read failed."
    assert c.session.commits == 0
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_version_query_selectors_and_writes_are_not_supported(catalog_http):
    c = catalog_http
    response = await call(
        c, suffix="/versions/" + str(UUID(int=1001)), query="?tenant_id=foreign")
    assert response.status_code == 422, response.text
    response = await call(c, method="POST")
    assert response.status_code == 405, response.text


def test_registered_openapi_is_read_only_and_documents_pagination():
    schema = create_app().openapi()
    assert set(schema["paths"][PATH]) == {"get"}
    assert set(schema["paths"][PATH + "/versions/{version_id}"]) == {"get"}
    parameters = schema["paths"][PATH]["get"]["parameters"]
    limit = next(value for value in parameters if value["name"] == "limit")
    assert limit["schema"]["default"] == 20
    assert limit["schema"]["minimum"] == 1
    assert limit["schema"]["maximum"] == 100
    assert not any(value["name"].lower() == "idempotency-key" for value in parameters)
    assert "429" in schema["paths"][PATH]["get"]["responses"]
