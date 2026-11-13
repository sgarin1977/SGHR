"""Original item-label editing through versioned, keyed existing routes."""
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
from test_construction_bot_organization_templates import version
from handlers.construction import construction_organization_templates as cards
from handlers.construction import construction_template_draft_structure as structure
from handlers.construction import construction_template_draft_header as editor
from services.construction.construction_template_draft_label_bot_api import (
    TemplateDraftLabelBotApi, label_fields, locate,
)
from services.construction.construction_template_draft_header_bot_api import (
    TemplateDraftHeaderBotApi, digest, used_keys,
)
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from api.routes.construction_template_versions import TemplateDraftWriteData


def identity(raw, kind):
    reference = raw["section_references"][0]
    return reference["id"] if kind == "SECTION" else reference["questions"][1]["id"]


def test_shared_label_is_detached_and_unrelated_content_is_preserved():
    raw = draft_data(version(uuid4()), uuid4())
    section = raw["definition"]["sections"][0]
    old_key = section["label_key"]
    section["questions"][1]["label_key"] = old_key
    keys = used_keys(raw["definition"])
    for values in raw["definition"]["labels"].values():
        for key in set(values) - keys:
            values.pop(key)
    raw["definition"]["labels"]["uk"][old_key] = "Спільний переклад"
    before = deepcopy(raw)
    body, candidate, original = label_fields(raw, "SECTION", identity(raw, "SECTION"), "Renamed")
    new_key = candidate["sections"][0]["label_key"]
    assert raw == before
    assert new_key != old_key and body["fields"] == {"label_key": new_key}
    assert candidate["sections"][0]["questions"] == before["definition"]["sections"][0]["questions"]
    assert candidate["labels"]["en"][old_key] == original
    assert candidate["labels"]["uk"][old_key] == "Спільний переклад"
    assert new_key not in candidate["labels"]["uk"]
    assert candidate["labels"]["en"][new_key] == "Renamed"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["SECTION", "QUESTION"])
@pytest.mark.parametrize("case", ["change", "noop", "foreign", "wrong_item", "hash", "denied"])
async def test_existing_item_patch_checks_identity_hash_version_and_permission(kind, case):
    tenant, user = uuid4(), uuid4()
    raw = draft_data(version(tenant), uuid4())
    target_id = identity(raw, kind)
    original = raw["definition"]["labels"]["en"][locate(raw, kind, target_id)["label_key"]]
    value = original if case == "noop" else "Renamed"
    calls = []

    def transport(request):
        calls.append(request)
        if request.method == "GET":
            data = raw
        else:
            body = json.loads(request.content)
            candidate = deepcopy(raw)
            locate(candidate, kind, target_id).update(body["fields"])
            if "labels" in body:
                candidate["definition"]["labels"] = body["labels"]
            changed = candidate["definition"] != raw["definition"]
            result = {
                name: deepcopy(raw[name])
                for name in TemplateDraftWriteData.model_fields if name in raw
            }
            result.update(
                changed=changed, definition_hash=digest(candidate["definition"]),
                section_count=2, question_count=3,
                version_row_version=8 + int(changed),
            )
            if case == "foreign":
                result["tenant_id"] = str(uuid4())
            elif case == "hash":
                result["definition_hash"] = "0" * 64
            data = {
                "draft": result, "kind": kind,
                "item_id": str(uuid4()) if case == "wrong_item" else target_id,
            }
        return httpx.Response(200, json={"data": data, "meta": {}, "request_id": "local"})

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        client = ConstructionApiClient(http=http)
        allowed = {"value": True}
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
        prepared = await api.prepare_header(
            template_id=UUID(raw["template_id"]), version_id=UUID(raw["version_id"]),
            expected_row_version=8, expected_definition_hash=digest(raw["definition"]),
            field="label", kind=kind, item_id=target_id, value=value, **binding,
        )
        operation = {**prepared["request"], "key": "same-label-key"}
        assert len(calls) == 1 and calls[0].method == "GET"
        if case == "denied":
            allowed["value"] = False
        if case in ("foreign", "wrong_item", "hash", "denied"):
            with pytest.raises(ConstructionApiClientError) as caught:
                await api.write_header(request=operation, **binding)
            assert caught.value.status_code == (403 if case == "denied" else 502)
        else:
            result = await api.write_header(request=operation, **binding)
            assert result["item"]["version_row_version"] == (8 if case == "noop" else 9)
            assert result["item"]["changed"] is (case != "noop")
        if case == "denied":
            assert len(calls) == 1
        else:
            patch = calls[1]
            segment = "sections" if kind == "SECTION" else "questions"
            assert patch.method == "PATCH"
            assert patch.url.path.endswith("/" + segment + "/" + target_id)
            assert patch.headers["idempotency-key"] == "same-label-key"
            assert json.loads(patch.content) == operation["body"]
            assert set(operation["body"]["fields"]) == {"label_key"}
            if case == "noop":
                assert "labels" not in operation["body"]


