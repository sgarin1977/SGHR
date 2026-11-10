"""Registered cache-state GETs over local SQL; no external provider operations."""
from dataclasses import replace
from datetime import datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from api.auth import get_current_actor
from api.errors import ApiHttpError
from api.routes import construction_template_translation_state as routes
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.module_access import ModuleDisabledError, SuiteDisabledError
from services.construction.construction_content_translation import (
    ContentTranslationStatus as Status,
)
from services.construction.construction_template_translation_source import (
    template_label_field,
)
from services.construction.construction_template_translation_state import (
    ConstructionTemplateTranslationStateService,
)
from test_construction_02_starter_translation import (
    case, onboarding, catalog_case, copy_case, read_case, source_case,
    workflow_case, translation_http, starter_http,
    get, cache_rows, domain_snapshot,
)
from test_construction_02_template_translation_state import seed


@pytest.fixture(params=["organization", "starter"])
async def state_http(request, starter_http):
    c = starter_http
    if request.param == "organization":
        c.state_workflow = c.translation_workflow
        c.state_version = c.base.template_version_id
        c.state_base = c.version_path
        c.state_dependency = routes.get_org_translation_state_service
        c.base_dependency = routes.get_org_workflow
    else:
        c.state_workflow = c.starter_translation
        c.state_version = c.starter_version
        c.state_base = c.starter_path
        c.state_dependency = routes.get_starter_translation_state_service
        c.base_dependency = routes.get_starter_workflow

    c.state_path = c.state_base + "/texts/title/translation-state"
    c.states = ConstructionTemplateTranslationStateService(workflow=c.state_workflow)
    c.state_calls = AsyncMock(wraps=c.states.read_for_actor)
    c.states.read_for_actor = c.state_calls
    c.app.dependency_overrides[c.state_dependency] = lambda: c.states
    yield c


@pytest.mark.asyncio
async def test_missing_cache_is_reported_without_generating_it(state_http):
    c = state_http
    before, commits = domain_snapshot(c), c.session.commits
    response = await get(c, path=c.state_path, headers={"Accept-Language": "en"})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["tenant_id"] == str(c.tenant) and data["user_id"] == str(c.user)
    assert data["version_id"] == str(c.state_version)
    assert data["field_name"] == "title"
    assert data["source_language"] == "en" and data["target_language"] == "uk"
    assert data["source_version"] == 1 and len(data["source_text_hash"]) == 64
    assert data["cache_present"] is False and data["usable"] is False
    assert all(data[name] is None for name in (
        "cache_id", "translation_status", "translated_at",
        "reviewed_by", "cache_row_version"))
    assert "translated_text" not in data and "original_text" not in data
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert response.json()["request_id"] == "template-translation-http"
    assert cache_rows(c) == () and domain_snapshot(c) == before
    assert c.session.commits == commits
    c.provider.translate.assert_not_awaited()
    c.rates.ensure_authenticated_user_allowed.assert_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [
    Status.VERIFIED, Status.AUTO_TRANSLATED, Status.STALE,
])
@pytest.mark.parametrize("selector", ["title", "label"])
async def test_exact_state_timestamp_and_reviewer_are_preserved(
    state_http, status, selector,
):
    c = state_http
    field = "title" if selector == "title" else template_label_field("area")
    saved = await seed(c, status=status, field=field)
    path = (
        c.state_path if selector == "title" else
        c.state_base + "/labels/area/translation-state")
    before, domain, commits = cache_rows(c), domain_snapshot(c), c.session.commits
    response = await get(c, path=path)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["field_name"] == field
    assert data["translation_status"] == status.value
    assert data["cache_id"] == str(saved["id"])
    assert data["cache_row_version"] == 1
    assert data["cache_present"] is True
    assert data["usable"] is (status is not Status.STALE)
    assert datetime.fromisoformat(
        data["translated_at"].replace("Z", "+00:00")) == c.now
    assert data["reviewed_by"] == (str(c.user) if status is Status.VERIFIED else None)
    if selector == "label":
        assert data["label_key"] == "area"
    assert cache_rows(c) == before and domain_snapshot(c) == domain
    assert c.session.commits == commits
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [
    "?tenant_id=foreign", "?user_id=foreign",
    "?target_language=en", "?source_version=99",
])
async def test_context_selectors_are_rejected_before_service(state_http, suffix):
    c = state_http
    response = await get(c, path=c.state_path + suffix)
    assert response.status_code == 422, response.text
    c.state_calls.assert_not_awaited()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_body_is_not_a_source_or_cache_selector(state_http):
    c = state_http
    response = await get(
        c, path=c.state_path, content='{"cache_id":"foreign"}',
        headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    c.state_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", [
    "authentication", "membership", "scope", "suite", "core", "rate",
])
async def test_existing_http_and_domain_boundaries_remain_required(state_http, gate):
    c = state_http
    expected = 403
    if gate == "authentication":
        async def denied():
            raise ApiHttpError(
                status_code=401, code="authentication_required",
                message="Authentication required.")
        c.app.dependency_overrides[get_current_actor] = denied
        expected = 401
    elif gate == "membership":
        c.membership = False
    elif gate == "scope":
        c.grant.role, c.grant.scope_type = "OBSERVER", "project"
    elif gate == "suite":
        c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif gate == "core":
        c.module.require_enabled.side_effect = ModuleDisabledError()
    else:
        c.rates.ensure_authenticated_user_allowed.side_effect = (
            ApiRequestRateLimitExceededError())
        expected = 429
    response = await get(c, path=c.state_path)
    assert response.status_code == expected, response.text
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["version", "label"])
async def test_unavailable_source_is_safe_not_found(state_http, missing):
    c = state_http
    if missing == "version":
        base = c.state_base.rsplit("/", 1)[0] + "/" + str(uuid4())
        path = base + "/texts/title/translation-state"
    else:
        path = c.state_base + "/labels/missing/translation-state"
    response = await get(c, path=path)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["language", "membership"])
