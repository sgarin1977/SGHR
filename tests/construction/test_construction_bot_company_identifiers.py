"""Local identifier transport and bot mutation-boundary tests."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_company_identifiers as handler
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.construction_company_identifiers import LABELS


@pytest.fixture
def identifier():
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": str(uuid4()), "country_code": "UA",
        "identifier_type": "custom_registration_code",
        "identifier_value": "PRIVATE-IDENTIFIER-123",
        "is_primary": False, "created_at": now, "updated_at": now,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "replace", "delete"])
async def test_existing_platform_mutation_routes_and_bodies(identifier, operation):
    target, calls = uuid4(), []
    payload = {
        "country_code": "UA", "identifier_type": "custom_registration_code",
        "identifier_value": "  ab-12 / cd  ", "is_primary": False,
    }
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={
            "data": {"id": str(target), "deleted": True} if operation == "delete" else identifier,
            "meta": {}, "request_id": "local",
        })
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        result = await ConstructionApiClient(http=http).mutate_company_identifier(
            session=PlatformApiClientSession(uuid4(), uuid4(), SecretStr("local-test")),
            operation=operation, identifier_id=None if operation == "create" else target,
            payload=None if operation == "delete" else payload,
        )
    suffix = "" if operation == "create" else "/" + str(target)
    if operation == "replace":
        suffix += "/replace"
    assert len(calls) == 1
    assert calls[0].url.path == "/api/v1/organization/company-profile/identifiers" + suffix
    assert calls[0].method == ("DELETE" if operation == "delete" else "POST")
    assert "idempotency-key" not in calls[0].headers
    if operation != "delete":
        assert json.loads(calls[0].content) == payload
    else:
        assert result.data == {"id": str(target), "deleted": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["duplicate", "boolean", "timestamp"])
async def test_identifier_list_rejects_malformed_response(identifier, failure):
    items = [deepcopy(identifier)]
    if failure == "duplicate":
        items.append(deepcopy(identifier))
    elif failure == "boolean":
        items[0]["is_primary"] = "false"
    else:
        items[0]["updated_at"] = "2026-10-26T12:00:00"
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"data": items, "meta": {}, "request_id": "local"},
        )),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).company_identifiers(
                session=PlatformApiClientSession(uuid4(), uuid4(), SecretStr("local-test")),
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["actor", "permission", "target"])
async def test_entry_denies_before_mutation(identifier, failure):
    user, tenant = uuid4(), uuid4()
    api = NS(
        company_profile_capabilities=AsyncMock(return_value=NS(data={
            "can_manage_profile": failure != "permission",
        })),
        company_identifiers=AsyncMock(return_value=NS(data=[])),
        mutate_company_identifier=AsyncMock(),
    )
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local-test")),
        {"name": "Organization", "language": "uk"},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.mutate_company_identifier(
            telegram_user_id=123, tenant_id=tenant,
            expected_user_id=uuid4() if failure == "actor" else user,
            operation="delete", identifier_id=uuid4(),
        )
    api.mutate_company_identifier.assert_not_awaited()
    if failure == "actor":
        api.company_profile_capabilities.assert_not_awaited()


@pytest.fixture
def screen(identifier, monkeypatch):
    c = NS(user=uuid4(), tenant=uuid4(), identifier=identifier)
    c.data = {
        handler.CONTEXT: {"telegram_user_id": 123, "tenant_id": str(c.tenant)},
        handler.PARENT: {
            "nonce": "source", "telegram_user_id": 123,
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
        data="CI:open:source", from_user=sender, answer=AsyncMock(),
        message=NS(message_id=1, chat=NS(id=123, type="private"), answer=AsyncMock()),
    )
    c.message = NS(
        from_user=sender, chat=NS(id=123, type="private"), text="UA",
        answer=AsyncMock(return_value=NS(message_id=1)),
    )
    c.result = {
        "items": [deepcopy(identifier)], "profile_id": str(uuid4()),
        "can_manage": True, "actor_user_id": str(c.user),
        "organization": "Organization", "language": "uk",
    }
    c.api = NS(
        company_identifiers=AsyncMock(side_effect=lambda **kwargs: deepcopy(c.result)),
        mutate_company_identifier=AsyncMock(return_value={
            "item": deepcopy(identifier), "actor_user_id": str(c.user),
        }),
    )
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.api
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    c.sent = AsyncMock(return_value=NS(message_id=1))
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", c.sent)
    return c


async def action(c, name, value=None):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = handler.cb(c.data[handler.VIEW], name, value)
    await handler.identifier_callback(c.callback, c.state)


async def fill(c):
    for value in ("UA", "custom_registration_code", "  private-value / 123  "):
        c.message.text = value
        await handler.identifier_message(c.message, c.state)
    assert c.data[handler.VIEW]["phase"] == "primary"
    await action(c, "primary", 0)
    assert c.data[handler.VIEW]["phase"] == "review"


@pytest.mark.asyncio
async def test_list_keeps_only_selectors_and_refetches_selected_item(screen):
    c = screen
    await handler.identifier_callback(c.callback, c.state)
    assert c.identifier["identifier_value"] in c.sent.await_args.kwargs["text"]
    assert c.identifier["identifier_value"] not in repr(c.data)
    await action(c, "item", 0)
    assert c.api.company_identifiers.await_count == 2
    assert c.data[handler.VIEW]["target_id"] == c.identifier["id"]
    assert c.identifier["identifier_value"] not in repr(c.data)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "replace", "delete"])
async def test_explicit_mutations_use_pinned_actor_and_single_commit_attempt(screen, operation):
    c = screen
    await handler.identifier_callback(c.callback, c.state)
    if operation == "create":
        await action(c, "add")
    else:
        await action(c, "item", 0)
        await action(c, operation)
    if operation != "delete":
        await fill(c)
    c.api.mutate_company_identifier.assert_not_awaited()
    old_callback = handler.cb(c.data[handler.VIEW], "commit")
    await action(c, "commit")
    arguments = c.api.mutate_company_identifier.await_args.kwargs
    assert arguments["operation"] == operation
    assert arguments["expected_user_id"] == c.user
    assert str(arguments["identifier_id"]) == (
        c.identifier["id"] if operation != "create" else "None"
    )
    if operation != "delete":
        assert arguments["payload"]["identifier_value"] == "  private-value / 123  "
        assert arguments["payload"]["is_primary"] is False
    else:
        assert arguments["payload"] is None
    assert c.data[handler.VIEW]["phase"] == "done"
    assert "payload" not in c.data[handler.VIEW]
    assert "private-value" not in repr(c.data)
    c.callback.data = old_callback
    await handler.identifier_callback(c.callback, c.state)
    assert c.api.mutate_company_identifier.await_count == 1


@pytest.mark.asyncio
async def test_unknown_write_result_has_no_retry(screen):
    c = screen
    await handler.identifier_callback(c.callback, c.state)
    await action(c, "item", 0)
    await action(c, "delete")
    c.api.mutate_company_identifier.side_effect = ConstructionApiClientError(
        status_code=503, code="api_unavailable",
    )
    await action(c, "commit")
    assert c.data[handler.VIEW]["phase"] == "uncertain"
    await action(c, "commit")
    assert c.api.mutate_company_identifier.await_count == 1


@pytest.mark.asyncio
async def test_changed_tenant_blocks_mutation(screen):
    c = screen
    await handler.identifier_callback(c.callback, c.state)
    await action(c, "item", 0)
    await action(c, "delete")
    c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
    await action(c, "commit")
    c.api.mutate_company_identifier.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_only_member_cannot_open_identifier_write_form(screen):
    c = screen
    c.result["can_manage"] = False
    await handler.identifier_callback(c.callback, c.state)
    await action(c, "add")
    assert c.data[handler.VIEW]["phase"] == "list"
    c.api.mutate_company_identifier.assert_not_awaited()


@pytest.mark.asyncio
async def test_absent_profile_lists_empty_without_creating_anything(screen):
    c = screen
    c.result.update(items=[], profile_id=None)
    await handler.identifier_callback(c.callback, c.state)
    await action(c, "add")
    assert c.data[handler.VIEW]["phase"] == "list"
    c.api.mutate_company_identifier.assert_not_awaited()


@pytest.mark.asyncio
async def test_leaving_identifier_form_discards_draft_and_preserves_other_attempt(screen):
    c = screen
    await handler.identifier_callback(c.callback, c.state)
    await action(c, "add")
    c.message.text = "UA"
    await handler.identifier_message(c.message, c.state)
    c.callback.data = "CP:open"
    await handler.identifier_navigation_middleware(
        AsyncMock(), c.callback, {"state": c.state},
    )
    assert c.data[handler.VIEW] == {}
    assert c.data["construction_project_replace_attempt"] == {"status": "pending"}


def test_identifier_callbacks_fit_telegram_and_labels_cover_seven_languages():
    view = {"nonce": "a" * 32}
    for action_name in ("refresh", "previous", "replace", "delete", "commit", "primary"):
        assert len(handler.cb(view, action_name, 0).encode()) <= 64
    assert all(len(values) == 7 for values in LABELS.values())
