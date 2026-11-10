"""Registered copy HTTP with local services and encrypted SQLite receipts."""
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
from api.routes import construction_template_copy as routes
from api.routes.construction import (
    get_construction_access_context, get_construction_scoped_session,
)
from api.settings import ApiConfigurationError
from services.api_rate_limits import (
    ApiRequestRateLimitService, InMemoryApiRequestRateLimitStore,
    ApiRequestRateLimitExceededError,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from services.construction.construction_template_copy_workflow import OPERATION
from test_construction_02_template_copy_workflow import (
    case, onboarding, catalog_case, copy_case, keyed_case,
    put, snapshot,
)
from test_construction_02_template_copy import definition, add_source

PATH = "/api/v1/construction/settings/measurement-templates/copy-starter"
BODY = {"source_version_id": str(UUID(int=1001))}


@pytest.fixture
async def copy_http(keyed_case):
    c = keyed_case
    put(c)
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
        return await c.copier.authorizer.resolver.resolve(actor=c.actor)

    async def workflow(
        scoped=Depends(get_construction_scoped_session),
        key=Depends(routes.require_template_copy_key),
    ):
        assert scoped is c.session
        return c.workflow

    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_scoped_session] = session
    c.app.dependency_overrides[get_construction_access_context] = context
    c.app.dependency_overrides[routes.get_template_copy_workflow] = workflow
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rates
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.core
    yield c


async def call(
    c, *, body=None, key="copy-key", headers=None,
    query="", request_id="template-copy-http",
):
    request_headers = [("X-Request-ID", request_id)]
    if headers is not None:
        request_headers.extend(headers)
    elif key is not None:
        request_headers.append(("Idempotency-Key", key))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        return await client.post(
            PATH + query, json=BODY if body is None else body,
            headers=request_headers)


@pytest.mark.asyncio
async def test_copy_uses_shared_gates_and_one_commit(copy_http):
    c = copy_http
    response = await call(c)
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["tenant_id"] == str(c.tenant)
    assert data["created_by"] == str(c.user)
    assert data["source_starter_version_id"] == BODY["source_version_id"]
    assert data["template_id"] != data["template_version_id"]
    assert data["template_row_version"] == data["version"] == 1
    assert data["version_row_version"] == 2
    assert data["section_count"] == 2 and data["question_count"] == 3
    assert c.session.commits == 1
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"
    c.rates.ensure_authenticated_user_allowed.assert_awaited_once_with(
        tenant_id=c.tenant, user_id=c.user)
    c.suite.require_enabled.assert_awaited_once_with(
        tenant_id=c.tenant, suite_code="construction")
    c.core.require_enabled.assert_awaited_once_with(
        tenant_id=c.tenant, module_code="construction.core")
    c.events.create_event.assert_awaited_once()
    assert c.events.create_event.await_args.kwargs["trace_id"] == "template-copy-http"


@pytest.mark.asyncio
async def test_http_replay_returns_original_after_later_changes(copy_http):
    c = copy_http
    first = await call(c)
    assert first.status_code == 201, first.text
    initial = first.json()["data"]
    c.sync.execute(update(c.copy_repo.templates).where(
        c.copy_repo.templates.c.id == UUID(initial["template_id"])
    ).values(code="LATER_CODE", active=False, row_version=2))
    c.sync.execute(update(c.catalog_templates).where(
        c.catalog_templates.c.id == UUID(int=101)).values(active=False))
    c.sync.commit()
    before = snapshot(c)
    retry = await call(c, request_id="copy-retry")
    assert retry.status_code == 201, retry.text
    assert retry.json()["data"] == initial
    assert retry.json()["request_id"] == "copy-retry"
    assert snapshot(c) == before
    c.copy_calls.assert_awaited_once()
    c.events.create_event.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"source_version_id": str(UUID(int=1002))}, {"code": "OTHER_COPY"},
])
async def test_same_key_with_changed_request_is_409(copy_http, changes):
    c = copy_http
    assert (await call(c)).status_code == 201
    before = snapshot(c)
    response = await call(c, body=BODY | changes)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_another_key_does_not_overwrite_existing_code(copy_http):
    c = copy_http
    assert (await call(c)).status_code == 201
    before = snapshot(c)
    response = await call(c, key="different-operation")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "TEMPLATE_COPY_CONFLICT"
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("source_id", [1003, 3001, 9999])
async def test_unavailable_sources_have_the_same_safe_error(copy_http, source_id):
    c = copy_http
    before = snapshot(c)
    response = await call(c, body={"source_version_id": str(UUID(int=source_id))})
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"
    assert response.json()["error"]["message"] == "Starter template version is unavailable."
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {}, {"source_version_id": None}, {"source_version_id": "not-a-uuid"},
    BODY | {"tenant_id": str(uuid4())},
    BODY | {"user_id": str(uuid4())},
    BODY | {"manage_transaction": False},
    BODY | {"expected_row_version": 1},
    BODY | {"unit_price": "20.00"},
    BODY | {"code": 123}, BODY | {"code": ""},
    BODY | {"code": "lowercase"}, BODY | {"code": "A" * 65},
])
async def test_body_accepts_only_exact_source_and_optional_code(copy_http, body):
    c = copy_http
    before = snapshot(c)
    response = await call(c, body=body)
    assert response.status_code == 422, response.text
    assert snapshot(c) == before
    c.copy_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [
    [], [("Idempotency-Key", "")], [("Idempotency-Key", " ")],
    [("Idempotency-Key", "x" * 256)],
    [("Idempotency-Key", "a"), ("Idempotency-Key", "b")],
])
async def test_key_is_required_unique_and_bounded(copy_http, headers):
    c = copy_http
    before = snapshot(c)
    response = await call(c, headers=headers)
    assert response.status_code == 422, response.text
    assert snapshot(c) == before
    c.copy_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["?tenant_id=foreign", "?limit=1"])
