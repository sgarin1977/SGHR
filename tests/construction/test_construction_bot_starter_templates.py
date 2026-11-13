"""Local starter localization, exact copy request and uncertain-result recovery."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4
import json

import httpx
import pytest
from pydantic import SecretStr

from test_construction_bot_onboarding import screen, wizard, progress, open_guide
from test_construction_02_starter_template_catalog import definition
from services.construction.construction_starter_template_definitions import validate_starter_definition
from handlers.construction import construction_onboarding as guide
from handlers.construction import construction_starter_templates as handler
from services.construction.construction_starter_templates_bot_api import StarterTemplatesBotApi
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from ui.construction_starter_templates import LABELS


def source():
    validated = validate_starter_definition(definition(), source_language="en")
    stamp = datetime.now(timezone.utc).isoformat()
    return {
        "template_id": str(UUID(int=101)), "version_id": str(UUID(int=1001)),
        "code": "STARTER", "version": 1, "definition_schema_version": 1,
        "source_language": "en", "content_hash": validated.content_hash,
        "published_at": stamp, "created_at": stamp,
        "title": "Starter", "description": "Structure without prices",
        "definition": validated.payload(),
    }


def operation(item):
    return {
        "source_version_id": item["version_id"], "source_template_id": item["template_id"],
        "source_code": item["code"], "content_hash": item["content_hash"],
        "section_count": 2, "question_count": 3, "key": "original-copy-key",
    }


def receipt(tenant, user, item):
    return {
        "tenant_id": str(tenant), "template_id": str(uuid4()), "code": item["code"],
        "template_row_version": 1, "template_version_id": str(uuid4()),
        "version": 1, "version_row_version": 2,
        "source_starter_version_id": item["version_id"], "definition_schema_version": 1,
        "source_language": "en", "content_hash": item["content_hash"],
        "section_count": 2, "question_count": 3, "created_by": str(user),
        "created_at": item["created_at"], "published_at": item["published_at"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "list", "version", "copy", "language_changed", "foreign_copy", "wrong_hash",
    "wrong_source", "bool_counter", "denied",
])
async def test_existing_http_and_profile_language_contract(case):
    tenant, user = uuid4(), uuid4()
    item, calls = source(), []
    output = receipt(tenant, user, item)
    if case == "foreign_copy":
        output["tenant_id"] = str(uuid4())
    elif case == "wrong_hash":
        output["content_hash"] = "0" * 64
    elif case == "wrong_source":
        output["source_starter_version_id"] = str(uuid4())
    elif case == "bool_counter":
        output["template_row_version"] = True

    def transport(request):
        calls.append(request)
        path = request.url.path
        meta = {}
        if path.endswith("/translated-texts"):
            assert request.url.params.get_list("fields") == ["title", "description"]
            assert "language" not in request.url.params
            data = []
            for field, display in (("title", "Початковий шаблон"), ("description", "Структура без цін")):
                data.append({
                    "tenant_id": str(tenant), "user_id": str(user),
                    "template_id": item["template_id"], "version_id": item["version_id"],
                    "field_name": field, "target_language": "pt" if case == "language_changed" else "uk",
                    "source_language": "en", "source_version": 1,
                    "original_text": item[field], "display_text": display,
                    "translation_status": None, "origin": "PUBLISHED_LABEL",
                    "cache_row_version": None, "cache_written": False,
                })
        elif path.endswith("/copy-starter"):
            data = output
        elif "/versions/" in path:
            data = item
        else:
            data = [{key: value for key, value in item.items() if key != "definition"}]
            meta = {"pagination": {"next_cursor": None}}
        return httpx.Response(201 if request.method == "POST" else 200, json={
            "data": data, "meta": meta, "request_id": "local",
        })

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        client = ConstructionApiClient(http=http)
        client.company_profile_capabilities = AsyncMock(return_value=NS(data={
            "tenant_id": str(tenant), "can_manage_settings": case != "denied",
        }))
        entry = NS(
            _presentation=AsyncMock(return_value=(
                PlatformApiClientSession(user, tenant, SecretStr("local-only")),
                {"name": "Org", "language": "uk"},
            )),
            api=NS(construction=client),
        )
        api = StarterTemplatesBotApi(entry)
        binding = dict(telegram_user_id=123, tenant_id=tenant, expected_user_id=user)
        call = (
            api.list_starters(**binding) if case in ("list", "language_changed")
            else api.get_starter(version_id=UUID(item["version_id"]), **binding) if case == "version"
            else api.copy(request=operation(item), **binding)
        )
        if case not in ("list", "version", "copy"):
            with pytest.raises(ConstructionApiClientError) as caught:
                await call
            assert caught.value.status_code == (
                409 if case == "language_changed" else 403 if case == "denied" else 502
            )
        else:
            result = await call
            if case == "list":
                assert result["items"][0]["display_title"] == "Початковий шаблон"
            elif case == "version":
                assert result["item"]["question_count"] == 3
                assert result["item"]["display_title"] == "Початковий шаблон"
                assert "definition" not in result["item"]
    if case == "denied":
        assert calls == []
    for request in calls:
        if request.method == "POST":
            assert request.url.path.endswith("/measurement-templates/copy-starter")
            assert request.headers["idempotency-key"] == "original-copy-key"
            assert json.loads(request.content) == {
                "source_version_id": item["version_id"], "code": None,
            }


@pytest.fixture
def starter_screen(wizard, monkeypatch):
    c = wizard
    c.progress = progress(c.tenant, started=True, step="MEASUREMENT_TEMPLATES")
    raw = source()
    c.starter = {
        **{key: value for key, value in raw.items() if key != "definition"},
        "display_title": "Початковий шаблон", "display_description": "Структура без цін",
        "section_count": 2, "question_count": 3,
    }
    c.page = [deepcopy(c.starter)]
    c.starter_api = NS(
        read=c.guide_api.read,
        list_starters=AsyncMock(side_effect=lambda **kwargs: {
            **c.common, "items": deepcopy(c.page), "next_cursor": None,
        }),
        get_starter=AsyncMock(return_value={**c.common, "item": deepcopy(c.starter)}),
        copy=AsyncMock(return_value={**c.common, "item": receipt(c.tenant, c.user, raw)}),
    )
    monkeypatch.setattr(handler, "StarterTemplatesBotApi", lambda service: c.starter_api)
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", c.sent)

    @asynccontextmanager
    async def entry(**kwargs):
        yield NS()

    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    return c


async def enter(c):
    await open_guide(c)
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = "STP:open"

    async def dispatch(event, data):
        return await handler.templates_callback(event, c.state)

    await guide.navigation_middleware(dispatch, c.callback, {"state": c.state})


async def click(c, action, index=None):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = handler.cb(c.data[handler.VIEW], action, index)
    await handler.templates_callback(c.callback, c.state)


@pytest.mark.asyncio
async def test_preview_pins_exact_version_and_copy_is_explicit(starter_screen):
    c = starter_screen
    await enter(c)
    assert c.data[handler.VIEW]["phase"] == "list"
    assert c.starter["display_title"] not in repr(c.data[handler.VIEW])
    c.page[0]["version_id"] = str(uuid4())
    await click(c, "pick", 0)
    assert c.starter_api.get_starter.await_args.kwargs["version_id"] == UUID(c.starter["version_id"])
    assert c.data[handler.VIEW]["phase"] == "review"
    c.starter_api.copy.assert_not_awaited()
    old = handler.cb(c.data[handler.VIEW], "copy")
    await click(c, "copy")
    request = c.starter_api.copy.await_args.kwargs["request"]
    assert request["source_version_id"] == c.starter["version_id"]
    assert request["content_hash"] == c.starter["content_hash"]
    assert len(request["key"]) == 32
    assert c.data[handler.PENDING] == {}
    c.callback.data = old
    await handler.templates_callback(c.callback, c.state)
    c.starter_api.copy.assert_awaited_once()
    c.guide_api.write.assert_not_awaited()
    assert c.progress["step_states"]["MEASUREMENT_TEMPLATES"] == "PENDING"


@pytest.mark.asyncio
async def test_cancel_and_empty_catalog_do_not_copy(starter_screen):
    c = starter_screen
    await enter(c)
    await click(c, "pick", 0)
    c.page = []
    await click(c, "list")
    assert c.data[handler.VIEW]["choices"] == []
    await click(c, "pick", 0)
    c.starter_api.copy.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_copy_survives_navigation_and_replays_original_request(starter_screen):
    c = starter_screen
    await enter(c)
    await click(c, "pick", 0)
    completed = c.starter_api.copy.return_value
    c.starter_api.copy.side_effect = ConstructionApiClientError(status_code=504, code="api_timeout")
    await click(c, "copy")
    original = deepcopy(c.starter_api.copy.await_args.kwargs["request"])
    assert c.data[handler.VIEW]["phase"] == "recovery"
    await click(c, "guide")
    assert c.data[guide.VIEW]["phase"] == "overview"
    assert c.data[handler.PENDING][str(c.tenant) + ":" + str(c.user)] == original
    await enter(c)
    assert c.data[handler.VIEW]["phase"] == "recovery"
    c.starter_api.copy.side_effect = None
    c.starter_api.copy.return_value = completed
    await click(c, "retry")
    assert c.starter_api.copy.await_args.kwargs["request"] == original
    assert c.starter_api.copy.await_count == 2
    assert c.data[handler.PENDING] == {}
    c.guide_api.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_catalog_cannot_restore_another_tenant(starter_screen):
    c = starter_screen

    async def late(**kwargs):
        c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
        return {**c.common, "items": deepcopy(c.page), "next_cursor": None}

    c.starter_api.list_starters.side_effect = late
    await enter(c)
    c.starter_api.copy.assert_not_awaited()
    assert not c.data.get(handler.VIEW)


@pytest.mark.asyncio
async def test_stale_and_group_callbacks_cannot_copy(starter_screen):
    c = starter_screen
    await enter(c)
    await click(c, "pick", 0)
    c.callback.message.chat.type = "group"
    await click(c, "copy")
    c.callback.message.chat.type = "private"
    c.callback.message.message_id = 999
    c.callback.data = handler.cb(c.data[handler.VIEW], "copy")
    await handler.templates_callback(c.callback, c.state)
    c.starter_api.copy.assert_not_awaited()


def test_translations_and_callback_limits():
    assert all(len(values) == 7 for values in LABELS.values())
    assert len(handler.cb({"nonce": "a" * 32}, "pick", 5).encode("utf-8")) <= 64
