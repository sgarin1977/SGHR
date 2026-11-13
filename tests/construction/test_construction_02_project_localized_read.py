"""Existing GET, real local cache workflow and fake provider; no native claims."""
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Literal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import Depends, Query
from sqlalchemy import update

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import (
    get_api_request_rate_limit_service,
    get_tenant_suite_access_service, get_tenant_module_access_service,
)
from api.routes.construction import (
    get_construction_scoped_session, get_construction_access_context,
)
from api.routes import construction_project_read as routes
from api import construction_project_localization as localization
from services.api_rate_limits import (
    ApiRequestRateLimitService, InMemoryApiRequestRateLimitStore,
)
from services.construction.construction_project_translation_batch import (
    ConstructionProjectTranslationBatchWorkflow,
)
from test_construction_01_project_read_http import project_http as legacy_project_http
from test_construction_02_project_translation import (
    case, onboarding, catalog_case, copy_case, read_case, source_case,
    workflow_case, project_case, cache_rows, domain_snapshot,
)


@pytest.fixture
async def localized_http(project_case, legacy_project_http, monkeypatch):
    c = project_case
    for name, value in vars(legacy_project_http.project).items():
        if not hasattr(c.project, name):
            setattr(c.project, name, value)
    c.project.created_by = c.user
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
        return await c.project_workflow.resolver.resolve(actor=c.actor)
    async def platform(
        include_translations: Literal["false", "true"] = Query(default="false"),
    ):
        yield c.session if include_translations == "true" else None

    c.build = MagicMock(return_value=c.project_workflow)
    monkeypatch.setattr(
        localization, "build_construction_project_translation_workflow", c.build)
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_scoped_session] = session
    c.app.dependency_overrides[get_construction_access_context] = context
    c.app.dependency_overrides[localization.get_project_localization_platform_session] = platform
    c.app.dependency_overrides[routes.get_project_read_service] = lambda: c.project_workflow.projects
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rates
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.module
    c.path = "/api/v1/construction/projects/" + str(c.project.id)

    async def translate(**values):
        assert c.serialization.lock_member.await_count == 0
        assert c.project_repository.get_active_project_for_update.await_count == 0
        assert values["source_language"] == "en" and values["target_language"] == "uk"
        return {"New home": "Новий дім", "Call tomorrow": "Зателефонувати завтра"}[values["text"]]
    c.provider.translate.side_effect = translate
    yield c


async def get(c, *, localized=True, path=None, content=None):
    if path is None:
        path = c.path + ("?include_translations=true" if localized else "")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        return await client.request(
            "GET", path, content=content,
            headers={"X-Request-ID": "localized-project", "Accept-Language": "en"})


@pytest.mark.asyncio
async def test_both_fields_commit_once_then_use_cache(localized_http):
    c = localized_http
    before, commits = domain_snapshot(c), c.session.commits
    first = await get(c)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["data"]["name"] == "New home"
    assert body["data"]["comment"] == "Call tomorrow"
    assert body["data"]["address_raw"] == "Original address"
    translated = body["meta"]["translations"]
    assert translated["target_language"] == "uk" and translated["source_version"] == 1
    assert translated["name"]["display_text"] == "Новий дім"
    assert translated["comment"]["display_text"] == "Зателефонувати завтра"
    assert translated["name"]["cache_written"] and translated["comment"]["cache_written"]
    assert first.headers["Cache-Control"] == "private, no-store"
    assert len(cache_rows(c)) == 2 and c.session.commits == commits + 1
    assert domain_snapshot(c) == before
    assert c.serialization.lock_member.await_count == 1
    assert c.project_repository.get_active_project_for_update.await_count == 1
    stored = cache_rows(c)
    second = await get(c)
    assert second.status_code == 200, second.text
    assert not second.json()["meta"]["translations"]["name"]["cache_written"]
    assert not second.json()["meta"]["translations"]["comment"]["cache_written"]
    assert cache_rows(c) == stored and c.session.commits == commits + 1
    assert c.provider.translate.await_count == c.provider.detect_language.await_count == 2
    assert c.rates.ensure_authenticated_user_allowed.await_count == 2


@pytest.mark.asyncio
async def test_default_get_keeps_existing_contract_without_translation_runtime(localized_http):
    c = localized_http
    c.build.side_effect = ValueError("private broken translation configuration")
    response = await get(c, localized=False)
    assert response.status_code == 200, response.text
    assert set(response.json()["meta"]) == {"capabilities"}
    assert response.json()["data"]["name"] == "New home"
    c.build.assert_not_called()
    c.provider.translate.assert_not_awaited()
    c.provider.detect_language.assert_not_awaited()
    assert cache_rows(c) == ()


@pytest.mark.asyncio
async def test_absent_comment_is_preserved_without_a_second_provider_call(localized_http):
    c = localized_http
    c.project.comment = None
    response = await get(c)
    assert response.status_code == 200, response.text
    comment = response.json()["meta"]["translations"]["comment"]
    assert comment["original_text"] is None and comment["display_text"] is None
    assert comment["translation_status"] is None and comment["source_language"] is None
    assert len(cache_rows(c)) == 1
    c.provider.translate.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["second_provider", "second_insert", "commit"])
