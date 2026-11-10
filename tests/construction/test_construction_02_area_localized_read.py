"""Existing area GET with local SQLite translation cache and fake provider."""
import asyncio
from types import SimpleNamespace as NS
from typing import Literal
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Depends, Query
from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import (
    get_api_request_rate_limit_service,
    get_tenant_suite_access_service, get_tenant_module_access_service,
)
from api.routes.construction import (
    get_construction_scoped_session, get_construction_access_context,
)
from api.routes import construction_project_areas as routes
from api import construction_area_localization as localization
from services.api_rate_limits import ApiRequestRateLimitService, InMemoryApiRequestRateLimitStore
from services.construction.construction_area_read import ConstructionProjectAreaReadService
from services.construction.construction_area_translation import ConstructionAreaTranslationWorkflow
from database.repositories.construction.construction_project_content_translations import (
    ConstructionProjectContentTranslationRepository,
)
from test_construction_02_project_translation import (
    case, onboarding, catalog_case, copy_case, read_case, source_case,
    workflow_case, project_case, cache_rows, domain_snapshot,
)


@pytest.fixture
async def area_localized(project_case, monkeypatch):
    c = project_case
    c.project.status = "active"
    def area(number, name):
        return NS(
            id=UUID(int=number), tenant_id=c.tenant, project_id=c.project.id,
            name=name, area_type="ROOM", sort_order=number,
            status="draft", row_version=1, deleted_at=None,
            private_marker="private-area-field")
    c.make_area = area
    c.area_rows = [area(100, "Kitchen"), area(200, "Bathroom")]
    async def visible(**values):
        assert values == {"tenant_id": c.tenant, "project_id": c.project.id}
        return [
            item for item in c.area_rows
            if item.deleted_at is None
            and item.tenant_id == c.tenant and item.project_id == c.project.id
        ]
    c.universal_area_repo = NS(
        session=c.session, list_active_areas=AsyncMock(side_effect=visible))
    c.area_read_service = ConstructionProjectAreaReadService(
        repository=c.universal_area_repo,
        project_repository=c.project_repository, access_repository=c.access)
    class LocalCache(ConstructionProjectContentTranslationRepository):
        def _insert(self):
            return sqlite_insert(self.table)
    c.area_cache = LocalCache(c.session)
    c.area_cache.table = c.translation_table
    def build(*, session, platform_session, area_ids):
        assert session is c.session and platform_session is c.session
        return ConstructionAreaTranslationWorkflow(
            project_workflow=c.project_workflow, area_service=c.area_read_service,
            cache_repository=c.area_cache, area_ids=area_ids)
    c.area_build = MagicMock(side_effect=build)
    monkeypatch.setattr(localization, "build_construction_area_translation_workflow", c.area_build)
    c.app = create_app()
    c.rates = ApiRequestRateLimitService(store=InMemoryApiRequestRateLimitStore())
    async def session():
        if not c.sync.in_transaction():
            c.sync.begin()
        yield c.session
    async def context(scoped=Depends(get_construction_scoped_session)):
        return await c.project_workflow.resolver.resolve(actor=c.actor)
    async def platform(
        include_translations: Literal["false", "true"] = Query(default="false"),
    ):
        yield c.session if include_translations == "true" else None
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_scoped_session] = session
    c.app.dependency_overrides[get_construction_access_context] = context
    c.app.dependency_overrides[localization.get_project_localization_platform_session] = platform
    c.app.dependency_overrides[routes.get_project_area_read_service] = lambda: c.area_read_service
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rates
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.module
    c.provider.translate.side_effect = lambda **values: "uk " + values["text"]
    c.path = "/api/v1/construction/projects/" + str(c.project.id) + "/areas"
    yield c


async def get(c, *, localized=True, suffix="", content=None):
    path = c.path + ("?include_translations=true" if localized else "") + suffix
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        return await client.request(
            "GET", path, content=content,
            headers={"X-Request-ID": "area-localization", "Accept-Language": "en"})


