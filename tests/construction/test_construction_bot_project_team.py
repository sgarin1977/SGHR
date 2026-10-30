"""Local transport, authorization binding and project participant UI tests."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_project_team as handler
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError,
    ConstructionApiResult, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.construction_project_team import project_team_text, LABELS
from test_construction_bot_project_areas import screen
from test_construction_1a_access_mutation_workflow import pipeline, snapshot
from test_construction_1a_access_mutation_http import http_case
from test_construction_1a_access_issuance_workflow import issuance_pipeline
from test_construction_1a_access_issuance_http import issuance_http


@pytest.fixture
def team_screen(screen, monkeypatch):
    c = screen
    c.target = uuid4()
    candidate = {
        "user_id": str(c.target), "display_name": "Private candidate", "email": None,
        "roles": ["OBSERVER"], "grants": [{
            "id": str(uuid4()), "role": "OBSERVER", "scope_type": "project",
            "scope_id": str(uuid4()), "expires_at": None,
        }], "scoped_permissions": [],
    }
    owner = {
        "user_id": str(c.user), "display_name": "Tenant owner", "email": None,
        "roles": ["OWNER"], "grants": [{
            "id": str(uuid4()), "role": "OWNER", "scope_type": "tenant",
            "scope_id": str(c.tenant), "expires_at": None,
        }], "scoped_permissions": [],
    }
    c.result = {
        "items": [owner], "candidates": [candidate], "can_add": True,
        "roles": [{"role": "SURVEYOR", "permissions": ["construction.projects.read"]}],
        "actor_user_id": str(c.user), "project_id": str(c.project),
        "project_name": "Private project", "organization": "Organization", "language": "uk",
    }
    c.receipt = {
        "actor_user_id": str(c.user), "project_id": str(c.project), "language": "uk",
        "item": {
            "id": str(uuid4()), "user_id": str(c.target), "role": "SURVEYOR",
            "scope_type": "project", "scope_id": str(c.project),
            "status": "active", "expires_at": None,
            "granted_at": "2026-10-23T12:00:00Z",
        },
    }
    c.service = NS(
        project_team=AsyncMock(side_effect=lambda **kwargs: deepcopy(c.result)),
        create_project_participant=AsyncMock(return_value=c.receipt),
    )
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.service
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", c.sent)
    c.callback.data = "PT:open:" + c.token
    return c


async def click(c, action, value=None):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = f'PT:{action}:{c.data[handler.VIEW]["nonce"]}'
    if value is not None:
        c.callback.data += ":" + str(value)
    await handler.project_team_callback(c.callback, c.state)


async def review(c):
    await handler.project_team_callback(c.callback, c.state)
    await click(c, "add")
    await click(c, "user", 0)
    await click(c, "role", 0)
    assert c.data[handler.VIEW]["phase"] == "expiry"
    await click(c, "noexpiry")
    assert c.data[handler.VIEW]["phase"] == "review"


@pytest.mark.asyncio
async def test_explicit_review_then_one_project_command(team_screen):
    c = team_screen
    await review(c)
    c.service.create_project_participant.assert_not_awaited()
    assert "Private candidate" not in repr(c.data)
    await click(c, "submit")
    args = c.service.create_project_participant.await_args.kwargs
    assert args["tenant_id"] == c.tenant and args["project_id"] == c.project
    assert args["expected_user_id"] == c.user and args["target_user_id"] == c.target
    assert args["role"] == "SURVEYOR" and "permissions" not in args
    assert c.data[handler.ATTEMPT]["status"] == "done"
    assert c.data[c.source_key]["menu_id"] == c.data["last_menu_message_id"]
    c.service.create_project_participant.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_outcome_freezes_command_and_retries_same_key(team_screen):
    c = team_screen
    await review(c)
    c.service.create_project_participant.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_unavailable"),
        c.receipt,
    ]
    await click(c, "submit")
    first = deepcopy(c.service.create_project_participant.await_args.kwargs)
    assert c.data[handler.ATTEMPT]["status"] == "pending"
    assert c.data[handler.VIEW]["phase"] == "pending"
    await click(c, "submit")
    assert c.service.create_project_participant.await_args.kwargs == first
    assert c.data[handler.ATTEMPT]["status"] == "done"


@pytest.mark.asyncio
async def test_expired_attempt_has_no_new_post_or_new_key(team_screen, monkeypatch):
    c = team_screen
    await review(c)
    c.service.create_project_participant.side_effect = RuntimeError("private")
    await click(c, "submit")
    before = deepcopy(c.data[handler.ATTEMPT])
    monkeypatch.setattr(handler, "time", lambda: before["started_at"] + 86400)
    await click(c, "submit")
    c.service.create_project_participant.assert_awaited_once()
    assert c.data[handler.ATTEMPT] == before
    buttons = [
        item.callback_data for row in c.sent.await_args.kwargs["reply_markup"].inline_keyboard
        for item in row
    ]
    assert not any(value.startswith("PT:submit:") for value in buttons)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "source", "sender", "menu"])
async def test_foreign_or_stale_project_rejected_before_api(team_screen, failure):
    c = team_screen
    if failure == "tenant":
        c.data["construction_suite_context"]["tenant_id"] = str(uuid4())
    elif failure == "source":
        c.data[c.source_key] = {}
    elif failure == "sender":
        c.callback.from_user.id = c.callback.message.chat.id = 999
    else:
        c.data["last_menu_message_id"] = 99
    await handler.project_team_callback(c.callback, c.state)
    c.service.project_team.assert_not_awaited()
    c.service.create_project_participant.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_replacement_preserves_parent_and_old_buttons_expire(team_screen):
    c = team_screen
    c.sent.return_value = NS(message_id=2)
    await handler.project_team_callback(c.callback, c.state)
    old_nonce = c.data[handler.VIEW]["nonce"]
    assert c.data[c.source_key]["menu_id"] == 2
    if c.source_key == handler.PROJECT:
        assert c.data[handler.CARD]["menu_id"] == 2
        assert c.data[handler.CARD]["positions"]["projects"] == "preserved-client-position"
    await click(c, "page", 0)
    count = c.service.project_team.await_count
    c.callback.data = "PT:add:" + old_nonce
    await handler.project_team_callback(c.callback, c.state)
    assert c.service.project_team.await_count == count


@pytest.mark.asyncio
async def test_changed_role_options_prevent_selection(team_screen):
    c = team_screen
    await handler.project_team_callback(c.callback, c.state)
    await click(c, "add")
    await click(c, "user", 0)
    c.result["roles"] = []
    await click(c, "role", 0)
    assert c.data[handler.VIEW]["phase"] == "error"
    c.service.create_project_participant.assert_not_awaited()


@pytest.mark.asyncio
async def test_navigation_invalidates_selector_but_retains_pending_attempt(team_screen):
    c = team_screen
    await review(c)
    c.service.create_project_participant.side_effect = RuntimeError("private")
    await click(c, "submit")
    attempt = deepcopy(c.data[handler.ATTEMPT])
    c.callback.data = "CS_HOME"
    downstream = AsyncMock()
    await handler.project_team_navigation_middleware(downstream, c.callback, {"state": c.state})
    assert c.data[handler.VIEW] == {} and c.data[handler.ATTEMPT] == attempt
    await handler.retain_project_team_attempt(c.state, {handler.ATTEMPT: attempt}, 123)
    assert c.data[handler.ATTEMPT] == attempt
    downstream.assert_awaited_once()


def test_organization_access_is_not_presented_as_project_participation(team_screen):
    text = project_team_text(team_screen.result, offset=0, language="uk")
    assert "Учасників об’єкта поки немає." in text
    assert "Доступ організації" in text and "Участь в об’єкті:" not in text
    assert str(team_screen.user) not in text
    assert all(len(value) == 7 for value in LABELS.values())


@pytest.mark.asyncio
async def test_changed_canonical_actor_stops_before_project_or_team_reads():
    user, tenant = uuid4(), uuid4()
    api = NS(
        project_card=AsyncMock(), project_details=AsyncMock(),
        team_members=AsyncMock(), create_project_participant=AsyncMock(),
    )
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local-test")),
        {"name": "Organization", "language": "uk", "can_manage_team": True},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.project_team(
            telegram_user_id=123, tenant_id=tenant, project_id=uuid4(),
            expected_user_id=uuid4(), selection=True,
        )
    api.project_details.assert_not_awaited()
    api.team_members.assert_not_awaited()


@pytest.mark.asyncio
async def test_project_only_authority_does_not_discover_tenant_team():
    user, tenant, project = uuid4(), uuid4(), uuid4()
    api = NS(
        project_details=AsyncMock(return_value=ConstructionApiResult({"name": "Project"}, {}, "local")),
        team_members=AsyncMock(return_value=ConstructionApiResult([], {}, "local")),
        project_role_options=AsyncMock(),
    )
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local-test")),
        {"name": "Organization", "language": "uk", "can_manage_team": False},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.project_team(
            telegram_user_id=123, tenant_id=tenant, project_id=project,
            expected_user_id=user, selection=True,
        )
    api.team_members.assert_awaited_once_with(
        session=entry._presentation.return_value[0], project_id=project,
    )
    api.project_role_options.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["scope", "duplicate", "private"])
async def test_role_options_response_binding(failure):
    user, tenant, project = uuid4(), uuid4(), uuid4()
    body = {
        "data": {
            "scope_type": "project", "scope_id": str(project),
            "roles": [{"role": "SURVEYOR", "permissions": ["construction.projects.read"]}],
            "permissions_read_only": True,
        }, "meta": {}, "request_id": "role-options",
    }
    if failure == "scope":
        body["data"]["scope_id"] = str(uuid4())
    elif failure == "duplicate":
        body["data"]["roles"] *= 2
    else:
        body["data"]["private"] = "hidden"
    def transport(request):
        assert request.url.path == "/api/v1/construction/team/role-options"
        assert dict(request.url.params) == {"scope_type": "project", "scope_id": str(project)}
        assert request.headers["Authorization"] == "Bearer local-test"
        return httpx.Response(200, json=body)
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).project_role_options(
                session=PlatformApiClientSession(user, tenant, SecretStr("local-test")),
                project_id=project,
            )


@pytest.mark.asyncio
async def test_client_calls_real_registered_issuance_workflow_and_replays_once(issuance_http):
    c = issuance_http
    session = PlatformApiClientSession(
        c.actor.user_id, c.actor.tenant_id, SecretStr("local-test"),
    )
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
    ) as http:
        client = ConstructionApiClient(http=http)
        if not c.db.in_transaction:
            c.db.execute("BEGIN")
        first = await client.create_project_participant(
            session=session, project_id=c.project_id, target_user_id=c.original.user_id,
            role="SURVEYOR", idempotency_key="project-team-local-test",
        )
        before = snapshot(c)
        if not c.db.in_transaction:
            c.db.execute("BEGIN")
        second = await client.create_project_participant(
            session=session, project_id=c.project_id, target_user_id=c.original.user_id,
            role="SURVEYOR", idempotency_key="project-team-local-test",
        )
    assert first.data == second.data and snapshot(c) == before
    assert first.data["scope_id"] == str(c.project_id)
    assert first.data["user_id"] == str(c.original.user_id)
    assert first.data["expires_at"] is None

# Project participant deadline regression v1.
def project_future_deadline():
    from datetime import datetime, timedelta, timezone
    return (
        datetime.now(timezone.utc) + timedelta(days=30)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")


async def dated_project_review(c):
    await handler.project_team_callback(c.callback, c.state)
    await click(c, "add")
    await click(c, "user", 0)
    await click(c, "role", 0)
    await click(c, "date")
    assert c.data[handler.VIEW]["phase"] == "date"
    message = NS(
        from_user=c.callback.from_user,
        chat=c.callback.message.chat,
        text=project_future_deadline(),
        answer=AsyncMock(return_value=NS(message_id=2)),
    )
    c.date_message = message
    await handler.project_team_date_message(message, c.state)
    assert c.data[handler.VIEW]["phase"] == "review"
    return message.text


@pytest.mark.asyncio
async def test_dated_project_grant_requires_review_and_updates_origin_menus(team_screen):
    c = team_screen
    deadline = await dated_project_review(c)
    c.service.create_project_participant.assert_not_awaited()
    assert c.data[handler.VIEW]["expires_at"] == deadline
    assert c.data[c.source_key]["menu_id"] == 2
    assert c.data["last_menu_message_id"] == 2
    assert "Private candidate" not in repr(c.data)
    c.receipt["item"]["expires_at"] = deadline
    await click(c, "submit")
    args = c.service.create_project_participant.await_args.kwargs
    assert args["expires_at"] == deadline
    assert args["target_user_id"] == c.target
    assert args["project_id"] == c.project
    assert c.data[handler.ATTEMPT]["status"] == "done"


@pytest.mark.asyncio
async def test_project_date_can_be_replaced_with_no_expiry(team_screen):
    c = team_screen
    await dated_project_review(c)
    await click(c, "date")
    await click(c, "noexpiry")
    await click(c, "submit")
    assert c.service.create_project_participant.await_args.kwargs["expires_at"] is None
    assert c.data[handler.ATTEMPT]["status"] == "done"


@pytest.mark.asyncio
async def test_project_deadline_retry_preserves_original_key_and_date(team_screen, monkeypatch):
    c = team_screen
    deadline = await dated_project_review(c)
    c.receipt["item"]["expires_at"] = deadline
    c.service.create_project_participant.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_unavailable"),
        c.receipt,
    ]
    await click(c, "submit")
    frozen = deepcopy(c.data[handler.ATTEMPT])
    assert c.data[handler.VIEW]["phase"] == "pending"
    def do_not_reparse(value):
        raise AssertionError("A frozen replay cannot reinterpret its deadline.")
    monkeypatch.setattr(handler, "parse_grant_expiry", do_not_reparse)
    await click(c, "submit")
    first, second = c.service.create_project_participant.await_args_list
    assert first.kwargs == second.kwargs
    assert second.kwargs["expires_at"] == frozen["expires_at"] == deadline
    assert second.kwargs["idempotency_key"] == frozen["id"]
    assert c.data[handler.ATTEMPT]["status"] == "done"


@pytest.mark.asyncio
async def test_project_date_elapsed_before_submit_does_not_create_attempt(team_screen, monkeypatch):
    c = team_screen
    await dated_project_review(c)
    def elapsed(value):
        raise ValueError("Elapsed while awaiting confirmation.")
    monkeypatch.setattr(handler, "parse_grant_expiry", elapsed)
    await click(c, "submit")
    c.service.create_project_participant.assert_not_awaited()
    assert not c.data.get(handler.ATTEMPT)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["naive", "role", "tenant"])
async def test_project_date_input_rejects_invalid_or_changed_context(team_screen, failure):
    c = team_screen
    await handler.project_team_callback(c.callback, c.state)
    await click(c, "add")
    await click(c, "user", 0)
    await click(c, "role", 0)
    await click(c, "date")
    message = NS(
        from_user=c.callback.from_user, chat=c.callback.message.chat,
        text=project_future_deadline(),
        answer=AsyncMock(return_value=NS(message_id=2)),
    )
    if failure == "naive":
        message.text = "2030-12-31 18:00"
    elif failure == "role":
        c.result["roles"] = []
    else:
        c.data["construction_suite_context"]["tenant_id"] = str(uuid4())
    await handler.project_team_date_message(message, c.state)
    c.service.create_project_participant.assert_not_awaited()
    assert c.data[handler.VIEW].get("phase") != "review"


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_receipt", [False, True])
async def test_project_grant_transport_checks_expiry_receipt(wrong_receipt):
    import json
    user, tenant, project, target = uuid4(), uuid4(), uuid4(), uuid4()
    deadline = project_future_deadline()
    receipt = {
        "id": str(uuid4()), "user_id": str(target), "role": "SURVEYOR",
        "scope_type": "project", "scope_id": str(project), "status": "active",
        "expires_at": None if wrong_receipt else deadline,
        "granted_at": "2026-10-01T12:00:00Z",
    }
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(201, json={
            "data": receipt, "meta": {}, "request_id": "project-date",
        })
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        client = ConstructionApiClient(http=http)
        async def call():
            return await client.create_project_participant(
                session=PlatformApiClientSession(user, tenant, SecretStr("local")),
                project_id=project, target_user_id=target, role="SURVEYOR",
                idempotency_key="project-date-local", expires_at=deadline,
            )
        if wrong_receipt:
            with pytest.raises(ConstructionApiClientError):
                await call()
        else:
            result = await call()
            assert result.data["expires_at"] == deadline
    assert calls[0].method == "POST"
    assert calls[0].url.path == f"/api/v1/construction/team/members/{target}/access-grants"
    assert calls[0].headers["idempotency-key"] == "project-date-local"
    assert json.loads(calls[0].content) == {
        "role": "SURVEYOR", "scope_type": "project",
        "scope_id": str(project), "expires_at": deadline,
    }


@pytest.mark.asyncio
async def test_project_date_roundtrip_through_registered_api_and_keyed_replay(issuance_http):
    from datetime import datetime, timedelta
    c = issuance_http
    deadline = (c.now + timedelta(hours=6)).isoformat()
    session = PlatformApiClientSession(
        c.actor.user_id, c.actor.tenant_id, SecretStr("local-test"),
    )
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
    ) as http:
        client = ConstructionApiClient(http=http)
        async def call():
            if not c.db.in_transaction:
                c.db.execute("BEGIN")
            return await client.create_project_participant(
                session=session, project_id=c.project_id,
                target_user_id=c.original.user_id, role="SURVEYOR",
                idempotency_key="project-timed-roundtrip", expires_at=deadline,
            )
        first = await call()
        before = snapshot(c)
        second = await call()
    assert first.data == second.data and snapshot(c) == before
    assert datetime.fromisoformat(
        first.data["expires_at"].replace("Z", "+00:00")
    ) == datetime.fromisoformat(deadline)