async def test_context_is_rechecked_after_cache_query(state_http, change):
    c = state_http
    await seed(c)
    original = c.translation_repo.get_exact

    async def changed(**values):
        row = await original(**values)
        if change == "language":
            c.language = "it"
        else:
            c.membership = False
        return row

    c.translation_repo.get_exact = changed
    before, commits = cache_rows(c), c.session.commits
    response = await get(c, path=c.state_path)
    assert response.status_code == (409 if change == "language" else 403), response.text
    if change == "language":
        assert response.json()["error"]["code"] == "LANGUAGE_CONTEXT_CHANGED"
    assert cache_rows(c) == before and c.session.commits == commits
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_repository_failure_is_sanitized(state_http):
    c = state_http
    c.translation_repo.get_exact = AsyncMock(
        side_effect=RuntimeError("private database credentials"))
    response = await get(c, path=c.state_path)
    assert response.status_code == 500, response.text
    assert "private database credentials" not in response.text
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong", ["user", "stale_usable", "missing_date"])
async def test_public_projection_rejects_wrong_identity_or_incoherent_state(
    state_http, wrong,
):
    c = state_http
    await seed(c, status=Status.STALE)
    if not c.sync.in_transaction():
        c.sync.begin()
    good = await c.states.read_for_actor(
        actor=c.actor, version_id=c.state_version, field_name="title")
    altered = (
        replace(good, user_id=uuid4()) if wrong == "user"
        else replace(good, usable=True) if wrong == "stale_usable"
        else replace(good, translated_at=None)
    )
    c.states.read_for_actor = AsyncMock(return_value=altered)
    before = cache_rows(c)
    response = await get(c, path=c.state_path)
    assert response.status_code == 500, response.text
    assert cache_rows(c) == before


@pytest.mark.asyncio
async def test_registered_factory_wraps_existing_shared_workflow(state_http):
    c = state_http
    c.app.dependency_overrides.pop(c.state_dependency)
    response = await get(c, path=c.state_path)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["cache_present"] is False
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_runtime_collaborator_is_sanitized_as_unavailable(state_http):
    c = state_http
    c.app.dependency_overrides.pop(c.state_dependency)
    c.app.dependency_overrides[c.base_dependency] = lambda: object()
    response = await get(c, path=c.state_path)
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "service_unavailable"
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_source_and_translation_reads_remain_available(state_http):
    c = state_http
    source = await get(c, path=c.state_base)
    assert source.status_code == 200, source.text
    assert source.json()["data"]["source_language"] == "en"
    translated = await get(c, path=c.state_base + "/texts/title")
    assert translated.status_code == 200, translated.text
    state = await get(c, path=c.state_path)
    assert state.status_code == 200, state.text
    assert state.json()["data"]["translation_status"] == "AUTO_TRANSLATED"
    assert c.provider.translate.await_count == 1


@pytest.mark.asyncio
async def test_openapi_describes_four_reads_and_exact_nullable_versions(state_http):
    c = state_http
    response = await get(c, path="/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    document = response.json()
    for kind in ("measurement-templates", "starter-measurement-templates"):
        base = "/api/v1/construction/settings/" + kind + "/versions/{version_id}"
        for selector, parameter in (
            ("texts/{field_name}", "field_name"),
            ("labels/{label_key}", "label_key"),
        ):
            operation = document["paths"][
                base + "/" + selector + "/translation-state"]["get"]
            assert {
                item["name"] for item in operation["parameters"]
            } == {"version_id", parameter}
            assert "requestBody" not in operation
            assert {"403", "404", "409", "429", "503"}.issubset(operation["responses"])

    for title in ("TranslationStateData", "LabelTranslationStateData"):
        schema = next(
            item for item in document["components"]["schemas"].values()
            if item.get("title") == title)
        assert schema["additionalProperties"] is False
        assert "translated_text" not in schema["properties"]
        for name in ("source_version", "cache_row_version"):
            integer = next(
                branch for branch in schema["properties"][name]["anyOf"]
                if branch.get("type") == "integer")
            assert type(integer["maximum"]) is int
            assert integer["maximum"] == 2**63 - 1
            assert integer["format"] == "int64"
