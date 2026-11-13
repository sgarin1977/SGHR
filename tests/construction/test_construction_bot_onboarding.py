"""Local onboarding transport and explicit Telegram progress operations."""
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

from test_construction_bot_tax_profiles import screen
from handlers.construction import construction_onboarding as handler
from handlers.construction import construction_stage2_settings as shell
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_onboarding_bot_api import (
    OnboardingBotApi, STEPS, SKIPPABLE,
)
from ui.construction_onboarding import LABELS


def progress(tenant, *, started=False, version=7, step="COMPANY"):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "tenant_id": str(tenant), "row_version": version,
        "current_step": step if started else None,
        "step_states": {key: "PENDING" for key in STEPS} if started else None,
        "created_at": now if version is not None else None,
        "updated_at": now if version is not None else None,
        "started": started, "is_complete": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, 7])
async def test_start_uses_explicit_original_version_and_no_key(version):
    tenant, user = uuid4(), uuid4()
    calls = []
    data = {
        **progress(tenant, started=True, version=1 if version is None else version + 1),
        "changed": True, "settings_created": version is None,
    }

    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={
            "data": data, "meta": {}, "request_id": "local",
        })

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        client = ConstructionApiClient(http=http)
        client.company_profile_capabilities = AsyncMock(return_value=NS(data={
            "tenant_id": str(tenant), "can_manage_settings": True,
            "can_manage_profile": False,
        }))
        entry = NS(
            _presentation=AsyncMock(return_value=(
                PlatformApiClientSession(user, tenant, SecretStr("local-only")),
                {"name": "Org", "language": "uk"},
            )),
            api=NS(construction=client),
        )
        await OnboardingBotApi(entry).write(
            telegram_user_id=123, tenant_id=tenant, expected_user_id=user,
            request={"action": "start", "step": None, "expected_row_version": version},
        )
    assert len(calls) == 1
    assert calls[0].method == "POST"
    assert calls[0].url.path == "/api/v1/construction/settings/onboarding/start"
    assert not calls[0].url.query
    assert json.loads(calls[0].content) == {"expected_row_version": version}
    assert "idempotency-key" not in calls[0].headers


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["tenant", "version", "terminal", "authority"])
async def test_invalid_write_response_and_missing_authority_are_rejected(fault):
    tenant, user = uuid4(), uuid4()
    data = progress(tenant, started=True, version=8, step="LOCALIZATION")
    data.update(changed=True, settings_created=False)
    data["step_states"]["COMPANY"] = "COMPLETED"
    if fault == "tenant":
        data["tenant_id"] = str(uuid4())
    elif fault == "version":
        data["row_version"] = 9
    elif fault == "terminal":
        data["step_states"]["COMPANY"] = "PENDING"
    client = NS(
        company_profile_capabilities=AsyncMock(return_value=NS(data={
            "tenant_id": str(tenant), "can_manage_settings": fault != "authority",
        })),
        _request=AsyncMock(return_value=NS(data=data, meta={})),
    )
    entry = NS(
        _presentation=AsyncMock(return_value=(
            PlatformApiClientSession(user, tenant, SecretStr("local-only")),
            {"name": "Org", "language": "uk"},
        )),
        api=NS(construction=client),
    )
    with pytest.raises(ConstructionApiClientError) as caught:
        await OnboardingBotApi(entry).write(
            telegram_user_id=123, tenant_id=tenant, expected_user_id=user,
            request={"action": "complete", "step": "COMPANY", "expected_row_version": 7},
        )
    assert caught.value.status_code == (403 if fault == "authority" else 502)
    if fault == "authority":
        client._request.assert_not_awaited()


@pytest.fixture
def wizard(screen, monkeypatch):
    c = screen
    c.progress = progress(c.tenant)
    c.guide_api = NS(
        read=AsyncMock(side_effect=lambda **kwargs: {
            **c.common, "item": deepcopy(c.progress),
        }),
        write=AsyncMock(return_value={
            **c.common,
            "item": {
                **progress(c.tenant, started=True, version=8),
                "changed": True, "settings_created": False,
            },
        }),
    )
    monkeypatch.setattr(handler, "OnboardingBotApi", lambda service: c.guide_api)
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", c.sent)

    @asynccontextmanager
    async def entry(**kwargs):
        yield NS()

    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    return c


async def open_guide(c):
    await handler.open_guide(
        c.callback, c.state, shell.snapshot(c.data), c.parent, NS(),
    )


