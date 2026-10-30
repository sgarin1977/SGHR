"""Company editor transport, binding, version and uncertain-write regression."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_company_edit as editor
from handlers.construction import construction_company_profile as card
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.construction_company_profile import LABELS


@pytest.fixture
def profile():
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": str(uuid4()), "tenant_id": str(uuid4()),
        "legal_name": "Private company", "commercial_name": "Trading name",
        "registered_address_raw": None, "operational_address_raw": None,
        "phone": "+12025550123", "email": "company@example.test",
        "website": None, "signatory_name": "Private signatory",
        "signatory_position": None, "logo_file_id": None,
        "created_at": now, "updated_at": now, "row_version": 7,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update"])
async def test_transport_uses_platform_routes_and_sparse_payload(profile, operation):
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(201 if operation == "create" else 200, json={
            "data": profile, "meta": {}, "request_id": "local-company-edit",
        })
    session = PlatformApiClientSession(
        uuid4(), UUID(profile["tenant_id"]), SecretStr("local-test"),
    )
    payload = (
        {"legal_name": "New legal name"} if operation == "create"
        else {"phone": None, "expected_row_version": 7}
    )
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        result = await ConstructionApiClient(http=http).save_company_profile(
            session=session, operation=operation, payload=payload,
            expected_profile_id=UUID(profile["id"]) if operation == "update" else None,
        )
    assert result.data["id"] == profile["id"]
    assert len(calls) == 1
    assert calls[0].method == ("POST" if operation == "create" else "PATCH")
    assert calls[0].url.path == "/api/v1/organization/company-profile"
    assert json.loads(calls[0].content) == payload
    assert calls[0].headers["authorization"] == "Bearer local-test"
    assert "idempotency-key" not in calls[0].headers


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "id", "version"])
async def test_transport_rejects_foreign_or_malformed_write_receipt(profile, failure):
    original = deepcopy(profile)
    if failure == "tenant":
        profile["tenant_id"] = str(uuid4())
    elif failure == "id":
        profile["id"] = str(uuid4())
    else:
        profile["row_version"] = True
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"data": profile, "meta": {}, "request_id": "local"},
        )),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).save_company_profile(
                session=PlatformApiClientSession(
                    uuid4(), UUID(original["tenant_id"]), SecretStr("local-test"),
                ),
                operation="update", payload={"phone": None, "expected_row_version": 7},
                expected_profile_id=UUID(original["id"]),
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"phone": None},
    {"phone": None, "expected_row_version": True},
    {"phone": None, "expected_row_version": 0},
    {"tenant_id": "foreign", "expected_row_version": 7},
    {"logo_file_id": None, "expected_row_version": 7},
    {"legal_name": None, "expected_row_version": 7},
])
async def test_invalid_update_never_reaches_http(profile, payload):
    calls = []
    def transport(request):
        calls.append(request)
        raise AssertionError("Unexpected HTTP request")
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        with pytest.raises(ValueError):
            await ConstructionApiClient(http=http).save_company_profile(
                session=PlatformApiClientSession(
                    uuid4(), UUID(profile["tenant_id"]), SecretStr("local-test"),
                ),
                operation="update", payload=payload,
                expected_profile_id=UUID(profile["id"]),
            )
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["actor", "permission"])
async def test_entry_checks_fresh_subject_and_server_permission(profile, failure):
    user, tenant = uuid4(), UUID(profile["tenant_id"])
    api = NS(
        company_profile_capabilities=AsyncMock(return_value=NS(data={
            "can_manage_profile": failure != "permission",
        })),
        save_company_profile=AsyncMock(),
    )
    entry = ConstructionBotApiEntry(
        api=NS(construction=api), context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local-test")),
        {"name": "Organization", "language": "uk"},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.save_company_profile(
            telegram_user_id=123, tenant_id=tenant,
            expected_user_id=uuid4() if failure == "actor" else user,
            operation="update", payload={"phone": None, "expected_row_version": 7},
            expected_profile_id=UUID(profile["id"]),
        )
    api.save_company_profile.assert_not_awaited()
    if failure == "actor":
        api.company_profile_capabilities.assert_not_awaited()


@pytest.fixture
def screen(profile, monkeypatch):
    c = NS(profile=profile, user=uuid4(), tenant=UUID(profile["tenant_id"]))
    c.data = {
        editor.CONTEXT: {"telegram_user_id": 123, "tenant_id": str(c.tenant)},
        editor.VIEW: {
            "nonce": "card-source", "telegram_user_id": 123,
            "tenant_id": str(c.tenant), "actor_user_id": str(c.user), "menu_id": 1,
        },
        "last_menu_message_id": 1,
        "construction_project_replace_attempt": {"status": "pending"},
    }
    async def update(**values):
        c.data.update(values)
    c.state = NS(
        get_data=AsyncMock(side_effect=lambda: c.data),
        update_data=AsyncMock(side_effect=update),
        get_state=AsyncMock(return_value=None),
    )
    sender = NS(id=123, is_bot=False, language_code="uk")
    c.callback = NS(
        data="CE:open:card-source", from_user=sender, answer=AsyncMock(),
        message=NS(message_id=1, chat=NS(id=123, type="private"), answer=AsyncMock()),
    )
    c.message = NS(
        from_user=sender, chat=NS(id=123, type="private"),
        text="Updated trading name", answer=AsyncMock(return_value=NS(message_id=1)),
    )
    c.result = {
        "item": deepcopy(profile), "actor_user_id": str(c.user),
        "organization": "Organization", "language": "uk", "can_manage_profile": True,
    }
    c.api = NS(
        company_profile=AsyncMock(side_effect=lambda **kwargs: deepcopy(c.result)),
        save_company_profile=AsyncMock(side_effect=lambda **kwargs: deepcopy(c.result)),
    )
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.api
    monkeypatch.setattr(editor, "construction_bot_api_entry", entry)
    c.sent = AsyncMock(return_value=NS(message_id=1))
    monkeypatch.setattr(editor, "edit_or_replace_menu_message", c.sent)
    return c


async def action(c, name, field=None):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = editor.cb(c.data[editor.FORM], name, field)
    await editor.company_edit_callback(c.callback, c.state)


async def open_and_review(c, field="commercial_name"):
    await editor.company_edit_callback(c.callback, c.state)
    await action(c, "field", field)
    await editor.company_edit_message(c.message, c.state)
    assert c.data[editor.FORM]["stage"] == "review"


@pytest.mark.asyncio
async def test_editor_saves_single_field_with_original_version_and_drops_draft(screen):
    c = screen
    await open_and_review(c)
    assert c.profile["legal_name"] not in repr(c.data)
    assert c.profile["phone"] not in repr(c.data)
    old_callback = editor.cb(c.data[editor.FORM], "save")
    await action(c, "save")
    arguments = c.api.save_company_profile.await_args.kwargs
    assert arguments["payload"] == {
        "commercial_name": c.message.text, "expected_row_version": 7,
    }
    assert arguments["expected_user_id"] == c.user
    assert arguments["expected_profile_id"] == UUID(c.profile["id"])
    assert c.data[editor.FORM]["stage"] == "done"
    assert "value" not in c.data[editor.FORM]
    assert c.message.text not in repr(c.data)
    assert c.data["construction_project_replace_attempt"] == {"status": "pending"}
    c.callback.data = old_callback
    await editor.company_edit_callback(c.callback, c.state)
    assert c.api.save_company_profile.await_count == 1


@pytest.mark.asyncio
async def test_clear_is_explicit_null_and_does_not_send_other_fields(screen):
    c = screen
    await editor.company_edit_callback(c.callback, c.state)
    await action(c, "field", "phone")
    await action(c, "clear")
    await action(c, "save")
    assert c.api.save_company_profile.await_args.kwargs["payload"] == {
        "phone": None, "expected_row_version": 7,
    }


@pytest.mark.asyncio
async def test_missing_profile_creates_only_after_explicit_review(screen):
    c = screen
    c.result["item"] = None
    await editor.company_edit_callback(c.callback, c.state)
    assert c.data[editor.FORM]["stage"] == "input"
    c.api.save_company_profile.assert_not_awaited()
    c.message.text = "New company"
    await editor.company_edit_message(c.message, c.state)
    c.result["item"] = deepcopy(c.profile)
    await action(c, "save")
    args = c.api.save_company_profile.await_args.kwargs
    assert args["operation"] == "create"
    assert args["payload"] == {"legal_name": "New company"}
    assert args["expected_profile_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status,stage", [(409, "blocked"), (422, "blocked"), (503, "uncertain")])
async def test_failed_write_cannot_retry_or_rebase(screen, status, stage):
    c = screen
    await open_and_review(c)
    c.api.save_company_profile.side_effect = ConstructionApiClientError(
        status_code=status, code="local_failure",
    )
    await action(c, "save")
    assert c.data[editor.FORM]["stage"] == stage
    assert c.data[editor.FORM]["expected_row_version"] == 7
    assert "value" not in c.data[editor.FORM]
    await action(c, "save")
    assert c.api.save_company_profile.await_count == 1


@pytest.mark.asyncio
async def test_tenant_change_blocks_submit(screen):
    c = screen
    await open_and_review(c)
    c.data[editor.CONTEXT]["tenant_id"] = str(uuid4())
    await action(c, "save")
    c.api.save_company_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_permission_revoked_before_editor_open_reveals_no_profile(screen):
    c = screen
    c.result["can_manage_profile"] = False
    await editor.company_edit_callback(c.callback, c.state)
    assert not c.data.get(editor.FORM)
    assert c.sent.await_count == 0
    c.api.save_company_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_navigation_discards_unsent_draft_but_preserves_other_attempts(screen):
    c = screen
    await open_and_review(c)
    c.callback.data = "CS_HOME"
    await card.company_navigation_middleware(
        AsyncMock(), c.callback, {"state": c.state},
    )
    assert c.data[editor.FORM] == {}
    assert c.data["construction_project_replace_attempt"] == {"status": "pending"}


@pytest.mark.asyncio
async def test_input_filter_rejects_detached_and_foreign_forms(screen):
    c = screen
    await editor.company_edit_callback(c.callback, c.state)
    await action(c, "field", "phone")
    assert await editor.CompanyFieldInput()(c.message, c.state)
    c.data[editor.FORM]["menu_id"] = None
    assert not await editor.CompanyFieldInput()(c.message, c.state)


def test_editor_translations_and_callbacks_are_bounded():
    assert all(len(values) == 7 for values in LABELS.values())
    form = {"nonce": "a" * 32}
    for field in editor.FIELDS:
        assert len(editor.cb(form, "field", field).encode("utf-8")) <= 64


@pytest.mark.asyncio
@pytest.mark.parametrize("field", editor.FIELDS)
async def test_compact_callback_selects_the_correct_company_field(screen, field):
    c = screen
    await editor.company_edit_callback(c.callback, c.state)
    await action(c, "field", field)
    assert c.data[editor.FORM]["stage"] == "input"
    assert c.data[editor.FORM]["field"] == field
    c.api.save_company_profile.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["99", "-1", "01", "operational_address_raw"])
async def test_invalid_compact_field_code_keeps_editor_unchanged(screen, code):
    c = screen
    await editor.company_edit_callback(c.callback, c.state)
    before = deepcopy(c.data[editor.FORM])
    c.callback.data = "CE:field:" + before["nonce"] + ":" + code
    await editor.company_edit_callback(c.callback, c.state)
    assert c.data[editor.FORM] == before
    c.api.save_company_profile.assert_not_awaited()
