"""Local tax-profile permission projection, HTTP transport and Telegram forms."""
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

from handlers.construction import construction_tax_profiles as handler
from handlers.construction import construction_stage2_settings as shell
from services.construction.construction_tax_profile_bot_api import TaxProfileBotApi
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_api_services import read_construction_navigation
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
)
from ui.construction_tax_profiles import LABELS


def profile(tenant, user, version=1):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": str(uuid4()), "tenant_id": str(tenant), "name": "Primary",
        "client_price_tax_mode": "TAX_EXCLUSIVE", "material_cost_basis": "NET",
        "row_version": version, "created_by": str(user),
        "created_at": now, "updated_at": now,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("role,scope,allowed", [
    ("OWNER", "tenant", True), ("ADMIN", "tenant", True),
    ("PROJECT_MANAGER", "project", False), ("OBSERVER", "project", False),
])
async def test_navigation_tax_edit_flag_uses_existing_permission_and_scope(role, scope, allowed):
    tenant, user = uuid4(), uuid4()
    actor = NS(tenant_id=tenant, user_id=user, status="active")
    context = ConstructionAccessContext(
        user, tenant, (role,), tuple(CONSTRUCTION_ROLE_PERMISSIONS[role]),
        (ConstructionGrantContext(role, scope, tenant if scope == "tenant" else uuid4()),),
    )
    result = await read_construction_navigation(
        actor=actor, context=context,
        access=NS(has_active_platform_membership=AsyncMock(return_value=True)),
        organizations=NS(require_current=AsyncMock(return_value=NS(
            tenant_id=tenant, name="Org", default_currency="EUR",
        ))),
    )
    assert result["can_edit_tax_profiles"] is allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["create", "patch", "denied", "foreign"])
async def test_http_write_uses_exact_body_key_and_existing_route(case):
    tenant, user = uuid4(), uuid4()
    item = profile(tenant, user, 1 if case != "patch" else 8)
    calls = []
    def transport(request):
        calls.append(request)
        data = {**item, "changed": True}
        if case == "foreign":
            data["tenant_id"] = str(uuid4())
        return httpx.Response(200, json={
            "data": data, "meta": {}, "request_id": "local",
        })
    session = PlatformApiClientSession(user, tenant, SecretStr("local"))
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        entry = NS(
            _presentation=AsyncMock(return_value=(session, {
                "name": "Org", "language": "uk",
                "can_edit_tax_profiles": case != "denied",
            })),
            api=NS(construction=ConstructionApiClient(http=http)),
        )
        body = {
            "name": "Primary", "client_price_tax_mode": "TAX_EXCLUSIVE",
            "material_cost_basis": "NET",
        }
        if case == "patch":
            body = {"name": "Primary", "expected_row_version": 7}
        operation = {
            "operation": "patch" if case == "patch" else "create",
            "profile_id": item["id"] if case == "patch" else None,
            "body": body, "key": "stable-local-key",
        }
        call = TaxProfileBotApi(entry).write_profile(
            request=operation, telegram_user_id=123, tenant_id=tenant,
            expected_user_id=user,
        )
        if case in ("denied", "foreign"):
            with pytest.raises(ConstructionApiClientError):
                await call
        else:
            await call
        if case == "denied":
            assert calls == []
        else:
            assert len(calls) == 1
            assert calls[0].method == ("PATCH" if case == "patch" else "POST")
            assert calls[0].url.path == (
                "/api/v1/construction/settings/tax-profiles"
                + ("/" + item["id"] if case == "patch" else "")
            )
            assert json.loads(calls[0].content) == body
            assert calls[0].headers["idempotency-key"] == "stable-local-key"


@pytest.fixture
def screen(monkeypatch):
    c = NS(tenant=uuid4(), user=uuid4())
    c.profile = profile(c.tenant, c.user, 7)
    c.common = {
        "actor_user_id": str(c.user), "language": "uk",
        "organization": "Org", "can_manage": True,
    }
    c.parent = {
        "telegram_user_id": 123, "tenant_id": str(c.tenant),
        "actor_user_id": str(c.user), "language": "uk", "menu_id": 10,
    }
    c.data = {
        handler.CONTEXT: {"telegram_user_id": 123, "tenant_id": str(c.tenant)},
        shell.VIEW: deepcopy(c.parent), "last_menu_message_id": 10,
    }
    async def update(**values):
        c.data.update(deepcopy(values))
    c.state = NS(
        get_data=AsyncMock(side_effect=lambda: deepcopy(c.data)),
        update_data=AsyncMock(side_effect=update),
        get_state=AsyncMock(return_value=None),
    )
    sender = NS(id=123, is_bot=False, language_code="uk")
    c.callback = NS(
        data="", from_user=sender, answer=AsyncMock(),
        message=NS(message_id=10, chat=NS(id=123, type="private")),
    )
    async def edited(*, callback, **kwargs):
        return NS(message_id=callback.message.message_id)
    c.sent = AsyncMock(side_effect=edited)
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", c.sent)
    c.message = NS(
        from_user=sender, chat=NS(id=123, type="private"), text="Primary",
        answer=AsyncMock(return_value=NS(message_id=20)),
    )
    async def write(**kwargs):
        operation = kwargs["request"]
        item = deepcopy(c.profile)
        item.update({
            key: value for key, value in operation["body"].items()
            if key != "expected_row_version"
        })
        item["row_version"] = (
            1 if operation["operation"] == "create"
            else operation["body"]["expected_row_version"] + 1
        )
        return {**c.common, "item": {**item, "changed": True}}
    c.api = NS(
        context=AsyncMock(side_effect=lambda **kwargs: (None, deepcopy(c.common))),
        list_profiles=AsyncMock(return_value={
            **c.common, "items": [], "next_cursor": None,
        }),
        get_profile=AsyncMock(side_effect=lambda **kwargs: {
            **c.common, "item": deepcopy(c.profile),
        }),
        write_profile=AsyncMock(side_effect=write),
    )
    c.successful_write = write
    monkeypatch.setattr(handler, "TaxProfileBotApi", lambda service: c.api)
    @asynccontextmanager
    async def entry(**kwargs):
        yield NS()
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    return c


