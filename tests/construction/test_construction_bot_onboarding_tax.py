"""Local exact profile selection and versioned HTTP behavior."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4
import json

import httpx
import pytest
from pydantic import SecretStr

from test_construction_bot_onboarding import (
    screen, wizard, progress, open_guide, click,
)
from test_construction_bot_tax_profiles import profile
from handlers.construction import construction_onboarding as guide
from handlers.construction import construction_onboarding_tax as handler
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_onboarding_tax_bot_api import OnboardingTaxBotApi
from ui.construction_onboarding_tax import LABELS


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "read_absent", "read", "changed", "noop",
    "foreign", "wrong_profile", "version", "bool_version", "denied", "actor",
])
async def test_existing_selection_http_contract(case):
    tenant, user, chosen = uuid4(), uuid4(), uuid4()
    stamp = progress(tenant)["created_at"]
    item = {
        "tenant_id": str(tenant), "row_version": 7,
        "onboarding_tax_profile_id": None,
        "created_at": stamp, "updated_at": stamp,
    }
    reading = case in ("read_absent", "read")
    if not reading:
        item.update(
            onboarding_tax_profile_id=str(chosen),
            changed=case == "changed",
            row_version=8 if case == "changed" else 7,
        )
    if case == "foreign":
        item["tenant_id"] = str(uuid4())
    elif case == "wrong_profile":
        item["onboarding_tax_profile_id"] = str(uuid4())
    elif case == "version":
        item["row_version"] = 99
    elif case == "bool_version":
        item["row_version"] = True
    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={
            "data": None if case == "read_absent" else item,
            "meta": {}, "request_id": "local",
        })

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        client = ConstructionApiClient(http=http)
        client.company_profile_capabilities = AsyncMock(return_value=NS(data={
            "tenant_id": str(tenant), "can_manage_settings": case != "denied",
            "can_manage_profile": False,
        }))
        entry = NS(
            _presentation=AsyncMock(return_value=(
                PlatformApiClientSession(user, tenant, SecretStr("local-only")),
                {"name": "Org", "language": "uk"},
            )),
            api=NS(construction=client),
        )
        binding = {
            "telegram_user_id": 123, "tenant_id": tenant,
            "expected_user_id": uuid4() if case == "actor" else user,
        }
        api = OnboardingTaxBotApi(entry)
        call = (
            api.selection(**binding) if reading else
            api.select(profile_id=chosen, expected_row_version=7, **binding)
        )
        bad = case in (
            "foreign", "wrong_profile", "version", "bool_version", "denied", "actor",
        )
        if bad:
            with pytest.raises(ConstructionApiClientError) as caught:
                await call
            assert caught.value.status_code == (
                403 if case in ("denied", "actor") else 502
            )
        else:
            result = await call
            if case == "read_absent":
                assert result["item"] is None
            elif not reading:
                assert result["item"]["onboarding_tax_profile_id"] == str(chosen)

    if case in ("denied", "actor"):
        assert calls == []
    else:
        assert len(calls) == 1
        assert calls[0].url.path == (
            "/api/v1/construction/settings/onboarding/tax-profile"
        )
        assert not calls[0].url.query
        assert calls[0].method == ("GET" if reading else "PATCH")
        assert "idempotency-key" not in calls[0].headers
        if not reading:
            assert json.loads(calls[0].content) == {
                "tax_profile_id": str(chosen), "expected_row_version": 7,
            }


@pytest.fixture
def tax_screen(wizard, monkeypatch):
    c = wizard
    c.progress = progress(c.tenant, started=True, step="TAXES")
    c.selection = {
        "tenant_id": str(c.tenant), "row_version": 7,
        "onboarding_tax_profile_id": None,
        "created_at": c.progress["created_at"], "updated_at": c.progress["updated_at"],
    }
    c.profiles = [profile(c.tenant, c.user), profile(c.tenant, c.user)]
    c.profiles[0]["name"] = "First tax profile"
    c.profiles[1]["name"] = "Second tax profile"
    c.by_id = {item["id"]: deepcopy(item) for item in c.profiles}
    c.page, c.next_cursor = deepcopy(c.profiles), None

    async def get_profile(**kwargs):
        return {**c.common, "item": deepcopy(c.by_id[str(kwargs["profile_id"])])}

    async def select(**kwargs):
        c.selection.update(
            onboarding_tax_profile_id=str(kwargs["profile_id"]),
            row_version=kwargs["expected_row_version"] + 1,
        )
        c.progress["row_version"] = c.selection["row_version"]
        return {
            **c.common,
            "item": {**deepcopy(c.selection), "changed": True},
        }

    c.tax_api = NS(
        read=c.guide_api.read,
        selection=AsyncMock(side_effect=lambda **kwargs: {
            **c.common, "item": deepcopy(c.selection),
        }),
        list_profiles=AsyncMock(side_effect=lambda **kwargs: {
            **c.common, "items": deepcopy(c.page), "next_cursor": c.next_cursor,
        }),
        get_profile=AsyncMock(side_effect=get_profile),
        select=AsyncMock(side_effect=select),
    )
    monkeypatch.setattr(handler, "OnboardingTaxBotApi", lambda service: c.tax_api)

    @asynccontextmanager
    async def entry(**kwargs):
        yield NS()

    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    return c


async def open_profiles(c):
    await open_guide(c)
    await click(c, "tax")


@pytest.mark.asyncio
async def test_list_never_selects_first_profile_or_completes_step(tax_screen):
    c = tax_screen
    await open_profiles(c)
    view = c.data[guide.VIEW]
    assert view["phase"] == "tax_list"
    assert "selected_profile_id" not in view
    assert c.profiles[0]["name"] not in repr(view)
    assert c.profiles[1]["name"] not in repr(view)
    c.tax_api.select.assert_not_awaited()
    c.guide_api.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_choice_requires_review_and_does_not_complete_step(tax_screen):
    c = tax_screen
    await open_profiles(c)
    chosen = c.profiles[0]["id"]
    c.page.reverse()
    await click(c, "taxpick", 0)
    view = c.data[guide.VIEW]
    assert view["phase"] == "tax_review"
    assert view["selected_profile_id"] == chosen
    assert c.tax_api.get_profile.await_args.kwargs["profile_id"] == UUID(chosen)
    c.tax_api.select.assert_not_awaited()
    old_callback = guide.cb(view, "taxsave")
    await click(c, "taxsave")
    args = c.tax_api.select.await_args.kwargs
    assert args["profile_id"] == UUID(chosen)
    assert args["expected_row_version"] == 7
    assert args["expected_user_id"] == c.user
    assert c.data[guide.VIEW]["phase"] == "tax_done"
    assert c.progress["step_states"]["TAXES"] == "PENDING"
    c.guide_api.write.assert_not_awaited()
    c.callback.data = old_callback
    await guide.onboarding_callback(c.callback, c.state)
    c.tax_api.select.assert_awaited_once()
    await click(c, "refresh")
    assert c.data[guide.VIEW]["progress"]["row_version"] == 8
    assert c.data[guide.VIEW]["progress"]["step_states"]["TAXES"] == "PENDING"


@pytest.mark.asyncio
async def test_cancel_does_not_select_or_create_rates(tax_screen):
    c = tax_screen
    await open_profiles(c)
    await click(c, "taxpick", 1)
    await click(c, "tax")
    assert c.data[guide.VIEW]["phase"] == "tax_list"
    c.tax_api.select.assert_not_awaited()
    c.guide_api.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_pagination_uses_the_original_server_cursor(tax_screen):
    c = tax_screen
    c.next_cursor = "opaque-server-cursor"
    await open_profiles(c)
    c.page = []
    c.next_cursor = None
    await click(c, "taxnext")
    assert c.tax_api.list_profiles.await_args.kwargs["cursor"] == "opaque-server-cursor"
    assert c.data[guide.VIEW]["profile_ids"] == []
    c.tax_api.select.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_catalog_has_no_implicit_selection(tax_screen):
    c = tax_screen
    c.page = []
    await open_profiles(c)
    assert c.data[guide.VIEW]["profile_ids"] == []
    await click(c, "taxpick", 0)
    c.tax_api.get_profile.assert_not_awaited()
    c.tax_api.select.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_settings_before_review_are_not_rebased(tax_screen):
    c = tax_screen
    await open_profiles(c)
    c.progress["row_version"] = 9
    c.selection["row_version"] = 9
    await click(c, "taxpick", 0)
    assert c.data[guide.VIEW]["phase"] == "blocked"
    c.tax_api.get_profile.assert_not_awaited()
    c.tax_api.select.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [
    (409, "ROW_VERSION_CONFLICT"), (504, "api_timeout"),
])
async def test_failed_save_requires_refresh_without_retry_or_rebase(tax_screen, status, code):
    c = tax_screen
    await open_profiles(c)
    await click(c, "taxpick", 0)
    c.selection["row_version"] = 99
    c.tax_api.select.side_effect = ConstructionApiClientError(
        status_code=status, code=code,
    )
    await click(c, "taxsave")
    assert c.tax_api.select.await_args.kwargs["expected_row_version"] == 7
    assert c.data[guide.VIEW]["phase"] == "blocked"
    assert "selected_profile_id" not in c.data[guide.VIEW]
    await click(c, "taxsave")
    c.tax_api.select.assert_awaited_once()
    c.guide_api.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_page_cannot_restore_another_organization(tax_screen):
    c = tax_screen
    await open_guide(c)
    c.sent.reset_mock()

    async def late(**kwargs):
        c.data[guide.CONTEXT]["tenant_id"] = str(uuid4())
        return {**c.common, "items": deepcopy(c.page), "next_cursor": None}

    c.tax_api.list_profiles.side_effect = late
    await click(c, "tax")
    c.sent.assert_not_awaited()
    c.tax_api.select.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_step_group_and_stale_menu_cannot_select(tax_screen):
    c = tax_screen
    c.progress["current_step"] = "COMPANY"
    await open_guide(c)
    await click(c, "tax")
    c.tax_api.selection.assert_not_awaited()
    c.progress["current_step"] = "TAXES"
    await click(c, "refresh")
    c.callback.message.chat.type = "group"
    await click(c, "tax")
    c.callback.message.chat.type = "private"
    c.callback.message.message_id = 999
    c.callback.data = guide.cb(c.data[guide.VIEW], "tax")
    await guide.onboarding_callback(c.callback, c.state)
    c.tax_api.selection.assert_not_awaited()
    c.tax_api.select.assert_not_awaited()


def test_labels_and_callbacks_are_bounded():
    assert all(len(values) == 7 for values in LABELS.values())
    for index in range(10):
        assert len(guide.cb({"nonce": "a" * 32}, "taxpick", index).encode("utf-8")) <= 64