@pytest.mark.asyncio
async def test_area_names_commit_once_then_use_cache(area_localized):
    c = area_localized
    before, commits = domain_snapshot(c), c.session.commits
    async def translate(**values):
        assert c.serialization.lock_member.await_count == 0
        assert c.project_repository.get_active_project_for_update.await_count == 0
        assert values["source_language"] == "en" and values["target_language"] == "uk"
        return "uk " + values["text"]
    c.provider.translate.side_effect = translate
    response = await get(c)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["name"] for item in body["data"]] == ["Kitchen", "Bathroom"]
    translated = body["meta"]["translations"]
    assert translated["target_language"] == "uk"
    assert [item["display_text"] for item in translated["items"]] == ["uk Kitchen", "uk Bathroom"]
    assert all(item["source_version"] == 1 and item["cache_written"] for item in translated["items"])
    assert "private-area-field" not in response.text
    assert len(cache_rows(c)) == 2 and c.session.commits == commits + 1
    assert domain_snapshot(c) == before
    assert c.serialization.lock_member.await_count == 1
    assert c.project_repository.get_active_project_for_update.await_count == 1
    stored = cache_rows(c)
    second = await get(c)
    assert second.status_code == 200, second.text
    assert all(not item["cache_written"] for item in second.json()["meta"]["translations"]["items"])
    assert cache_rows(c) == stored and c.session.commits == commits + 1
    assert c.provider.translate.await_count == c.provider.detect_language.await_count == 2


@pytest.mark.asyncio
async def test_default_list_does_not_build_translation_runtime(area_localized):
    c = area_localized
    c.area_build.side_effect = ValueError("private broken configuration")
    response = await get(c, localized=False)
    assert response.status_code == 200, response.text
    assert set(response.json()["meta"]) == {"capabilities"}
    c.area_build.assert_not_called()
    c.provider.detect_language.assert_not_awaited()
    assert cache_rows(c) == ()


