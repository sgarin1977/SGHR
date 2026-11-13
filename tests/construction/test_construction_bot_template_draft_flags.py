"""Boolean draft-item edits preserve structure, versions and replay intent."""
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
from services.construction.construction_template_draft_label_bot_api import (
    TemplateDraftLabelBotApi, flag_fields, locate,
)
from services.construction.construction_template_draft_header_bot_api import (
    TemplateDraftHeaderBotApi, digest,
)
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from api.routes.construction_template_versions import TemplateDraftWriteData
from ui.construction_template_draft_header import LABELS


@pytest.mark.parametrize("field", ["active", "required"])
def test_phase_opening_question_cannot_be_disabled_or_optional(field):
    raw = draft_data(version(uuid4()), uuid4())
    item_id = raw["section_references"][0]["questions"][0]["id"]
    before = deepcopy(raw)
    with pytest.raises(ConstructionApiClientError) as caught:
        flag_fields(raw, "QUESTION", item_id, field, False)
    assert caught.value.status_code == 422
    assert raw == before


@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_flags_require_actual_booleans(value):
    raw = draft_data(version(uuid4()), uuid4())
    with pytest.raises(ValueError):
        flag_fields(raw, "QUESTION", identity(raw, "QUESTION"), "active", value)


def test_flag_change_preserves_labels_conditions_and_every_other_field():
    raw = draft_data(version(uuid4()), uuid4())
    before = deepcopy(raw)
    item_id = identity(raw, "QUESTION")
    body, candidate, old = flag_fields(raw, "QUESTION", item_id, "required", False)
    assert raw == before
    assert body == {"fields": {"required": False}}
    expected = deepcopy(raw["definition"])
    locate({**raw, "definition": expected}, "QUESTION", item_id)["required"] = False
    assert candidate == expected
    assert candidate["labels"] == before["definition"]["labels"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,field", [
    ("SECTION", "active"), ("QUESTION", "active"), ("QUESTION", "required"),
])
@pytest.mark.parametrize("case", ["change", "noop", "stale", "foreign", "hash", "permission"])
async def test_real_client_uses_sparse_boolean_patch_and_checks_receipt(kind, field, case):
    tenant, user = uuid4(), uuid4()
    raw = draft_data(version(tenant), uuid4())
    item_id = identity(raw, kind)
    calls = []
    allowed = {"value": True}

    def transport(request):
        calls.append(request)
        if request.method == "GET":
            data = raw
        else:
            body = json.loads(request.content)
            candidate = deepcopy(raw)
            locate(candidate, kind, item_id).update(body["fields"])
            changed = candidate["definition"] != raw["definition"]
            result = {
                name: deepcopy(raw[name])
                for name in TemplateDraftWriteData.model_fields if name in raw
            }
            result.update(
                changed=changed, version_row_version=8 + int(changed),
                definition_hash=digest(candidate["definition"]),
                section_count=2, question_count=3,
            )
            if case == "foreign":
                result["tenant_id"] = str(uuid4())
            elif case == "hash":
                result["definition_hash"] = "0" * 64
            data = {"draft": result, "kind": kind, "item_id": item_id}
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
        api = TemplateDraftLabelBotApi(entry)
        binding = dict(telegram_user_id=123, tenant_id=tenant, expected_user_id=user)
        kwargs = dict(
            template_id=UUID(raw["template_id"]), version_id=UUID(raw["version_id"]),
            expected_row_version=9 if case == "stale" else 8,
            expected_definition_hash=digest(raw["definition"]),
            kind=kind, item_id=item_id, field=field,
        )
        if case == "stale":
            with pytest.raises(ConstructionApiClientError) as caught:
                await api.prepare_flag(**kwargs, **binding)
            assert caught.value.status_code == 409
            assert len(calls) == 1 and calls[0].method == "GET"
            return
        reviewed = await api.prepare_flag(**kwargs, **binding)
        assert reviewed["value"] is not reviewed["original"]
        operation = {**reviewed["request"], "key": "same-flag-key"}
        if case == "noop":
            operation["body"]["fields"][field] = locate(raw, kind, item_id)[field]
            operation["definition_hash"] = digest(raw["definition"])
        if case == "permission":
            allowed["value"] = False
        if case in ("foreign", "hash", "permission"):
            with pytest.raises(ConstructionApiClientError) as caught:
                await TemplateDraftHeaderBotApi(entry).write_header(
                    request=operation, **binding,
                )
            assert caught.value.status_code == (403 if case == "permission" else 502)
        else:
            result = await TemplateDraftHeaderBotApi(entry).write_header(
                request=operation, **binding,
            )
            assert result["item"]["changed"] is (case != "noop")
            assert result["item"]["version_row_version"] == (8 if case == "noop" else 9)
        if case == "permission":
            assert len(calls) == 1
        else:
            patch = calls[1]
            segment = "sections" if kind == "SECTION" else "questions"
            assert patch.method == "PATCH"
            assert patch.url.path.endswith("/" + segment + "/" + item_id)
            assert patch.headers["idempotency-key"] == "same-flag-key"
            assert json.loads(patch.content) == operation["body"]
            assert set(operation["body"]) == {"expected_row_version", "fields"}
            assert set(operation["body"]["fields"]) == {field}
            assert type(operation["body"]["fields"][field]) is bool


