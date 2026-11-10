"""Starter localization through the shared local source/cache workflow."""
from dataclasses import replace
from uuid import UUID

import pytest
from fastapi import Depends
from sqlalchemy import update

from api.dependencies import get_api_session
from api.routes import construction_starter_templates as routes
from api.routes.construction import get_construction_scoped_session
from services.construction.construction_starter_translation import (
    ConstructionStarterTranslationSourceService,
    build_construction_starter_translation_workflow,
)
from services.construction.construction_template_translation_workflow import (
    ConstructionTemplateTranslationWorkflow,
)
from services.construction.construction_starter_template_definitions import (
    validate_starter_definition,
)
from test_construction_02_template_translation_http import (
    case, onboarding, catalog_case, copy_case, read_case, source_case,
    workflow_case, translation_http, get, cache_rows, domain_snapshot,
)

PREFIX = "/api/v1/construction/settings/starter-measurement-templates"


@pytest.fixture
async def starter_http(translation_http):
    c = translation_http
    source = ConstructionStarterTranslationSourceService(
        session=c.session, platform_session=c.session,
        starter_service=c.catalog,
        identity=c.identity, language_repository=c.languages)
    c.starter_translation = ConstructionTemplateTranslationWorkflow(
        session=c.session, source_service=source,
        cache_repository=c.translation_repo, translator=c.translator,
        suite_access=c.suite, module_access=c.module)

    async def workflow(
        scoped=Depends(get_construction_scoped_session),
        platform=Depends(get_api_session),
    ):
        assert scoped is c.session and platform is c.session
        return c.starter_translation

    c.app.dependency_overrides[routes.get_starter_translation_workflow] = workflow
    c.app.dependency_overrides[routes.get_starter_template_service] = lambda: c.catalog
    # SECOND has never been copied by the parent fixture.
    c.starter_version = UUID(int=2001)
    c.starter_path = PREFIX + "/versions/" + str(c.starter_version)
    yield c


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
@pytest.mark.parametrize("suffix,original", [
    ("/texts/title", "Starter"),
    ("/texts/description", "Structure without prices"),
    ("/labels/area", "Measured area"),
    ("/labels/yes", "Yes"),
])
async def test_starter_text_uses_shared_cache_without_copying_or_catalog_mutation(
    starter_http, role, suffix, original,
):
    c = starter_http
    c.grant.role = role
    before, commits = domain_snapshot(c), c.session.commits
    response = await get(c, path=c.starter_path + suffix)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["version_id"] == str(c.starter_version)
    assert data["template_id"] == str(UUID(int=202))
    assert data["tenant_id"] == str(c.tenant)
    assert data["original_text"] == original and data["display_text"] == "Переклад"
    assert data["source_language"] == "en" and data["target_language"] == "uk"
    assert data["origin"] == "GENERATED" and data["cache_written"] is True
    assert data["translation_status"] == "AUTO_TRANSLATED"
    assert domain_snapshot(c) == before and c.session.commits == commits + 1
    row, = cache_rows(c)
    assert row["entity_type"] == "construction.starter_measurement_template_version"
    assert row["entity_id"] == c.starter_version
    c.provider.translate.assert_awaited_once_with(
        text=original, source_language="en", target_language="uk")

    saved = cache_rows(c)
    second = await get(c, path=c.starter_path + suffix)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["origin"] == "CACHE"
    assert second.json()["data"]["cache_written"] is False
    assert cache_rows(c) == saved and domain_snapshot(c) == before
    c.provider.translate.assert_awaited_once()
    assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.asyncio
async def test_starter_translation_works_without_any_organization_copy(starter_http):
    c = starter_http
    # Remove only local in-memory fixture copies. No runtime database is used.
    for table in reversed(c.copy_tables):
        c.sync.execute(table.delete())
    c.sync.commit()
    before = domain_snapshot(c)
    assert all(not rows for rows in before[:4])
    response = await get(c, path=c.starter_path + "/labels/area")
    assert response.status_code == 200, response.text
    assert domain_snapshot(c) == before
    assert len(cache_rows(c)) == 1


@pytest.mark.asyncio
async def test_global_source_cache_remains_tenant_scoped(starter_http):
    c = starter_http
    first_tenant = c.tenant
    first = await get(c, path=c.starter_path + "/texts/title")
    assert first.status_code == 200, first.text
    c.tenant = c.foreign
    c.actor = replace(c.actor, tenant_id=c.tenant)
    c.grant.scope_id = c.tenant
    second = await get(c, path=c.starter_path + "/texts/title")
    assert second.status_code == 200, second.text
    rows = cache_rows(c)
    assert {row["tenant_id"] for row in rows} == {first_tenant, c.tenant}
    assert len(rows) == 2 and c.provider.translate.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("number", [1003, 3001, 9999])
