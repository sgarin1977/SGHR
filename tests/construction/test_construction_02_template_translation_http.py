"""Registered translated-text GET with local workflow and fake provider only."""
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import Depends

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import (
    get_api_session, get_api_request_rate_limit_service,
    get_tenant_suite_access_service, get_tenant_module_access_service,
)
from api.errors import ApiHttpError
from api.routes import construction_organization_templates as routes
from api.routes.construction import (
    get_construction_scoped_session, get_construction_access_context,
)
from services.api_rate_limits import (
    ApiRequestRateLimitService, InMemoryApiRequestRateLimitStore,
    ApiRequestRateLimitExceededError,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from services.construction.construction_content_translation import (
    ContentTranslationStatus as Status,
)
from test_construction_02_template_translation_workflow import (
    case, onboarding, catalog_case, copy_case, read_case, source_case,
    workflow_case, cache_rows, seed, domain_snapshot,
)

PREFIX = "/api/v1/construction/settings/measurement-templates"


@pytest.fixture
async def translation_http(workflow_case):
    c = workflow_case
    c.app = create_app()
    c.rates = ApiRequestRateLimitService(store=InMemoryApiRequestRateLimitStore())
    c.rates.ensure_authenticated_user_allowed = AsyncMock(
        wraps=c.rates.ensure_authenticated_user_allowed)

    async def session():
        if not c.sync.in_transaction():
            c.sync.begin()
        yield c.session

    async def context(scoped=Depends(get_construction_scoped_session)):
        assert scoped is c.session
        return await c.source_service.templates.authorizer.resolver.resolve(actor=c.actor)

    async def workflow(
        scoped=Depends(get_construction_scoped_session),
        platform=Depends(get_api_session),
    ):
        assert scoped is c.session and platform is c.session
        return c.translation_workflow

    c.workflow_calls = AsyncMock(wraps=c.translation_workflow.read_for_actor)
    c.translation_workflow.read_for_actor = c.workflow_calls
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_scoped_session] = session
    c.app.dependency_overrides[get_api_session] = lambda: c.session
    c.app.dependency_overrides[get_construction_access_context] = context
    c.app.dependency_overrides[routes.get_template_translation_workflow] = workflow
    c.app.dependency_overrides[routes.get_organization_template_service] = lambda: c.reader
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rates
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.module
    c.version_path = PREFIX + "/versions/" + str(c.base.template_version_id)
    c.text_path = c.version_path + "/texts/title"
    yield c


async def get(c, *, path=None, headers=None, content=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        return await client.request(
            "GET", c.text_path if path is None else path,
            headers={"X-Request-ID": "template-translation-http", **(headers or {})},
            content=content)


@pytest.mark.asyncio
async def test_translation_get_preserves_original_and_reuses_cache(translation_http):
    c = translation_http
    before, commits = domain_snapshot(c), c.session.commits
    first = await get(c, headers={"Accept-Language": "en"})
    assert first.status_code == 200, first.text
    data = first.json()["data"]
    assert data["tenant_id"] == str(c.tenant) and data["user_id"] == str(c.user)
    assert data["version_id"] == str(c.base.template_version_id)
    assert data["field_name"] == "title"
    assert data["source_language"] == "en" and data["target_language"] == "uk"
    assert data["source_version"] == 1
    assert data["original_text"] == "Starter" and data["display_text"] == "Переклад"
    assert data["translation_status"] == "AUTO_TRANSLATED"
    assert data["origin"] == "GENERATED" and data["cache_written"] is True
    assert data["cache_row_version"] == 1
    assert first.json()["request_id"] == "template-translation-http"
    assert first.headers["Cache-Control"] == "private, no-store"
    assert first.headers["Pragma"] == "no-cache"
    assert c.session.commits == commits + 1
    assert domain_snapshot(c) == before

    stored = cache_rows(c)
    second = await get(c)
    assert second.status_code == 200, second.text
    replay = second.json()["data"]
    assert replay["display_text"] == data["display_text"]
    assert replay["origin"] == "CACHE" and replay["cache_written"] is False
    assert cache_rows(c) == stored and c.session.commits == commits + 1
    c.provider.translate.assert_awaited_once()

    original = await get(c, path=c.version_path)
    assert original.status_code == 200, original.text
    assert original.json()["data"]["title"] == "Starter"
    assert original.json()["data"]["source_language"] == "en"
    assert domain_snapshot(c) == before


@pytest.mark.asyncio
async def test_original_language_description_needs_no_provider_or_write(translation_http):
    c = translation_http
    c.language = "en"
    commits = c.session.commits
    response = await get(c, path=c.version_path + "/texts/description")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["original_text"] == data["display_text"] == "Structure without prices"
    assert data["origin"] == "ORIGINAL" and data["translation_status"] is None
    assert data["cache_row_version"] is None and data["cache_written"] is False
    assert cache_rows(c) == () and c.session.commits == commits
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_verified_cache_is_returned_without_provider_call(translation_http):
    c = translation_http
    await seed(c, status=Status.VERIFIED, text="Перевірений переклад")
    before = cache_rows(c)
    response = await get(c)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["translation_status"] == "VERIFIED"
    assert data["origin"] == "CACHE" and data["cache_written"] is False
    assert data["display_text"] == "Перевірений переклад"
    assert cache_rows(c) == before
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [
    "?language=en", "?target_language=de", "?tenant_id=foreign",
    "?user_id=foreign", "?source_version=99",
])
async def test_caller_context_selectors_are_rejected(translation_http, suffix):
    c = translation_http
    before = cache_rows(c)
    response = await get(c, path=c.text_path + suffix)
    assert response.status_code == 422, response.text
    c.workflow_calls.assert_not_awaited()
    assert cache_rows(c) == before


