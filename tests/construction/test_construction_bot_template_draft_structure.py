"""Original structure navigation, canonical UUIDs and read-only HTTP."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from test_construction_bot_template_drafts import (
    screen, templates_screen, draft_screen, open_card, click, draft_data,
)
from test_construction_bot_organization_templates import version
from handlers.construction import construction_organization_templates as cards
from handlers.construction import construction_template_draft_structure as handler
from services.construction.construction_template_draft_structure_bot_api import TemplateDraftStructureBotApi
from services.construction.construction_template_draft_header_bot_api import digest
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from ui.construction_template_draft_structure import LABELS


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["read", "units", "foreign", "reference", "missing_label", "denied"])
async def test_structure_uses_exact_draft_get_and_canonical_units(case):
    tenant, user, draft_id = uuid4(), uuid4(), uuid4()
    raw = draft_data(version(tenant), draft_id)
    if case == "foreign":
        raw["tenant_id"] = str(uuid4())
    elif case == "reference":
        raw["section_references"][0]["questions"][0]["code"] = "OTHER"
    elif case == "missing_label":
        raw["definition"]["labels"]["en"].pop("area")
    calls = []

    def transport(request):
        calls.append(request)
        assert request.method == "GET" and not request.url.query
        data = raw
        if request.url.path.endswith("/settings/units"):
            data = {
                "tenant_id": str(tenant), "catalog_version": 1, "language_code": "uk",
                "label_source_version": 1,
                "units": [{
                    "code": "M2", "dimension": "AREA", "symbol": "м²",
                    "label_key": "construction.unit.m2",
                    "label": "Квадратний метр", "label_status": "provided",
                }],
            }
        return httpx.Response(200, json={"data": data, "meta": {}, "request_id": "local"})

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
        api = TemplateDraftStructureBotApi(entry)
        binding = dict(telegram_user_id=123, tenant_id=tenant, expected_user_id=user)
        call = api.read_structure(
            template_id=UUID(raw["template_id"]), version_id=draft_id, **binding,
        )
        if case in ("foreign", "reference", "missing_label", "denied"):
            with pytest.raises(ConstructionApiClientError) as caught:
                await call
            assert caught.value.status_code == (403 if case == "denied" else 502)
        else:
            result = await call
            section = result["item"]["sections"][0]
            assert section["id"] == raw["section_references"][0]["id"]
            assert section["label"] == "Demolition"
            assert len(result["item"]["sections"]) == 2
            assert len(section["questions"]) == 2
            assert section["questions"][1]["has_condition"] is True
            if case == "units":
                unit = await api.unit_option(code="M2", expected_language="uk", **binding)
                assert unit["symbol"] == "м²"
    if case == "denied":
        assert calls == []


@pytest.fixture
def structure_screen(draft_screen, monkeypatch):
    c = draft_screen
    c.raw = draft_data(version(c.tenant), c.draft_id)
    c.raw["definition_hash"] = c.current["definition_hash"]
    c.reverse_sections = False
    c.reverse_questions = False
    c.structure_api = NS(
        read_draft=AsyncMock(side_effect=lambda **kwargs: {
            **c.common, "item": deepcopy(c.raw),
        }),
        unit_option=AsyncMock(return_value={
            "label": "Квадратний метр", "symbol": "м²",
        }),
    )

    async def read(**kwargs):
        result = await TemplateDraftStructureBotApi.read_structure(c.structure_api, **kwargs)
        if c.reverse_sections:
            result["item"]["sections"].reverse()
        if c.reverse_questions:
            for section in result["item"]["sections"]:
                section["questions"].reverse()
        return result

    c.structure_api.read_structure = AsyncMock(side_effect=read)
    monkeypatch.setattr(handler, "TemplateDraftStructureBotApi", lambda service: c.structure_api)

    @asynccontextmanager
    async def entry(**kwargs):
        yield NS()

    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    return c


async def open_draft(c):
    await open_card(c)
    await click(c, "draftnew")
    await click(c, "draftcreate")
    await click(c, "draftopen")


@pytest.mark.asyncio
async def test_section_and_question_choices_pin_uuid_not_fresh_position(structure_screen):
    c = structure_screen
    await open_draft(c)
    await click(c, "str_sections")
    chosen_section = c.data[cards.VIEW]["choice_ids"][0]
    assert "Demolition" not in repr(c.data[cards.VIEW])
    c.reverse_sections = True
    await click(c, "str_section", 0)
    assert c.data[cards.VIEW]["structure"]["section_id"] == chosen_section
    chosen_question = c.data[cards.VIEW]["choice_ids"][1]
    c.reverse_questions = True
    await click(c, "str_question", 1)
    assert c.data[cards.VIEW]["structure"]["question_id"] == chosen_question
    text = c.sent.await_args.kwargs["text"]
    assert "Мова оригіналу: en" in text
    assert "Число" in text and "м²" in text
    assert "NUMBER" not in text and "M2" not in text
    c.structure_api.unit_option.assert_awaited_once()


@pytest.mark.asyncio
async def test_version_change_requires_explicit_structure_refresh(structure_screen):
    c = structure_screen
    await open_draft(c)
    await click(c, "str_sections")
    c.raw["version_row_version"] = 9
    await click(c, "str_section", 0)
    assert c.data[cards.VIEW]["phase"] == "structure_blocked"
    assert c.data[cards.VIEW]["draft"]["version_row_version"] == 8
    await click(c, "str_refresh")
    assert c.data[cards.VIEW]["phase"] == "structure_sections"
    assert c.data[cards.VIEW]["draft"]["version_row_version"] == 9


@pytest.mark.asyncio
async def test_question_pagination_keeps_the_section_and_selected_uuid(structure_screen):
    c = structure_screen
    section = c.raw["definition"]["sections"][0]
    references = c.raw["section_references"][0]["questions"]
    for number in range(7):
        question = deepcopy(section["questions"][1])
        question.update(
            code="EXTRA_" + str(number), label_key="extra_" + str(number),
            question_type="TEXT", sort_order=30 + number * 10,
            unit_code=None, formula_type=None, input_schema={},
            validation_schema={}, show_if=None,
        )
        section["questions"].append(question)
        c.raw["definition"]["labels"]["en"][question["label_key"]] = "Extra " + str(number)
        references.append({
            "id": str(uuid4()), "code": question["code"], "row_version": 1,
        })
    c.raw["definition_hash"] = digest(c.raw["definition"])
    c.current["definition_hash"] = c.raw["definition_hash"]
    c.current["question_count"] = 10
    await open_draft(c)
    await click(c, "str_sections")
    await click(c, "str_section", 0)
    section_id = c.data[cards.VIEW]["structure"]["section_id"]
    assert len(c.data[cards.VIEW]["choice_ids"]) == 6
    await click(c, "str_qp", 1)
    assert c.data[cards.VIEW]["structure"]["section_id"] == section_id
    assert len(c.data[cards.VIEW]["choice_ids"]) == 3
    question_id = c.data[cards.VIEW]["choice_ids"][0]
    await click(c, "str_question", 0)
    assert c.data[cards.VIEW]["structure"]["question_id"] == question_id


@pytest.mark.asyncio
async def test_late_structure_read_does_not_restore_another_tenant(structure_screen):
    c = structure_screen
    await open_draft(c)
    c.sent.reset_mock()
    original = c.structure_api.read_structure.side_effect

    async def late(**kwargs):
        result = await original(**kwargs)
        c.data[cards.CONTEXT]["tenant_id"] = str(uuid4())
        return result

    c.structure_api.read_structure.side_effect = late
    await click(c, "str_sections")
    c.sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_and_old_menu_cannot_read_structure(structure_screen):
    c = structure_screen
    await open_draft(c)
    c.callback.message.chat.type = "group"
    await click(c, "str_sections")
    c.callback.message.chat.type = "private"
    c.callback.message.message_id = 999
    c.callback.data = cards.cb(c.data[cards.VIEW], "str_sections")
    await cards.templates_callback(c.callback, c.state)
    c.structure_api.read_structure.assert_not_awaited()


def test_all_controls_are_localized_and_callbacks_bounded():
    assert all(len(values) == 7 for values in LABELS.values())
    for action in ("str_sections", "str_section", "str_question", "str_qp", "str_refresh"):
        assert len(cards.cb({"nonce": "a" * 32}, action, 333).encode("utf-8")) <= 64
