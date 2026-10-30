"""Construction settings client and private bot workflow regression."""
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

from handlers.construction import construction_company_settings as handler
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_company_settings_api import ConstructionCompanySettingsApi, FIELDS, LOGO_FIELD
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.construction_company_settings import LABELS, settings_text

PNG = b"\x89PNG\r\n\x1a\n" + b"local-test"


@pytest.fixture
def settings():
    now = datetime.now(timezone.utc).isoformat()
    return {
        "tenant_id": str(uuid4()), "display_name_override": "PRIVATE_CONSTRUCTION_NAME",
        "phone_override": "+12025550123", "email_override": "construction@example.test",
        "website_override": "https://example.test", "document_header_text": "PRIVATE_HEADER",
        "document_footer_text": None, "accent_color": "#AABBCC",
        "logo_file_id_override": str(uuid4()),
        "created_at": now, "updated_at": now, "row_version": 7,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [False, True])
async def test_registered_nullable_settings_contract(settings, missing):
    session = PlatformApiClientSession(uuid4(), __import__("uuid").UUID(settings["tenant_id"]), SecretStr("local"))
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"data": None if missing else settings, "meta": {}, "request_id": "local"},
        )),
    ) as http:
        item = await ConstructionCompanySettingsApi(ConstructionApiClient(http=http)).read(session=session)
    if missing:
        assert item is None
    else:
        assert isinstance(item, dict)
        assert set(item) == set(settings)
        timestamps = {"created_at", "updated_at"}
        assert {
            key: value for key, value in item.items() if key not in timestamps
        } == {
            key: value for key, value in settings.items() if key not in timestamps
        }
        for field in timestamps:
            actual = datetime.fromisoformat(item[field].replace("Z", "+00:00"))
            expected = datetime.fromisoformat(settings[field].replace("Z", "+00:00"))
            assert actual.tzinfo is not None and actual.utcoffset() is not None
            assert expected.tzinfo is not None and expected.utcoffset() is not None
            assert actual == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("creating", [False, True])
async def test_settings_write_is_sparse_and_never_targets_platform_profile(settings, creating):
    calls = []
    session = PlatformApiClientSession(uuid4(), __import__("uuid").UUID(settings["tenant_id"]), SecretStr("local"))
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={"data": settings, "meta": {}, "request_id": "local"})
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(transport),
    ) as http:
        await ConstructionCompanySettingsApi(ConstructionApiClient(http=http)).write(
            session=session, changes={"document_header_text": "  Header\nLine two  "},
            expected_row_version=None if creating else 7,
        )
    assert calls[0].url.path == "/api/v1/construction/settings/company"
    assert calls[0].method == ("POST" if creating else "PATCH")
    expected = {"document_header_text": "  Header\nLine two  "}
    if not creating:
        expected["expected_row_version"] = 7
    assert json.loads(calls[0].content) == expected
    assert "idempotency-key" not in calls[0].headers


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "version", "extra", "timestamp"])
async def test_bad_or_foreign_settings_response_is_rejected(settings, failure):
    original_tenant = settings["tenant_id"]
    if failure == "tenant":
        settings["tenant_id"] = str(uuid4())
    elif failure == "version":
        settings["row_version"] = True
    elif failure == "extra":
        settings["legal_name"] = "Unsupported"
    else:
        settings["updated_at"] = "2026-10-26T12:00:00"
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"data": settings, "meta": {}, "request_id": "local"},
        )),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionCompanySettingsApi(ConstructionApiClient(http=http)).read(
                session=PlatformApiClientSession(uuid4(), __import__("uuid").UUID(original_tenant), SecretStr("local")),
            )


@pytest.mark.parametrize("changes", [
    {"legal_name": "Forbidden"}, {"tenant_id": str(uuid4())},
    {"signatory_name": "Forbidden"}, {"email_override": True},
    {"logo_file_id_override": "https://example.test/logo.png"}, {},
])
def test_settings_client_rejects_fields_outside_approved_overrides(changes):
    with pytest.raises(ValueError):
        ConstructionCompanySettingsApi.validate_changes(changes)


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [False, True])
async def test_settings_permission_is_independent_from_platform_profile_permission(settings, allowed):
    user, tenant = uuid4(), __import__("uuid").UUID(settings["tenant_id"])
    api = NS(
        company_profile_capabilities=AsyncMock(return_value=NS(data={
            "can_manage_settings": allowed, "can_manage_profile": not allowed,
        })),
        _request=AsyncMock(return_value=NS(data=settings, meta={}, request_id="local")),
    )
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local")),
        {"name": "Organization", "language": "uk"},
    ))
    if allowed:
        await entry.save_construction_company_settings(
            telegram_user_id=123, tenant_id=tenant, expected_user_id=user,
            changes={"phone_override": "New phone"}, expected_row_version=6,
        )
        assert api._request.await_args.args == ("PATCH", "construction/settings/company")
    else:
        with pytest.raises(ConstructionApiClientError):
            await entry.save_construction_company_settings(
                telegram_user_id=123, tenant_id=tenant, expected_user_id=user,
                changes={"phone_override": "New phone"}, expected_row_version=6,
            )
        api._request.assert_not_awaited()


