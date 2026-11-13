"""Visible-page translation batching, source coherence and original editing data."""
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from test_construction_bot_project_areas import screen, click
from handlers.construction import construction_project_areas as handler
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_area_presentation import AreaPresentationResult
from services.construction.construction_project_presentation import ProjectPresentationResult
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.construction_project_areas import areas_text
from ui.construction_project_translation import LANGUAGES, tr


def rows(tenant, project, count):
    return [{
        "id": str(uuid4()), "tenant_id": str(tenant), "project_id": str(project),
        "name": "Original room " + str(n), "area_type": "ROOM",
        "status": "draft", "sort_order": n, "row_version": n + 1,
    } for n in range(count)]


def metadata(items, language="uk"):
    return {
        "target_language": language,
        "items": [{
            "area_id": item["id"], "source_version": item["row_version"],
            "target_language": language, "original_text": item["name"],
            "display_text": "Переклад " + item["name"],
            "source_language": "en" if language != "en" else "uk",
            "translation_status": "AUTO_TRANSLATED",
            "translated_at": "2026-11-12T10:00:00Z", "reviewed_by": None,
            "cache_row_version": 1, "cache_written": False,
        } for item in items],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "ok", "empty", "clamped", "language", "version", "original", "missing",
    "foreign", "changed", "forbidden", "unavailable", "capability",
])
async def test_only_visible_ids_are_translated_and_every_source_is_validated(case):
    tenant, user, project = uuid4(), uuid4(), uuid4()
    data = rows(tenant, project, 0 if case == "empty" else 40)
    offset = 999 if case == "clamped" else 5
    actual = 35 if case == "clamped" else 5
    visible = data[actual:actual + 5]
    calls = []

    def transport(request):
        calls.append(request)
        localized = request.url.params.get("include_translations") == "true"
        if localized and case in ("forbidden", "unavailable"):
            status, code = (403, "forbidden") if case == "forbidden" else (503, "TRANSLATION_UNAVAILABLE")
            return httpx.Response(status, json={"error": {
                "code": code, "message": "Private detail", "request_id": "local",
            }})
        body = deepcopy(data)
        caps = {"can_create_area": True, "can_edit_area": True}
        meta = {"capabilities": caps}
        if localized:
            assert request.url.params.get_list("translation_area_ids") == [item["id"] for item in visible]
            translated = metadata(visible)
            if case == "language":
                translated = metadata(visible, "pl")
            elif case == "version":
                translated["items"][0]["source_version"] += 1
            elif case == "original":
                translated["items"][0]["original_text"] = "Different original"
            elif case == "missing":
                translated["items"].pop()
            elif case == "foreign":
                body[0]["tenant_id"] = str(uuid4())
            elif case == "changed":
                body[0]["row_version"] += 1
            elif case == "capability":
                caps["can_edit_area"] = False
            meta["translations"] = translated
        elif len(calls) > 1:
            caps["can_edit_area"] = False
        return httpx.Response(200, json={"data": body, "meta": meta, "request_id": "local"})

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        client = ConstructionApiClient(http=http)
        call = client.project_areas(
            session=PlatformApiClientSession(user, tenant, SecretStr("local-only")),
            project_id=project, include_translations=True,
            expected_language="uk", display_offset=offset,
        )
        errors = {
            "language": 409, "version": 502, "original": 502, "missing": 502,
            "foreign": 502, "changed": 409, "forbidden": 403,
        }
        if case in errors:
            with pytest.raises(ConstructionApiClientError) as caught:
                await call
            assert caught.value.status_code == errors[case]
            assert len(calls) == 2
        else:
            result = await call
            assert result.data == data
            assert result.display_offset == (0 if case == "empty" else actual)
            assert result.translation_unavailable is (case == "unavailable")
            assert len(calls) == (1 if case == "empty" else 3 if case == "unavailable" else 2)
            if case in ("capability", "unavailable"):
                assert result.meta["capabilities"]["can_edit_area"] is False
        for request in calls:
            assert request.method == "GET"
            assert request.url.path == f"/api/v1/construction/projects/{project}/areas"
            assert set(request.url.params) <= {"include_translations", "translation_area_ids"}
            assert "X-Tenant-ID" not in request.headers