async def test_future_inactive_and_missing_starter_versions_are_hidden(starter_http, number):
    c = starter_http
    response = await get(
        c, path=PREFIX + "/versions/" + str(UUID(int=number)) + "/texts/title")
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"
    assert cache_rows(c) == ()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_old_and_new_versions_do_not_share_cache_records(starter_http):
    c = starter_http
    old = await get(c, path=PREFIX + "/versions/" + str(UUID(int=1001)) + "/texts/title")
    new = await get(c, path=PREFIX + "/versions/" + str(UUID(int=1002)) + "/texts/title")
    assert old.status_code == new.status_code == 200
    assert old.json()["data"]["source_version"] == 1
    assert new.json()["data"]["source_version"] == 2
    assert len(cache_rows(c)) == 2
    assert c.provider.translate.await_count == 2


@pytest.mark.asyncio
async def test_supplied_starter_translation_is_used_without_cache_review_invention(starter_http):
    c = starter_http
    if not c.sync.in_transaction():
        c.sync.begin()
    state = await c.catalog.get_version(actor=c.actor, version_id=c.starter_version)
    body = state.definition.payload()
    body["labels"]["uk"] = {"area": "Готова площа"}
    checked = validate_starter_definition(body, source_language="en")
    # Local fixture only; production source publication remains immutable.
    c.sync.execute(update(c.catalog_versions).where(
        c.catalog_versions.c.id == c.starter_version
    ).values(definition=checked.payload(), content_hash=checked.content_hash))
    c.sync.commit()
    response = await get(c, path=c.starter_path + "/labels/area")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["display_text"] == "Готова площа"
    assert data["origin"] == "PUBLISHED_LABEL"
    assert data["translation_status"] is None and data["cache_written"] is False
    assert cache_rows(c) == ()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_source_language_preserves_original(starter_http):
    c = starter_http
    c.language = "en"
    response = await get(c, path=c.starter_path + "/labels/yes")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["original_text"] == data["display_text"] == "Yes"
    assert data["origin"] == "ORIGINAL" and data["cache_written"] is False
    assert cache_rows(c) == ()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["Area", "missing", "DIRECT", "YES"])
async def test_unknown_keys_and_machine_values_are_not_guessed(starter_http, key):
    c = starter_http
    response = await get(c, path=c.starter_path + "/labels/" + key)
    assert response.status_code == 404, response.text
    assert cache_rows(c) == ()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["?language=en", "?tenant_id=foreign", "?source_text=caller"])
async def test_starter_reads_reject_caller_context_selectors(starter_http, query):
    c = starter_http
    response = await get(c, path=c.starter_path + "/texts/title" + query)
    assert response.status_code == 422, response.text
    assert cache_rows(c) == ()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["language", "grant"])
async def test_late_profile_or_access_change_prevents_cache_write(starter_http, change):
    c = starter_http
    async def changed(**values):
        if change == "language":
            c.language = "it"
        else:
            c.grant.status = "revoked"
        return "Переклад"
    c.provider.translate.side_effect = changed
    response = await get(c, path=c.starter_path + "/texts/title")
    assert response.status_code == (409 if change == "language" else 403), response.text
    assert cache_rows(c) == ()


@pytest.mark.asyncio
async def test_provider_failure_is_sanitized_without_catalog_or_copy_writes(starter_http):
    c = starter_http
    c.provider.translate.side_effect = RuntimeError("private provider failure")
    before = domain_snapshot(c)
    response = await get(c, path=c.starter_path + "/labels/area")
    assert response.status_code == 503, response.text
    assert "private provider failure" not in response.text
    assert cache_rows(c) == () and domain_snapshot(c) == before


@pytest.mark.asyncio
async def test_cache_commit_failure_rolls_back_insert(starter_http):
    c = starter_http
    c.session.fail_commit = True
    before, commits = domain_snapshot(c), c.session.commits
    response = await get(c, path=c.starter_path + "/labels/area")
    assert response.status_code == 500, response.text
    assert cache_rows(c) == () and domain_snapshot(c) == before
    assert c.session.commits == commits


@pytest.mark.asyncio
async def test_original_catalog_read_remains_in_source_language(starter_http):
    c = starter_http
    assert (await get(c, path=c.starter_path + "/texts/title")).status_code == 200
    response = await get(c, path=c.starter_path)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["title"] == "Starter" and data["source_language"] == "en"
    question = data["definition"]["sections"][0]["questions"][1]
    assert question["unit_code"] == "M2" and question["formula_type"] == "DIRECT"


@pytest.mark.asyncio
async def test_starter_openapi_has_no_language_selector_or_replay_key(starter_http):
    c = starter_http
    response = await get(c, path="/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    document = response.json()
    for suffix, names in (
        ("/texts/{field_name}", {"version_id", "field_name"}),
        ("/labels/{label_key}", {"version_id", "label_key"}),
    ):
        operation = document["paths"][PREFIX + "/versions/{version_id}" + suffix]["get"]
        assert {item["name"] for item in operation["parameters"]} == names
        assert "requestBody" not in operation
        assert {"409", "503", "429"}.issubset(operation["responses"])


def test_factory_binds_existing_sessions_and_provider_without_io(starter_http):
    c = starter_http
    workflow = build_construction_starter_translation_workflow(
        session=c.session, platform_session=c.session,
        provider=c.provider, now_provider=lambda: c.now)
    assert workflow.session is c.session
    assert workflow.cache.session is c.session
    assert workflow.sources.templates.session is c.session
    assert workflow.sources.identity.session is c.session
    assert workflow.translator.provider is c.provider
