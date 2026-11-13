"""Sparse versioned ordering preserves phase gates and conditional dependencies."""
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4
import json

import httpx
import pytest
from pydantic import SecretStr

from test_construction_bot_template_drafts import (
    screen, templates_screen, draft_screen, click, draft_data,
)
from test_construction_bot_template_draft_structure import structure_screen, open_draft
from test_construction_bot_template_draft_labels import label_screen, identity
from test_construction_bot_organization_templates import version
from handlers.construction import construction_organization_templates as cards
from handlers.construction import construction_template_draft_header as editor
from services.construction.construction_template_draft_label_bot_api import (
    TemplateDraftLabelBotApi, order_fields, order_value, locate,
)
from services.construction.construction_template_draft_header_bot_api import (
    TemplateDraftHeaderBotApi, digest,
)
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from api.routes.construction_template_versions import TemplateDraftWriteData
from ui.construction_template_draft_header import LABELS


def extra(raw):
    question = deepcopy(raw["definition"]["sections"][0]["questions"][1])
    question.update(
        code="NOTE", label_key="note", question_type="TEXT", sort_order=30,
        unit_code=None, formula_type=None, input_schema={},
        validation_schema={}, show_if=None,
    )
    raw["definition"]["sections"][0]["questions"].append(question)
    raw["definition"]["labels"]["en"]["note"] = "Additional note"
    raw["section_references"][0]["questions"].append({
        "id": str(uuid4()), "code": "NOTE", "row_version": 1,
    })
    return raw


@pytest.mark.parametrize("value", [True, False, -1, 2147483648, 1.5, "", "1.2", "-1", "١", None])
def test_order_rejects_noncanonical_numeric_types_or_range(value):
    with pytest.raises(ValueError):
        order_value(value)


def test_reordering_changes_only_selected_rank_and_sorts_before_hashing():
    raw = extra(draft_data(version(uuid4()), uuid4()))
    before = deepcopy(raw)
    body, candidate, old = order_fields(raw, "QUESTION", identity(raw, "QUESTION"), 40)
    assert raw == before and old == 20
    assert body == {"fields": {"sort_order": 40}}
    assert [q["code"] for q in candidate["sections"][0]["questions"]] == [
        "DEMO_GATE", "NOTE", "AREA",
    ]
    expected = deepcopy(raw["definition"])
    expected["sections"][0]["questions"][1]["sort_order"] = 40
    expected["sections"][0]["questions"].sort(key=lambda q: q["sort_order"])
    assert candidate == expected


@pytest.mark.parametrize("case", ["duplicate", "phase", "gate", "forward"])
def test_order_cannot_break_existing_template_rules(case):
    raw = extra(draft_data(version(uuid4()), uuid4()))
    kind, item_id, value = "QUESTION", identity(raw, "QUESTION"), 10
    if case == "phase":
        kind, item_id, value = "SECTION", identity(raw, "SECTION"), 30
    elif case == "gate":
        item_id = raw["section_references"][0]["questions"][0]["id"]
        value = 25
    elif case == "forward":
        raw["definition"]["sections"][0]["questions"][2]["show_if"] = {
            "question_code": "AREA", "operator": "IS_SET",
        }
        value = 40
    before = deepcopy(raw)
    with pytest.raises(ConstructionApiClientError) as caught:
        order_fields(raw, kind, item_id, value)
    assert caught.value.status_code == 422
    assert caught.value.code == "TEMPLATE_ORDER_INVALID"
    assert raw == before


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["SECTION", "QUESTION"])
@pytest.mark.parametrize("case", ["change", "noop", "stale", "foreign", "hash", "permission"])
async def test_existing_order_patch_and_receipt_contract(kind, case):
    tenant, user = uuid4(), uuid4()
    raw = extra(draft_data(version(tenant), uuid4()))
    item_id = identity(raw, kind)
    value = 5 if kind == "SECTION" else 40
    if case == "noop":
        value = locate(raw, kind, item_id)["sort_order"]
    calls = []
    allowed = {"value": True}

    def transport(request):
        calls.append(request)
        if request.method == "GET":
            data = raw
        else:
            body = json.loads(request.content)
            _, candidate, _ = order_fields(
                raw, kind, item_id, body["fields"]["sort_order"],
            )
            changed = candidate != raw["definition"]
            receipt = {
                name: deepcopy(raw[name])
                for name in TemplateDraftWriteData.model_fields if name in raw
            }
            receipt.update(
                changed=changed, version_row_version=8 + int(changed),
                definition_hash=digest(candidate), section_count=2, question_count=4,
            )
            if case == "foreign":
                receipt["tenant_id"] = str(uuid4())
            elif case == "hash":
                receipt["definition_hash"] = "0" * 64
            data = {"draft": receipt, "kind": kind, "item_id": item_id}
        return httpx.Response(200, json={"data": data, "meta": {}, "request_id": "local"})

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        client = ConstructionApiClient(http=http)
        client.company_profile_capabilities = AsyncMock(side_effect=lambda **kwargs: NS(data={
            "tenant_id": str(tenant), "can_manage_settings": allowed["value"],
        }))
        entry = NS(
            _presentation=AsyncMock(return_value=(
                PlatformApiClientSession(user, tenant, SecretStr("local-only")),
                {"name": "Org", "language": "uk"},
            )),
            api=NS(construction=client),
        )
        api = TemplateDraftHeaderBotApi(entry)
        binding = dict(telegram_user_id=123, tenant_id=tenant, expected_user_id=user)
        kwargs = dict(
            template_id=UUID(raw["template_id"]), version_id=UUID(raw["version_id"]),
            expected_row_version=9 if case == "stale" else 8,
            expected_definition_hash=digest(raw["definition"]),
            field="sort_order", kind=kind, item_id=item_id, value=value,
        )
        if case == "stale":
            with pytest.raises(ConstructionApiClientError) as caught:
                await api.prepare_header(**kwargs, **binding)
            assert caught.value.status_code == 409 and len(calls) == 1
            return
        reviewed = await api.prepare_header(**kwargs, **binding)
        operation = {**reviewed["request"], "key": "same-order-key"}
        assert len(calls) == 1 and calls[0].method == "GET"
        assert operation["body"] == {
            "expected_row_version": 8, "fields": {"sort_order": value},
        }
        if case == "permission":
            allowed["value"] = False
        if case in ("foreign", "hash", "permission"):
            with pytest.raises(ConstructionApiClientError) as caught:
                await api.write_header(request=operation, **binding)
            assert caught.value.status_code == (403 if case == "permission" else 502)
        else:
            result = await api.write_header(request=operation, **binding)
            assert result["item"]["changed"] is (case != "noop")
            assert result["item"]["version_row_version"] == (8 if case == "noop" else 9)
        if case == "permission":
            assert len(calls) == 1
        else:
            patch = calls[1]
            segment = "sections" if kind == "SECTION" else "questions"
            assert patch.method == "PATCH"
            assert patch.url.path.endswith("/" + segment + "/" + item_id)
            assert patch.headers["idempotency-key"] == "same-order-key"
            assert json.loads(patch.content) == operation["body"]


