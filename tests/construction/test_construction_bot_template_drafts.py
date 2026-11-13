"""Local exact-base creation, historical receipts and fresh draft reads."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4
import json

import httpx
import pytest
from pydantic import SecretStr

from test_construction_bot_organization_templates import (
    screen, templates_screen, version, open_templates, click,
)
from handlers.construction import construction_organization_templates as cards
from handlers.construction import construction_template_drafts as handler
from services.construction.construction_template_draft_bot_api import TemplateDraftBotApi
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from api.routes.construction_template_versions import TemplateDraftData
from ui.construction_template_drafts import LABELS


def creation(tenant, user, published, draft_id):
    return {
        "tenant_id": str(tenant), "template_id": published["template_id"],
        "template_row_version": published["template_row_version"],
        "base_version_id": published["version_id"], "version_id": str(draft_id),
        "version": 5, "version_row_version": 1, "status": "DRAFT",
        "source_starter_version_id": published["source_starter_version_id"],
        "source_language": published["source_language"],
        "section_count": 2, "question_count": 3,
        "created_by": str(user), "created_at": published["created_at"],
    }


def draft_data(published, draft_id):
    fields = {
        name: deepcopy(published[name])
        for name in TemplateDraftData.model_fields if name in published
    }
    fields.update(
        version_id=str(draft_id), version=5, version_row_version=8, status="DRAFT",
    )
    for section in fields["section_references"]:
        section["id"] = str(uuid4())
        for question in section["questions"]:
            question["id"] = str(uuid4())
    return fields


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "create", "read", "foreign_create", "wrong_base", "wrong_root_version",
    "reused_version_id", "bool_counter", "foreign_read", "reference", "published", "denied",
])
async def test_existing_draft_http_contract(case):
    tenant, user, draft_id = uuid4(), uuid4(), uuid4()
    published = version(tenant)
    created = creation(tenant, user, published, draft_id)
    current = draft_data(published, draft_id)
    reading = case in ("read", "foreign_read", "reference", "published")
    if case == "foreign_create":
        created["tenant_id"] = str(uuid4())
    elif case == "wrong_base":
        created["base_version_id"] = str(uuid4())
    elif case == "wrong_root_version":
        created["template_row_version"] += 1
    elif case == "reused_version_id":
        created["version_id"] = published["version_id"]
    elif case == "bool_counter":
        created["version_row_version"] = True
    elif case == "foreign_read":
        current["tenant_id"] = str(uuid4())
    elif case == "reference":
        current["section_references"][0]["questions"][0]["code"] = "OTHER"
    elif case == "published":
        current["status"] = "PUBLISHED"
    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(200 if reading else 201, json={
            "data": current if reading else created, "meta": {}, "request_id": "local",
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
        api = TemplateDraftBotApi(entry)
        binding = dict(telegram_user_id=123, tenant_id=tenant, expected_user_id=user)
        operation = {
            **handler.base({
                **published, "section_count": 2, "question_count": 3,
            }),
            "key": "original-draft-key",
        }
        call = (
            api.read_draft(
                template_id=UUID(published["template_id"]), version_id=draft_id, **binding,
            ) if reading else api.create_draft(request=operation, **binding)
        )
        if case not in ("create", "read"):
            with pytest.raises(ConstructionApiClientError) as caught:
                await call
            assert caught.value.status_code == (403 if case == "denied" else 502)
        else:
            result = await call
            assert result["item"]["version"] == 5
            if reading:
                assert result["item"]["version_row_version"] == 8
                assert result["item"]["question_count"] == 3
                assert "definition" not in result["item"]
                assert "title" not in result["item"]
            else:
                assert result["item"]["template_row_version"] == published["template_row_version"]
    if case == "denied":
        assert calls == []
    else:
        assert len(calls) == 1
        request = calls[0]
        assert not request.url.query
        if reading:
            assert request.method == "GET"
            assert request.url.path.endswith(
                "/" + published["template_id"] + "/drafts/" + str(draft_id)
            )
            assert "idempotency-key" not in request.headers
        else:
            assert request.method == "POST"
            assert request.url.path.endswith("/" + published["template_id"] + "/drafts")
            assert request.headers["idempotency-key"] == "original-draft-key"
            assert json.loads(request.content) == {
                "base_version_id": published["version_id"],
                "expected_template_row_version": published["template_row_version"],
            }


@pytest.fixture
def draft_screen(templates_screen, monkeypatch):
    c = templates_screen
    c.draft_id = uuid4()
    c.created = creation(c.tenant, c.user, c.item, c.draft_id)
    c.current = {
        "template_id": c.item["template_id"], "version_id": str(c.draft_id),
        "version": 5, "version_row_version": 8, "template_row_version": 1,
        "definition_hash": c.item["content_hash"],
        "section_count": 2, "question_count": 3,
    }
    c.draft_api = NS(
        get_template=c.templates_api.get_template,
        create_draft=AsyncMock(return_value={**c.common, "item": deepcopy(c.created)}),
        read_draft=AsyncMock(side_effect=lambda **kwargs: {
            **c.common, "item": deepcopy(c.current),
        }),
    )
    monkeypatch.setattr(handler, "TemplateDraftBotApi", lambda service: c.draft_api)

    @asynccontextmanager
    async def entry(**kwargs):
        yield NS()

    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    return c


async def open_card(c):
    await open_templates(c)
    await click(c, "open", 0)


@pytest.mark.asyncio
async def test_explicit_creation_and_separate_current_read(draft_screen):
    c = draft_screen
    await open_card(c)
    await click(c, "draftnew")
    assert c.data[cards.VIEW]["phase"] == "draft_review"
    c.draft_api.create_draft.assert_not_awaited()
    old = cards.cb(c.data[cards.VIEW], "draftcreate")
    await click(c, "draftcreate")
    request = c.draft_api.create_draft.await_args.kwargs["request"]
    assert request["base_version_id"] == c.item["version_id"]
    assert request["expected_template_row_version"] == 1
    assert request["base_version"] == 1
    assert len(request["key"]) == 32
    assert c.data[cards.VIEW]["phase"] == "draft_created"
    c.draft_api.read_draft.assert_not_awaited()
    c.callback.data = old
    await cards.templates_callback(c.callback, c.state)
    c.draft_api.create_draft.assert_awaited_once()
    await click(c, "draftopen")
    assert c.draft_api.read_draft.await_args.kwargs["version_id"] == c.draft_id
    assert c.data[cards.VIEW]["draft"]["version_row_version"] == 8
    await click(c, "draftrefresh")
    assert c.draft_api.read_draft.await_args.kwargs["version_id"] == c.draft_id


@pytest.mark.asyncio
async def test_cancel_and_changed_root_before_review_never_create(draft_screen):
    c = draft_screen
    await open_card(c)
    await click(c, "draftnew")
    await click(c, "list")
    c.draft_api.create_draft.assert_not_awaited()
    await click(c, "open", 0)
    c.item["template_row_version"] = 9
    await click(c, "draftnew")
    assert c.data[cards.VIEW]["phase"] == "blocked"
    c.draft_api.create_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_creation_reuses_request_after_navigation(draft_screen):
    c = draft_screen
    await open_card(c)
    await click(c, "draftnew")
    c.draft_api.create_draft.side_effect = ConstructionApiClientError(
        status_code=504, code="api_timeout",
    )
    await click(c, "draftcreate")
    original = deepcopy(c.draft_api.create_draft.await_args.kwargs["request"])
    assert c.data[cards.VIEW]["phase"] == "draft_pending"
    await click(c, "list")
    assert c.data[cards.PENDING][handler.slot(c.parent)] == original
    c.draft_api.create_draft.side_effect = None
    await click(c, "draftretry")
    assert c.draft_api.create_draft.await_args.kwargs["request"] == original
    assert c.draft_api.create_draft.await_count == 2
    assert c.data[cards.PENDING] == {}
    c.draft_api.read_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_server_version_conflict_does_not_rebase(draft_screen):
    c = draft_screen
    await open_card(c)
    await click(c, "draftnew")
    c.item["template_row_version"] = 9
    c.draft_api.create_draft.side_effect = ConstructionApiClientError(
        status_code=409, code="ROW_VERSION_CONFLICT",
    )
    await click(c, "draftcreate")
    assert c.draft_api.create_draft.await_args.kwargs["request"]["expected_template_row_version"] == 1
    assert c.data[cards.PENDING] == {}
    assert c.data[cards.VIEW]["phase"] == "blocked"
    c.draft_api.create_draft.assert_awaited_once()


@pytest.mark.asyncio
async def test_created_selector_survives_return_to_list(draft_screen):
    c = draft_screen
    await open_card(c)
    await click(c, "draftnew")
    await click(c, "draftcreate")
    await click(c, "list")
    await click(c, "open", 0)
    assert c.data[cards.VIEW]["known_draft"]["version_id"] == str(c.draft_id)
    await click(c, "draftopen")
    assert c.data[cards.VIEW]["draft"]["version_id"] == str(c.draft_id)
    c.draft_api.create_draft.assert_awaited_once()


@pytest.mark.asyncio
async def test_late_draft_read_cannot_restore_another_tenant(draft_screen):
    c = draft_screen
    await open_card(c)
    await click(c, "draftnew")
    await click(c, "draftcreate")
    c.sent.reset_mock()

    async def late(**kwargs):
        c.data[cards.CONTEXT]["tenant_id"] = str(uuid4())
        return {**c.common, "item": deepcopy(c.current)}

    c.draft_api.read_draft.side_effect = late
    await click(c, "draftopen")
    c.sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_inactive_template_can_prepare_a_draft_without_enabling_it(draft_screen):
    c = draft_screen
    c.item["active"] = False
    c.page[0]["active"] = False
    await open_card(c)
    await click(c, "draftnew")
    await click(c, "draftcreate")
    c.draft_api.create_draft.assert_awaited_once()
    assert c.item["active"] is False
    assert "active" not in c.draft_api.create_draft.await_args.kwargs["request"]


def test_labels_and_callback_size():
    assert all(len(values) == 7 for values in LABELS.values())
    for action in ("draftnew", "draftcreate", "draftretry", "draftopen", "draftrefresh"):
        assert len(cards.cb({"nonce": "a" * 32}, action).encode("utf-8")) <= 64