@pytest.fixture
def label_screen(structure_screen, monkeypatch):
    c = structure_screen
    c.raw.update(section_count=2, question_count=3)
    c.label_api = NS(read_draft=AsyncMock(side_effect=lambda **kwargs: {
        **c.common, "item": deepcopy(c.raw),
    }))

    async def prepare(**kwargs):
        return await TemplateDraftHeaderBotApi.prepare_header(c.label_api, **kwargs)

    c.label_api.prepare_header = AsyncMock(side_effect=prepare)
    c.label_api.write_header = AsyncMock(return_value={
        **c.common, "item": {
            "template_id": c.raw["template_id"], "version_id": c.raw["version_id"],
        },
    })
    monkeypatch.setattr(structure, "TemplateDraftLabelBotApi", lambda service: c.label_api)
    monkeypatch.setattr(editor, "TemplateDraftHeaderBotApi", lambda service: c.label_api)

    # Reuse the same isolated entry context as structure navigation.
    monkeypatch.setattr(editor, "construction_bot_api_entry", structure.construction_bot_api_entry)
    return c


async def open_label(c, kind):
    await open_draft(c)
    await click(c, "str_sections")
    await click(c, "str_section", 0)
    if kind == "QUESTION":
        await click(c, "str_question", 1)
    await click(c, "str_label")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["SECTION", "QUESTION"])
async def test_original_label_needs_review_and_keeps_exact_target(label_screen, kind):
    c = label_screen
    await open_label(c, kind)
    assert c.data[cards.VIEW]["phase"] == "header_input"
    edit = c.data[cards.VIEW]["header_edit"]
    assert edit["kind"] == kind and edit["item_id"] == identity(c.raw, kind)
    assert "definition" not in c.data[cards.VIEW]
    c.message.text = "Renamed"
    await editor.header_message(c.message, c.state)
    assert c.data[cards.VIEW]["phase"] == "header_review"
    c.label_api.write_header.assert_not_awaited()
    old_callback = cards.cb(c.data[cards.VIEW], "hdr_save")
    await click(c, "hdr_save")
    operation = c.label_api.write_header.await_args.kwargs["request"]
    assert operation["kind"] == kind and operation["item_id"] == identity(c.raw, kind)
    assert operation["body"]["expected_row_version"] == 8
    assert c.data[cards.HEADER_PENDING] == {}
    c.callback.data = old_callback
    await cards.templates_callback(c.callback, c.state)
    c.label_api.write_header.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_does_not_patch(label_screen):
    c = label_screen
    await open_label(c, "SECTION")
    c.message.text = "Renamed"
    await editor.header_message(c.message, c.state)
    await click(c, "hdr_back")
    assert c.data[cards.VIEW]["phase"] == "draft"
    c.label_api.write_header.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_change_even_for_noop_is_not_rebased(label_screen):
    c = label_screen
    await open_label(c, "SECTION")
    c.raw["version_row_version"] = 9
    c.message.text = "Demolition"
    await editor.header_message(c.message, c.state)
    assert c.data[cards.VIEW]["phase"] == "blocked"
    c.label_api.write_header.assert_not_awaited()


@pytest.mark.asyncio
async def test_timeout_replays_identical_item_body_and_key_after_navigation(label_screen):
    c = label_screen
    await open_label(c, "QUESTION")
    c.message.text = "Renamed"
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
    assert c.data[cards.HEADER_PENDING] == {}


@pytest.mark.asyncio
async def test_late_preparation_cannot_restore_another_organization(label_screen):
    c = label_screen
    await open_label(c, "SECTION")
    original = c.label_api.prepare_header.side_effect

    async def late(**kwargs):
        result = await original(**kwargs)
        c.data[cards.CONTEXT]["tenant_id"] = str(uuid4())
        return result

    c.label_api.prepare_header.side_effect = late
    c.message.answer.reset_mock()
    c.message.text = "Renamed"
    await editor.header_message(c.message, c.state)
    c.message.answer.assert_not_awaited()
    c.label_api.write_header.assert_not_awaited()
