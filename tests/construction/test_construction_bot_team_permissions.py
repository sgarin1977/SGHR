"""Read-only permissions UI; no real API, database or Telegram operations."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from handlers.construction import construction_team_permissions as handler
from handlers.construction import construction_team as organization
from handlers.construction import construction_project_team as project
from services.construction.construction_api_client import ConstructionApiClientError
from services.construction.construction_permissions import CONSTRUCTION_PERMISSION_CODES
from ui.construction_team_permissions import (
    LANGUAGES, LABELS, permission_label,
)


@pytest.fixture(params=["o", "p"])
def screen(request, monkeypatch):
    c = NS(mode=request.param, user=uuid4(), target=uuid4(), tenant=uuid4(), project=uuid4())
    c.grant = {
        "id": str(uuid4()), "role": "OBSERVER", "scope_type": "project",
        "scope_id": str(c.project), "expires_at": None,
    }
    c.member = {
        "user_id": str(c.target), "display_name": "Private member",
        "email": "member@example.com", "roles": ["OBSERVER"],
        "grants": [deepcopy(c.grant)],
        "scoped_permissions": [{
            "grant_id": c.grant["id"], "role": c.grant["role"],
            "scope_type": "project", "scope_id": str(c.project),
            "permissions": ["construction.projects.read"],
        }],
    }
    c.result = {
        "items": [c.member], "actor_user_id": str(c.user),
        "organization": "Organization", "language": "uk",
        "project_id": str(c.project), "project_name": "Project",
    }
    common = {
        "nonce": "source", "telegram_user_id": 123, "tenant_id": str(c.tenant),
        "actor_user_id": str(c.user), "menu_id": 1, "offset": 0,
        "user_ids": [str(c.target)],
    }
    c.data = {
        handler.CONTEXT: {"telegram_user_id": 123, "tenant_id": str(c.tenant)},
        "last_menu_message_id": 1,
    }
    if c.mode == "o":
        c.key = organization.VIEW
        c.data[c.key] = common
    else:
        c.key = project.VIEW
        c.data[project.PROJECT_FORM] = {
            "id": "form", "telegram_user_id": 123, "tenant_id": str(c.tenant),
            "actor_user_id": str(c.user), "project_id": str(c.project),
            "menu_id": 1, "phase": "done", "step": 4, "payload": {},
        }
        c.data[c.key] = {
            **common, "phase": "list",
            "source_key": project.PROJECT_FORM, "source_token": "form",
            "project_id": str(c.project), "client_id": None, "back": "PF:open:form:4",
        }
    async def update(**values):
        c.data.update(values)
    c.state = NS(
        get_data=AsyncMock(side_effect=lambda: c.data),
        update_data=AsyncMock(side_effect=update),
        get_state=AsyncMock(return_value=None),
    )
    c.callback = NS(
        data="TP:" + c.mode + ":source:0",
        from_user=NS(id=123, is_bot=False, language_code="uk"),
        answer=AsyncMock(),
        message=NS(message_id=1, chat=NS(id=123, type="private"), answer=AsyncMock()),
    )
    c.service = NS(
        project_scope_names=AsyncMock(side_effect=lambda **kwargs: {
            "actor_user_id": str(c.user), "tenant_id": str(c.tenant),
            "project_names": {str(value): "Project" for value in kwargs["project_ids"]},
        }),
        team_members=AsyncMock(side_effect=lambda **kwargs: deepcopy(c.result)),
        project_team=AsyncMock(side_effect=lambda **kwargs: deepcopy(c.result)),
    )
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.service
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    c.sent = AsyncMock(return_value=NS(message_id=1))
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", c.sent)
    return c


async def click(c, action, position=0):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = handler.cb(c.data[c.key], c.mode + action, position)
    await handler.permissions_callback(c.callback, c.state)


@pytest.mark.asyncio
async def test_permissions_are_fresh_scoped_reads_without_payload_cache(screen):
    c = screen
    await handler.permissions_callback(c.callback, c.state)
    assert c.data[c.key]["phase"] == "rights_scopes"
    await click(c, "g")
    assert c.data[c.key]["phase"] == "rights_permissions"
    text = c.sent.await_args.kwargs["text"]
    assert permission_label("construction.projects.read", "uk") in text
    assert permission_label("construction.projects.edit", "uk") not in text
    saved = repr(c.data[c.key])
    assert "Private member" not in saved and "member@example.com" not in saved
    assert "construction.projects.read" not in saved and "scoped_permissions" not in saved
    await click(c, "r")
    api = c.service.team_members if c.mode == "o" else c.service.project_team
    assert api.await_count == 3
    assert api.await_args.kwargs["expected_user_id"] == c.user
    if c.mode == "p":
        assert api.await_args.kwargs["project_id"] == c.project
        assert "selection" not in api.await_args.kwargs


@pytest.mark.asyncio
async def test_permissions_pagination_uses_current_api_projection(screen):
    c = screen
    codes = sorted(CONSTRUCTION_PERMISSION_CODES)
    c.member["scoped_permissions"][0]["permissions"] = codes
    await handler.permissions_callback(c.callback, c.state)
    await click(c, "g")
    await click(c, "r", 10)
    assert c.data[c.key]["rights_permission_offset"] == 10
    text = c.sent.await_args.kwargs["text"]
    assert permission_label(codes[10], "uk") in text
    assert permission_label(codes[0], "uk") not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["member", "grant", "authorization"])
async def test_revoked_or_changed_access_never_shows_cached_permissions(screen, failure):
    c = screen
    await handler.permissions_callback(c.callback, c.state)
    await click(c, "g")
    if failure == "member":
        c.result["items"] = []
    elif failure == "grant":
        c.member["grants"][0]["id"] = str(uuid4())
    else:
        api = c.service.team_members if c.mode == "o" else c.service.project_team
        api.side_effect = ConstructionApiClientError(status_code=403, code="forbidden")
    await click(c, "r")
    assert c.data[c.key]["phase"] == "rights_error"
    assert permission_label("construction.projects.read", "uk") not in c.sent.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_scope_projection_is_not_taken_from_a_different_grant(screen):
    c = screen
    await handler.permissions_callback(c.callback, c.state)
    c.member["scoped_permissions"][0]["scope_id"] = str(uuid4())
    await click(c, "g")
    assert c.data[c.key]["phase"] == "rights_error"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "actor", "menu", "nonce"])
async def test_stale_origin_never_calls_team_api(screen, failure):
    c = screen
    if failure == "tenant":
        c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
    elif failure == "actor":
        c.callback.from_user.id = c.callback.message.chat.id = 999
    elif failure == "menu":
        c.callback.message.message_id = 99
    else:
        c.callback.data = "TP:" + c.mode + ":obsolete:0"
    await handler.permissions_callback(c.callback, c.state)
    c.service.team_members.assert_not_awaited()
    c.service.project_team.assert_not_awaited()


@pytest.mark.asyncio
async def test_leaving_permissions_clears_only_its_read_view(screen):
    c = screen
    await handler.permissions_callback(c.callback, c.state)
    c.data["construction_project_replace_attempt"] = {"status": "pending"}
    c.callback.data = "CS_HOME"
    await handler.permissions_navigation_middleware(
        AsyncMock(), c.callback, {"state": c.state},
    )
    assert c.data[c.key] == {}
    assert c.data["construction_project_replace_attempt"] == {"status": "pending"}


def test_all_current_permissions_have_labels_in_seven_languages():
    assert all(len(values) == 7 for values in LABELS.values())
    for language in LANGUAGES:
        for code in CONSTRUCTION_PERMISSION_CODES:
            assert permission_label(code, language) != code
    for action in ("o", "p", "os", "ps", "og", "pg", "or", "pr"):
        assert len(handler.cb({"nonce": "a" * 32}, action, 10000).encode("utf-8")) <= 64

# construction_scope_names_v1