@pytest.mark.asyncio
async def test_entry_keeps_raw_items_and_enables_translation_only_for_list_display():
    tenant, user, project = uuid4(), uuid4(), uuid4()
    data = rows(tenant, project, 7)
    meta = {"capabilities": {"can_create_area": False, "can_edit_area": False},
            "translations": metadata(data[5:])}
    reader = AsyncMock(return_value=AreaPresentationResult(data, meta, "local", False, 5))
    parent = AsyncMock(return_value=ProjectPresentationResult(
        {"name": "Original project", "row_version": 1}, {}, "local",
    ))
    entry = ConstructionBotApiEntry(
        api=NS(construction=NS(project_details=parent, project_areas=reader)),
        context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local-only")),
        {"name": "Org", "language": "uk"},
    ))
    binding = dict(telegram_user_id=123, tenant_id=tenant, project_id=project, expected_user_id=user)
    result = await entry.project_areas(**binding, display_offset=5)
    assert result["items"] == data
    assert result["display_offset"] == 5
    assert reader.await_args.kwargs["include_translations"] is True
    assert reader.await_args.kwargs["expected_language"] == "uk"
    assert parent.await_args.kwargs["include_translations"] is True
    await entry.project_areas(**binding)
    assert "include_translations" not in reader.await_args.kwargs
    assert "include_translations" not in parent.await_args.kwargs


@pytest.mark.parametrize("language", LANGUAGES)
def test_render_has_translation_and_original_without_changing_editable_data(language):
    tenant, project = uuid4(), uuid4()
    data = rows(tenant, project, 7)
    result = {
        "items": data, "organization": "Org", "project_name": "Project",
        "translations": metadata(data[5:], language), "translation_unavailable": False,
    }
    before = deepcopy(result)
    text = areas_text(result, offset=5, language=language)
    assert data[5]["name"] in text
    assert "Переклад " + data[5]["name"] in text
    assert tr("AUTO_TRANSLATED", language) in text
    assert tr("original", language) in text
    assert str(project) not in text and data[5]["id"] not in text
    assert result == before


def test_outage_is_labelled_and_mismatched_page_cannot_reuse_translations():
    tenant, project = uuid4(), uuid4()
    data = rows(tenant, project, 7)
    result = {
        "items": data, "organization": "Org", "project_name": "Project",
        "translations": None, "translation_unavailable": True,
    }
    assert tr("unavailable", "uk") in areas_text(result, offset=0, language="uk")
    result.update(translations=metadata(data[:5]), translation_unavailable=False)
    with pytest.raises(ValueError):
        areas_text(result, offset=5, language="uk")


@pytest.mark.asyncio
async def test_handler_uses_translated_buttons_but_stores_only_canonical_selectors(screen):
    c = screen
    c.result["can_edit_area"] = True

    async def read(**kwargs):
        result = deepcopy(c.result)
        offset = min(kwargs["display_offset"], 5)
        result.update(
            display_offset=offset, translations=metadata(result["items"][offset:offset + 5]),
            translation_unavailable=False,
        )
        return result

    c.service.project_areas.side_effect = read
    await handler.areas_callback(c.callback, c.state)
    assert c.service.project_areas.await_args.kwargs["display_offset"] == 0
    markup = c.sent.await_args.kwargs["reply_markup"]
    assert any("Переклад " in button.text for row in markup.inline_keyboard for button in row)
    assert c.data[handler.VIEW]["editable_areas"][0] == {
        "id": c.result["items"][0]["id"], "row_version": c.result["items"][0]["row_version"],
    }
    assert "Переклад " not in repr(c.data) and "Private room" not in repr(c.data)
    await click(c, 5)
    assert c.service.project_areas.await_args.kwargs["display_offset"] == 5
    assert c.data[handler.VIEW]["editable_areas"][0]["id"] == c.result["items"][5]["id"]


@pytest.mark.asyncio
async def test_late_area_result_does_not_restore_changed_organization(screen):
    c = screen
    async def late(**kwargs):
        c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
        return deepcopy(c.result)
    c.service.project_areas.side_effect = late
    await handler.areas_callback(c.callback, c.state)
    c.sent.assert_not_awaited()