async def click(c, action, index=None):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = handler.cb(c.data[handler.VIEW], action, index)
    await handler.onboarding_callback(c.callback, c.state)


@pytest.mark.asyncio
async def test_start_requires_confirmation_and_duplicate_callback_cannot_write(wizard):
    c = wizard
    await open_guide(c)
    c.guide_api.write.assert_not_awaited()
    await click(c, "start")
    assert c.data[handler.VIEW]["phase"] == "review"
    c.guide_api.write.assert_not_awaited()
    old_callback = handler.cb(c.data[handler.VIEW], "confirm")
    await click(c, "confirm")
    assert c.guide_api.write.await_args.kwargs["request"] == {
        "action": "start", "step": None, "expected_row_version": 7,
    }
    c.callback.data = old_callback
    await handler.onboarding_callback(c.callback, c.state)
    c.guide_api.write.assert_awaited_once()


@pytest.mark.asyncio
async def test_reopen_resumes_saved_step_without_reset_or_completion(wizard):
    c = wizard
    c.progress = progress(c.tenant, started=True, step="TAXES")
    await open_guide(c)
    assert c.data[handler.VIEW]["progress"]["current_step"] == "TAXES"
    assert set(c.data[handler.VIEW]["progress"]["step_states"].values()) == {"PENDING"}
    c.guide_api.write.assert_not_awaited()
    await click(c, "refresh")
    c.guide_api.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_writes_nothing(wizard):
    c = wizard
    await open_guide(c)
    await click(c, "start")
    await click(c, "refresh")
    assert c.data[handler.VIEW]["phase"] == "overview"
    c.guide_api.write.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("step", STEPS)
async def test_skip_is_available_only_for_the_two_approved_steps(wizard, step):
    c = wizard
    c.progress = progress(c.tenant, started=True, step=step)
    await open_guide(c)
    rows = c.sent.await_args.kwargs["reply_markup"].inline_keyboard
    assert any(":skip:" in button.callback_data for row in rows for button in row) == (
        step in SKIPPABLE
    )
    await click(c, "skip")
    if step in SKIPPABLE:
        assert c.data[handler.VIEW]["request"] == {
            "action": "skip", "step": step, "expected_row_version": 7,
        }
    else:
        assert c.data[handler.VIEW]["phase"] == "overview"
    c.guide_api.write.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("code,status", [
    ("ONBOARDING_STEP_NOT_READY", 409),
    ("ROW_VERSION_CONFLICT", 409),
    ("api_timeout", 504),
])
async def test_failed_or_uncertain_completion_never_rebases_or_retries(wizard, code, status):
    c = wizard
    c.progress = progress(c.tenant, started=True)
    await open_guide(c)
    await click(c, "complete")
    c.progress["row_version"] = 99
    c.guide_api.write.side_effect = ConstructionApiClientError(
        status_code=status, code=code,
    )
    await click(c, "confirm")
    assert c.guide_api.write.await_args.kwargs["request"] == {
        "action": "complete", "step": "COMPANY", "expected_row_version": 7,
    }
    assert c.data[handler.VIEW]["phase"] == "blocked"
    assert "request" not in c.data[handler.VIEW]
    await click(c, "confirm")
    c.guide_api.write.assert_awaited_once()
    await click(c, "refresh")
    assert c.data[handler.VIEW]["progress"]["row_version"] == 99
    c.guide_api.write.assert_awaited_once()


@pytest.mark.asyncio
async def test_late_read_cannot_restore_another_tenant(wizard):
    c = wizard

    async def late(**kwargs):
        c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
        return {**c.common, "item": deepcopy(c.progress)}

    c.guide_api.read.side_effect = late
    await open_guide(c)
    c.sent.assert_not_awaited()
    assert handler.VIEW not in c.data


@pytest.mark.asyncio
async def test_group_and_stale_menu_cannot_mutate(wizard):
    c = wizard
    await open_guide(c)
    c.guide_api.read.reset_mock()
    c.callback.message.chat.type = "group"
    await click(c, "start")
    c.callback.message.chat.type = "private"
    c.callback.message.message_id = 999
    c.callback.data = handler.cb(c.data[handler.VIEW], "start")
    await handler.onboarding_callback(c.callback, c.state)
    c.guide_api.read.assert_not_awaited()
    c.guide_api.write.assert_not_awaited()


def test_all_labels_and_callbacks_are_bounded():
    assert all(len(values) == 7 for values in LABELS.values())
    for index in range(7):
        assert len(handler.cb({"nonce": "a" * 32}, "step", index).encode("utf-8")) <= 64
