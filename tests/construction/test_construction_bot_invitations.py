"""Local invitation transport and sender UI regression."""
from contextlib import asynccontextmanager
from copy import deepcopy
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_invitations as handler
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_invitation_api_client import ConstructionInvitationApiClient
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.construction_invitations import LABELS


@pytest.fixture
def invitation():
    tenant = uuid4()
    return {
        "id": str(uuid4()), "email": "recipient@example.com", "role": "ADMIN",
        "status": "pending", "scopes": [{"scope_type": "tenant", "scope_id": str(tenant)}],
        "expires_at": "2030-01-01T12:00:00Z", "grant_expires_at": None, "row_version": 1,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "resend", "revoke"])
async def test_transport_uses_registered_keyed_operations(invitation, operation):
    calls = []
    receipt = deepcopy(invitation)
    payload = {
        "email": invitation["email"], "role": invitation["role"], "scopes": invitation["scopes"],
    } if operation == "create" else {"expected_row_version": 1}
    if operation != "create":
        receipt["row_version"] = 2
    if operation == "revoke":
        receipt["status"] = "revoked"
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={"data": receipt, "meta": {}, "request_id": "local"})
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        result = await ConstructionInvitationApiClient(ConstructionApiClient(http=http)).write(
            session=PlatformApiClientSession(
                uuid4(), UUID(invitation["scopes"][0]["scope_id"]), SecretStr("local"),
            ),
            operation=operation, payload=payload, idempotency_key="stable-local-key",
            invitation_id=None if operation == "create" else UUID(invitation["id"]),
        )
    suffix = "" if operation == "create" else "/" + invitation["id"] + "/" + operation
    assert calls[0].url.path == "/api/v1/construction/invitations" + suffix
    assert calls[0].headers["idempotency-key"] == "stable-local-key"
    assert json.loads(calls[0].content) == payload
    assert result["status"] == receipt["status"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["token", "tenant", "duplicate"])
async def test_invitation_list_rejects_secrets_foreign_tenant_and_duplicates(invitation, failure):
    original = deepcopy(invitation)
    items = [invitation]
    if failure == "token":
        invitation["token"] = "private-token"
    elif failure == "tenant":
        invitation["scopes"][0]["scope_id"] = str(uuid4())
    else:
        items.append(deepcopy(invitation))
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"data": items, "meta": {}, "request_id": "local"},
        )),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionInvitationApiClient(ConstructionApiClient(http=http)).list(
                session=PlatformApiClientSession(
                    uuid4(), UUID(original["scopes"][0]["scope_id"]), SecretStr("local"),
                ),
            )


@pytest.mark.asyncio
async def test_entry_does_not_lookup_later_invitation_status_before_keyed_replay(invitation):
    user, tenant = uuid4(), UUID(invitation["scopes"][0]["scope_id"])
    receipt = {**invitation, "row_version": 2}
    api = NS(_request=AsyncMock(return_value=NS(data=receipt, meta={}, request_id="local")))
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local")),
        {"can_manage_team": True},
    ))
    await entry.invitation_write(
        telegram_user_id=123, tenant_id=tenant, expected_user_id=user,
        operation="resend", payload={"expected_row_version": 1},
        idempotency_key="same-key", invitation_id=UUID(invitation["id"]),
    )
    assert api._request.await_count == 1
    assert api._request.await_args.args[0] == "POST"


@pytest.mark.asyncio
async def test_entry_denies_changed_subject_before_api(invitation):
    user, tenant = uuid4(), UUID(invitation["scopes"][0]["scope_id"])
    api = NS(_request=AsyncMock())
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local")),
        {"can_manage_team": True},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.invitation_write(
            telegram_user_id=123, tenant_id=tenant, expected_user_id=uuid4(),
            operation="revoke", payload={"expected_row_version": 1},
            idempotency_key="same-key", invitation_id=UUID(invitation["id"]),
        )
    api._request.assert_not_awaited()