async def test_query_selectors_are_rejected(copy_http, query):
    c = copy_http
    before = snapshot(c)
    response = await call(c, query=query)
    assert response.status_code == 422, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", [
    "authentication", "suite", "core", "rate", "membership", "scope", "revoked",
])
async def test_shared_and_fresh_access_gates(copy_http, gate):
    c = copy_http
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
    elif gate == "scope":
        c.grant.role, c.grant.scope_type = "OBSERVER", "project"
    else:
        c.grant.status = "revoked"
    response = await call(c)
    assert response.status_code == expected, response.text
    assert snapshot(c) == before
    c.copy_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["membership", "scope", "core"])
async def test_receipt_does_not_bypass_current_http_access(copy_http, gate):
    c = copy_http
    assert (await call(c)).status_code == 201
    before = snapshot(c)
    if gate == "membership":
        c.membership = False
    elif gate == "scope":
        c.grant.role, c.grant.scope_type = "OBSERVER", "project"
    else:
        c.core.require_enabled.side_effect = ModuleDisabledError()
    response = await call(c)
    assert response.status_code == 403, response.text
    assert snapshot(c) == before
    c.copy_calls.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "complete", "commit", "private"])
async def test_http_failures_are_sanitized_and_atomic(copy_http, failure):
    c = copy_http
    before = snapshot(c)
    if failure == "audit":
        c.fail_audit = True
    elif failure == "complete":
        c.fail_complete = "error"
    elif failure == "commit":
        c.session.fail_commit = True
    else:
        c.workflow.copy_template = AsyncMock(
            side_effect=RuntimeError("PRIVATE_DATABASE_DETAILS"))
    response = await call(c)
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "internal_error"
    assert "PRIVATE_DATABASE_DETAILS" not in response.text
    assert snapshot(c) == before and c.session.commits == 0


@pytest.mark.asyncio
async def test_processing_receipt_is_409_without_copy(copy_http):
    c = copy_http
    if not c.sync.in_transaction():
        c.sync.begin()
    await c.serialization.lock_member(tenant_id=c.tenant, user_id=c.user)
    await c.idempotency.reserve(
        tenant_id=c.tenant, principal_type="user", principal_id=c.user,
        operation=OPERATION, idempotency_key="processing",
        payload={"source_version_id": BODY["source_version_id"], "code": None},
        now=c.now)
    await c.session.commit()
    before = snapshot(c)
    response = await call(c, key="processing")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "idempotency_in_progress"
    assert snapshot(c) == before
    c.copy_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_unverified_work_bindings_have_an_explicit_conflict(copy_http):
    c = copy_http
    value = definition()
    value["sections"][0]["questions"][1].update(
        work_type_source="MASTER", work_type_id=str(uuid4()),
        work_type_version_policy="CURRENT_PUBLISHED")
    source_id = add_source(c, value)
    before = snapshot(c)
    response = await call(c, body={"source_version_id": str(source_id)})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "TEMPLATE_BINDING_UNAVAILABLE"
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["settings", "factory"])
async def test_configuration_errors_are_503_without_secret_details(
    copy_http, monkeypatch, failure,
):
    c = copy_http
    c.app.dependency_overrides.pop(routes.get_template_copy_workflow)
    if failure == "settings":
        def settings():
            raise ApiConfigurationError("PRIVATE_CONFIG_DETAILS")
        monkeypatch.setattr(
            routes.ApiIdempotencySettings, "from_env", staticmethod(settings))
    else:
        c.app.dependency_overrides[routes.get_template_copy_settings] = (
            lambda: NS(encryption_key="private-test-placeholder"))
        def factory(**values):
            raise ValueError("PRIVATE_FACTORY_DETAILS")
        monkeypatch.setattr(routes, "build_construction_template_copy_workflow", factory)
    before = snapshot(c)
    response = await call(c)
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "service_unavailable"
    assert "PRIVATE_" not in response.text
    assert snapshot(c) == before


def test_openapi_documents_the_creation_command_and_required_key():
    schema = create_app().openapi()
    operation = schema["paths"][PATH]["post"]
    assert operation["security"]
    keys = [
        value for value in operation["parameters"]
        if value["in"] == "header" and value["name"] == "Idempotency-Key"
    ]
    assert len(keys) == 1 and keys[0]["required"] is True
    assert {401, 403, 404, 409, 422, 429, 500, 503} <= {
        int(value) for value in operation["responses"]}
    body = schema["components"]["schemas"]["TemplateCopyRequest"]
    assert body["required"] == ["source_version_id"]
    assert set(body["properties"]) == {"source_version_id", "code"}
    assert body["additionalProperties"] is False
    assert "201" in operation["responses"]