async def open_order(c, kind):
    await open_draft(c)
    await click(c, "str_sections")
    await click(c, "str_section", 0)
    if kind == "QUESTION":
        await click(c, "str_question", 1)
    await click(c, "str_order")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,value", [("SECTION", "5"), ("QUESTION", "25")])
async def test_number_input_requires_review_and_keeps_exact_target(label_screen, kind, value):
    c = label_screen
    await open_order(c, kind)
    edit = c.data[cards.VIEW]["header_edit"]
    assert edit["field"] == "sort_order" and edit["kind"] == kind
    assert edit["item_id"] == identity(c.raw, kind)
    assert "definition" not in c.data[cards.VIEW]
    c.message.text = value
    await editor.header_message(c.message, c.state)
    assert c.data[cards.VIEW]["phase"] == "header_review"
    assert c.data[cards.VIEW]["header_request"]["body"] == {
        "expected_row_version": 8, "fields": {"sort_order": int(value)},
    }
    c.label_api.write_header.assert_not_awaited()
    old = cards.cb(c.data[cards.VIEW], "hdr_save")
    await click(c, "hdr_save")
    c.callback.data = old
    await cards.templates_callback(c.callback, c.state)
    c.label_api.write_header.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_input_does_not_read_or_prepare_again(label_screen):
    c = label_screen
    await open_order(c, "QUESTION")
    before = c.label_api.read_draft.await_count
    c.message.text = "-1"
    await editor.header_message(c.message, c.state)
    assert c.data[cards.VIEW]["phase"] == "header_input"
    assert c.label_api.read_draft.await_count == before
    c.label_api.prepare_header.assert_not_awaited()
    c.label_api.write_header.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_rank_is_rejected_before_save(label_screen):
    c = label_screen
    await open_order(c, "QUESTION")
    c.message.text = "10"
    await editor.header_message(c.message, c.state)
    assert c.data[cards.VIEW]["phase"] == "blocked"
    assert "header_request" not in c.data[cards.VIEW]
    c.label_api.write_header.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_noop_does_not_rebase(label_screen):
    c = label_screen
    await open_order(c, "QUESTION")
    c.raw["version_row_version"] = 9
    c.message.text = "20"
    await editor.header_message(c.message, c.state)
    assert c.data[cards.VIEW]["phase"] == "blocked"
    c.label_api.write_header.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_does_not_patch(label_screen):
    c = label_screen
    await open_order(c, "QUESTION")
    c.message.text = "25"
    await editor.header_message(c.message, c.state)
    await click(c, "hdr_back")
    assert c.data[cards.VIEW]["phase"] == "draft"
    c.label_api.write_header.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_outcome_replays_exact_number_and_key(label_screen):
    c = label_screen
    await open_order(c, "QUESTION")
    c.message.text = "25"
    await editor.header_message(c.message, c.state)
    c.label_api.write_header.side_effect = ConstructionApiClientError(
        status_code=504, code="api_timeout",
    )
    await click(c, "hdr_save")
    original = deepcopy(c.label_api.write_header.await_args.kwargs["request"])
    await click(c, "list")
    c.label_api.write_header.side_effect = None
    await click(c, "hdr_retry")
    assert c.label_api.write_header.await_args.kwargs["request"] == original
    c.label_api.prepare_header.assert_awaited_once()


@pytest.mark.asyncio
async def test_late_read_cannot_restore_another_organization(label_screen):
    c = label_screen
    await open_order(c, "QUESTION")
    original = c.label_api.prepare_header.side_effect

    async def late(**kwargs):
        result = await original(**kwargs)
        c.data[cards.CONTEXT]["tenant_id"] = str(uuid4())
        return result

    c.label_api.prepare_header.side_effect = late
    c.message.answer.reset_mock()
    c.message.text = "25"
    await editor.header_message(c.message, c.state)
    c.message.answer.assert_not_awaited()
    c.label_api.write_header.assert_not_awaited()


def test_order_controls_have_seven_languages_and_bounded_callbacks():
    for key in ("change_order", "sort_order", "order_enter", "order_review", "invalid_order"):
        assert len(LABELS[key]) == 7
    assert len(cards.cb({"nonce": "a" * 32}, "str_order").encode("utf-8")) <= 64
