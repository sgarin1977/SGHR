"""Local adjustment transport, project authority and all three card origins."""
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from handlers.construction import construction_project_adjustments as handler
from handlers.construction import construction_project_edit as editor
from services.construction.construction_project_adjustment_bot_api import ProjectAdjustmentBotApi
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from test_construction_bot_project_edit import screen, project_item
from test_construction_01_project_read_http import project_http, get
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("role,scope,read,edit", [
    ("OWNER", "tenant", True, True), ("ADMIN", "tenant", True, True),
    ("PROJECT_MANAGER", "project", True, False),
    ("ESTIMATOR", "project", True, False), ("SURVEYOR", "project", False, False),
])
async def test_project_pricing_capabilities_follow_scope(project_http, role, scope, read, edit):
    c = project_http
    c.context = ConstructionAccessContext(
        c.user, c.tenant, (role,), tuple(CONSTRUCTION_ROLE_PERMISSIONS[role]),
        (ConstructionGrantContext(role, scope, c.tenant if scope == "tenant" else c.project_id),),
    )
    response = await get(c)
    assert response.status_code == 200, response.text
    caps = response.json()["meta"]["capabilities"]
    assert caps["can_read_pricing_adjustments"] is read
    assert caps["can_edit_pricing_adjustments"] is edit


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "cancelled"])
async def test_terminal_project_pricing_edit_is_disabled(project_http, status):
    c = project_http
    c.context = ConstructionAccessContext(
        c.user, c.tenant, ("OWNER",), tuple(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"]),
        (ConstructionGrantContext("OWNER", "tenant", c.tenant),),
    )
    c.project.status = status
    response = await get(c)
    assert response.status_code == 200, response.text
    assert response.json()["meta"]["capabilities"]["can_edit_pricing_adjustments"] is False


@pytest.mark.parametrize("value,expected", [("1,25", "1.25"), ("0.000001", "0.000001")])
def test_exact_coefficient(value, expected):
    assert handler.parse_coefficient(value) == expected


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", 1.25])
def test_invalid_coefficient(value):
    with pytest.raises(ValueError):
        handler.parse_coefficient(value)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "denied", "foreign", "float"])
async def test_http_create_keeps_exact_project_and_explicit_enabled(project_item, failure):
    tenant, user, project = UUID(project_item["tenant_id"]), uuid4(), UUID(project_item["id"])
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={
            "data": {
                "id": str(uuid4()), "tenant_id": str(tenant),
                "project_id": str(uuid4()) if failure == "foreign" else str(project),
                "name": "Complexity", "coefficient": 1.25 if failure == "float" else "1.25",
                "enabled": False, "row_version": 1, "created_by": str(user),
                "created_at": project_item["created_at"], "updated_at": project_item["updated_at"],
                "changed": True,
            }, "meta": {}, "request_id": "local",
        })
    session = PlatformApiClientSession(user, tenant, SecretStr("local"))
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        api = ConstructionApiClient(http=http)
        api.project_details = AsyncMock(return_value=NS(
            data=project_item, meta={"capabilities": {
                "can_read_pricing_adjustments": True,
                "can_edit_pricing_adjustments": failure != "denied",
            }},
        ))
        entry = NS(
            _presentation=AsyncMock(return_value=(session, {"language": "uk"})),
            api=NS(construction=api),
        )
        request = {
            "operation": "create", "project_id": str(project),
            "adjustment_id": None,
            "body": {"name": "Complexity", "coefficient": "1.25", "enabled": False},
            "key": "stable-adjustment-key",
        }
        call = ProjectAdjustmentBotApi(entry).write_adjustment(
            request=request, telegram_user_id=123, tenant_id=tenant,
            project_id=project, expected_user_id=user,
        )
        if failure:
            with pytest.raises(ConstructionApiClientError):
                await call
        else:
            await call
    if failure == "denied":
        assert calls == []
    else:
        import json
        assert len(calls) == 1 and calls[0].method == "POST"
        assert calls[0].url.path == (
            "/api/v1/construction/projects/" + str(project) + "/pricing-adjustments"
        )
        assert json.loads(calls[0].content) == request["body"]
        assert calls[0].headers["idempotency-key"] == "stable-adjustment-key"