async def open_screen(c):
    await handler.open_profiles(
        c.callback, c.state, shell.snapshot(c.data), c.parent, NS(),
    )


async def click(c, action, value=None):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = handler.cb(c.data[handler.VIEW], action, value)
    await handler.tax_callback(c.callback, c.state)


async def ready_create(c):
    await open_screen(c)
    await click(c, "new")
    assert c.data[handler.VIEW]["fields"] == {}
    await handler.name_message(c.message, c.state)
    assert c.data[handler.VIEW]["fields"] == {"name": "Primary"}
    await click(c, "mode", 0)
    assert "material_cost_basis" not in c.data[handler.VIEW]["fields"]
    await click(c, "mode", 0)
    assert c.data[handler.VIEW]["phase"] == "review"


@pytest.mark.asyncio
async def test_create_requires_both_explicit_modes_and_final_confirmation(screen):
    c = screen
    await ready_create(c)
    c.api.write_profile.assert_not_awaited()
    await click(c, "save")
    request = c.api.write_profile.await_args.kwargs["request"]
    assert request["body"] == {
        "name": "Primary", "client_price_tax_mode": "TAX_EXCLUSIVE",
        "material_cost_basis": "NET",
    }
    assert request["operation"] == "create"
    assert c.data[handler.PENDING] == {}
    assert c.data[handler.VIEW]["phase"] == "card"


@pytest.mark.asyncio
async def test_cancel_discards_unsubmitted_form(screen):
    c = screen
    await ready_create(c)
    await click(c, "list")
    c.api.write_profile.assert_not_awaited()
    assert c.data[handler.VIEW]["phase"] == "list"
    assert not c.data.get(handler.PENDING)


@pytest.mark.asyncio
async def test_edit_is_sparse_and_uses_original_reviewed_version(screen):
    c = screen
    c.api.list_profiles.return_value["items"] = [c.profile]
    await open_screen(c)
    await click(c, "open", 0)
    await click(c, "field", 2)
    await click(c, "mode", 1)
    await click(c, "save")
    request = c.api.write_profile.await_args.kwargs["request"]
    assert request["operation"] == "patch"
    assert request["profile_id"] == c.profile["id"]
    assert request["body"] == {
        "material_cost_basis": "GROSS", "expected_row_version": 7,
    }


@pytest.mark.asyncio
async def test_uncertain_retry_keeps_identical_operation_and_key(screen):
    c = screen
    await ready_create(c)
    attempts = 0
    async def uncertain(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConstructionApiClientError(status_code=504, code="api_timeout")
        return await c.successful_write(**kwargs)
    c.api.write_profile.side_effect = uncertain
    await click(c, "save")
    first = deepcopy(c.api.write_profile.await_args.kwargs["request"])
    assert c.data[handler.PENDING]
    await click(c, "retry")
    assert c.api.write_profile.await_args.kwargs["request"] == first
    assert c.api.write_profile.await_count == 2
    assert c.data[handler.PENDING] == {}


@pytest.mark.asyncio
async def test_stale_conflict_is_not_rebased_or_automatically_retried(screen):
    c = screen
    await ready_create(c)
    c.api.write_profile.side_effect = ConstructionApiClientError(
        status_code=409, code="ROW_VERSION_CONFLICT",
    )
    await click(c, "save")
    assert c.api.write_profile.await_count == 1
    assert c.data[handler.VIEW]["phase"] == "blocked"
    assert c.data[handler.PENDING] == {}


@pytest.mark.asyncio
async def test_server_capability_denial_prevents_starting_mutation(screen):
    c = screen
    await open_screen(c)
    c.common["can_manage"] = False
    await click(c, "new")
    assert c.data[handler.VIEW]["phase"] == "blocked"
    c.api.write_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_page_cannot_restore_another_context(screen):
    c = screen
    async def late(**kwargs):
        c.data[handler.CONTEXT] = {}
        return {**c.common, "items": [], "next_cursor": None}
    c.api.list_profiles.side_effect = late
    await open_screen(c)
    c.sent.assert_not_awaited()


def test_labels_and_callback_size():
    assert all(len(values) == 7 for values in LABELS.values())
    view = {"nonce": "a" * 32}
    for action, value in (("field", 2), ("mode", 1), ("retry", None)):
        assert len(handler.cb(view, action, value).encode("utf-8")) <= 64
