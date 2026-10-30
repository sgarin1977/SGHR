"""Local project revocation transport, confirmation and replay checks."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_project_revoke as handler
from handlers.construction import construction_project_team as team
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.construction_project_revoke import LABELS
from test_construction_bot_project_areas import screen
from test_construction_bot_project_team import team_screen
from test_construction_1a_access_mutation_workflow import pipeline, snapshot
from test_construction_1a_access_mutation_http import http_case


@pytest.fixture
def revoke_screen(team_screen, monkeypatch):
    c = team_screen
    c.grant = {
        "id": str(uuid4()), "role": "ESTIMATOR", "scope_type": "project",
        "scope_id": str(c.project), "expires_at": None,
    }
    c.result["items"].append({
        "user_id": str(c.target), "display_name": "Private participant", "email": None,
        "roles": ["ESTIMATOR"], "grants": [deepcopy(c.grant)], "scoped_permissions": [],
    })
    c.result["roles"].append({"role": "ESTIMATOR", "permissions": []})
    c.revoked = {
        "actor_user_id": str(c.user), "project_id": str(c.project), "language": "uk",
        "item": {
            **c.grant, "user_id": str(c.target), "status": "revoked",
            "granted_at": "2026-10-23T12:00:00Z",
        },
    }
    c.service.revoke_project_participant = AsyncMock(return_value=c.revoked)
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.service
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    return c


async def click(c, action, value=None):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = f'PG:{action}:{c.data[team.VIEW]["nonce"]}'
    if value is not None:
        c.callback.data += ":" + str(value)
    await handler.revoke_callback(c.callback, c.state)


async def review(c):
    await team.project_team_callback(c.callback, c.state)
    await click(c, "open")
    assert c.data[team.VIEW]["phase"] == "revoke_choose"
    await click(c, "choose", 0)
    assert c.data[team.VIEW]["phase"] == "revoke_review"


@pytest.mark.asyncio
async def test_explicit_review_and_exact_original_project_role(revoke_screen):
    c = revoke_screen
    await review(c)
    c.service.revoke_project_participant.assert_not_awaited()
    assert "Private participant" not in repr(c.data)
    await click(c, "submit")
    args = c.service.revoke_project_participant.await_args.kwargs
    assert args["grant"] == c.grant and args["target_user_id"] == c.target
    assert args["project_id"] == c.project and args["expected_user_id"] == c.user
    assert c.data[handler.ATTEMPT]["status"] == "done"
    assert c.data[c.source_key]["menu_id"] == c.data["last_menu_message_id"]


@pytest.mark.asyncio
async def test_unknown_outcome_retries_identical_command_and_key(revoke_screen):
    c = revoke_screen
    await review(c)
    c.service.revoke_project_participant.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_unavailable"), c.revoked,
    ]
    await click(c, "submit")
    first = deepcopy(c.service.revoke_project_participant.await_args.kwargs)
    assert c.data[team.VIEW]["phase"] == "revoke_pending"
    await click(c, "submit")
    assert c.service.revoke_project_participant.await_args.kwargs == first
    assert c.data[handler.ATTEMPT]["status"] == "done"


@pytest.mark.asyncio
async def test_expired_retry_does_not_issue_a_new_operation(revoke_screen, monkeypatch):
    c = revoke_screen
    await review(c)
    c.service.revoke_project_participant.side_effect = RuntimeError("private")
    await click(c, "submit")
    before = deepcopy(c.data[handler.ATTEMPT])
    monkeypatch.setattr(handler, "time", lambda: before["started_at"] + 86400)
    await click(c, "submit")
    c.service.revoke_project_participant.assert_awaited_once()
    assert c.data[handler.ATTEMPT] == before


@pytest.mark.asyncio
async def test_revoked_or_changed_grant_between_selection_and_review_is_not_substituted(revoke_screen):
    c = revoke_screen
    await team.project_team_callback(c.callback, c.state)
    await click(c, "open")
    c.result["items"][-1]["grants"][0]["id"] = str(uuid4())
    await click(c, "choose", 0)
    assert c.data[team.VIEW]["phase"] == "revoke_error"
    c.service.revoke_project_participant.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "actor", "menu", "nonce"])
async def test_foreign_or_stale_confirmation_never_posts(revoke_screen, failure):
    c = revoke_screen
    await review(c)
    if failure == "tenant":
        c.data["construction_suite_context"]["tenant_id"] = str(uuid4())
    elif failure == "actor":
        c.data[c.source_key]["actor_user_id"] = str(uuid4())
    elif failure == "menu":
        c.data[c.source_key]["menu_id"] = 99
    else:
        c.data[team.VIEW]["nonce"] = "other"
        c.callback.data = "PG:submit:old"
        await handler.revoke_callback(c.callback, c.state)
        c.service.revoke_project_participant.assert_not_awaited()
        return
    await click(c, "submit")
    c.service.revoke_project_participant.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_replacement_keeps_parent_and_pending_survives_navigation(revoke_screen):
    c = revoke_screen
    c.sent.return_value = NS(message_id=2)
    await review(c)
    assert c.data[c.source_key]["menu_id"] == 2
    if c.source_key == team.PROJECT:
        assert c.data[team.CARD]["positions"]["projects"] == "preserved-client-position"
    c.service.revoke_project_participant.side_effect = RuntimeError("private")
    await click(c, "submit")
    attempt = deepcopy(c.data[handler.ATTEMPT])
    c.callback.data = "CS_HOME"
    await team.project_team_navigation_middleware(AsyncMock(), c.callback, {"state": c.state})
    assert c.data[team.VIEW] == {} and c.data[handler.ATTEMPT] == attempt
    await handler.retain_project_revoke_attempt(c.state, {handler.ATTEMPT: attempt}, 123)
    assert c.data[handler.ATTEMPT] == attempt


def test_choices_exclude_tenant_foreign_project_and_roles_outside_server_options(revoke_screen):
    c = revoke_screen
    result = deepcopy(c.result)
    result["items"][-1]["grants"].extend([
        {**c.grant, "id": str(uuid4()), "scope_type": "tenant",
         "scope_id": str(c.tenant), "role": "OWNER"},
        {**c.grant, "id": str(uuid4()), "scope_id": str(uuid4())},
        {**c.grant, "id": str(uuid4()), "role": "ADMIN"},
    ])
    available = handler.choices(result, c.project)
    assert len(available) == 1 and available[0][0]["grant"] == c.grant
    assert all(len(values) == 7 for values in LABELS.values())


@pytest.mark.asyncio
async def test_changed_principal_cannot_revoke_before_http_call():
    user, tenant = uuid4(), uuid4()
    api = NS(revoke_project_participant=AsyncMock())
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local-test")),
        {"language": "uk"},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.revoke_project_participant(
            telegram_user_id=123, tenant_id=tenant, project_id=uuid4(),
            expected_user_id=uuid4(), target_user_id=uuid4(), grant={},
            idempotency_key="local-key",
        )
    api.revoke_project_participant.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["id", "user", "scope", "role", "status", "expiry", "private"])
async def test_client_rejects_mismatched_revocation_receipt(failure):
    user, tenant, project, target = uuid4(), uuid4(), uuid4(), uuid4()
    grant = {
        "id": str(uuid4()), "role": "ESTIMATOR", "scope_type": "project",
        "scope_id": str(project), "expires_at": None,
    }
    item = {
        **grant, "user_id": str(target), "status": "revoked",
        "granted_at": "2026-10-23T12:00:00Z",
    }
    if failure == "id":
        item["id"] = str(uuid4())
    elif failure == "user":
        item["user_id"] = str(uuid4())
    elif failure == "scope":
        item["scope_id"] = str(uuid4())
    elif failure == "role":
        item["role"] = "ADMIN"
    elif failure == "status":
        item["status"] = "active"
    elif failure == "expiry":
        item["expires_at"] = "2026-11-01T12:00:00Z"
    else:
        item["private"] = "hidden"

    def transport(request):
        assert request.method == "POST"
        assert request.url.path == f'/api/v1/construction/team/access-grants/{grant["id"]}/revoke'
        assert not request.url.query and request.content == b"{}"
        assert request.headers["Idempotency-Key"] == "local-key"
        assert request.headers["Authorization"] == "Bearer local-test"
        return httpx.Response(200, json={"data": item, "meta": {}, "request_id": "local"})

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        with pytest.raises(ConstructionApiClientError) as caught:
            await ConstructionApiClient(http=http).revoke_project_participant(
                session=PlatformApiClientSession(user, tenant, SecretStr("local-test")),
                project_id=project, target_user_id=target, grant=grant,
                idempotency_key="local-key",
            )
        assert caught.value.status_code == 502


@pytest.mark.asyncio
async def test_real_http_workflow_revokes_once_and_rechecks_authority_on_replay(http_case):
    c = http_case
    grant = {
        "id": str(c.original.id), "role": c.original.role, "scope_type": "project",
        "scope_id": str(c.project_id), "expires_at": None,
    }
    session = PlatformApiClientSession(c.actor.user_id, c.actor.tenant_id, SecretStr("local-test"))
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
    ) as http:
        client = ConstructionApiClient(http=http)
        async def revoke():
            if not c.db.in_transaction:
                c.db.execute("BEGIN")
            return await client.revoke_project_participant(
                session=session, project_id=c.project_id,
                target_user_id=c.original.user_id, grant=grant, idempotency_key="revoke-key",
            )
        first = await revoke()
        before = snapshot(c)
        second = await revoke()
        assert first.data == second.data and snapshot(c) == before
        assert first.data["status"] == "revoked" and len(c.rows("audit")) == 1
        assert len(c.rows("grant")) == 1 and c.rows("grant")[0].role == c.original.role
        c.authorized = False
        with pytest.raises(ConstructionApiClientError) as caught:
            await revoke()
        assert caught.value.status_code == 403 and snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit"])
async def test_real_failure_rolls_back_grant_and_audit(http_case, failure):
    c = http_case
    before = snapshot(c)
    if failure == "commit":
        c.fail_commit = True
    else:
        events = c.service.audit_logger.event_repository
        original = events.create_event.side_effect
        async def fail_after_write(**fields):
            await original(**fields)
            raise RuntimeError("private audit failure")
        events.create_event.side_effect = fail_after_write
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
    ) as http:
        if not c.db.in_transaction:
            c.db.execute("BEGIN")
        with pytest.raises(ConstructionApiClientError) as caught:
            await ConstructionApiClient(http=http).revoke_project_participant(
                session=PlatformApiClientSession(
                    c.actor.user_id, c.actor.tenant_id, SecretStr("local-test"),
                ),
                project_id=c.project_id, target_user_id=c.original.user_id,
                grant={
                    "id": str(c.original.id), "role": c.original.role,
                    "scope_type": "project", "scope_id": str(c.project_id), "expires_at": None,
                },
                idempotency_key="failed-revoke",
            )
    assert caught.value.status_code == 500
    assert snapshot(c) == before and not c.db.in_transaction