@pytest.mark.asyncio
async def test_absent_settings_reset_is_authorized_read_noop_without_creation(settings):
    user, tenant = uuid4(), __import__("uuid").UUID(settings["tenant_id"])
    api = NS(
        company_profile_capabilities=AsyncMock(return_value=NS(data={"can_manage_settings": True})),
        _request=AsyncMock(return_value=NS(data=None, meta={}, request_id="local")),
    )
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local")),
        {"name": "Organization", "language": "uk"},
    ))
    receipt = await entry.save_construction_company_settings(
        telegram_user_id=123, tenant_id=tenant, expected_user_id=user,
        changes={"phone_override": None}, expected_row_version=None,
    )
    assert receipt["item"] is None and receipt["changed"] is False
    assert api._request.await_count == 1
    assert api._request.await_args.args == ("GET", "construction/settings/company")


@pytest.fixture
def screen(settings, monkeypatch):
    c = NS(user=uuid4(), tenant=__import__("uuid").UUID(settings["tenant_id"]), settings=settings, file=uuid4())
    c.data = {
        handler.CONTEXT: {"telegram_user_id": 123, "tenant_id": str(c.tenant)},
        handler.PARENT: {
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
        data="CST:open:source", from_user=sender, answer=AsyncMock(),
        message=NS(
            message_id=1, chat=NS(id=123, type="private"),
            answer=AsyncMock(), answer_document=AsyncMock(),
        ),
    )
    c.message = NS(
        from_user=sender, chat=NS(id=123, type="private"), text="New private override",
        photo=None, document=None, answer=AsyncMock(return_value=NS(message_id=1)),
    )
    c.result = {
        "item": deepcopy(settings), "actor_user_id": str(c.user),
        "organization": "Organization", "language": "uk",
        "can_manage": True, "can_upload_logo": True, "can_preview_logo": True,
        "max_bytes": 1024, "mime_types": ("image/png",),
    }
    c.api = NS(
        construction_company_settings=AsyncMock(side_effect=lambda **kwargs: deepcopy(c.result)),
        save_construction_company_settings=AsyncMock(return_value={
            "item": {**settings, "row_version": 8},
            "actor_user_id": str(c.user), "changed": True,
        }),
        upload_company_logo=AsyncMock(return_value={
            "item": {"id": str(c.file), "mime_type": "image/png", "size_bytes": len(PNG)},
            "actor_user_id": str(c.user),
        }),
        download_construction_company_settings_logo=AsyncMock(return_value={
            "id": settings[LOGO_FIELD], "content": PNG, "filename": "organization-logo.png",
            "actor_user_id": str(c.user),
        }),
    )
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.api
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    c.sent = AsyncMock(return_value=NS(message_id=1))
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", c.sent)
    async def download(path, *, destination):
        destination.write(PNG)
        destination.seek(0)
    c.bot = NS(
        id=999, get_file=AsyncMock(return_value=NS(file_path="test/logo.png", file_size=len(PNG))),
        download_file=AsyncMock(side_effect=download),
    )
    return c


async def action(c, name, index=None):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = handler.cb(c.data[handler.VIEW], name, index)
    await handler.settings_callback(c.callback, c.state, bot=c.bot)


async def field(c, name):
    await action(c, "field", FIELDS.index(name))


async def review(c, name="phone_override", text="New private override"):
    await handler.settings_callback(c.callback, c.state, bot=c.bot)
    await field(c, name)
    c.message.text = text
    await handler.settings_message(c.message, c.state, bot=c.bot)
    assert c.data[handler.VIEW]["phase"] == "review"


@pytest.mark.asyncio
async def test_read_view_contains_only_selectors(screen):
    c = screen
    await handler.settings_callback(c.callback, c.state, bot=c.bot)
    assert c.settings["display_name_override"] in c.sent.await_args.kwargs["text"]
    assert c.settings["display_name_override"] not in repr(c.data)
    assert c.settings["phone_override"] not in repr(c.data)


@pytest.mark.asyncio
async def test_update_sends_only_edited_field_and_original_version(screen):
    c = screen
    await review(c)
    old_callback = handler.cb(c.data[handler.VIEW], "save")
    await action(c, "save")
    args = c.api.save_construction_company_settings.await_args.kwargs
    assert args["changes"] == {"phone_override": "New private override"}
    assert args["expected_row_version"] == 7
    assert args["expected_user_id"] == c.user
    assert "New private override" not in repr(c.data)
    assert "changes" not in c.data[handler.VIEW]
    c.callback.data = old_callback
    await handler.settings_callback(c.callback, c.state, bot=c.bot)
    assert c.api.save_construction_company_settings.await_count == 1


@pytest.mark.asyncio
async def test_new_settings_do_not_require_company_profile_creation(screen):
    c = screen
    c.result["item"] = None
    await review(c, "display_name_override", "Construction name")
    await action(c, "save")
    args = c.api.save_construction_company_settings.await_args.kwargs
    assert args["expected_row_version"] is None
    assert args["changes"] == {"display_name_override": "Construction name"}


@pytest.mark.asyncio
async def test_reset_uses_null_and_preserves_version(screen):
    c = screen
    await handler.settings_callback(c.callback, c.state, bot=c.bot)
    await field(c, "email_override")
    await action(c, "reset")
    await action(c, "save")
    args = c.api.save_construction_company_settings.await_args.kwargs
    assert args["changes"] == {"email_override": None}
    assert args["expected_row_version"] == 7


@pytest.mark.asyncio
async def test_document_text_whitespace_is_preserved_and_review_is_not_truncated(screen):
    c = screen
    value = "  Header\n" + "X" * 4000 + "\nFooter  "
    await review(c, "document_header_text", value)
    assert c.data[handler.VIEW]["changes"] == {"document_header_text": value}
    rendered = "".join(call.args[0] for call in c.message.answer.await_args_list)
    assert value in rendered
    await action(c, "save")
    assert c.api.save_construction_company_settings.await_args.kwargs["changes"] == {
        "document_header_text": value,
    }


@pytest.mark.asyncio
async def test_invalid_accent_color_never_reaches_write(screen):
    c = screen
    await handler.settings_callback(c.callback, c.state, bot=c.bot)
    await field(c, "accent_color")
    c.message.text = "red"
    await handler.settings_message(c.message, c.state, bot=c.bot)
    assert c.data[handler.VIEW]["phase"] == "input"
    c.api.save_construction_company_settings.assert_not_awaited()


@pytest.mark.asyncio
async def test_override_logo_uses_platform_upload_then_explicit_settings_save(screen):
    c = screen
    await handler.settings_callback(c.callback, c.state, bot=c.bot)
    await field(c, LOGO_FIELD)
    c.message.text = None
    c.message.document = NS(
        file_id="telegram-test", file_size=len(PNG), mime_type="image/png", file_name="logo.png",
    )
    await handler.settings_message(c.message, c.state, bot=c.bot)
    assert c.data[handler.VIEW]["phase"] == "review"
    c.api.save_construction_company_settings.assert_not_awaited()
    await action(c, "save")
    args = c.api.save_construction_company_settings.await_args.kwargs
    assert args["changes"] == {LOGO_FIELD: str(c.file)}
    assert args["expected_row_version"] == 7
    assert "telegram-test" not in repr(c.data)


@pytest.mark.asyncio
async def test_reset_logo_restores_inheritance_without_upload(screen):
    c = screen
    await handler.settings_callback(c.callback, c.state, bot=c.bot)
    await field(c, LOGO_FIELD)
    await action(c, "reset")
    await action(c, "save")
    assert c.api.save_construction_company_settings.await_args.kwargs["changes"] == {LOGO_FIELD: None}
    c.api.upload_company_logo.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,phase", [(409, "blocked"), (503, "uncertain")])
async def test_failed_save_never_retries_or_rebases(screen, status, phase):
    c = screen
    await review(c)
    c.api.save_construction_company_settings.side_effect = ConstructionApiClientError(
        status_code=status, code="local",
    )
    await action(c, "save")
    assert c.data[handler.VIEW]["phase"] == phase
    assert c.data[handler.VIEW]["expected_row_version"] == 7
    await action(c, "save")
    assert c.api.save_construction_company_settings.await_count == 1


@pytest.mark.asyncio
async def test_changed_tenant_blocks_submit(screen):
    c = screen
    await review(c)
    c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
    await action(c, "save")
    c.api.save_construction_company_settings.assert_not_awaited()


@pytest.mark.asyncio
async def test_readonly_member_cannot_open_settings_editor(screen):
    c = screen
    c.result["can_manage"] = False
    await handler.settings_callback(c.callback, c.state, bot=c.bot)
    await field(c, "phone_override")
    assert c.data[handler.VIEW]["phase"] == "menu"


@pytest.mark.asyncio
async def test_navigation_discards_draft_preserving_other_pending_operation(screen):
    c = screen
    await review(c)
    c.callback.data = "CP:open"
    await handler.settings_navigation_middleware(AsyncMock(), c.callback, {"state": c.state})
    assert c.data[handler.VIEW] == {}
    assert c.data["construction_project_replace_attempt"] == {"status": "pending"}


def test_callbacks_and_translations_are_bounded():
    view = {"nonce": "a" * 32}
    for index, name in enumerate(FIELDS):
        assert len(handler.cb(view, "field", index).encode()) <= 64
    assert all(len(values) == 7 for values in LABELS.values())


def test_read_presentation_is_bounded_without_exposing_logo_uuid(settings):
    settings["document_header_text"] = "X" * 10000
    settings["document_footer_text"] = "Y" * 10000
    text = settings_text({"item": settings, "organization": "Organization"}, "uk")
    assert len(text) < 3900
    assert settings[LOGO_FIELD] not in text