@pytest.fixture
def screen(invitation, monkeypatch):
    c = NS(user=uuid4(), tenant=UUID(invitation["scopes"][0]["scope_id"]), invitation=invitation)
    c.data = {
        handler.CONTEXT: {"telegram_user_id": 123, "tenant_id": str(c.tenant)},
        handler.ORG_SOURCE: {
            "nonce": "source", "telegram_user_id": 123, "tenant_id": str(c.tenant),
            "actor_user_id": str(c.user), "menu_id": 1,
        },
        "last_menu_message_id": 1,
        "construction_project_replace_attempt": {"status": "pending"},
    }
    async def update(**values):
        c.data.update(values)
    c.state = NS(
        get_data=AsyncMock(side_effect=lambda: c.data),
        update_data=AsyncMock(side_effect=update), get_state=AsyncMock(return_value=None),
    )
    sender = NS(id=123, is_bot=False, language_code="uk")
    c.callback = NS(
        data="IV:org:source", from_user=sender, answer=AsyncMock(),
        message=NS(message_id=1, chat=NS(id=123, type="private"), answer=AsyncMock()),
    )
    c.message = NS(
        from_user=sender, chat=NS(id=123, type="private"), text="new-recipient@example.com",
        answer=AsyncMock(return_value=NS(message_id=1)),
    )
    c.result = {
        "items": [deepcopy(invitation)], "roles": ["ADMIN"],
        "actor_user_id": str(c.user), "organization": "Organization", "language": "uk",
    }
    c.api = NS(
        invitation_context=AsyncMock(side_effect=lambda **kwargs: deepcopy(c.result)),
        invitation_write=AsyncMock(return_value={
            "item": deepcopy(invitation), "actor_user_id": str(c.user),
        }),
    )
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.api
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    monkeypatch.setattr(handler, "time", lambda: 1000.0)
    c.sent = AsyncMock(return_value=NS(message_id=1))
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", c.sent)
    return c


async def action(c, name, index=None):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = handler.cb(c.data[handler.VIEW], name, index)
    await handler.invitation_callback(c.callback, c.state)


async def create_review(c):
    await handler.invitation_callback(c.callback, c.state)
    await action(c, "new")
    await handler.invitation_email_message(c.message, c.state)
    await action(c, "role", 0)
    assert c.data[handler.VIEW]["phase"] == "review"


@pytest.mark.asyncio
async def test_read_state_does_not_cache_recipient_email(screen):
    c = screen
    await handler.invitation_callback(c.callback, c.state)
    await action(c, "item", 0)
    assert c.invitation["email"] not in repr(c.data)
    assert c.invitation["email"] in c.sent.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_create_requires_review_uses_fixed_scope_and_clears_email_after_success(screen):
    c = screen
    await create_review(c)
    c.api.invitation_write.assert_not_awaited()
    old_callback = handler.cb(c.data[handler.VIEW], "submit")
    await action(c, "submit")
    args = c.api.invitation_write.await_args.kwargs
    assert args["payload"] == {
        "email": c.message.text, "role": "ADMIN",
        "scopes": [{"scope_type": "tenant", "scope_id": str(c.tenant)}],
    }
    assert args["expected_user_id"] == c.user
    assert c.data[handler.ATTEMPT] == {}
    assert c.message.text not in repr(c.data)
    c.callback.data = old_callback
    await handler.invitation_callback(c.callback, c.state)
    assert c.api.invitation_write.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["resend", "revoke"])
async def test_management_requires_confirmation_and_original_version(screen, operation):
    c = screen
    await handler.invitation_callback(c.callback, c.state)
    await action(c, "item", 0)
    await action(c, operation)
    c.api.invitation_write.assert_not_awaited()
    await action(c, "submit")
    args = c.api.invitation_write.await_args.kwargs
    assert args["operation"] == operation
    assert args["invitation_id"] == UUID(c.invitation["id"])
    assert args["payload"] == {"expected_row_version": 1}


