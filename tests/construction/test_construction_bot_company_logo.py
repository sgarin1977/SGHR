"""Local logo protocol and private Telegram workflow regression."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import ssl
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from handlers.construction import construction_company_logo as handler
from services.construction.construction_api_client import ConstructionApiClientError, PlatformApiClientSession
from services.construction.construction_company_logo_api import (
    CompanyLogoApi, SignedLogoTransfer, signed_url,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from ui.construction_company_logo import LABELS

PNG = b"\x89PNG\r\n\x1a\n" + b"local-test-payload"
ORIGIN = "https://storage.example.test"
UPLOAD = ORIGIN + "/storage/v1/object/upload/sign/bucket/logo.png?token=local-secret"
DOWNLOAD = ORIGIN + "/storage/v1/object/sign/bucket/logo.png?token=local-secret"


def result(data):
    return NS(data=data, meta={}, request_id="local-logo")


@pytest.fixture
def protocol():
    c = NS(user=uuid4(), tenant=uuid4(), file=uuid4(), profile=uuid4())
    c.session = PlatformApiClientSession(c.user, c.tenant, SecretStr("local-test"))
    c.caps = {
        "can_manage_profile": True, "storage_origin": ORIGIN,
        "image_max_size_bytes": 1024,
        "image_types": [{"extension": ".png", "mime_type": "image/png"}],
    }
    now = datetime.now(timezone.utc).isoformat()
    c.reserved = {
        "id": str(c.file), "upload_url": UPLOAD, "expires_in": 900,
        "status": "pending_upload", "mime_type": "image/png", "size_bytes": len(PNG),
    }
    c.ready = {
        "id": str(c.file), "status": "ready", "antivirus_status": "not_scanned",
        "mime_type": "image/png", "size_bytes": len(PNG), "completed_at": now,
    }
    c.profile_data = {
        "id": str(c.profile), "tenant_id": str(c.tenant), "legal_name": "Private legal name",
        "commercial_name": None, "registered_address_raw": None, "operational_address_raw": None,
        "phone": None, "email": None, "website": None,
        "signatory_name": None, "signatory_position": None,
        "logo_file_id": str(c.file), "row_version": 8,
        "created_at": now, "updated_at": now,
    }
    c.api = NS(
        company_profile_capabilities=AsyncMock(side_effect=lambda **kwargs: result(deepcopy(c.caps))),
        _request=AsyncMock(side_effect=[result(c.reserved), result(c.ready)]),
    )
    c.transfer = NS(put=AsyncMock(), get=AsyncMock(return_value=PNG))
    c.client = CompanyLogoApi(c.api, transfer=c.transfer)
    return c


@pytest.mark.asyncio
async def test_upload_uses_organization_files_then_complete_without_creating_profile(protocol):
    c = protocol
    item = await c.client.upload(session=c.session, content=PNG, mime="image/png")
    first, second = c.api._request.await_args_list
    assert first.args == ("POST", "files/upload-request")
    assert first.kwargs["json_body"] == {
        "object_type": "organization", "entity_id": str(c.tenant),
        "filename": "organization-logo.png", "mime_type": "image/png", "size_bytes": len(PNG),
    }
    assert second.args == ("POST", "files/" + str(c.file) + "/complete")
    assert item["id"] == str(c.file)
    assert c.transfer.put.await_count == 1
    assert "local-secret" not in repr(c.transfer.put.await_args.kwargs["url"])
    assert not any("company-profile" in call.args[1] for call in c.api._request.await_args_list)


@pytest.mark.asyncio
async def test_revoked_permission_stops_upload_before_reservation(protocol):
    c = protocol
    c.caps["can_manage_profile"] = False
    with pytest.raises(ConstructionApiClientError):
        await c.client.upload(session=c.session, content=PNG, mime="image/png")
    c.api._request.assert_not_awaited()
    c.transfer.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_bad_reservation_does_not_send_image_to_storage(protocol):
    c = protocol
    c.reserved["upload_url"] = "https://foreign.example.test/path?token=local-secret"
    with pytest.raises(ConstructionApiClientError):
        await c.client.upload(session=c.session, content=PNG, mime="image/png")
    c.transfer.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_bad_completion_cannot_be_used_as_ready_logo(protocol):
    c = protocol
    c.ready["id"] = str(uuid4())
    with pytest.raises(ConstructionApiClientError):
        await c.client.upload(session=c.session, content=PNG, mime="image/png")
    assert c.transfer.put.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("creating", [False, True])
async def test_logo_save_uses_exact_profile_version_or_explicit_creation(protocol, creating):
    c = protocol
    c.api._request.side_effect = None
    c.api._request.return_value = result(c.profile_data)
    await c.client.save(
        session=c.session, profile_id=None if creating else c.profile,
        expected_row_version=None if creating else 7,
        logo_file_id=c.file, legal_name="New legal name" if creating else None,
    )
    call = c.api._request.await_args
    assert call.args == ("POST" if creating else "PATCH", "organization/company-profile")
    assert call.kwargs["json_body"] == (
        {"legal_name": "New legal name", "logo_file_id": str(c.file)}
        if creating else {"expected_row_version": 7, "logo_file_id": str(c.file)}
    )


@pytest.mark.asyncio
async def test_removal_only_clears_profile_reference(protocol):
    c = protocol
    c.profile_data["logo_file_id"] = None
    c.api._request.side_effect = None
    c.api._request.return_value = result(c.profile_data)
    await c.client.save(
        session=c.session, profile_id=c.profile, expected_row_version=7, logo_file_id=None,
    )
    assert c.api._request.await_count == 1
    assert c.api._request.await_args.args == ("PATCH", "organization/company-profile")
    assert c.api._request.await_args.kwargs["json_body"] == {
        "expected_row_version": 7, "logo_file_id": None,
    }
    c.transfer.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_private_download_returns_bytes_without_signed_url(protocol):
    c = protocol
    c.api._request.side_effect = None
    c.api._request.return_value = result({
        "id": str(c.file), "download_url": DOWNLOAD, "expires_in": 900,
        "mime_type": "image/png", "size_bytes": len(PNG),
    })
    item = await c.client.download(session=c.session, file_id=c.file)
    assert item["content"] == PNG
    assert "local-secret" not in repr(item)
    c.transfer.get.assert_awaited_once()


@pytest.mark.parametrize("url", [
    "http://storage.example.test/storage/v1/object/upload/sign/bucket/logo.png?token=x",
    "https://other.example.test/storage/v1/object/upload/sign/bucket/logo.png?token=x",
    ORIGIN + "/storage/v1/object/upload/sign/bucket/../logo.png?token=x",
    ORIGIN + "/storage/v1/object/upload/sign/bucket/%2e%2e/logo.png?token=x",
    UPLOAD + "&token=second",
    UPLOAD + "#fragment",
    DOWNLOAD,
])
def test_signed_upload_url_rejects_unapproved_targets(url):
    with pytest.raises(ConstructionApiClientError) as caught:
        signed_url(url, ORIGIN, upload=True)
    assert "local-secret" not in str(caught.value)


def test_signed_https_transfer_has_no_bearer_and_requires_verified_tls(monkeypatch):
    headers = {"Content-Type": "image/png", "Content-Length": str(len(PNG))}
    response = NS(
        status=200, getheader=lambda key: headers.get(key),
        read=Mock(side_effect=[PNG, b""]),
    )
    connection = NS(
        set_debuglevel=Mock(), request=Mock(),
        getresponse=Mock(return_value=response), close=Mock(),
    )
    def factory(host, port, *, timeout, context):
        assert host == "storage.example.test" and port == 443
        assert context.check_hostname is True
        assert context.verify_mode == ssl.CERT_REQUIRED
        return connection
    monkeypatch.setattr("services.construction.construction_company_logo_api.http.client.HTTPSConnection", factory)
    content = SignedLogoTransfer()._sync(
        method="GET", url=SecretStr(DOWNLOAD), origin=ORIGIN,
        mime="image/png", content=None, size=len(PNG),
    )
    assert content == PNG
    sent_headers = connection.request.call_args.kwargs["headers"]
    assert not {"authorization", "apikey"} & {key.lower() for key in sent_headers}
    connection.set_debuglevel.assert_called_once_with(0)
    connection.close.assert_called_once()


@pytest.mark.asyncio
async def test_subject_change_blocks_all_logo_api_operations(protocol):
    c = protocol
    entry = ConstructionBotApiEntry(api=NS(construction=c.api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        c.session, {"name": "Organization", "language": "uk"},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.upload_company_logo(
            telegram_user_id=123, tenant_id=c.tenant, expected_user_id=uuid4(),
            content=PNG, mime="image/png",
        )
    c.api.company_profile_capabilities.assert_not_awaited()


@pytest.fixture
def screen(monkeypatch):
    c = NS(user=uuid4(), tenant=uuid4(), profile=uuid4(), file=uuid4())
    c.data = {
        handler.CONTEXT: {"telegram_user_id": 123, "tenant_id": str(c.tenant)},
        handler.PARENT: {
            "nonce": "source", "telegram_user_id": 123,
            "tenant_id": str(c.tenant), "actor_user_id": str(c.user), "menu_id": 1,
        },
        "last_menu_message_id": 1,
        "construction_project_replace_attempt": {"status": "pending"},
    }
    async def update(**values):
        c.data.update(values)
    c.state = NS(
        get_data=AsyncMock(side_effect=lambda: c.data),
        update_data=AsyncMock(side_effect=update),
        get_state=AsyncMock(return_value=None),
    )
    sender = NS(id=123, is_bot=False, language_code="uk")
    c.callback = NS(
        data="CLG:open:source", from_user=sender, answer=AsyncMock(),
        message=NS(
            message_id=1, chat=NS(id=123, type="private"), answer=AsyncMock(),
            answer_document=AsyncMock(),
        ),
    )
    c.message = NS(
        from_user=sender, chat=NS(id=123, type="private"), text=None, photo=None,
        document=NS(file_id="telegram-test", file_size=len(PNG), mime_type="image/png", file_name="logo.png"),
        answer=AsyncMock(return_value=NS(message_id=1)),
    )
    c.context = {
        "profile_id": str(c.profile), "row_version": 7, "logo_file_id": str(c.file),
        "can_manage": True, "can_transfer": True, "max_bytes": 1024,
        "mime_types": ("image/png",), "actor_user_id": str(c.user),
        "organization": "Organization", "language": "uk",
    }
    c.api = NS(
        company_logo_context=AsyncMock(side_effect=lambda **kwargs: deepcopy(c.context)),
        upload_company_logo=AsyncMock(return_value={
            "item": {"id": str(c.file), "mime_type": "image/png", "size_bytes": len(PNG)},
            "actor_user_id": str(c.user),
        }),
        save_company_logo=AsyncMock(return_value={
            "item": {"id": str(c.profile), "tenant_id": str(c.tenant), "row_version": 8},
            "actor_user_id": str(c.user),
        }),
        download_company_logo=AsyncMock(return_value={
            "id": str(c.file), "content": PNG, "filename": "organization-logo.png",
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


async def action(c, name):
    c.callback.message.message_id = c.data["last_menu_message_id"]
    c.callback.data = handler.cb(c.data[handler.VIEW], name)
    await handler.logo_callback(c.callback, c.state, bot=c.bot)


async def upload(c):
    await handler.logo_callback(c.callback, c.state, bot=c.bot)
    await action(c, "upload")
    await handler.logo_message(c.message, c.state, bot=c.bot)


@pytest.mark.asyncio
async def test_upload_requires_explicit_profile_save_and_drops_binary_from_state(screen):
    c = screen
    await upload(c)
    assert c.data[handler.VIEW]["phase"] == "review"
    c.api.save_company_logo.assert_not_awaited()
    assert PNG not in c.data.values()
    assert "telegram-test" not in repr(c.data)
    assert "upload_url" not in repr(c.data)
    old_callback = handler.cb(c.data[handler.VIEW], "save")
    await action(c, "save")
    arguments = c.api.save_company_logo.await_args.kwargs
    assert arguments["expected_row_version"] == 7
    assert arguments["logo_file_id"] == c.file
    assert arguments["expected_user_id"] == c.user
    assert c.data[handler.VIEW]["phase"] == "done"
    c.callback.data = old_callback
    await handler.logo_callback(c.callback, c.state, bot=c.bot)
    assert c.api.save_company_logo.await_count == 1


@pytest.mark.asyncio
async def test_upload_before_profile_creation_then_explicit_legal_name(screen):
    c = screen
    c.context.update(profile_id=None, row_version=None, logo_file_id=None)
    await upload(c)
    assert c.data[handler.VIEW]["phase"] == "legal"
    assert c.api.upload_company_logo.await_count == 1
    c.api.save_company_logo.assert_not_awaited()
    c.message.text = "New private legal name"
    await handler.logo_message(c.message, c.state, bot=c.bot)
    assert c.data[handler.VIEW]["phase"] == "review"
    await action(c, "save")
    arguments = c.api.save_company_logo.await_args.kwargs
    assert arguments["profile_id"] is None
    assert arguments["expected_row_version"] is None
    assert arguments["legal_name"] == "New private legal name"
    assert "New private legal name" not in repr(c.data)


@pytest.mark.asyncio
async def test_logo_removal_requires_confirmation_and_does_not_upload(screen):
    c = screen
    await handler.logo_callback(c.callback, c.state, bot=c.bot)
    await action(c, "remove")
    c.api.save_company_logo.assert_not_awaited()
    await action(c, "save")
    assert c.api.save_company_logo.await_args.kwargs["logo_file_id"] is None
    c.api.upload_company_logo.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,phase", [(409, "blocked"), (503, "uncertain")])
async def test_save_failure_never_retries_or_rebases(screen, status, phase):
    c = screen
    await upload(c)
    c.api.save_company_logo.side_effect = ConstructionApiClientError(status_code=status, code="local")
    await action(c, "save")
    assert c.data[handler.VIEW]["phase"] == phase
    assert c.data[handler.VIEW]["expected_row_version"] == 7
    await action(c, "save")
    assert c.api.save_company_logo.await_count == 1


@pytest.mark.asyncio
async def test_oversized_telegram_image_is_rejected_before_download_or_reservation(screen):
    c = screen
    await handler.logo_callback(c.callback, c.state, bot=c.bot)
    await action(c, "upload")
    c.message.document.file_size = 1025
    await handler.logo_message(c.message, c.state, bot=c.bot)
    assert c.data[handler.VIEW]["phase"] == "image"
    c.bot.get_file.assert_not_awaited()
    c.api.upload_company_logo.assert_not_awaited()


@pytest.mark.asyncio
async def test_tenant_switch_blocks_profile_write(screen):
    c = screen
    await upload(c)
    c.data[handler.CONTEXT]["tenant_id"] = str(uuid4())
    await action(c, "save")
    c.api.save_company_logo.assert_not_awaited()


@pytest.mark.asyncio
async def test_preview_sends_bytes_and_keeps_signed_urls_out_of_state(screen):
    c = screen
    await handler.logo_callback(c.callback, c.state, bot=c.bot)
    await action(c, "preview")
    document = c.callback.message.answer_document.await_args.args[0]
    assert document.data == PNG
    assert "content" not in c.data[handler.VIEW]
    assert "download_url" not in repr(c.data)


@pytest.mark.asyncio
async def test_navigation_detaches_uploaded_logo_and_preserves_other_attempts(screen):
    c = screen
    await upload(c)
    c.callback.data = "CP:open"
    await handler.logo_navigation_middleware(AsyncMock(), c.callback, {"state": c.state})
    assert c.data[handler.VIEW]["menu_id"] is None
    assert c.data["construction_project_replace_attempt"] == {"status": "pending"}


def test_logo_callbacks_and_languages_are_bounded():
    view = {"nonce": "a" * 32}
    for action_name in ("refresh", "upload", "preview", "remove", "save"):
        assert len(handler.cb(view, action_name).encode()) <= 64
    assert all(len(values) == 7 for values in LABELS.values())