@pytest.mark.asyncio
async def test_get_body_cannot_supply_source_text(translation_http):
    c = translation_http
    response = await get(
        c, content='{"source_text":"caller text"}',
        headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    c.workflow_calls.assert_not_awaited()
    assert cache_rows(c) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["phone", "address_raw", "labels", "formula"])
async def test_only_supported_text_fields_are_registered(translation_http, field):
    c = translation_http
    response = await get(c, path=c.version_path + "/texts/" + field)
    assert response.status_code == 422, response.text
    c.workflow_calls.assert_not_awaited()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_version_is_safe_not_found(translation_http):
    c = translation_http
    path = PREFIX + "/versions/" + str(uuid4()) + "/texts/title"
    response = await get(c, path=path)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"
    assert cache_rows(c) == ()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", [
    "authentication", "suite", "core", "rate", "membership", "scope",
])
async def test_shared_http_and_domain_gates_remain_required(translation_http, gate):
    c = translation_http
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
        c.module.require_enabled.side_effect = ModuleDisabledError()
    elif gate == "rate":
        c.rates.ensure_authenticated_user_allowed.side_effect = ApiRequestRateLimitExceededError()
        expected = 429
    elif gate == "membership":
        c.membership = False
    else:
        c.grant.role, c.grant.scope_type = "OBSERVER", "project"
    response = await get(c)
    assert response.status_code == expected, response.text
    assert cache_rows(c) == ()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["language", "membership", "suite", "core"])
async def test_changes_during_provider_are_mapped_without_persisting(translation_http, change):
    c = translation_http

    async def changed(**values):
        if change == "language":
            c.language = "it"
        elif change == "membership":
            c.membership = False
        elif change == "suite":
            c.suite.require_enabled.side_effect = SuiteDisabledError()
        else:
            c.module.require_enabled.side_effect = ModuleDisabledError()
        return "Переклад"
    c.provider.translate.side_effect = changed
    response = await get(c)
    assert response.status_code == (409 if change == "language" else 403), response.text
    if change == "language":
        assert response.json()["error"]["code"] == "LANGUAGE_CONTEXT_CHANGED"
    elif change in ("suite", "core"):
        assert response.json()["error"]["code"] == "MODULE_DISABLED"
    assert cache_rows(c) == ()


@pytest.mark.asyncio
async def test_inactive_profile_language_is_not_replaced_by_request_language(translation_http):
    c = translation_http
    c.language_active = False
    response = await get(c, headers={"Accept-Language": "en"})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "LANGUAGE_CONFIGURATION_REQUIRED"
    assert cache_rows(c) == ()


@pytest.mark.asyncio
async def test_provider_failure_is_sanitized_as_unavailable(translation_http):
    c = translation_http
    c.provider.translate.side_effect = RuntimeError("private provider credentials")
    response = await get(c)
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "TRANSLATION_UNAVAILABLE"
    assert "private provider credentials" not in response.text
    assert cache_rows(c) == ()


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_local_cache_insert(translation_http):
    c = translation_http
    c.session.fail_commit = True
    before, commits = domain_snapshot(c), c.session.commits
    response = await get(c)
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "internal_error"
    assert cache_rows(c) == () and domain_snapshot(c) == before
    assert c.session.commits == commits


@pytest.mark.asyncio
async def test_factory_configuration_error_is_sanitized(translation_http, monkeypatch):
    c = translation_http
    c.app.dependency_overrides.pop(routes.get_template_translation_workflow)

    def broken(**values):
        raise ValueError("private provider configuration")
    monkeypatch.setattr(routes, "build_construction_template_translation_workflow", broken)
    response = await get(c)
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "TRANSLATION_UNAVAILABLE"
    assert "private provider configuration" not in response.text
    assert cache_rows(c) == ()


@pytest.mark.asyncio
async def test_public_projection_rejects_foreign_result(translation_http):
    c = translation_http
    if not c.sync.in_transaction():
        c.sync.begin()
    good = await c.translation_workflow.read_for_actor(
        actor=c.actor, version_id=c.base.template_version_id, field_name="title")
    c.translation_workflow.read_for_actor = AsyncMock(
        return_value=replace(good, user_id=uuid4()))
    before = cache_rows(c)
    response = await get(c)
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "internal_error"
    assert cache_rows(c) == before


@pytest.mark.asyncio
async def test_wire_openapi_describes_profile_language_read_and_nullable_exact_versions(translation_http):
    c = translation_http
    response = await get(c, path="/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    document = response.json()
    path = PREFIX + "/versions/{version_id}/texts/{field_name}"
    operation = document["paths"][path]["get"]
    parameters = {p["name"]: p for p in operation.get("parameters", [])}
    assert set(parameters) == {"version_id", "field_name"}
    assert set(parameters["field_name"]["schema"]["enum"]) == {"title", "description"}
    assert "requestBody" not in operation
    assert {"409", "503", "429"}.issubset(operation["responses"])
    schema = next(
        item for item in document["components"]["schemas"].values()
        if item.get("title") == "TemplateTranslatedTextData")
    assert schema["additionalProperties"] is False
    for name in ("source_version", "cache_row_version"):
        branches = schema["properties"][name]["anyOf"]
        integer = next(branch for branch in branches if branch.get("type") == "integer")
        assert any(branch.get("type") == "null" for branch in branches)
        assert type(integer["maximum"]) is int and integer["maximum"] == 2**63 - 1
        assert integer["minimum"] == 1 and integer["format"] == "int64"