@pytest.fixture
def adjustment_screen(screen, monkeypatch):
    c = screen
    c.callback.data = "PJA:open:" + "b" * 32
    c.common = {
        "actor_user_id": str(c.actor), "language": "uk",
        "project_name": "Project", "can_manage": True,
    }
    c.adjustment = {
        "id": str(uuid4()), "tenant_id": c.item["tenant_id"],
        "project_id": c.item["id"], "name": "Complexity",
        "coefficient": "1.25", "enabled": False, "row_version": 7,
        "created_by": str(c.actor),
        "created_at": c.item["created_at"], "updated_at": c.item["updated_at"],
    }
    c.page = {**c.common, "items": [], "next_cursor": None}
    async def write(**kwargs):
        operation = kwargs["request"]
        item = deepcopy(c.adjustment)
        item.update({k: v for k, v in operation["body"].items() if k != "expected_row_version"})
        item["row_version"] = (
            1 if operation["operation"] == "create"
            else operation["body"]["expected_row_version"] + 1
        )
        return {**c.common, "item": {**item, "changed": True}}
    c.adjustments_api = NS(
        context=AsyncMock(side_effect=lambda **kwargs: (None, deepcopy(c.common))),
        list_adjustments=AsyncMock(return_value=c.page),
        get_adjustment=AsyncMock(side_effect=lambda **kwargs: {
            **c.common, "item": deepcopy(c.adjustment),
        }),
        write_adjustment=AsyncMock(side_effect=write),
    )
    c.successful_write = write
    monkeypatch.setattr(handler, "ProjectAdjustmentBotApi", lambda service: c.adjustments_api)
    monkeypatch.setattr(handler, "construction_bot_api_entry", editor.construction_bot_api_entry)
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", AsyncMock(
        side_effect=lambda **kwargs: NS(message_id=kwargs["callback"].message.message_id),
    ))
    return c


async def open_screen(c):
    await handler.adjustment_callback(c.callback, c.state)
    assert c.data[handler.VIEW]["phase"] == "list"


async def click(c, action, index=None):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = handler.cb(c.data[handler.VIEW], action, index)
    await handler.adjustment_callback(c.callback, c.state)


async def ready(c):
    await open_screen(c)
    await click(c, "new")
    assert c.data[handler.VIEW]["fields"] == {}
    c.message.text = "Complexity"
    await handler.adjustment_message(c.message, c.state)
    c.message.text = "1,25"
    await handler.adjustment_message(c.message, c.state)
    assert "enabled" not in c.data[handler.VIEW]["fields"]
    await click(c, "enabled", 1)
    assert c.data[handler.VIEW]["phase"] == "review"


@pytest.mark.asyncio
async def test_create_is_explicit_and_preserves_origin(adjustment_screen):
    c = adjustment_screen
    await ready(c)
    c.adjustments_api.write_adjustment.assert_not_awaited()
    await click(c, "save")
    request = c.adjustments_api.write_adjustment.await_args.kwargs["request"]
    assert request["body"] == {"name": "Complexity", "coefficient": "1.25", "enabled": False}
    assert handler.selected(
        c.data, c.data[handler.VIEW], 123, c.data["last_menu_message_id"],
    ) is not None


@pytest.mark.asyncio
async def test_edit_enabled_is_sparse_and_versioned(adjustment_screen):
    c = adjustment_screen
    c.page["items"] = [c.adjustment]
    await open_screen(c)
    await click(c, "open", 0)
    await click(c, "field", 2)
    await click(c, "enabled", 0)
    await click(c, "save")
    assert c.adjustments_api.write_adjustment.await_args.kwargs["request"]["body"] == {
        "enabled": True, "expected_row_version": 7,
    }


@pytest.mark.asyncio
async def test_cancel_writes_nothing(adjustment_screen):
    c = adjustment_screen
    await ready(c)
    await click(c, "list")
    c.adjustments_api.write_adjustment.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_retry_keeps_request_and_key(adjustment_screen):
    c = adjustment_screen
    await ready(c)
    attempts = 0
    async def uncertain(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConstructionApiClientError(status_code=504, code="api_timeout")
        return await c.successful_write(**kwargs)
    c.adjustments_api.write_adjustment.side_effect = uncertain
    await click(c, "save")
    first = deepcopy(c.adjustments_api.write_adjustment.await_args.kwargs["request"])
    await click(c, "list")
    await click(c, "retry")
    assert c.adjustments_api.write_adjustment.await_args.kwargs["request"] == first


@pytest.mark.asyncio
async def test_late_read_does_not_restore_changed_tenant(adjustment_screen):
    c = adjustment_screen
    await open_screen(c)
    before = deepcopy(c.data[handler.VIEW])
    async def late(**kwargs):
        c.data[handler.areas.CONTEXT]["tenant_id"] = str(uuid4())
        return c.page
    c.adjustments_api.list_adjustments.side_effect = late
    await click(c, "list")
    assert c.data[handler.VIEW] == before