@pytest.mark.asyncio
async def test_uncertain_request_reuses_exact_key_and_payload(screen):
    c = screen
    await create_review(c)
    c.api.invitation_write.side_effect = ConstructionApiClientError(status_code=503, code="api_unavailable")
    await action(c, "submit")
    assert c.data[handler.VIEW]["phase"] == "attempt"
    original = deepcopy(c.data[handler.ATTEMPT])
    c.api.invitation_write.side_effect = None
    await action(c, "retry")
    first, second = c.api.invitation_write.await_args_list
    assert first.kwargs["idempotency_key"] == second.kwargs["idempotency_key"]
    assert first.kwargs["payload"] == second.kwargs["payload"] == original["payload"]
    assert c.data[handler.ATTEMPT] == {}


@pytest.mark.asyncio
async def test_retry_window_expiry_does_not_issue_new_request_or_key(screen, monkeypatch):
    c = screen
    await create_review(c)
    c.api.invitation_write.side_effect = ConstructionApiClientError(status_code=503, code="api_unavailable")
    await action(c, "submit")
    original = deepcopy(c.data[handler.ATTEMPT])
    monkeypatch.setattr(handler, "time", lambda: 1000.0 + 86400)
    await action(c, "retry")
    assert c.api.invitation_write.await_count == 1
    assert c.data[handler.ATTEMPT] == original


@pytest.mark.asyncio
async def test_local_finish_requires_confirmation_and_does_not_revoke_server_invitation(screen):
    c = screen
    await create_review(c)
    c.api.invitation_write.side_effect = ConstructionApiClientError(status_code=503, code="api_unavailable")
    await action(c, "submit")
    await action(c, "finish")
    assert c.data[handler.VIEW]["phase"] == "finish"
    assert c.data[handler.ATTEMPT]["status"] == "pending"
    await action(c, "forget")
    assert c.data[handler.ATTEMPT] == {}
    assert c.api.invitation_write.await_count == 1


@pytest.mark.asyncio
async def test_tenant_switch_blocks_submit(screen):
    c = screen
    await create_review(c)
    c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
    await action(c, "submit")
    c.api.invitation_write.assert_not_awaited()


@pytest.mark.asyncio
async def test_role_revocation_before_selection_blocks_create(screen):
    c = screen
    await handler.invitation_callback(c.callback, c.state)
    await action(c, "new")
    await handler.invitation_email_message(c.message, c.state)
    c.result["roles"] = []
    await action(c, "role", 0)
    c.api.invitation_write.assert_not_awaited()
    assert c.data[handler.VIEW]["phase"] == "roles"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["accepted", "declined", "expired", "revoked"])
async def test_terminal_invitation_has_no_resend_or_revoke_actions(screen, status):
    c = screen
    c.result["items"][0]["status"] = status
    await handler.invitation_callback(c.callback, c.state)
    await action(c, "item", 0)
    rows = c.sent.await_args.kwargs["reply_markup"].inline_keyboard
    assert not any(
        ":resend:" in button.callback_data or ":revoke:" in button.callback_data
        for row in rows for button in row
    )
    await action(c, "resend")
    c.api.invitation_write.assert_not_awaited()


@pytest.mark.asyncio
async def test_navigation_preserves_pending_receipt_attempt(screen):
    c = screen
    await create_review(c)
    c.api.invitation_write.side_effect = ConstructionApiClientError(status_code=503, code="api_unavailable")
    await action(c, "submit")
    attempt = deepcopy(c.data[handler.ATTEMPT])
    c.callback.data = "CS_HOME"
    await handler.invitation_navigation_middleware(AsyncMock(), c.callback, {"state": c.state})
    assert c.data[handler.VIEW] == {}
    assert c.data[handler.ATTEMPT] == attempt
    assert c.data["construction_project_replace_attempt"] == {"status": "pending"}