@pytest.mark.asyncio
async def test_empty_list_has_no_provider_or_cache_write(area_localized):
    c = area_localized
    c.area_rows.clear()
    commits = c.session.commits
    response = await get(c)
    assert response.status_code == 200, response.text
    assert response.json()["data"] == []
    assert response.json()["meta"]["translations"] == {"target_language": "uk", "items": []}
    assert cache_rows(c) == () and c.session.commits == commits
    c.provider.detect_language.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_selection_does_not_filter_original_area_list(area_localized):
    c = area_localized
    response = await get(c, suffix="&translation_area_ids=" + str(c.area_rows[1].id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["data"]) == 2
    names = body["meta"]["translations"]["items"]
    assert len(names) == 1 and names[0]["area_id"] == str(c.area_rows[1].id)
    assert len(cache_rows(c)) == 1
    c.provider.translate.assert_awaited_once()


@pytest.mark.asyncio
async def test_large_lists_remain_available_and_allow_bounded_translation_selection(area_localized):
    c = area_localized
    c.area_rows.extend(c.make_area(300 + i, "Room " + str(i)) for i in range(31))
    response = await get(c)
    assert response.status_code == 422, response.text
    c.provider.translate.assert_not_awaited()
    original = await get(c, localized=False)
    assert original.status_code == 200 and len(original.json()["data"]) == 33
    selected = await get(c, suffix="&translation_area_ids=" + str(c.area_rows[0].id))
    assert selected.status_code == 200, selected.text
    assert len(selected.json()["data"]) == 33
    assert len(selected.json()["meta"]["translations"]["items"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["provider", "second_insert", "commit"])
async def test_batch_failures_roll_back_all_cache_rows(area_localized, failure):
    c = area_localized
    before, commits = domain_snapshot(c), c.session.commits
    if failure == "provider":
        c.provider.translate.side_effect = ["uk Kitchen", RuntimeError("private provider failure")]
    elif failure == "second_insert":
        original = c.area_cache.insert_automatic
        count = 0
        async def insert_then_fail(**values):
            nonlocal count
            count += 1
            saved = await original(**values)
            if count == 2:
                raise RuntimeError("private insert failure")
            return saved
        c.area_cache.insert_automatic = insert_then_fail
    else:
        c.session.fail_commit = True
    response = await get(c)
    assert response.status_code == (503 if failure == "provider" else 500), response.text
    assert "private" not in response.text
    assert cache_rows(c) == () and domain_snapshot(c) == before
    assert c.session.commits == commits


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["rename", "add", "archive", "language", "membership"])
async def test_changes_during_provider_prevent_stale_response_and_cache(area_localized, change):
    c = area_localized
    async def changed(**values):
        if change == "rename":
            c.area_rows[0].name = "Changed"
            c.area_rows[0].row_version += 1
        elif change == "add":
            c.area_rows.append(c.make_area(300, "New room"))
        elif change == "archive":
            c.area_rows[1].deleted_at = c.now
        elif change == "language":
            c.language = "it"
        else:
            c.membership = False
        return "Переклад"
    c.provider.translate.side_effect = changed
    response = await get(c)
    assert response.status_code == (403 if change == "membership" else 409), response.text
    assert cache_rows(c) == ()


@pytest.mark.asyncio
async def test_capabilities_follow_final_read_only_project_grant(area_localized):
    c = area_localized
    async def changed(**values):
        c.grant.role, c.grant.scope_type, c.grant.scope_id = "OBSERVER", "project", c.project.id
        return "Переклад"
    c.provider.translate.side_effect = changed
    response = await get(c)
    assert response.status_code == 200, response.text
    assert response.json()["meta"]["capabilities"] == {
        "can_create_area": False, "can_edit_area": False}


@pytest.mark.asyncio
async def test_unknown_selected_area_is_safe_not_found(area_localized):
    c = area_localized
    response = await get(c, suffix="&translation_area_ids=" + str(uuid4()))
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"
    c.provider.detect_language.assert_not_awaited()


@pytest.mark.asyncio
async def test_verified_cache_is_reused(area_localized):
    c = area_localized
    assert (await get(c)).status_code == 200
    c.sync.execute(update(c.translation_table).where(
        c.translation_table.c.entity_type == "construction.project_area",
        c.translation_table.c.entity_id == c.area_rows[0].id,
    ).values(translation_status="VERIFIED", reviewed_by=c.user))
    c.sync.commit()
    c.provider.translate.reset_mock()
    c.provider.detect_language.reset_mock()
    response = await get(c)
    assert response.status_code == 200, response.text
    first = response.json()["meta"]["translations"]["items"][0]
    assert first["translation_status"] == "VERIFIED" and first["reviewed_by"] == str(c.user)
    c.provider.translate.assert_not_awaited()
    c.provider.detect_language.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [
    "&target_language=de", "&tenant_id=foreign",
    "&source_text=caller", "&include_translations=false",
])
async def test_caller_cannot_supply_translation_authority_or_source(area_localized, suffix):
    c = area_localized
    response = await get(c, suffix=suffix)
    assert response.status_code == 422, response.text
    c.provider.detect_language.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_body_is_rejected(area_localized):
    c = area_localized
    response = await get(c, content='{"name":"caller"}')
    assert response.status_code == 422, response.text
    c.provider.detect_language.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_rolls_back_without_cache_writes(area_localized):
    c = area_localized
    if not c.sync.in_transaction():
        c.sync.begin()
    workflow = c.area_build(
        session=c.session, platform_session=c.session, area_ids=None)
    c.provider.translate.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await workflow.read_for_actor(actor=c.actor, project_id=c.project.id)
    assert not c.sync.in_transaction()
    assert cache_rows(c) == ()


@pytest.mark.asyncio
async def test_openapi_uses_existing_area_list_with_exact_source_versions(area_localized):
    c = area_localized
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        response = await client.get("/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    document = response.json()
    operation = document["paths"]["/api/v1/construction/projects/{id}/areas"]["get"]
    parameters = {item["name"]: item for item in operation["parameters"]}
    assert {"id", "include_translations", "translation_area_ids"} == set(parameters)
    assert parameters["include_translations"]["schema"]["default"] == "false"
    schema = next(
        item for item in document["components"]["schemas"].values()
        if item.get("title") == "AreaNameTranslation")
    for name in ("source_version", "cache_row_version"):
        field = schema["properties"][name]
        branches = [field] if field.get("type") == "integer" else field["anyOf"]
        integer = next(item for item in branches if item.get("type") == "integer")
        assert type(integer["maximum"]) is int
        assert integer["maximum"] == 2**63 - 1 and integer["minimum"] == 1