@pytest.fixture
def flag_screen(label_screen):
    c = label_screen

    async def prepare(**kwargs):
        return await TemplateDraftLabelBotApi.prepare_flag(c.label_api, **kwargs)

    c.label_api.prepare_flag = AsyncMock(side_effect=prepare)
    return c


async def open_target(c, kind, gate=False):
    await open_draft(c)
    await click(c, "str_sections")
    await click(c, "str_section", 0)
    if kind == "QUESTION":
        await click(c, "str_question", 0 if gate else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,action,field", [
    ("SECTION", "str_active", "active"),
    ("QUESTION", "str_active", "active"),
    ("QUESTION", "str_required", "required"),
])
async def test_toggle_only_reviews_until_confirmed_and_pins_exact_item(flag_screen, kind, action, field):
    c = flag_screen
    await open_target(c, kind)
    await click(c, action)
    assert c.data[cards.VIEW]["phase"] == "header_review"
    request = c.data[cards.VIEW]["header_request"]
    assert request["kind"] == kind and request["item_id"] == identity(c.raw, kind)
    assert request["body"] == {
        "expected_row_version": 8,
        "fields": {field: not locate(c.raw, kind, identity(c.raw, kind))[field]},
    }
    c.label_api.write_header.assert_not_awaited()
    old = cards.cb(c.data[cards.VIEW], "hdr_save")
    await click(c, "hdr_save")
    c.label_api.write_header.assert_awaited_once()
    assert c.data[cards.HEADER_PENDING] == {}
    c.callback.data = old
    await cards.templates_callback(c.callback, c.state)
    c.label_api.write_header.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_has_no_patch(flag_screen):
    c = flag_screen
    await open_target(c, "QUESTION")
    await click(c, "str_required")
    await click(c, "hdr_back")
    assert c.data[cards.VIEW]["phase"] == "draft"
    c.label_api.write_header.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["str_active", "str_required"])
async def test_gate_change_is_rejected_without_save_or_patch(flag_screen, action):
    c = flag_screen
    await open_target(c, "QUESTION", gate=True)
    await click(c, action)
    assert c.data[cards.VIEW]["phase"] == "structure_blocked"
    assert "header_request" not in c.data[cards.VIEW]
    c.label_api.write_header.assert_not_awaited()


@pytest.mark.asyncio
async def test_required_is_not_a_section_field(flag_screen):
    c = flag_screen
    await open_target(c, "SECTION")
    await click(c, "str_required")
    c.label_api.prepare_flag.assert_not_awaited()
    c.label_api.write_header.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_draft_does_not_silently_rebase(flag_screen):
    c = flag_screen
    await open_target(c, "QUESTION")
    c.raw["version_row_version"] = 9
    await click(c, "str_required")
    assert c.data[cards.VIEW]["phase"] == "structure_blocked"
    c.label_api.write_header.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_result_replays_same_boolean_body_and_key(flag_screen):
    c = flag_screen
    await open_target(c, "QUESTION")
    await click(c, "str_required")
    c.label_api.write_header.side_effect = ConstructionApiClientError(
        status_code=504, code="api_timeout",
    )
    await click(c, "hdr_save")
    original = deepcopy(c.label_api.write_header.await_args.kwargs["request"])
    await click(c, "list")
    c.label_api.write_header.side_effect = None
    await click(c, "hdr_retry")
    assert c.label_api.write_header.await_args.kwargs["request"] == original
    c.label_api.prepare_flag.assert_awaited_once()
    assert c.data[cards.HEADER_PENDING] == {}


@pytest.mark.asyncio
async def test_late_flag_preparation_cannot_restore_another_tenant(flag_screen):
    c = flag_screen
    await open_target(c, "QUESTION")
    original = c.label_api.prepare_flag.side_effect

    async def late(**kwargs):
        result = await original(**kwargs)
        c.data[cards.CONTEXT]["tenant_id"] = str(uuid4())
        return result

    c.label_api.prepare_flag.side_effect = late
    c.sent.reset_mock()
    await click(c, "str_required")
    c.sent.assert_not_awaited()
    c.label_api.write_header.assert_not_awaited()


def test_flag_controls_are_localized_and_callbacks_fit():
    for key in ("change_active", "change_required", "review_flags", "invalid_change"):
        assert len(LABELS[key]) == 7
    for action in ("str_active", "str_required"):
        assert len(cards.cb({"nonce": "a" * 32}, action).encode("utf-8")) <= 64