async def test_failures_roll_back_the_whole_batch(localized_http, failure):
    c = localized_http
    before, commits = domain_snapshot(c), c.session.commits
    if failure == "second_provider":
        c.provider.translate.side_effect = ["Переклад", RuntimeError("private provider failure")]
    elif failure == "second_insert":
        original = c.project_cache.insert_automatic
        count = 0
        async def fail_after_real_insert(**values):
            nonlocal count
            count += 1
            saved = await original(**values)
            if count == 2:
                raise RuntimeError("private insert failure")
            return saved
        c.project_cache.insert_automatic = fail_after_real_insert
    else:
        c.session.fail_commit = True
    response = await get(c)
    assert response.status_code == (503 if failure == "second_provider" else 500), response.text
    assert "private" not in response.text
    assert cache_rows(c) == () and domain_snapshot(c) == before
    assert c.session.commits == commits


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["version", "language", "membership", "scope"])
async def test_changes_during_provider_do_not_persist_translations(localized_http, change):
    c = localized_http
    async def changed(**values):
        if change == "version":
            c.project.row_version += 1
        elif change == "language":
            c.language = "it"
        elif change == "membership":
            c.membership = False
        else:
            c.grant.scope_type = "project"
            c.grant.role = "PROJECT_MANAGER"
            c.grant.scope_id = uuid4()
        return "Переклад"
    c.provider.translate.side_effect = changed
    response = await get(c)
    assert response.status_code == (409 if change in ("version", "language") else 403), response.text
    assert cache_rows(c) == ()


@pytest.mark.asyncio
async def test_late_membership_loss_rolls_back_already_flushed_cache(localized_http):
    c = localized_http
    original = c.project_cache.insert_automatic
    count = 0
    async def revoke_after_insert(**values):
        nonlocal count
        saved = await original(**values)
        count += 1
        if count == 1:
            c.membership = False
        return saved
    c.project_cache.insert_automatic = revoke_after_insert
    response = await get(c)
    assert response.status_code == 403, response.text
    assert cache_rows(c) == ()


@pytest.mark.asyncio
async def test_capabilities_use_final_fresh_grants(localized_http):
    c = localized_http
    async def changed(**values):
        c.grant.role, c.grant.scope_type, c.grant.scope_id = (
            "OBSERVER", "project", c.project.id)
        return "Переклад"
    c.provider.translate.side_effect = changed
    response = await get(c)
    assert response.status_code == 200, response.text
    assert response.json()["meta"]["capabilities"] == {
        "can_edit_project": False, "can_plan_activity": False, "can_open_team": False,
        "can_read_pricing_adjustments": True, "can_edit_pricing_adjustments": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [
    "&language=en", "&tenant_id=foreign", "&source_text=caller",
    "&include_translations=false",
])
async def test_localization_selectors_are_bounded(localized_http, suffix):
    c = localized_http
    response = await get(c, path=c.path + "?include_translations=true" + suffix)
    assert response.status_code == 422, response.text
    c.provider.translate.assert_not_awaited()
    assert cache_rows(c) == ()


@pytest.mark.asyncio
async def test_localized_get_rejects_a_source_body(localized_http):
    c = localized_http
    response = await get(c, content='{"source_text":"caller"}')
    assert response.status_code == 422, response.text
    c.provider.detect_language.assert_not_awaited()


@pytest.mark.asyncio
async def test_unavailable_profile_language_has_no_request_language_fallback(localized_http):
    c = localized_http
    c.language_active = False
    response = await get(c)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "LANGUAGE_CONFIGURATION_REQUIRED"
    assert cache_rows(c) == ()


@pytest.mark.asyncio
async def test_verified_field_is_reused_without_provider(localized_http):
    c = localized_http
    assert (await get(c)).status_code == 200
    c.sync.execute(update(c.translation_table).where(
        c.translation_table.c.entity_id == c.project.id,
        c.translation_table.c.field_name == "name",
    ).values(translation_status="VERIFIED", reviewed_by=c.user))
    c.sync.commit()
    c.provider.translate.reset_mock()
    c.provider.detect_language.reset_mock()
    response = await get(c)
    assert response.status_code == 200, response.text
    name = response.json()["meta"]["translations"]["name"]
    assert name["translation_status"] == "VERIFIED" and name["reviewed_by"] == str(c.user)
    c.provider.translate.assert_not_awaited()
    c.provider.detect_language.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_localization_dependency_opens_no_platform_session(monkeypatch):
    async def forbidden():
        raise AssertionError("Platform session must not be opened.")
        yield None
    monkeypatch.setattr(localization, "get_api_session", forbidden)
    iterator = localization.get_project_localization_platform_session(
        include_translations="false")
    assert await anext(iterator) is None
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)


@pytest.mark.asyncio
async def test_wire_openapi_extends_existing_get_only(localized_http):
    c = localized_http
    response = await get(c, path="/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    document = response.json()
    path = "/api/v1/construction/projects/{id}"
    operation = document["paths"][path]["get"]
    parameters = {item["name"]: item for item in operation["parameters"]}
    assert parameters["include_translations"]["schema"]["default"] == "false"
    assert set(parameters["include_translations"]["schema"]["enum"]) == {"false", "true"}
    assert "/api/v1/construction/projects/{id}/texts/{field_name}" not in document["paths"]
    schemas = document["components"]["schemas"].values()
    for title, name in (
        ("ProjectTranslationMetadata", "source_version"),
        ("ProjectTranslatedField", "cache_row_version"),
    ):
        schema = next(item for item in schemas if item.get("title") == title)
        field = schema["properties"][name]
        branches = [field] if field.get("type") == "integer" else field["anyOf"]
        integer = next(item for item in branches if item.get("type") == "integer")
        assert type(integer["maximum"]) is int
        assert integer["maximum"] == 2**63 - 1 and integer["minimum"] == 1
