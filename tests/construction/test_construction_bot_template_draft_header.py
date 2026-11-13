"""Original header edits preserve other content and keyed retry intent."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4
import json

import httpx
import pytest
from pydantic import SecretStr

from test_construction_bot_template_drafts import (
    screen, templates_screen, draft_screen, open_card, click, draft_data,
)
from test_construction_bot_organization_templates import version
from handlers.construction import construction_organization_templates as cards
from handlers.construction import construction_template_draft_header as handler
from services.construction.construction_template_draft_header_bot_api import (
    TemplateDraftHeaderBotApi, build_fields, digest,
)
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from api.routes.construction_template_versions import TemplateDraftWriteData
from ui.construction_template_draft_header import LABELS


def test_title_edit_preserves_questions_and_other_field_translations():
    raw = version(uuid4())["definition"]
    before = deepcopy(raw)
    fields, candidate = build_fields(raw, "en", "title", "Renamed")
    assert raw == before
    assert candidate["sections"] == before["sections"]
    assert candidate["labels"]["en"][candidate["title_key"]] == "Renamed"
    assert candidate["labels"]["uk"]["description"] == before["labels"]["uk"]["description"]
    assert candidate["title_key"] not in candidate["labels"]["uk"]
    assert set(fields) == {"labels"}


def test_shared_title_label_is_detached_without_changing_section():
    raw = version(uuid4())["definition"]
    old = raw["title_key"]
    raw["title_key"] = raw["sections"][0]["label_key"]
    for values in raw["labels"].values():
        values.pop(old, None)
    before = deepcopy(raw)
    fields, candidate = build_fields(raw, "en", "title", "Renamed")
    assert candidate["title_key"] != before["title_key"]
    assert candidate["sections"] == before["sections"]
    assert candidate["labels"]["en"][before["title_key"]] == before["labels"]["en"][before["title_key"]]
    assert fields["title_key"] == candidate["title_key"]


def test_clear_description_prunes_only_unused_description_labels():
    raw = version(uuid4())["definition"]
    before = deepcopy(raw)
    fields, candidate = build_fields(raw, "en", "description", None)
    assert fields["description_key"] is None
    assert candidate["description_key"] is None
    assert candidate["sections"] == before["sections"]
    assert candidate["labels"]["uk"]["title"] == before["labels"]["uk"]["title"]
    assert all(before["description_key"] not in values for values in candidate["labels"].values())


def test_current_noop_preserves_all_supplied_translations():
    raw = version(uuid4())["definition"]
    fields, candidate = build_fields(raw, "en", "title", "Starter")
    assert candidate == raw
    assert fields == {"labels": raw["labels"]}


def prepared(tenant, noop=False):
    raw = version(tenant)
    current = draft_data(raw, uuid4())
    fields, candidate = build_fields(
        current["definition"], "en", "title", "Starter" if noop else "Renamed",
    )
    request = {
        "template_id": current["template_id"], "version_id": current["version_id"],
        "body": {"expected_row_version": 8, **fields}, "version": 5,
        "source_language": "en", "expected_title": "Starter" if noop else "Renamed",
        "expected_description": current["description"],
        "definition_hash": digest(candidate), "section_count": 2, "question_count": 3,
        "key": "original-header-key",
    }
    response = {name: current[name] for name in TemplateDraftWriteData.model_fields if name in current}
    response.update(
        title=request["expected_title"], definition_hash=request["definition_hash"],
        changed=not noop, version_row_version=8 if noop else 9,
        section_count=2, question_count=3,
    )
    return current, request, response


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["change", "noop", "foreign", "hash", "version", "denied"])
async def test_existing_sparse_patch_and_original_receipt_contract(case):
    tenant, user = uuid4(), uuid4()
    current, request, response = prepared(tenant, noop=case == "noop")
    if case == "foreign":
        response["tenant_id"] = str(uuid4())
    elif case == "hash":
        response["definition_hash"] = "0" * 64
    elif case == "version":
        response["version_row_version"] = 99
    calls = []

    def transport(http_request):
        calls.append(http_request)
        return httpx.Response(200, json={"data": response, "meta": {}, "request_id": "local"})

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
        call = TemplateDraftHeaderBotApi(entry).write_header(
            request=request, telegram_user_id=123, tenant_id=tenant, expected_user_id=user,
        )
        if case in ("foreign", "hash", "version", "denied"):
            with pytest.raises(ConstructionApiClientError) as caught:
                await call
            assert caught.value.status_code == (403 if case == "denied" else 502)
        else:
            result = await call
            assert result["item"]["version_row_version"] == (8 if case == "noop" else 9)
    if case == "denied":
        assert calls == []
    else:
        assert len(calls) == 1 and calls[0].method == "PATCH"
        assert calls[0].url.path.endswith(
            "/" + current["template_id"] + "/drafts/" + current["version_id"]
        )
        assert calls[0].headers["idempotency-key"] == "original-header-key"
        assert json.loads(calls[0].content) == request["body"]
        assert "source_language" not in request["body"]


@pytest.fixture
def header_screen(draft_screen, monkeypatch):
    c = draft_screen
    raw = version(c.tenant)
    c.header_item = {
        **deepcopy(c.current), "definition": deepcopy(raw["definition"]),
        "source_language": "en", "title": raw["title"], "description": raw["description"],
    }
    c.header_api = NS(
        read_draft=AsyncMock(side_effect=lambda **kwargs: {
            **c.common, "item": deepcopy(c.header_item),
        }),
    )
    async def prepare(**kwargs):
        return await TemplateDraftHeaderBotApi.prepare_header(c.header_api, **kwargs)
    async def write(**kwargs):
        request = kwargs["request"]
        c.current["version_row_version"] = request["body"]["expected_row_version"] + 1
        c.current["definition_hash"] = request["definition_hash"]
        return {
            **c.common,
            "item": {"template_id": request["template_id"], "version_id": request["version_id"]},
        }
    c.header_api.prepare_header = AsyncMock(side_effect=prepare)
    c.header_api.write_header = AsyncMock(side_effect=write)
    monkeypatch.setattr(handler, "TemplateDraftHeaderBotApi", lambda service: c.header_api)

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


async def enter(c, value):
    c.message.text = value
    await handler.header_message(c.message, c.state)


@pytest.mark.asyncio
async def test_edit_requires_review_and_keeps_original_version(header_screen):
    c = header_screen
    await open_draft(c)
    await click(c, "hdr_title")
    assert c.data[cards.VIEW]["phase"] == "header_input"
    await enter(c, "Renamed")
    assert c.data[cards.VIEW]["phase"] == "header_review"
    c.header_api.write_header.assert_not_awaited()
    read_count = c.header_api.read_draft.await_count
    old_callback = cards.cb(c.data[cards.VIEW], "hdr_save")
    await click(c, "hdr_save")
    request = c.header_api.write_header.await_args.kwargs["request"]
    assert request["body"]["expected_row_version"] == 8
    assert len(request["key"]) == 32
    assert c.header_api.read_draft.await_count == read_count
    assert c.data[cards.VIEW]["phase"] == "header_saved"
    assert c.data[cards.HEADER_PENDING] == {}
    c.callback.data = old_callback
    await cards.templates_callback(c.callback, c.state)
    c.header_api.write_header.assert_awaited_once()
    assert c.item["version"] == 1 and c.item["title"] == "Starter"


@pytest.mark.asyncio
async def test_cancel_writes_nothing(header_screen):
    c = header_screen
    await open_draft(c)
    await click(c, "hdr_title")
    await enter(c, "Renamed")
    await click(c, "hdr_back")
    assert c.data[cards.VIEW]["phase"] == "draft"
    c.header_api.write_header.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_description_clear_is_reviewed(header_screen):
    c = header_screen
    await open_draft(c)
    await click(c, "hdr_description")
    await click(c, "hdr_clear")
    assert c.data[cards.VIEW]["header_request"]["body"]["description_key"] is None
    assert c.data[cards.VIEW]["phase"] == "header_review"
    c.header_api.write_header.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_version_even_for_noop_does_not_rebase(header_screen):
    c = header_screen
    await open_draft(c)
    await click(c, "hdr_title")
    c.header_item["version_row_version"] = 9
    await enter(c, "Starter")
    assert c.data[cards.VIEW]["phase"] == "blocked"
    c.header_api.write_header.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_write_reuses_prepared_body_and_key(header_screen):
    c = header_screen
    await open_draft(c)
    await click(c, "hdr_title")
    await enter(c, "Renamed")
    original_write = c.header_api.write_header.side_effect
    c.header_api.write_header.side_effect = ConstructionApiClientError(
        status_code=504, code="api_timeout",
    )
    await click(c, "hdr_save")
    original = deepcopy(c.header_api.write_header.await_args.kwargs["request"])
    await click(c, "list")
    c.header_api.write_header.side_effect = original_write
    await click(c, "hdr_retry")
    assert c.header_api.write_header.await_args.kwargs["request"] == original
    assert c.header_api.prepare_header.await_count == 1
    assert c.data[cards.HEADER_PENDING] == {}


@pytest.mark.asyncio
async def test_late_input_result_cannot_restore_another_tenant(header_screen):
    c = header_screen
    await open_draft(c)
    await click(c, "hdr_title")
    original_prepare = c.header_api.prepare_header.side_effect

    async def late(**kwargs):
        result = await original_prepare(**kwargs)
        c.data[cards.CONTEXT]["tenant_id"] = str(uuid4())
        return result

    c.header_api.prepare_header.side_effect = late
    c.message.answer.reset_mock()
    await enter(c, "Renamed")
    c.message.answer.assert_not_awaited()
    c.header_api.write_header.assert_not_awaited()


def test_labels_and_callback_bounds():
    assert all(len(values) == 7 for values in LABELS.values())
    assert len(cards.cb({"nonce": "a" * 32}, "hdr_description").encode("utf-8")) <= 64
