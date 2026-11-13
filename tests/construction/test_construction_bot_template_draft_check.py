"""Read-only readiness preserves exact identity/version and private navigation."""
from copy import deepcopy
from unittest.mock import AsyncMock
from types import SimpleNamespace as NS
from uuid import uuid4
import json

import httpx
import pytest
from pydantic import SecretStr

from test_construction_bot_template_drafts import (
    screen, templates_screen, draft_screen, open_card, click,
)
from handlers.construction import construction_organization_templates as cards
from handlers.construction import construction_template_draft_check as handler
from services.construction.construction_template_draft_check_bot_api import TemplateDraftCheckBotApi
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from ui.construction_template_draft_check import LABELS


def checked(selector, tenant, **changes):
    return {
        "tenant_id": str(tenant), **deepcopy(selector),
        "active": True, "section_count": 2, "question_count": 3,
        "binding_count": 0, **changes,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "ok", "inactive", "foreign", "wrong_draft", "hash", "version",
    "root_version", "bool_counter", "binding_count", "permission", "conflict",
])
async def test_existing_validate_operation_has_no_key_and_checks_context(case):
    tenant, user = uuid4(), uuid4()
    selector = {
        "template_id": str(uuid4()), "version_id": str(uuid4()),
        "version": 5, "version_row_version": 8,
        "template_row_version": 1, "definition_hash": "a" * 64,
    }
    data = checked(selector, tenant)
    if case == "inactive":
        data["active"] = False
    elif case == "foreign":
        data["tenant_id"] = str(uuid4())
    elif case == "wrong_draft":
        data["version_id"] = str(uuid4())
    elif case == "hash":
        data["definition_hash"] = "b" * 64
    elif case == "version":
        data["version_row_version"] = 9
    elif case == "root_version":
        data["template_row_version"] = 2
    elif case == "bool_counter":
        data["binding_count"] = True
    elif case == "binding_count":
        data["binding_count"] = 4
    calls = []

    def transport(request):
        calls.append(request)
        if case == "conflict":
            return httpx.Response(409, json={"error": {
                "code": "ROW_VERSION_CONFLICT", "message": "Conflict", "request_id": "local",
            }})
        return httpx.Response(200, json={"data": data, "meta": {}, "request_id": "local"})

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        client = ConstructionApiClient(http=http)
        client.company_profile_capabilities = AsyncMock(return_value=NS(data={
            "tenant_id": str(tenant), "can_manage_settings": case != "permission",
        }))
        entry = NS(
            _presentation=AsyncMock(return_value=(
                PlatformApiClientSession(user, tenant, SecretStr("local-only")),
                {"name": "Org", "language": "uk"},
            )),
            api=NS(construction=client),
        )
        api = TemplateDraftCheckBotApi(entry)
        call = api.check_draft(
            selector=selector, telegram_user_id=123,
            tenant_id=tenant, expected_user_id=user,
        )
        if case in ("ok", "inactive"):
            result = await call
            assert result["item"]["active"] is (case != "inactive")
            assert result["item"]["version_row_version"] == 8
        else:
            with pytest.raises(ConstructionApiClientError) as caught:
                await call
            expected = (
                403 if case == "permission"
                else 409 if case in ("hash", "version", "root_version", "conflict")
                else 502
            )
            assert caught.value.status_code == expected
    if case == "permission":
        assert calls == []
    else:
        assert len(calls) == 1
        request = calls[0]
        assert request.method == "POST"
        assert request.url.path.endswith(
            "/" + selector["template_id"] + "/drafts/" + selector["version_id"] + "/validate"
        )
        assert not request.url.query
        assert "idempotency-key" not in request.headers
        assert json.loads(request.content) == {"expected_row_version": 8}


@pytest.fixture
def check_screen(draft_screen, monkeypatch):
    c = draft_screen
    c.check_api = NS(check_draft=AsyncMock(side_effect=lambda **kwargs: {
        **c.common, "item": checked(kwargs["selector"], c.tenant),
    }))
    monkeypatch.setattr(handler, "TemplateDraftCheckBotApi", lambda service: c.check_api)
    return c


async def open_draft(c):
    await open_card(c)
    await click(c, "draftnew")
    await click(c, "draftcreate")
    await click(c, "draftopen")


@pytest.mark.asyncio
async def test_check_is_separate_from_creation_and_does_not_publish(check_screen):
    c = check_screen
    await open_draft(c)
    original = deepcopy(c.data[cards.VIEW]["draft"])
    c.check_api.check_draft.assert_not_awaited()
    await click(c, "draftcheck")
    assert c.check_api.check_draft.await_args.kwargs["selector"] == original
    assert c.data[cards.VIEW]["phase"] == "draft_checked"
    assert c.data[cards.VIEW]["draft"] == original
    assert "header_request" not in c.data[cards.VIEW]
    c.draft_api.create_draft.assert_awaited_once()
    assert "не опубліковано" in c.sent.await_args.kwargs["text"]
    await click(c, "draftrefresh")
    assert c.data[cards.VIEW]["phase"] == "draft"


@pytest.mark.asyncio
async def test_unresolved_edit_is_not_reported_as_ready(check_screen):
    c = check_screen
    await open_draft(c)
    view = c.data[cards.VIEW]
    slot = view["tenant_id"] + ":" + view["actor_user_id"]
    c.data[cards.HEADER_PENDING] = {slot: {"key": "pending-local-operation"}}
    await click(c, "draftcheck")
    c.check_api.check_draft.assert_not_awaited()
    assert c.data[cards.VIEW]["phase"] == "draft_check_failed"
    assert c.data[cards.HEADER_PENDING][slot]["key"] == "pending-local-operation"


@pytest.mark.asyncio
async def test_conflict_keeps_old_selector_until_explicit_refresh(check_screen):
    c = check_screen
    await open_draft(c)
    old = deepcopy(c.data[cards.VIEW]["draft"])
    c.check_api.check_draft.side_effect = ConstructionApiClientError(
        status_code=409, code="ROW_VERSION_CONFLICT",
    )
    await click(c, "draftcheck")
    assert c.data[cards.VIEW]["phase"] == "draft_check_failed"
    assert c.data[cards.VIEW]["draft"] == old
    c.current["version_row_version"] = 9
    await click(c, "draftrefresh")
    assert c.data[cards.VIEW]["draft"]["version_row_version"] == 9


@pytest.mark.asyncio
async def test_late_result_cannot_restore_another_organization(check_screen):
    c = check_screen
    await open_draft(c)
    original = c.check_api.check_draft.side_effect

    async def late(**kwargs):
        result = original(**kwargs)
        c.data[cards.CONTEXT]["tenant_id"] = str(uuid4())
        return result

    c.check_api.check_draft.side_effect = late
    c.sent.reset_mock()
    await click(c, "draftcheck")
    c.sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_stale_menu_and_old_nonce_cannot_check(check_screen):
    c = check_screen
    await open_draft(c)
    c.callback.message.chat.type = "group"
    await click(c, "draftcheck")
    c.callback.message.chat.type = "private"
    c.callback.data = cards.cb(c.data[cards.VIEW], "draftcheck")
    c.callback.message.message_id = 999
    await cards.templates_callback(c.callback, c.state)
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = cards.cb({"nonce": "0" * 32}, "draftcheck")
    await cards.templates_callback(c.callback, c.state)
    c.check_api.check_draft.assert_not_awaited()


def test_labels_and_callbacks():
    assert all(len(values) == 7 for values in LABELS.values())
    assert len(cards.cb({"nonce": "a" * 32}, "draftcheck").encode("utf-8")) <= 64
