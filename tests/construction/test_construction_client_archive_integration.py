"""Local client archive HTTP and Telegram contracts; no native acceptance."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import update

from database.models import ConstructionClient
from handlers.construction import construction_client_edit as editor
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_1a_client_link_model import client_store
from test_construction_client_version_noop import version_case, snapshot
from test_construction_client_patch_http import patch_http
from test_construction_bot_client_edit import client_item, screen


async def archive_http(c, *, expected=1, body=None, identifier=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        return await client.post(
            "/api/v1/construction/clients/" + str(identifier or c.client_id) + "/archive",
            json=body if body is not None else {"expected_row_version": expected},
            headers={"X-Request-ID": "client-archive-http"},
        )


@pytest.mark.asyncio
async def test_http_archive_preserves_project_and_identity_and_commits_one_audit(patch_http):
    c = patch_http
    c.sync.execute(
        update(ConstructionClient.__table__)
        .where(ConstructionClient.id == c.client_id)
        .values(linked_user_id=c.target)
    )
    c.sync.commit()
    before = snapshot(c)
    response = await archive_http(c)
    assert response.status_code == 200, response.text
    body = response.json()
    data = body["data"]
    assert set(data) == {"id", "tenant_id", "row_version", "deleted_at"}
    assert data["id"] == str(c.client_id) and data["tenant_id"] == str(c.tenant)
    assert data["row_version"] == 2
    assert datetime.fromisoformat(data["deleted_at"].replace("Z", "+00:00")) == c.now
    assert body["meta"] == {} and body["request_id"] == "client-archive-http"
    after = snapshot(c)
    assert after["client"].deleted_at is not None
    assert after["client"].linked_user_id == c.target
    assert after["project"] == before["project"]
    assert len(after["audit"]) == 1
    payload = json.loads(after["audit"][0].payload)
    assert payload["related_client_id"] == str(c.client_id)
    assert payload["operation"] == "archive"
    c.session.commit.assert_awaited_once()
    assert not any(sql.lstrip().upper().startswith("DELETE") for sql in c.statements)


@pytest.mark.asyncio
async def test_second_archive_does_not_change_version_or_add_audit(patch_http):
    c = patch_http
    first = await archive_http(c)
    assert first.status_code == 200, first.text
    before = snapshot(c)
    second = await archive_http(c, expected=2)
    assert second.status_code == 404, second.text
    assert snapshot(c) == before
    assert len(before["audit"]) == 1


@pytest.mark.asyncio
async def test_stale_archive_is_conflict_without_mutation(patch_http):
    c = patch_http
    before = snapshot(c)
    response = await archive_http(c, expected=2)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "ROW_VERSION_CONFLICT"
    assert snapshot(c) == before
    c.repo.soft_delete_client.assert_not_called()
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {}, {"expected_row_version": True}, {"expected_row_version": "1"},
    {"expected_row_version": 0}, {"expected_row_version": None},
    {"expected_row_version": 2**63},
    {"expected_row_version": 1, "tenant_id": str(uuid4())},
    {"expected_row_version": 1, "linked_user_id": None},
])
async def test_archive_body_is_version_only_and_strict(patch_http, body):
    c = patch_http
    response = await archive_http(c, body=body)
    assert response.status_code == 422, response.text
    c.repo.soft_delete_client.assert_not_called()
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["permission", "project_scope", "suite", "module"])
async def test_archive_security_gates_prevent_mutation(patch_http, failure):
    c = patch_http
    if failure == "suite":
        c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif failure == "module":
        c.module.require_enabled.side_effect = ModuleDisabledError()
    elif failure == "permission":
        c.context = ConstructionAccessContext(
            c.actor.user_id, c.actor.tenant_id, ("OWNER",), (), c.context.grants,
        )
    else:
        c.context = ConstructionAccessContext(
            c.actor.user_id, c.actor.tenant_id, ("PROJECT_MANAGER",),
            ("construction.projects.edit",),
            (ConstructionGrantContext("PROJECT_MANAGER", "project", c.project_id),),
        )
    before = snapshot(c)
    response = await archive_http(c)
    assert response.status_code == 403, response.text
    assert snapshot(c) == before
    c.repo.soft_delete_client.assert_not_called()
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
async def test_missing_client_is_safe_not_found(patch_http):
    c = patch_http
    before = snapshot(c)
    response = await archive_http(c, identifier=uuid4())
    assert response.status_code == 404, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit"])
async def test_archive_and_real_audit_insert_roll_back_together(patch_http, failure):
    c = patch_http
    if failure == "audit":
        original_audit = c.events.create_event.side_effect
        async def fail_after_insert(**values):
            await original_audit(**values)
            raise RuntimeError("private archive audit failure")
        c.events.create_event.side_effect = fail_after_insert
    else:
        c.session.commit.side_effect = RuntimeError("private archive commit failure")
    before = snapshot(c)
    response = await archive_http(c)
    assert response.status_code == 500, response.text
    assert "private" not in response.text
    assert snapshot(c) == before
    c.session.rollback.assert_awaited_once()


def receipt(item):
    return {
        "id": item["id"], "tenant_id": item["tenant_id"],
        "row_version": 8, "deleted_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.mark.asyncio
async def test_transport_sends_only_original_version_without_replay_key(client_item):
    item = receipt(client_item)
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"data": item, "meta": {}, "request_id": "archive"})
    session = PlatformApiClientSession(
        uuid4(), UUID(item["tenant_id"]), SecretStr("local-archive-test"),
    )
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(handle),
    ) as http:
        result = await ConstructionApiClient(http=http).archive_client(
            session=session, client_id=UUID(item["id"]), expected_row_version=7,
        )
    assert len(calls) == 1
    assert calls[0].method == "POST"
    assert calls[0].url.path == "/api/v1/construction/clients/" + item["id"] + "/archive"
    assert json.loads(calls[0].content) == {"expected_row_version": 7}
    assert "idempotency-key" not in calls[0].headers
    assert {key: value for key, value in result.data.items() if key != "deleted_at"} == {
        key: value for key, value in item.items() if key != "deleted_at"
    }
    assert datetime.fromisoformat(result.data["deleted_at"].replace("Z", "+00:00")) == (
        datetime.fromisoformat(item["deleted_at"])
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "id", "version", "naive", "missing_date"])
async def test_transport_rejects_invalid_archive_receipt(client_item, failure):
    item = receipt(client_item)
    if failure in ("tenant", "id"):
        item["tenant_id" if failure == "tenant" else "id"] = str(uuid4())
    elif failure == "version":
        item["row_version"] = 7
    elif failure == "naive":
        item["deleted_at"] = datetime.now().isoformat()
    else:
        item["deleted_at"] = None
    session = PlatformApiClientSession(
        uuid4(), UUID(client_item["tenant_id"]), SecretStr("local-archive-test"),
    )
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"data": item, "meta": {}, "request_id": "archive"},
        )),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).archive_client(
                session=session, client_id=UUID(client_item["id"]), expected_row_version=7,
            )


@pytest.mark.asyncio
async def test_entry_denies_changed_actor_before_archive(client_item):
    session = PlatformApiClientSession(
        uuid4(), UUID(client_item["tenant_id"]), SecretStr("local-archive-test"),
    )
    transport = NS(archive_client=AsyncMock())
    entry = ConstructionBotApiEntry(api=NS(construction=transport), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(session, {"language": "uk"}))
    with pytest.raises(ConstructionApiClientError):
        await entry.archive_client(
            telegram_user_id=123, tenant_id=session.tenant_id,
            client_id=UUID(client_item["id"]),
            expected_user_id=uuid4(), expected_row_version=7,
        )
    transport.archive_client.assert_not_awaited()


@pytest.fixture
def archive_screen(screen):
    c = screen
    c.service.archive_client = AsyncMock(return_value={
        "item": receipt(c.item), "actor_user_id": str(c.actor),
    })
    return c


async def open_archive(c):
    c.callback.data = "CDE:archive:" + c.data[editor.CARD]["nonce"]
    c.callback.message.message_id = c.data["last_menu_message_id"]
    await editor.client_edit_callback(c.callback, c.state)
    assert c.data[editor.FORM]["stage"] == "archive_review"


async def click(c, action):
    c.callback.data = editor.cb(c.data[editor.FORM], action)
    c.callback.message.message_id = c.data["last_menu_message_id"]
    await editor.client_edit_callback(c.callback, c.state)


@pytest.mark.asyncio
async def test_bot_requires_confirmation_and_preserves_reviewed_version(archive_screen):
    c = archive_screen
    await open_archive(c)
    c.service.archive_client.assert_not_awaited()
    c.service.client_card.return_value["item"]["row_version"] = 99
    await click(c, "archive_confirm")
    args = c.service.archive_client.await_args.kwargs
    assert args["expected_row_version"] == 7
    assert args["client_id"] == UUID(c.item["id"])
    assert args["expected_user_id"] == c.actor
    assert c.data[editor.FORM]["stage"] == "archived"
    c.service.client_card.assert_awaited_once()
    markup = c.message.answer.await_args.kwargs["reply_markup"]
    commands = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "CL:open" in commands
    assert not any(command.startswith("CC:") for command in commands)
    await click(c, "archive_confirm")
    c.service.archive_client.assert_awaited_once()


@pytest.mark.asyncio
async def test_bot_cancel_does_not_archive_or_edit(archive_screen):
    c = archive_screen
    await open_archive(c)
    await click(c, "cancel")
    assert c.data[editor.FORM]["stage"] == "cancelled"
    c.service.archive_client.assert_not_awaited()
    c.service.patch_client.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,stage", [(409, "conflict"), (403, "blocked"), (504, "uncertain")])
async def test_bot_archive_failure_has_no_rebase_or_automatic_retry(archive_screen, status, stage):
    c = archive_screen
    await open_archive(c)
    c.service.archive_client.side_effect = ConstructionApiClientError(
        status_code=status, code="archive_test_failure",
    )
    await click(c, "archive_confirm")
    assert c.data[editor.FORM]["stage"] == stage
    await click(c, "archive_confirm")
    c.service.archive_client.assert_awaited_once()
    c.service.client_card.assert_awaited_once()