@pytest.mark.asyncio
async def test_project_source_pins_the_actual_project_not_tenant_scope(screen):
    c = screen
    project = uuid4()
    c.data[handler.PROJECT_SOURCE] = {
        "nonce": "project-source", "phase": "list", "telegram_user_id": 123,
        "tenant_id": str(c.tenant), "actor_user_id": str(c.user),
        "project_id": str(project), "menu_id": 1,
    }
    c.callback.data = "IV:project:project-source"
    await handler.invitation_callback(c.callback, c.state)
    assert c.api.invitation_context.await_args.kwargs["project_id"] == project
    await action(c, "new")
    await handler.invitation_email_message(c.message, c.state)
    await action(c, "role", 0)
    await action(c, "submit")
    assert c.api.invitation_write.await_args.kwargs["payload"]["scopes"] == [
        {"scope_type": "project", "scope_id": str(project)},
    ]


def test_invitation_callbacks_and_translations_are_bounded():
    view = {"nonce": "a" * 32}
    for action_name in ("refresh", "previous", "resend", "revoke", "submit", "retry", "forget"):
        assert len(handler.cb(view, action_name, 6).encode()) <= 64
    assert all(len(values) == 7 for values in LABELS.values())

# Invitation grant deadline regression v1.
def future_grant_deadline():
    from datetime import datetime, timedelta, timezone
    return (
        datetime.now(timezone.utc) + timedelta(days=30)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@pytest.mark.parametrize("value", [
    "2060-01-02 14:30+02:00",
    "2060-01-02T12:30:00Z",
    "2060-01-02 07:30-05:00",
])
def test_grant_deadline_normalizes_explicit_offsets(value):
    from datetime import datetime, timezone
    from ui.construction_invitations import parse_grant_expiry
    assert parse_grant_expiry(
        value, now=datetime(2060, 1, 1, tzinfo=timezone.utc),
    ) == "2060-01-02T12:30:00Z"


@pytest.mark.parametrize("value", [
    None, True, 123, "", "tomorrow",
    "2060-01-02 12:30", "2060-02-30 12:30Z",
    "2060-01-01T00:00:00Z", "2059-12-31T23:59:59Z",
])
def test_grant_deadline_rejects_ambiguous_invalid_and_elapsed_values(value):
    from datetime import datetime, timezone
    from ui.construction_invitations import parse_grant_expiry
    with pytest.raises(ValueError):
        parse_grant_expiry(
            value, now=datetime(2060, 1, 1, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_grant_deadline_requires_review_and_is_separate_from_invitation_ttl(screen):
    c = screen
    await create_review(c)
    await action(c, "grant")
    assert c.data[handler.VIEW]["phase"] == "grant_expiry"
    c.message.text = future_grant_deadline()
    await handler.invitation_email_message(c.message, c.state)
    assert c.data[handler.VIEW]["phase"] == "review"
    assert c.message.text in c.data[handler.VIEW]["grant_expires_at"]
    c.api.invitation_write.assert_not_awaited()
    await action(c, "submit")
    payload = c.api.invitation_write.await_args.kwargs["payload"]
    assert payload["grant_expires_at"] == c.message.text
    assert "expires_at" not in payload
    assert "grant_expires_at" not in c.data[handler.VIEW]
    assert c.data[handler.ATTEMPT] == {}


@pytest.mark.asyncio
async def test_grant_deadline_can_be_removed_explicitly_before_submit(screen):
    c = screen
    await create_review(c)
    await action(c, "grant")
    c.message.text = future_grant_deadline()
    await handler.invitation_email_message(c.message, c.state)
    await action(c, "grant")
    await action(c, "grant_none")
    assert c.data[handler.VIEW]["grant_expires_at"] is None
    await action(c, "submit")
    assert "grant_expires_at" not in c.api.invitation_write.await_args.kwargs["payload"]


@pytest.mark.asyncio
async def test_invalid_grant_date_does_not_change_draft_or_call_mutation(screen):
    c = screen
    await create_review(c)
    await action(c, "grant")
    before = deepcopy(c.data[handler.VIEW])
    c.message.text = "2030-01-01 18:00"
    await handler.invitation_email_message(c.message, c.state)
    assert c.data[handler.VIEW] == before
    c.api.invitation_write.assert_not_awaited()
    await action(c, "grant_back")
    assert c.data[handler.VIEW]["phase"] == "review"
    assert c.data[handler.VIEW]["grant_expires_at"] is None


@pytest.mark.asyncio
async def test_date_elapsed_during_review_blocks_first_submission(screen, monkeypatch):
    c = screen
    await create_review(c)
    await action(c, "grant")
    c.message.text = future_grant_deadline()
    await handler.invitation_email_message(c.message, c.state)
    def elapsed(value):
        raise ValueError("Elapsed during review.")
    monkeypatch.setattr(handler, "parse_grant_expiry", elapsed)
    await action(c, "submit")
    assert c.data[handler.VIEW]["phase"] == "review"
    assert not c.data.get(handler.ATTEMPT)
    c.api.invitation_write.assert_not_awaited()


@pytest.mark.asyncio
async def test_grant_deadline_replay_keeps_original_date_and_key(screen, monkeypatch):
    c = screen
    await create_review(c)
    await action(c, "grant")
    c.message.text = future_grant_deadline()
    await handler.invitation_email_message(c.message, c.state)
    c.api.invitation_write.side_effect = ConstructionApiClientError(
        status_code=503, code="api_unavailable",
    )
    await action(c, "submit")
    frozen = deepcopy(c.data[handler.ATTEMPT])
    assert frozen["payload"]["grant_expires_at"] == c.message.text
    def must_not_parse_again(value):
        raise AssertionError("Replay must not reinterpret or renew the deadline.")
    monkeypatch.setattr(handler, "parse_grant_expiry", must_not_parse_again)
    c.api.invitation_write.side_effect = None
    await action(c, "retry")
    assert c.data[handler.VIEW]["phase"] == "done"
    first, second = c.api.invitation_write.await_args_list
    assert first.kwargs["payload"] == second.kwargs["payload"] == frozen["payload"]
    assert first.kwargs["idempotency_key"] == second.kwargs["idempotency_key"]


@pytest.mark.asyncio
async def test_grant_date_action_cannot_edit_existing_invitation(screen):
    c = screen
    await handler.invitation_callback(c.callback, c.state)
    await action(c, "item", 0)
    before = deepcopy(c.data[handler.VIEW])
    await action(c, "grant")
    assert c.data[handler.VIEW] == before
    c.api.invitation_write.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_receipt", [False, True])
async def test_entry_transmits_deadline_and_rejects_changed_receipt(invitation, wrong_receipt):
    deadline = future_grant_deadline()
    user, tenant = uuid4(), UUID(invitation["scopes"][0]["scope_id"])
    receipt = {**invitation, "grant_expires_at": None if wrong_receipt else deadline}
    api = NS(_request=AsyncMock(return_value=NS(
        data=receipt, meta={}, request_id="local",
    )))
    entry = ConstructionBotApiEntry(
        api=NS(construction=api), context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local")),
        {"can_manage_team": True},
    ))
    async def call():
        return await entry.invitation_write(
            telegram_user_id=123, tenant_id=tenant, expected_user_id=user,
            operation="create", idempotency_key="deadline-local-key",
            payload={
                "email": invitation["email"], "role": invitation["role"],
                "scopes": deepcopy(invitation["scopes"]),
                "grant_expires_at": deadline,
            },
        )
    if wrong_receipt:
        with pytest.raises(ConstructionApiClientError):
            await call()
    else:
        result = await call()
        assert result["item"]["grant_expires_at"] == deadline
    sent = api._request.await_args.kwargs["json_body"]
    assert sent["grant_expires_at"] == deadline
    assert "expires_at" not in sent


def test_grant_deadline_actions_fit_telegram_callback_limit():
    view = {"nonce": "a" * 32}
    for name in ("grant", "grant_none", "grant_back"):
        assert len(handler.cb(view, name).encode("utf-8")) <= 64
    assert all(len(values) == 7 for values in LABELS.values())
