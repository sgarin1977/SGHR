"""Local replacement UI, immutable command and real workflow integration."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_project_replace as handler
from handlers.construction import construction_project_team as team
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.construction_project_replace import LABELS
from test_construction_bot_project_areas import screen
from test_construction_bot_project_team import team_screen
from test_construction_bot_project_revoke import revoke_screen
from test_construction_1a_access_mutation_workflow import pipeline, snapshot
from test_construction_1a_access_mutation_http import http_case


@pytest.fixture
def replace_screen(revoke_screen, monkeypatch):
    c = revoke_screen
    c.replaced = {
        **deepcopy(c.revoked),
        "item": {
            **deepcopy(c.revoked["item"]), "id": str(uuid4()),
            "role": "SURVEYOR", "status": "active",
        },
    }
    c.service.replace_project_participant = AsyncMock(return_value=c.replaced)
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.service
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    return c


async def click(c, action, value=None):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = f'PX:{action}:{c.data[team.VIEW]["nonce"]}'
    if value is not None:
        c.callback.data += ":" + str(value)
    await handler.replace_callback(c.callback, c.state)


async def review(c):
    await team.project_team_callback(c.callback, c.state)
    await click(c, "open")
    await click(c, "choose", 0)
    assert c.data[team.VIEW]["phase"] == "replace_roles"
    await click(c, "role", 0)
    assert c.data[team.VIEW]["phase"] == "replace_review"


@pytest.mark.asyncio
async def test_review_then_replace_exact_grant_without_identity_or_expiry_change(replace_screen):
    c = replace_screen
    await review(c)
    c.service.replace_project_participant.assert_not_awaited()
    assert "Private participant" not in repr(c.data)
    await click(c, "submit")
    args = c.service.replace_project_participant.await_args.kwargs
    assert args["grant"] == c.grant and args["target_user_id"] == c.target
    assert args["new_role"] == "SURVEYOR" and args["project_id"] == c.project
    assert args["expected_user_id"] == c.user
    assert c.data[handler.ATTEMPT]["status"] == "done"
    assert c.data[handler.ATTEMPT]["new_grant_id"] != c.grant["id"]


@pytest.mark.asyncio
async def test_unknown_outcome_retries_same_original_grant_role_and_key(replace_screen):
    c = replace_screen
    await review(c)
    c.service.replace_project_participant.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_unavailable"), c.replaced,
    ]
    await click(c, "submit")
    first = deepcopy(c.service.replace_project_participant.await_args.kwargs)
    assert c.data[team.VIEW]["phase"] == "replace_pending"
    await click(c, "submit")
    assert c.service.replace_project_participant.await_args.kwargs == first
    assert c.data[handler.ATTEMPT]["status"] == "done"


@pytest.mark.asyncio
async def test_changed_role_options_refuse_stale_role_selection(replace_screen):
    c = replace_screen
    await team.project_team_callback(c.callback, c.state)
    await click(c, "open")
    await click(c, "choose", 0)
    c.result["roles"] = [{"role": "ESTIMATOR", "permissions": []}]
    await click(c, "role", 0)
    assert c.data[team.VIEW]["phase"] == "replace_error"
    c.service.replace_project_participant.assert_not_awaited()


@pytest.mark.asyncio
async def test_replaced_original_between_pages_is_never_substituted(replace_screen):
    c = replace_screen
    await team.project_team_callback(c.callback, c.state)
    await click(c, "open")
    c.result["items"][-1]["grants"][0]["id"] = str(uuid4())
    await click(c, "choose", 0)
    assert c.data[team.VIEW]["phase"] == "replace_error"
    c.service.replace_project_participant.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "actor", "menu", "nonce"])
async def test_stale_or_foreign_confirmation_never_posts(replace_screen, failure):
    c = replace_screen
    await review(c)
    if failure == "tenant":
        c.data["construction_suite_context"]["tenant_id"] = str(uuid4())
    elif failure == "actor":
        c.data[c.source_key]["actor_user_id"] = str(uuid4())
    elif failure == "menu":
        c.data[c.source_key]["menu_id"] = 99
    else:
        c.callback.data = "PX:submit:obsolete"
        await handler.replace_callback(c.callback, c.state)
        c.service.replace_project_participant.assert_not_awaited()
        return
    await click(c, "submit")
    c.service.replace_project_participant.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_attempt_survives_navigation_and_expiry_never_renews_key(replace_screen, monkeypatch):
    c = replace_screen
    await review(c)
    c.service.replace_project_participant.side_effect = RuntimeError("private")
    await click(c, "submit")
    attempt = deepcopy(c.data[handler.ATTEMPT])
    c.callback.data = "CS_HOME"
    await team.project_team_navigation_middleware(AsyncMock(), c.callback, {"state": c.state})
    assert c.data[team.VIEW] == {} and c.data[handler.ATTEMPT] == attempt
    await handler.retain_project_replace_attempt(c.state, {handler.ATTEMPT: attempt}, 123)
    c.callback.data = "PT:open:" + c.token
    await team.project_team_callback(c.callback, c.state)
    await click(c, "open")
    assert c.data[team.VIEW]["phase"] == "replace_pending"
    monkeypatch.setattr(handler.revoke, "time", lambda: attempt["started_at"] + 86400)
    await click(c, "submit")
    c.service.replace_project_participant.assert_awaited_once()
    assert c.data[handler.ATTEMPT] == attempt


@pytest.mark.asyncio
async def test_message_replacement_keeps_client_positions_and_expiry_is_reviewed(replace_screen):
    c = replace_screen
    deadline = "2026-11-01T12:00:00Z"
    c.grant["expires_at"] = deadline
    c.result["items"][-1]["grants"][0]["expires_at"] = deadline
    c.replaced["item"]["expires_at"] = deadline
    c.sent.return_value = NS(message_id=2)
    await review(c)
    assert "2026-11-01" in c.sent.await_args.kwargs["text"]
    assert c.data[c.source_key]["menu_id"] == 2
    if c.source_key == team.PROJECT:
        assert c.data[team.CARD]["positions"]["projects"] == "preserved-client-position"
    await click(c, "submit")
    assert c.service.replace_project_participant.await_args.kwargs["grant"]["expires_at"] == deadline


@pytest.mark.asyncio
async def test_subject_change_stops_before_mutation_http():
    user, tenant = uuid4(), uuid4()
    api = NS(replace_project_participant=AsyncMock())
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local-test")), {"language": "uk"},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.replace_project_participant(
            telegram_user_id=123, tenant_id=tenant, project_id=uuid4(),
            expected_user_id=uuid4(), target_user_id=uuid4(), grant={},
            new_role="SURVEYOR", idempotency_key="local",
        )
    api.replace_project_participant.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["id", "user", "scope", "role", "status", "expiry", "private"])
async def test_transport_binds_new_receipt_and_preserves_existing_expiry(failure):
    user, tenant, target, project = uuid4(), uuid4(), uuid4(), uuid4()
    grant = {
        "id": str(uuid4()), "role": "ESTIMATOR", "scope_type": "project",
        "scope_id": str(project), "expires_at": "2026-11-01T12:00:00Z",
    }
    item = {
        **grant, "id": str(uuid4()), "role": "SURVEYOR", "user_id": str(target),
        "status": "active", "granted_at": "2026-10-26T12:00:00Z",
    }
    if failure == "id":
        item["id"] = grant["id"]
    elif failure == "user":
        item["user_id"] = str(uuid4())
    elif failure == "scope":
        item["scope_id"] = str(uuid4())
    elif failure == "role":
        item["role"] = "ADMIN"
    elif failure == "status":
        item["status"] = "revoked"
    elif failure == "expiry":
        item["expires_at"] = None
    else:
        item["private"] = "hidden"

    def transport(request):
        assert request.method == "POST"
        assert request.url.path == f'/api/v1/construction/team/access-grants/{grant["id"]}/replace'
        assert not request.url.query and request.headers["Idempotency-Key"] == "local"
        assert json.loads(request.content) == {
            "role": "SURVEYOR", "scope_type": "project",
            "scope_id": str(project), "expires_at": grant["expires_at"],
        }
        return httpx.Response(200, json={"data": item, "meta": {}, "request_id": "local"})

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        with pytest.raises(ConstructionApiClientError) as caught:
            await ConstructionApiClient(http=http).replace_project_participant(
                session=PlatformApiClientSession(user, tenant, SecretStr("local-test")),
                project_id=project, target_user_id=target, grant=grant,
                new_role="SURVEYOR", idempotency_key="local",
            )
    assert caught.value.status_code == 502


@pytest.mark.asyncio
@pytest.mark.parametrize("with_expiry", [False, True])
async def test_real_workflow_preserves_history_and_replays_without_second_replacement(http_case, with_expiry):
    c = http_case
    if with_expiry:
        c.original.expires_at = c.now + timedelta(hours=1)
        c.save("grant", c.original.id, c.original)
        c.db.commit()
    grant = {
        "id": str(c.original.id), "role": c.original.role,
        "scope_type": "project", "scope_id": str(c.project_id),
        "expires_at": c.original.expires_at.isoformat() if c.original.expires_at else None,
    }
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
    ) as http:
        client = ConstructionApiClient(http=http)
        async def replace():
            if not c.db.in_transaction:
                c.db.execute("BEGIN")
            return await client.replace_project_participant(
                session=PlatformApiClientSession(
                    c.actor.user_id, c.actor.tenant_id, SecretStr("local-test"),
                ),
                project_id=c.project_id, target_user_id=c.original.user_id,
                grant=grant, new_role="SURVEYOR", idempotency_key="replace-key",
            )
        first = await replace()
        before = snapshot(c)
        if with_expiry:
            c.now += timedelta(hours=2)
        second = await replace()
        assert first.data == second.data and snapshot(c) == before
        assert first.data["id"] != str(c.original.id)
        assert first.data["role"] == "SURVEYOR" and first.data["status"] == "active"
        if with_expiry:
            assert datetime.fromisoformat(first.data["expires_at"]) == c.original.expires_at
        else:
            assert first.data["expires_at"] is None
        original = next(g for g in c.rows("grant") if g.id == c.original.id)
        assert original.status == "revoked" and original.role == "ESTIMATOR"
        assert len(c.rows("grant")) == 2 and len(c.rows("audit")) == 2
        assert c.repo.create_grant.await_count == 1
        c.authorized = False
        with pytest.raises(ConstructionApiClientError) as caught:
            await replace()
        assert caught.value.status_code == 403 and snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit"])
async def test_real_failure_rolls_back_old_grant_new_grant_and_audit(http_case, failure):
    c = http_case
    before = snapshot(c)
    if failure == "audit":
        c.fail_second_audit = True
    else:
        c.fail_commit = True
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
    ) as http:
        if not c.db.in_transaction:
            c.db.execute("BEGIN")
        with pytest.raises(ConstructionApiClientError) as caught:
            await ConstructionApiClient(http=http).replace_project_participant(
                session=PlatformApiClientSession(
                    c.actor.user_id, c.actor.tenant_id, SecretStr("local-test"),
                ),
                project_id=c.project_id, target_user_id=c.original.user_id,
                grant={
                    "id": str(c.original.id), "role": c.original.role,
                    "scope_type": "project", "scope_id": str(c.project_id), "expires_at": None,
                },
                new_role="SURVEYOR", idempotency_key="failed-replace",
            )
    assert caught.value.status_code == 500
    assert snapshot(c) == before and not c.db.in_transaction


def test_all_new_labels_have_seven_languages():
    assert all(len(values) == 7 for values in LABELS.values())

# Existing project access deadline regression v1.
def replacement_future_deadline():
    from datetime import timezone
    return (
        datetime.now(timezone.utc) + timedelta(days=30)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")


async def expiry_review(c, *, choose_new_role=False):
    await team.project_team_callback(c.callback, c.state)
    await click(c, "open")
    await click(c, "choose", 0)
    if choose_new_role:
        await click(c, "role", 0)
    await click(c, "expiry")
    assert c.data[team.VIEW]["phase"] == "replace_expiry"
    message = NS(
        from_user=c.callback.from_user, chat=c.callback.message.chat,
        text=replacement_future_deadline(),
        answer=AsyncMock(return_value=NS(message_id=2)),
    )
    await handler.replacement_expiry_message(message, c.state)
    assert c.data[team.VIEW]["phase"] == "replace_review"
    return message.text


@pytest.mark.asyncio
@pytest.mark.parametrize("choose_new_role", [False, True])
async def test_expiry_change_keeps_original_grant_and_requires_confirmation(
    replace_screen, choose_new_role,
):
    c = replace_screen
    original = deepcopy(c.grant)
    deadline = await expiry_review(c, choose_new_role=choose_new_role)
    c.service.replace_project_participant.assert_not_awaited()
    desired_role = "SURVEYOR" if choose_new_role else original["role"]
    c.replaced["item"].update(role=desired_role, expires_at=deadline)
    await click(c, "submit")
    args = c.service.replace_project_participant.await_args.kwargs
    assert args["grant"] == original == c.grant
    assert args["new_role"] == desired_role
    assert args["expires_at"] == deadline
    assert args["target_user_id"] == c.target
    assert c.data[handler.ATTEMPT]["status"] == "done"
    assert c.data[handler.ATTEMPT]["new_grant_id"] != original["id"]


@pytest.mark.asyncio
async def test_expiry_only_edit_available_when_no_alternative_role_exists(replace_screen):
    c = replace_screen
    c.result["roles"] = [{"role": c.grant["role"], "permissions": []}]
    deadline = await expiry_review(c)
    c.replaced["item"].update(role=c.grant["role"], expires_at=deadline)
    await click(c, "submit")
    assert c.data[handler.ATTEMPT]["status"] == "done"


@pytest.mark.asyncio
async def test_existing_expiry_can_be_removed_without_changing_role(replace_screen):
    c = replace_screen
    deadline = replacement_future_deadline()
    c.grant["expires_at"] = deadline
    c.result["items"][-1]["grants"][0]["expires_at"] = deadline
    c.replaced["item"].update(role=c.grant["role"], expires_at=None)
    await team.project_team_callback(c.callback, c.state)
    await click(c, "open")
    await click(c, "choose", 0)
    await click(c, "expiry")
    await click(c, "clear")
    c.service.replace_project_participant.assert_not_awaited()
    await click(c, "submit")
    args = c.service.replace_project_participant.await_args.kwargs
    assert args["grant"]["expires_at"] == deadline
    assert args["expires_at"] is None
    assert args["new_role"] == c.grant["role"]
    assert c.data[handler.ATTEMPT]["status"] == "done"


@pytest.mark.asyncio
async def test_unchanged_role_and_expiry_have_no_confirmation_or_mutation(replace_screen):
    c = replace_screen
    await team.project_team_callback(c.callback, c.state)
    await click(c, "open")
    await click(c, "choose", 0)
    await click(c, "expiry")
    await click(c, "keep")
    rows = c.sent.await_args.kwargs["reply_markup"].inline_keyboard
    assert not any(
        (button.callback_data or "").startswith("PX:submit:")
        for row in rows for button in row
    )
    await click(c, "submit")
    c.service.replace_project_participant.assert_not_awaited()
    assert not c.data.get(handler.ATTEMPT)


@pytest.mark.asyncio
async def test_replacement_deadline_retry_keeps_frozen_command_and_key(replace_screen, monkeypatch):
    c = replace_screen
    deadline = await expiry_review(c)
    c.replaced["item"].update(role=c.grant["role"], expires_at=deadline)
    c.service.replace_project_participant.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_unavailable"), c.replaced,
    ]
    await click(c, "submit")
    frozen = deepcopy(c.data[handler.ATTEMPT])
    assert frozen["command"]["new_expires_at"] == deadline
    def must_not_reparse(value):
        raise AssertionError("Historical replay must not renew its date.")
    monkeypatch.setattr(handler, "parse_grant_expiry", must_not_reparse)
    await click(c, "submit")
    first, second = c.service.replace_project_participant.await_args_list
    assert first.kwargs == second.kwargs
    assert second.kwargs["idempotency_key"] == frozen["id"]
    assert second.kwargs["grant"] == frozen["command"]["grant"]
    assert c.data[handler.ATTEMPT]["status"] == "done"


@pytest.mark.asyncio
async def test_elapsed_replacement_date_blocks_first_mutation(replace_screen, monkeypatch):
    c = replace_screen
    await expiry_review(c)
    def elapsed(value):
        raise ValueError("Expired before confirmation.")
    monkeypatch.setattr(handler, "parse_grant_expiry", elapsed)
    await click(c, "submit")
    c.service.replace_project_participant.assert_not_awaited()
    assert not c.data.get(handler.ATTEMPT)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["grant", "role", "tenant", "naive"])
async def test_replacement_date_input_rechecks_source_and_authority(replace_screen, failure):
    c = replace_screen
    await team.project_team_callback(c.callback, c.state)
    await click(c, "open")
    await click(c, "choose", 0)
    await click(c, "expiry")
    message = NS(
        from_user=c.callback.from_user, chat=c.callback.message.chat,
        text=replacement_future_deadline(),
        answer=AsyncMock(return_value=NS(message_id=2)),
    )
    if failure == "grant":
        c.result["items"][-1]["grants"][0]["id"] = str(uuid4())
    elif failure == "role":
        c.result["roles"] = []
    elif failure == "tenant":
        c.data["construction_suite_context"]["tenant_id"] = str(uuid4())
    else:
        message.text = "2030-12-31 18:00"
    await handler.replacement_expiry_message(message, c.state)
    c.service.replace_project_participant.assert_not_awaited()
    assert c.data[team.VIEW].get("phase") != "replace_review"


@pytest.mark.asyncio
@pytest.mark.parametrize("remove_expiry", [False, True])
async def test_registered_replace_api_changes_only_deadline_and_replays_once(
    http_case, remove_expiry,
):
    c = http_case
    deadline = None if remove_expiry else (c.now + timedelta(hours=6)).isoformat()
    # The fixture's canonical original grant supplies its current expiry.
    if remove_expiry and c.original.expires_at is None:
        pytest.skip("This fixture has no existing deadline to remove.")
    grant = {
        "id": str(c.original.id), "role": c.original.role,
        "scope_type": "project", "scope_id": str(c.project_id),
        "expires_at": c.original.expires_at.isoformat() if c.original.expires_at else None,
    }
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
            return await client.replace_project_participant(
                session=session, project_id=c.project_id,
                target_user_id=c.original.user_id, grant=deepcopy(grant),
                new_role=c.original.role, expires_at=deadline,
                idempotency_key="same-role-new-deadline",
            )
        first = await call()
        before = snapshot(c)
        second = await call()
    assert first.data == second.data and snapshot(c) == before
    assert first.data["id"] != grant["id"]
    assert first.data["role"] == grant["role"]
    assert first.data["scope_id"] == grant["scope_id"]
    actual = first.data["expires_at"]
    if deadline is None:
        assert actual is None
    else:
        assert datetime.fromisoformat(actual.replace("Z", "+00:00")) == datetime.fromisoformat(deadline)


def test_existing_expiry_actions_and_translations_are_bounded():
    assert all(len(values) == 7 for values in LABELS.values())
    for action in ("expiry", "clear", "keep"):
        assert len(("PX:" + action + ":" + "a" * 32).encode("utf-8")) <= 64
