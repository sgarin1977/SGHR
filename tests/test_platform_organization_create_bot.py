"""Organization wizard behavior and local end-to-end API transport."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from handlers.construction import construction_suite as suite
from handlers import platform_organization_create as handler
from services.construction.construction_api_client import ConstructionApiClientError
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from services.platform_bot_api_client import PlatformBotApiClient
from test_platform_organization_bootstrap import case, counts
from test_platform_organization_creation_policy import release_case
from test_platform_construction_onboarding import onboarding_case, assignment_counts
from test_platform_organization_create_http import creation_http


@pytest.fixture
def wizard(monkeypatch):
    c = NS(user=uuid4(), tenant=uuid4(), number=1, screens=[])
    c.data = {
        handler.CONTEXT: {"telegram_user_id": 123, "org_after": None},
        "last_menu_message_id": 1,
    }
    async def update(**values):
        c.data.update(values)
    c.state = NS(
        get_data=AsyncMock(side_effect=lambda: c.data),
        update_data=AsyncMock(side_effect=update),
        get_state=AsyncMock(return_value=None),
    )
    sender = NS(id=123, is_bot=False, language_code="uk")
    async def answer(text, **kwargs):
        return await emit(text=text, reply_markup=kwargs.get("reply_markup"))
    async def emit(*, text, reply_markup=None, callback=None):
        c.number += 1
        c.screens.append((text, reply_markup))
        return NS(message_id=c.number)
    c.callback = NS(
        data="", from_user=sender, answer=AsyncMock(),
        message=NS(
            message_id=1, chat=NS(id=123, type="private"), answer=answer,
        ),
    )
    c.message = NS(
        from_user=sender, chat=NS(id=123, type="private"),
        text="", answer=answer,
    )
    c.service = NS(
        organization_form=AsyncMock(return_value={
            "actor_user_id": str(c.user), "language": "uk",
            "languages": ["uk", "en", "de"],
        }),
        create_organization=AsyncMock(return_value={"item": {
            "id": str(c.tenant), "name": "Organization",
        }}),
        organizations=AsyncMock(return_value={
            "items": [], "next_after": None, "language": "uk",
        }),
    )
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.service
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    monkeypatch.setattr(suite, "construction_bot_api_entry", entry)
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", emit)
    monkeypatch.setattr(suite, "edit_or_replace_menu_message", emit)
    return c


async def open_form(c):
    row = await handler.organization_entry_button(
        c.state, telegram_user_id=123, language="uk",
    )
    c.callback.data = row[0].callback_data
    c.callback.message.message_id = c.data["last_menu_message_id"]
    await handler.organization_callback(c.callback, c.state)
    assert c.data[handler.FORM]["phase"] == "name"


async def click(c, action):
    c.callback.data = handler.cb(c.data[handler.FORM], action)
    c.callback.message.message_id = c.data["last_menu_message_id"]
    await handler.organization_callback(c.callback, c.state)


async def text(c, value):
    c.message.text = value
    await handler.organization_message(c.message, c.state)


async def review(c):
    await open_form(c)
    await text(c, "Organization")
    await click(c, "lang_uk")
    await text(c, "eur")
    assert c.data[handler.FORM]["phase"] == "review"


@pytest.mark.asyncio
async def test_empty_organization_selector_has_creation_button(wizard):
    c = wizard
    c.callback.data = "CS_ORGS"
    await suite.construction_callback(c.callback, c.state)
    rows = c.screens[-1][1].inline_keyboard
    assert any(button.callback_data.startswith("OC:new:") for row in rows for button in row)
    assert "tenant_id" not in c.data[handler.CONTEXT]


@pytest.mark.asyncio
async def test_form_without_membership_requires_explicit_confirmation(wizard):
    c = wizard
    await review(c)
    c.service.create_organization.assert_not_awaited()
    assert c.data[handler.FORM]["payload"] == {
        "name": "Organization", "language": "uk", "currency": "EUR",
    }
    await click(c, "submit")
    c.service.create_organization.assert_awaited_once()
    assert c.data[handler.FORM]["phase"] == "done"
    assert "tenant_id" not in c.data[handler.CONTEXT]
    rows = c.screens[-1][1].inline_keyboard
    assert any(
        button.callback_data == "CS_ORG:" + c.tenant.hex
        for row in rows for button in row
    )
    await click(c, "submit")
    c.service.create_organization.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_before_submission_creates_nothing(wizard):
    c = wizard
    await review(c)
    await click(c, "cancel")
    assert c.data[handler.FORM] == {}
    c.service.create_organization.assert_not_awaited()


@pytest.mark.asyncio
async def test_timeout_retry_preserves_body_key_and_creator(wizard):
    c = wizard
    await review(c)
    success = c.service.create_organization.return_value
    c.service.create_organization.side_effect = [
        ConstructionApiClientError(status_code=504, code="api_timeout"), success,
    ]
    await click(c, "submit")
    first = c.service.create_organization.await_args.kwargs
    await click(c, "edit")
    assert c.service.create_organization.await_count == 1
    await click(c, "submit")
    assert c.service.create_organization.await_args.kwargs == first
    assert c.data[handler.FORM]["phase"] == "done"


@pytest.mark.asyncio
async def test_navigation_and_resume_do_not_automatically_resubmit(wizard):
    c = wizard
    await review(c)
    c.service.create_organization.side_effect = ConstructionApiClientError(
        status_code=504, code="api_timeout",
    )
    await click(c, "submit")
    saved = deepcopy(c.data[handler.FORM])
    await handler.suspend_organization_form(c.state, telegram_user_id=123)
    assert c.data[handler.FORM]["menu_id"] is None
    row = await handler.organization_entry_button(
        c.state, telegram_user_id=123, language="uk",
    )
    c.callback.data = row[0].callback_data
    c.callback.message.message_id = c.data["last_menu_message_id"]
    await handler.organization_callback(c.callback, c.state)
    assert c.data[handler.FORM]["id"] == saved["id"]
    assert c.data[handler.FORM]["payload"] == saved["payload"]
    assert c.data[handler.FORM]["phase"] == "pending"
    assert c.service.create_organization.await_count == 1


@pytest.mark.asyncio
async def test_changed_canonical_creator_cannot_resume_pending_body(wizard):
    c = wizard
    await review(c)
    c.service.create_organization.side_effect = ConstructionApiClientError(
        status_code=504, code="api_timeout",
    )
    await click(c, "submit")
    saved = deepcopy(c.data[handler.FORM])
    await handler.suspend_organization_form(c.state, telegram_user_id=123)
    c.service.organization_form.return_value["actor_user_id"] = str(uuid4())
    row = await handler.organization_entry_button(
        c.state, telegram_user_id=123, language="uk",
    )
    c.callback.data = row[0].callback_data
    c.callback.message.message_id = c.data["last_menu_message_id"]
    await handler.organization_callback(c.callback, c.state)
    assert c.service.create_organization.await_count == 1
    assert c.data[handler.FORM]["actor_user_id"] == saved["actor_user_id"]
    assert c.data[handler.FORM]["menu_id"] is None
    assert "Organization" not in c.screens[-1][0]


@pytest.mark.asyncio
async def test_known_validation_rejection_can_start_explicit_new_intent(wizard):
    c = wizard
    await review(c)
    old_id = c.data[handler.FORM]["id"]
    c.service.create_organization.side_effect = ConstructionApiClientError(
        status_code=422, code="validation_error",
    )
    await click(c, "submit")
    assert c.data[handler.FORM]["phase"] == "rejected"
    await click(c, "edit")
    assert c.data[handler.FORM]["id"] != old_id
    assert c.data[handler.FORM]["attempted_at"] is None
    assert c.data[handler.FORM]["phase"] == "name"
    assert c.service.create_organization.await_count == 1


@pytest.mark.asyncio
async def test_expired_pending_request_is_not_resubmitted(wizard):
    c = wizard
    await review(c)
    c.data[handler.FORM]["attempted_at"] = 1.0
    c.data[handler.FORM]["phase"] = "pending"
    await click(c, "submit")
    c.service.create_organization.assert_not_awaited()
    await click(c, "finish")
    assert c.data[handler.FORM] == {}


@pytest.mark.asyncio
async def test_input_and_callbacks_are_isolated_from_legacy_and_stale_menus(wizard):
    c = wizard
    await open_form(c)
    check = handler.OrganizationFormInput()
    c.message.text = "Organization"
    assert await check(c.message, c.state)
    c.state.get_state.return_value = "legacy:input"
    assert not await check(c.message, c.state)
    c.state.get_state.return_value = None
    old = deepcopy(c.data[handler.FORM])
    await text(c, "Organization")
    c.callback.data = handler.cb(old, "cancel")
    c.callback.message.message_id = old["menu_id"]
    await handler.organization_callback(c.callback, c.state)
    assert c.data[handler.FORM]["phase"] == "language"
    c.data[handler.CONTEXT] = {"telegram_user_id": 123, "tenant_id": str(c.tenant)}
    assert not await check(c.message, c.state)
    c.service.create_organization.assert_not_awaited()


@pytest.mark.asyncio
async def test_main_menu_retains_only_the_pending_attempt(wizard):
    c = wizard
    await review(c)
    c.data[handler.FORM]["attempted_at"] = 123.0
    c.data[handler.FORM]["phase"] = "pending"
    previous = deepcopy(c.data)
    c.data.clear()
    await handler.retain_organization_attempt(c.state, previous, 123)
    assert set(c.data) == {handler.FORM}
    assert c.data[handler.FORM]["menu_id"] is None
    assert c.data[handler.FORM]["id"] == previous[handler.FORM]["id"]


@pytest.mark.asyncio
async def test_navigation_middleware_does_not_change_unrelated_legacy_state(wizard):
    c = wizard
    c.data = {"legacy": {"value": "preserved"}}
    next_handler = AsyncMock(return_value="handled")
    c.callback.data = "M_SUITES"
    assert await handler.organization_navigation_middleware(
        next_handler, c.callback, {"state": c.state},
    ) == "handled"
    assert c.data == {"legacy": {"value": "preserved"}}
    next_handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_wizard_creates_through_real_local_platform_api(wizard, creation_http, monkeypatch):
    c, api_case = wizard, creation_http
    @asynccontextmanager
    async def entry(**kwargs):
        async with PlatformBotApiClient(
            application=api_case.app, context=api_case.context,
        ) as api:
            yield ConstructionBotApiEntry(
                api=api, context=api_case.context, sessions=None,
            )
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    await review(c)
    assert counts(api_case) == (0, 0, 0, 0, 0, 0)
    await click(c, "submit")
    assert c.data[handler.FORM]["phase"] == "done"
    UUID(c.data[handler.FORM]["tenant_id"])
    assert counts(api_case) == (1, 1, 1, 1, 1, 0)
    assert assignment_counts(api_case) == (1, 1, 1)
