"""Local immutable organization reads, localization and selector binding."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from test_construction_bot_tax_profiles import screen
from test_construction_02_starter_template_catalog import definition
from services.construction.construction_organization_template_definitions import (
    validate_organization_template_definition,
)
from services.construction.construction_organization_templates_bot_api import OrganizationTemplatesBotApi
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from handlers.construction import construction_organization_templates as handler
from handlers.construction import construction_stage2_settings as shell
from ui.construction_organization_templates import LABELS


def version(tenant):
    raw = definition()
    raw["labels"]["uk"] = {
        "title": "Шаблон організації", "description": "Власна копія",
    }
    checked = validate_organization_template_definition(raw, source_language="en")
    payload = checked.payload()
    stamp = datetime.now(timezone.utc).isoformat()
    return {
        "tenant_id": str(tenant), "template_id": str(UUID(int=301)),
        "code": "ORG_TEMPLATE", "active": True, "template_row_version": 1,
        "version_id": str(UUID(int=3001)), "version": 1, "version_row_version": 2,
        "source_starter_version_id": str(UUID(int=1001)),
        "definition_schema_version": 1, "source_language": "en",
        "title": "Starter", "description": "Structure without prices",
        "content_hash": checked.content_hash, "created_by": str(uuid4()),
        "created_at": stamp, "updated_at": stamp, "published_at": stamp,
        "definition": payload,
        "section_references": [
            {
                "id": str(uuid4()), "code": section["code"], "row_version": 1,
                "questions": [
                    {"id": str(uuid4()), "code": question["code"], "row_version": 1}
                    for question in section["questions"]
                ],
            }
            for section in payload["sections"]
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "list", "version", "inactive", "empty", "foreign_list", "foreign_version",
    "hash", "reference", "duplicate_question", "language", "denied",
])
async def test_existing_read_contract_rejects_foreign_or_incoherent_data(case):
    tenant, user = uuid4(), uuid4()
    item, calls = version(tenant), []
    if case in ("foreign_list", "foreign_version"):
        item["tenant_id"] = str(uuid4())
    elif case == "hash":
        item["content_hash"] = "0" * 64
    elif case == "reference":
        item["section_references"][0]["questions"][0]["code"] = "OTHER"
    elif case == "duplicate_question":
        refs = item["section_references"][0]["questions"]
        refs[1]["id"] = refs[0]["id"]
    elif case == "inactive":
        item["active"] = False

    def transport(request):
        calls.append(request)
        assert request.method == "GET"
        meta = {}
        if request.url.path.endswith("/translated-texts"):
            assert request.url.params.get_list("fields") == ["title", "description"]
            assert "language" not in request.url.params
            data = [{
                "tenant_id": str(tenant), "user_id": str(user),
                "template_id": item["template_id"], "version_id": item["version_id"],
                "field_name": field, "target_language": "pt" if case == "language" else "uk",
                "source_language": "en", "source_version": 1,
                "original_text": item[field], "display_text": translated,
                "translation_status": None, "origin": "PUBLISHED_LABEL",
                "cache_row_version": None, "cache_written": False,
            } for field, translated in (
                ("title", "Шаблон організації"), ("description", "Власна копія"),
            )]
        elif "/versions/" in request.url.path:
            data = item
        else:
            data = [] if case == "empty" else [{
                key: value for key, value in item.items()
                if key not in ("definition", "section_references")
            }]
            meta = {"pagination": {"next_cursor": None}}
        return httpx.Response(200, json={"data": data, "meta": meta, "request_id": "local"})

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        client = ConstructionApiClient(http=http)
        client.company_profile_capabilities = AsyncMock(return_value=NS(data={
            "tenant_id": str(tenant), "can_manage_settings": case != "denied",
            "can_manage_profile": False,
        }))
        entry = NS(
            _presentation=AsyncMock(return_value=(
                PlatformApiClientSession(user, tenant, SecretStr("local-only")),
                {"name": "Org", "language": "uk"},
            )),
            api=NS(construction=client),
        )
        api = OrganizationTemplatesBotApi(entry)
        binding = dict(telegram_user_id=123, tenant_id=tenant, expected_user_id=user)
        listing = case in ("list", "empty", "foreign_list", "language", "denied")
        call = (
            api.list_templates(**binding) if listing
            else api.get_template(version_id=UUID(item["version_id"]), **binding)
        )
        if case not in ("list", "version", "inactive", "empty"):
            with pytest.raises(ConstructionApiClientError) as caught:
                await call
            assert caught.value.status_code == (
                403 if case == "denied" else 409 if case == "language" else 502
            )
        else:
            result = await call
            if case == "empty":
                assert result["items"] == [] and len(calls) == 1
            elif listing:
                assert result["items"][0]["display_title"] == "Шаблон організації"
            else:
                assert result["item"]["question_count"] == 3
                assert result["item"]["section_count"] == 2
                assert result["item"]["active"] is (case != "inactive")
                assert "definition" not in result["item"]
    if case == "denied":
        assert calls == []


@pytest.fixture
def templates_screen(screen, monkeypatch):
    c = screen
    raw = version(c.tenant)
    c.item = {
        **{key: value for key, value in raw.items()
           if key not in ("definition", "section_references")},
        "display_title": "Шаблон організації", "display_description": "Власна копія",
        "section_count": 2, "question_count": 3,
    }
    c.page, c.cursor = [deepcopy(c.item)], None
    c.templates_api = NS(
        list_templates=AsyncMock(side_effect=lambda **kwargs: {
            **c.common, "items": deepcopy(c.page), "next_cursor": c.cursor,
        }),
        get_template=AsyncMock(side_effect=lambda **kwargs: {
            **c.common, "item": deepcopy(c.item),
        }),
    )
    monkeypatch.setattr(handler, "OrganizationTemplatesBotApi", lambda service: c.templates_api)
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", c.sent)

    @asynccontextmanager
    async def entry(**kwargs):
        yield NS()

    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    return c


async def open_templates(c):
    await handler.open_templates(
        c.callback, c.state, shell.snapshot(c.data), c.parent, NS(),
    )


async def click(c, action, index=None):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = handler.cb(c.data[handler.VIEW], action, index)
    await handler.templates_callback(c.callback, c.state)


@pytest.mark.asyncio
async def test_open_and_refresh_keep_the_exact_selected_version(templates_screen):
    c = templates_screen
    await open_templates(c)
    chosen = c.item["version_id"]
    assert c.item["display_title"] not in repr(c.data[handler.VIEW])
    c.page[0]["version_id"] = str(uuid4())
    await click(c, "open", 0)
    assert c.templates_api.get_template.await_args.kwargs["version_id"] == UUID(chosen)
    assert c.data[handler.VIEW]["phase"] == "card"
    c.item["active"] = False
    c.item["template_row_version"] = 2
    await click(c, "refresh")
    assert c.templates_api.get_template.await_args.kwargs["version_id"] == UUID(chosen)
    assert c.data[handler.VIEW]["choice"]["version_id"] == chosen
    assert "Вимкнений" in c.sent.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_next_uses_the_original_cursor_and_empty_page_has_no_choices(templates_screen):
    c = templates_screen
    c.cursor = "opaque-server-cursor"
    await open_templates(c)
    c.page, c.cursor = [], None
    await click(c, "next")
    assert c.templates_api.list_templates.await_args.kwargs["cursor"] == "opaque-server-cursor"
    assert c.data[handler.VIEW]["choices"] == []
    await click(c, "open", 0)
    c.templates_api.get_template.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_immutable_content_is_not_silently_replaced(templates_screen):
    c = templates_screen
    await open_templates(c)
    c.item["content_hash"] = "0" * 64
    await click(c, "open", 0)
    assert c.data[handler.VIEW]["phase"] == "blocked"
    assert "choice" not in c.data[handler.VIEW]


@pytest.mark.asyncio
async def test_late_read_cannot_restore_another_tenant(templates_screen):
    c = templates_screen

    async def late(**kwargs):
        c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
        return {**c.common, "items": deepcopy(c.page), "next_cursor": None}

    c.templates_api.list_templates.side_effect = late
    await open_templates(c)
    c.sent.assert_not_awaited()
    assert handler.VIEW not in c.data


@pytest.mark.asyncio
async def test_group_and_stale_menu_cannot_read_versions(templates_screen):
    c = templates_screen
    await open_templates(c)
    c.callback.message.chat.type = "group"
    await click(c, "open", 0)
    c.callback.message.chat.type = "private"
    c.callback.message.message_id = 999
    c.callback.data = handler.cb(c.data[handler.VIEW], "open", 0)
    await handler.templates_callback(c.callback, c.state)
    c.templates_api.get_template.assert_not_awaited()


def test_labels_and_callback_size():
    assert all(len(values) == 7 for values in LABELS.values())
    assert len(handler.cb({"nonce": "a" * 32}, "open", 5).encode("utf-8")) <= 64
